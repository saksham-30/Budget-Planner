import asyncio
import json
import threading
import time
from datetime import date
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from starlette.requests import ClientDisconnect

from backend import main, supervisor
from backend.models import AnalyzeRequest, TripPlanRequest, TripPreferences
from backend.persistence import TripStore
from conftest import TEST_OWNER, register_test_trip


def _preferences():
    return TripPreferences(
        destination="Goa",
        start_date=date(2027, 1, 10),
        end_date=date(2027, 1, 15),
        travelers=2,
        budget=30000,
        currency="INR",
    )


def _http_scope():
    return {
        "type": "http",
        "asgi": {"spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/stream",
        "raw_path": b"/api/stream",
        "query_string": b"",
        "headers": [],
        "server": ("testserver", 80),
        "client": ("testclient", 1234),
    }


async def _disconnect_on_body(response):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            raise OSError("simulated client disconnect")

    with pytest.raises(ClientDisconnect):
        await response(_http_scope(), receive, send)
    return sent


def _decode_sent_body(sent):
    return b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    ).decode()


def test_disconnect_during_planning_closes_generator_without_success_or_save(
    tmp_path, monkeypatch
):
    store = TripStore(tmp_path / "disconnect-plan.sqlite3")
    store.save_preferences("disconnect-plan", _preferences())
    register_test_trip(store, "disconnect-plan")
    monkeypatch.setattr(main, "TripStore", lambda: store)
    cleanup = []
    continued = []

    def fake_plan_stream(*args):
        try:
            yield {"type": "stage", "stage": "retrieve_context"}
            continued.append(True)
        finally:
            cleanup.append("closed")

    monkeypatch.setattr(supervisor, "run_planning_thread_stream", fake_plan_stream)

    async def run():
        response = await main.plan_trip_stream(
            "disconnect-plan",
            TripPlanRequest(query="Plan this trip"),
                main.LocalPrincipal(TEST_OWNER, "test-session", "test-csrf"),
        )
        return await _disconnect_on_body(response)

    sent = asyncio.run(run())
    body = _decode_sent_body(sent)

    assert sent[0]["status"] == 200
    assert '"type": "stage"' in body
    assert '"type": "complete"' not in body
    assert '"type": "error"' not in body
    assert cleanup == ["closed"]
    assert continued == []
    assert store.get_conversation("disconnect-plan") == []


def test_disconnect_during_follow_up_closes_generator_without_saving(
    tmp_path, monkeypatch
):
    store = TripStore(tmp_path / "disconnect-follow-up.sqlite3")
    store.save_preferences("disconnect-follow-up", _preferences())
    register_test_trip(store, "disconnect-follow-up")
    monkeypatch.setattr(main, "TripStore", lambda: store)
    cleanup = []
    observed_queries = []

    def fake_plan_stream(trip_id, query, preferences):
        observed_queries.append(query)
        try:
            yield {"type": "stage", "stage": "retrieve_context"}
            raise AssertionError("The cancelled stream was advanced again.")
        finally:
            cleanup.append("closed")

    monkeypatch.setattr(supervisor, "run_planning_thread_stream", fake_plan_stream)

    async def run():
        response = await main.plan_trip_stream(
            "disconnect-follow-up",
            TripPlanRequest(query="Increase my budget to 45000 INR"),
                main.LocalPrincipal(TEST_OWNER, "test-session", "test-csrf"),
        )
        return await _disconnect_on_body(response)

    sent = asyncio.run(run())

    assert '"type": "complete"' not in _decode_sent_body(sent)
    assert observed_queries == ["Increase my budget to 45000 INR"]
    assert cleanup == ["closed"]
    assert store.get_conversation("disconnect-follow-up") == []


def test_agent_cancellation_closes_async_iterator_without_error_or_complete(
    monkeypatch,
):
    monkeypatch.setattr(main, "_ollama_status", AsyncMock(return_value=(True, True)))
    cleanup = []
    waiting = asyncio.Event()
    agent_waiting = asyncio.Event()

    class FakeAgent:
        async def astream_events(self, inputs, version):
            try:
                yield {
                    "event": "on_tool_start",
                    "name": "check_budget_feasibility",
                    "data": {"input": {}},
                }
                agent_waiting.set()
                await waiting.wait()
                yield {
                    "event": "on_chain_end",
                    "name": "LangGraph",
                    "data": {"output": {"messages": [HumanMessage(content="query"), AIMessage(content="done")]}},
                }
            finally:
                cleanup.append("closed")

    monkeypatch.setattr("backend.agent.agent", FakeAgent())

    async def run():
        response = await main.analyze_stream(AnalyzeRequest(query="Check this trip"))
        first_body_sent = asyncio.Event()
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.body" and message.get("body"):
                first_body_sent.set()

        task = asyncio.create_task(response(_http_scope(), receive, send))
        await asyncio.wait_for(first_body_sent.wait(), timeout=2)
        await asyncio.wait_for(agent_waiting.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return sent

    sent = asyncio.run(run())
    body = _decode_sent_body(sent)

    assert sent[0]["status"] == 200
    assert '"type": "tool_start"' in body
    assert '"type": "complete"' not in body
    assert '"type": "error"' not in body
    assert "[DONE]" not in body
    assert cleanup == ["closed"]


def test_supervisor_closes_graph_stream_when_consumer_closes_generator(
    monkeypatch,
):
    cleanup = []

    class FakeGraph:
        def compile(self, checkpointer):
            return self

        def stream(self, *args, **kwargs):
            def updates():
                try:
                    yield {"retrieve_context": {}}
                    raise AssertionError("The closed graph stream was advanced again.")
                finally:
                    cleanup.append("closed")

            return updates()

    monkeypatch.setattr(supervisor, "build_supervisor", FakeGraph)
    events = supervisor.run_planning_thread_stream(
        "cancelled-graph",
        "Plan this trip",
        _preferences(),
        checkpointer=object(),
    )

    assert next(events)["stage"] == "retrieve_context"
    events.close()

    assert cleanup == ["closed"]


def test_sync_graph_step_finishes_then_iterator_is_closed_on_cancellation():
    started = threading.Event()
    step_finished = threading.Event()
    advanced_again = []
    cleanup = []

    def sync_steps():
        try:
            started.set()
            time.sleep(0.03)
            step_finished.set()
            yield "first step"
            advanced_again.append(True)
        finally:
            cleanup.append("closed")

    async def consume():
        async for _ in main._iterate_sync_and_close(sync_steps()):
            pass

    async def cancel_while_step_is_running():
        task = asyncio.create_task(consume())
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_while_step_is_running())

    assert step_finished.is_set()
    assert advanced_again == []
    assert cleanup == ["closed"]


def test_normal_follow_up_stream_still_emits_completion(tmp_path, monkeypatch, allow_local_owner):
    database = tmp_path / "normal-follow-up.sqlite3"
    store = TripStore(database)
    store.save_preferences("normal-follow-up", _preferences())
    register_test_trip(store, "normal-follow-up")
    monkeypatch.setattr(main, "TripStore", lambda: store)
    monkeypatch.setattr(supervisor, "DATABASE_PATH", database)
    response = TestClient(main.app).post(
        "/api/trips/normal-follow-up/plan/stream",
        json={"query": "Increase my budget to 45000 INR"},
    )

    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: {")
    ]
    complete = next(event for event in events if event["type"] == "complete")
    assert complete["plan"]["preferences"]["budget"] == 45000
    assert events[-1]["type"] == "complete"
    assert "[DONE]" in response.text
    assert len(store.get_conversation("normal-follow-up")) == 2
