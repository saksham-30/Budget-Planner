"""
FastAPI application — Travel Budget Planner backend.

Endpoints:
  POST /api/analyze   — runs the LangGraph agent and returns a structured response.
  GET  /api/health    — checks Ollama reachability and model availability.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, suppress
from typing import TypeVar

import anyio
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from . import rates
from .config import CORS_ORIGINS, FX_ENABLED, OLLAMA_HOST, OLLAMA_MODEL
from .models import (
    AnalyzeRequest,
    AnalyzeResponse,
    ErrorResponse,
    HealthResponse,
    RateInfo,
    RatesResponse,
    TraceEvent,
    TripPlanRequest,
    TripPlanResponse,
    TripPreferences,
    TripSummary,
)
from .fallback import reconcile_agent_result
from .tracing import build_trace, extract_structured_data
from .persistence import PersistenceError, TripStore
from .rag import DestinationCostGuide
from .auth import LocalPrincipal, require_csrf, require_local_owner, router as auth_router

logger = logging.getLogger(__name__)

_StreamItem = TypeVar("_StreamItem")
_SyncResult = TypeVar("_SyncResult")


class _SynchronousIteratorExhausted(Exception):
    pass


def _next_or_exhausted(iterator: Iterator[_StreamItem]) -> _StreamItem:
    try:
        return next(iterator)
    except StopIteration as exc:
        raise _SynchronousIteratorExhausted from exc


async def _run_sync_to_completion(
    function: Callable[..., _SyncResult],
    *args: object,
) -> _SyncResult:
    operation = asyncio.create_task(run_in_threadpool(function, *args))
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        with anyio.CancelScope(shield=True):
            try:
                await operation
            except Exception as exc:
                logger.warning(
                    "Synchronous stream operation failed during cancellation (error_type=%s)",
                    type(exc).__name__,
                )
        raise


async def _iterate_sync_and_close(
    iterator: Iterator[_StreamItem],
) -> AsyncIterator[_StreamItem]:
    try:
        while True:
            try:
                item = await _run_sync_to_completion(_next_or_exhausted, iterator)
            except _SynchronousIteratorExhausted:
                break
            yield item
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            await _run_sync_to_completion(close)


class ClosingStreamingResponse(StreamingResponse):
    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            close = getattr(self.body_iterator, "aclose", None)
            if close is not None:
                try:
                    with anyio.CancelScope(shield=True):
                        await close()
                except Exception:
                    logger.exception("Unable to close streaming response body")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    rates.initialize_rates()
    refresh_task = None
    if FX_ENABLED:
        await rates.refresh_rates(force=True)
        refresh_task = asyncio.create_task(rates.refresh_rates_forever())
    try:
        yield
    finally:
        if refresh_task is not None:
            refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await refresh_task


app = FastAPI(
    title="Travel Budget Planner API",
    description="Autonomous AI agent that answers travel-budget questions via two-tool reasoning.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=5),
    retry=retry_if_exception_type(httpx.HTTPError),
    reraise=True,
)
async def _check_ollama() -> tuple[bool, bool]:
    """Returns (ollama_reachable, model_available) with retry on network errors."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(f"{OLLAMA_HOST}/api/tags")
        if r.status_code != 200:
            return False, False
        data = r.json()
        models = [m.get("name", "") for m in data.get("models", [])]
        model_available = OLLAMA_MODEL in models
        return True, model_available


async def _ollama_status() -> tuple[bool, bool]:
    try:
        return await _check_ollama()
    except (httpx.HTTPError, ValueError):
        logger.warning("Unable to check Ollama health at %s", OLLAMA_HOST, exc_info=True)
        return False, False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/api/health", response_model=HealthResponse)
async def health():
    """Check whether Ollama is running and the required model is pulled."""
    reachable, available = await _ollama_status()

    if not reachable:
        msg = (
            "Unable to connect to Ollama. Make sure Ollama is running "
            f"at {OLLAMA_HOST} (run: ollama serve)."
        )
    elif not available:
        msg = (
            f"Ollama is running but model '{OLLAMA_MODEL}' is not pulled. "
            f"Run: ollama pull {OLLAMA_MODEL}"
        )
    else:
        msg = f"Ollama is running and model '{OLLAMA_MODEL}' is available."

    return HealthResponse(
        ollama_reachable=reachable,
        model_available=available,
        model=OLLAMA_MODEL,
        message=msg,
    )


@app.get("/api/destinations", response_model=list[str])
async def list_destinations():
    return DestinationCostGuide().destinations()


@app.get("/api/rates", response_model=RatesResponse)
async def get_rates():
    """Current exchange-rate snapshot with its provenance and freshness."""
    snapshot = rates.get_snapshot()
    return RatesResponse(
        base=rates.BASE_CURRENCY,
        pairs=[
            RateInfo(
                from_currency=pair["from"],
                to_currency=pair["to"],
                rate=pair["rate"],
            )
            for pair in rates.rate_pairs()
        ],
        source=snapshot.source,
        label=snapshot.label,
        as_of=snapshot.as_of,
        fetched_at=snapshot.fetched_at,
        is_live=snapshot.is_live,
        stale=snapshot.stale,
        note=snapshot.note,
    )


@app.get("/api/trips", response_model=list[TripSummary])
async def list_trips(principal: LocalPrincipal = Depends(require_local_owner)):
    return TripStore().list_trips_for_owner(principal.owner_id)


@app.put("/api/trips/{trip_id}/preferences", response_model=TripPreferences)
@app.post("/api/trips/{trip_id}/preferences", response_model=TripPreferences)
async def save_trip_preferences(
    trip_id: str,
    preferences: TripPreferences,
    principal: LocalPrincipal = Depends(require_csrf),
):
    if not trip_id.strip() or len(trip_id) > 100:
        raise HTTPException(status_code=422, detail="trip_id must contain 1 to 100 characters.")
    TripStore().save_preferences_for_owner(trip_id, preferences, principal.owner_id)
    return preferences


@app.get("/api/trips/{trip_id}/preferences", response_model=TripPreferences)
async def get_trip_preferences(
    trip_id: str,
    principal: LocalPrincipal = Depends(require_local_owner),
):
    preferences = TripStore().get_preferences_for_owner(trip_id, principal.owner_id)
    if preferences is None:
        raise HTTPException(status_code=404, detail="No saved preferences for this trip.")
    return preferences


@app.get("/api/trips/{trip_id}/conversation", response_model=list[dict[str, str]])
async def get_trip_conversation(
    trip_id: str,
    principal: LocalPrincipal = Depends(require_local_owner),
):
    return TripStore().get_conversation_for_owner(trip_id, principal.owner_id)


@app.get("/api/trips/{trip_id}/plan", response_model=TripPlanResponse)
async def get_trip_plan(
    trip_id: str,
    principal: LocalPrincipal = Depends(require_local_owner),
):
    store = TripStore()
    if store.get_preferences_for_owner(trip_id, principal.owner_id) is None:
        raise HTTPException(status_code=404, detail="No saved preferences for this trip.")
    from .supervisor import get_saved_plan

    result = await run_in_threadpool(get_saved_plan, trip_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No plan has been generated for this trip.")
    result.conversation = store.get_conversation_for_owner(trip_id, principal.owner_id)
    return result


@app.post("/api/trips/{trip_id}/plan", response_model=TripPlanResponse)
async def plan_trip(
    trip_id: str,
    request: TripPlanRequest,
    principal: LocalPrincipal = Depends(require_csrf),
):
    if not trip_id.strip() or len(trip_id) > 100:
        raise HTTPException(status_code=422, detail="trip_id must contain 1 to 100 characters.")
    store = TripStore()
    preferences = store.get_preferences_for_owner(trip_id, principal.owner_id)
    if preferences is None:
        raise HTTPException(
            status_code=404,
            detail="Save trip preferences before requesting a plan.",
        )
    try:
        from .supervisor import run_planning_thread

        result = await run_in_threadpool(
            run_planning_thread, trip_id, request.query, preferences
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Persistent trip planner failed for trip %s", trip_id)
        raise HTTPException(
            status_code=500,
            detail="Trip planning failed; no result was saved.",
        ) from exc
    result.conversation = store.save_plan_update_for_owner(
        trip_id,
        result.preferences,
        request.query,
        result.answer,
        principal.owner_id,
    )
    return result


@app.post("/api/trips/{trip_id}/plan/stream")
async def plan_trip_stream(
    trip_id: str,
    request: TripPlanRequest,
    principal: LocalPrincipal = Depends(require_csrf),
):
    """Stream planning stages as Server-Sent Events, ending with the complete plan."""
    if not trip_id.strip() or len(trip_id) > 100:
        raise HTTPException(status_code=422, detail="trip_id must contain 1 to 100 characters.")
    store = TripStore()
    preferences = store.get_preferences_for_owner(trip_id, principal.owner_id)
    if preferences is None:
        raise HTTPException(
            status_code=404,
            detail="Save trip preferences before requesting a plan.",
        )

    async def event_stream():
        completed = False
        events = None
        try:
            from .supervisor import run_planning_thread_stream

            events = _iterate_sync_and_close(
                run_planning_thread_stream(trip_id, request.query, preferences)
            )
            async for event in events:
                if event.get("type") == "complete":
                    plan = TripPlanResponse.model_validate(event["plan"])
                    plan.conversation = store.save_plan_update_for_owner(
                        trip_id,
                        plan.preferences,
                        request.query,
                        plan.answer,
                        principal.owner_id,
                    )
                    event["plan"] = json.loads(plan.model_dump_json())
                    completed = True
                yield f"data: {json.dumps(event)}\n\n"
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Streaming trip planner failed for trip %s", trip_id)
            yield f"data: {json.dumps({'type': 'error', 'message': 'Trip planning failed; no result was saved.'})}\n\n"
        else:
            if not completed:
                logger.error("Trip planning stream ended without a result")
                yield f"data: {json.dumps({'type': 'error', 'message': 'Trip planning ended before a plan was produced.'})}\n\n"
        finally:
            if events is not None:
                try:
                    with anyio.CancelScope(shield=True):
                        await events.aclose()
                except Exception:
                    logger.exception("Unable to close trip-planning event iterator")

        yield "data: [DONE]\n\n"

    return ClosingStreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest):
    """
    Run the autonomous LangGraph agent on the user's travel-budget query.

    The agent decides which tools to call and in what order.
    Returns a structured response with the final answer, feasibility data,
    and the full execution trace derived from real LangGraph messages.
    """
    # Pre-flight Ollama check
    reachable, available = await _ollama_status()
    if not reachable:
        raise HTTPException(
            status_code=503,
            detail=(
                "Unable to connect to Ollama. Make sure Ollama is running "
                f"at {OLLAMA_HOST} (run: ollama serve)."
            ),
        )
    if not available:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Model '{OLLAMA_MODEL}' is not available in Ollama. "
                f"Run: ollama pull {OLLAMA_MODEL}"
            ),
        )

    # Run agent (import here to avoid build-time Ollama requirement)
    try:
        from .agent import run_agent
        messages = await run_in_threadpool(run_agent, request.query)
    except httpx.HTTPError as exc:
        logger.exception("Unable to communicate with Ollama")
        raise HTTPException(
            status_code=502,
            detail="Unable to communicate with Ollama while processing the request.",
        ) from exc
    except Exception as exc:
        logger.exception("Travel budget agent failed")
        raise HTTPException(
            status_code=500,
            detail="The travel budget agent failed while processing the request.",
        ) from exc

    # Build trace from real messages
    trace = build_trace(messages)

    # Extract structured data from tool results
    structured = extract_structured_data(trace)

    # Collect the final answer text
    final_answer = ""
    from langchain_core.messages import AIMessage
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", []):
            final_answer = msg.content if isinstance(msg.content, str) else str(msg.content)
            break

    if not final_answer:
        raise HTTPException(
            status_code=502,
            detail="The agent did not produce a final text response.",
        )

    outcome = reconcile_agent_result(request.query, final_answer, structured, trace)

    return AnalyzeResponse(
        answer=outcome.answer,
        feasible=outcome.structured["feasible"],
        total_cost=outcome.structured["total_cost"],
        budget=outcome.structured["budget"],
        remaining=outcome.structured["remaining"],
        overage=outcome.structured["overage"],
        currency=outcome.structured["currency"],
        trace=outcome.trace,
    )


@app.post("/api/analyze/stream")
async def analyze_stream(request: AnalyzeRequest):
    """
    Streaming version of /api/analyze using Server-Sent Events.
    Pushes trace events progressively as the agent processes them.
    """
    reachable, available = await _ollama_status()
    if not reachable or not available:
        message = (
            f"Ollama is unavailable at {OLLAMA_HOST}."
            if not reachable
            else f"Model '{OLLAMA_MODEL}' is not available. Run: ollama pull {OLLAMA_MODEL}"
        )
        raise HTTPException(status_code=503, detail=message)

    async def event_stream():
        agent_events = None
        try:
            from langchain_core.messages import HumanMessage
            from .agent import agent as _agent

            completed = False
            agent_events = _agent.astream_events(
                {"messages": [HumanMessage(content=request.query)]},
                version="v2",
            )
            async for event in agent_events:
                kind = event.get("event", "")
                data = event.get("data", {})

                if kind == "on_tool_start":
                    payload = {
                        "type": "tool_start",
                        "tool": event.get("name", ""),
                        "arguments": data.get("input", {}),
                    }
                    yield f"data: {json.dumps(payload)}\n\n"

                elif kind == "on_tool_end":
                    output = data.get("output", "")
                    if hasattr(output, "content"):
                        output = output.content
                    payload = {
                        "type": "tool_end",
                        "tool": event.get("name", ""),
                        "result": output if isinstance(output, str) else str(output),
                    }
                    yield f"data: {json.dumps(payload)}\n\n"

                elif kind == "on_chain_end" and event.get("name") == "LangGraph":
                    # Final state — emit the complete response
                    output = data.get("output", {})
                    msgs = output.get("messages", [])
                    trace = build_trace(msgs)
                    structured = extract_structured_data(trace)
                    final_answer = ""
                    from langchain_core.messages import AIMessage
                    for msg in reversed(msgs):
                        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", []):
                            final_answer = msg.content if isinstance(msg.content, str) else str(msg.content)
                            break
                    if not final_answer:
                        logger.error("Agent stream ended without a final assistant response")
                        yield f"data: {json.dumps({'type': 'error', 'message': 'The agent did not produce a final text response.'})}\n\n"
                        completed = True
                        continue
                    outcome = reconcile_agent_result(
                        request.query, final_answer, structured, trace
                    )
                    payload = {
                        "type": "complete",
                        "answer": outcome.answer,
                        **outcome.structured,
                        "trace": [t.model_dump() for t in outcome.trace],
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
                    completed = True

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Streaming travel budget agent failed")
            yield f"data: {json.dumps({'type': 'error', 'message': 'The travel budget agent failed while processing the request.'})}\n\n"
        else:
            if not completed:
                logger.error("Agent event stream ended without a final graph response")
                yield f"data: {json.dumps({'type': 'error', 'message': 'The agent stream ended before a result was produced.'})}\n\n"
        finally:
            close = getattr(agent_events, "aclose", None)
            if close is not None:
                try:
                    with anyio.CancelScope(shield=True):
                        await close()
                except Exception:
                    logger.exception("Unable to close agent event iterator")

        yield "data: [DONE]\n\n"

    return ClosingStreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------

@app.exception_handler(PersistenceError)
async def persistence_exception_handler(request: Request, exc: PersistenceError):
    logger.warning(
        "Persistence request failed (kind=%s)",
        exc.kind,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(error=exc.public_message).model_dump(),
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled application exception for %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(error="Internal server error").model_dump(),
    )
