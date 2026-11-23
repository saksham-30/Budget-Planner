"""Reliability tests for agent outputs, tool calls, and loop termination."""

import logging

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

import backend.agent as agent_module
from backend.agent import AgentExecutionError, build_agent


class SequenceLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.inputs = []

    def bind_tools(self, tools):
        self.tools = tools
        return self

    def invoke(self, messages):
        self.inputs.append(messages)
        return self.responses.pop(0)


def _call(name, arguments, call_id):
    return {"name": name, "args": arguments, "id": call_id}


def _invoke(llm, messages=None, config=None):
    return build_agent(llm=llm).invoke(
        {"messages": messages if messages is not None else [HumanMessage(content="Plan my trip")]},
        config=config,
    )


@pytest.mark.parametrize(
    "tool_call",
    [
        _call("unknown_tool", {}, "unknown-1"),
        _call("convert_currency", {"amount": 5}, "missing-1"),
        _call(
            "convert_currency",
            {"amount": "not-a-number", "from_currency": "EUR", "to_currency": "INR"},
            "type-1",
        ),
        _call(
            "convert_currency",
            {"amount": 5, "from_currency": "EUR", "to_currency": "INR", "extra": True},
            "extra-1",
        ),
    ],
)
def test_invalid_tool_calls_are_rejected_before_execution(tool_call):
    llm = SequenceLLM(AIMessage(content="", tool_calls=[tool_call]))

    with pytest.raises(AgentExecutionError):
        _invoke(llm)

    assert len(llm.inputs) == 1


def test_malformed_model_tool_call_cannot_be_treated_as_final_text():
    malformed = AIMessage(
        content="The trip is feasible.",
        invalid_tool_calls=[
            {
                "name": "check_budget_feasibility",
                "args": "{not-json",
                "id": "malformed-1",
                "error": "parse failure",
            }
        ],
    )
    llm = SequenceLLM(malformed)

    with pytest.raises(AgentExecutionError, match="malformed tool call"):
        _invoke(llm)


def test_empty_model_output_is_not_a_completed_agent_result():
    llm = SequenceLLM(AIMessage(content=""))

    with pytest.raises(AgentExecutionError, match="final text response"):
        _invoke(llm)


def test_invalid_agent_state_is_rejected():
    llm = SequenceLLM(AIMessage(content="This should not run."))

    with pytest.raises(AgentExecutionError, match="state is invalid"):
        _invoke(llm, messages=[])

    assert llm.inputs == []


def test_identical_tool_calls_are_rejected_before_either_executes():
    call = _call(
        "convert_currency",
        {"amount": 5, "from_currency": "EUR", "to_currency": "INR"},
        "duplicate-1",
    )
    duplicate = {**call, "id": "duplicate-2"}
    llm = SequenceLLM(AIMessage(content="", tool_calls=[call, duplicate]))

    with pytest.raises(AgentExecutionError, match="identical tool call"):
        _invoke(llm)

    assert len(llm.inputs) == 1


def test_distinct_arguments_for_same_tool_remain_valid():
    llm = SequenceLLM(
        AIMessage(
            content="",
            tool_calls=[
                _call(
                    "convert_currency",
                    {"amount": 5, "from_currency": "EUR", "to_currency": "INR"},
                    "convert-1",
                ),
                _call(
                    "convert_currency",
                    {"amount": 10, "from_currency": "EUR", "to_currency": "INR"},
                    "convert-2",
                ),
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                _call(
                    "check_budget_feasibility",
                    {
                        "total_cost": 475,
                        "cost_currency": "INR",
                        "budget": 1000,
                        "budget_currency": "INR",
                    },
                    "budget-1",
                )
            ],
        ),
        AIMessage(content="Both conversions are complete; the trip is feasible."),
    )

    result = _invoke(llm)
    tool_results = [message for message in result["messages"] if isinstance(message, ToolMessage)]

    assert [message.name for message in tool_results] == [
        "convert_currency",
        "convert_currency",
        "check_budget_feasibility",
    ]
    assert result["messages"][-1].content.startswith("Both conversions")


def test_model_iteration_limit_stops_tool_loop(monkeypatch):
    monkeypatch.setattr(agent_module, "MAX_ITERATIONS", 2)
    monkeypatch.setattr(agent_module, "MAX_TOOL_CALLS", 2)
    llm = SequenceLLM(
        AIMessage(
            content="",
            tool_calls=[
                _call(
                    "convert_currency",
                    {"amount": 5, "from_currency": "EUR", "to_currency": "INR"},
                    "convert-1",
                )
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                _call(
                    "convert_currency",
                    {"amount": 6, "from_currency": "EUR", "to_currency": "INR"},
                    "convert-2",
                )
            ],
        ),
    )

    with pytest.raises(AgentExecutionError, match="iteration limit"):
        _invoke(llm, config={"recursion_limit": agent_module.MAX_ITERATIONS * 3})

    assert len(llm.inputs) == 2


def test_tool_call_count_limit_rejects_an_oversized_batch():
    calls = [
        _call(
            "convert_currency",
            {"amount": index + 1, "from_currency": "EUR", "to_currency": "INR"},
            f"convert-{index}",
        )
        for index in range(agent_module.MAX_TOOL_CALLS + 1)
    ]
    llm = SequenceLLM(AIMessage(content="", tool_calls=calls))

    with pytest.raises(AgentExecutionError, match="tool-call limit"):
        _invoke(llm)

    assert len(llm.inputs) == 1


def test_tool_exception_is_sanitized_and_cannot_complete_successfully(
    monkeypatch, caplog
):
    @tool
    def fail_with_sensitive_detail(value: int) -> str:
        """Raise a test exception containing a secret-like path."""
        raise RuntimeError(r"C:\private\secret-token")

    monkeypatch.setattr(agent_module, "TOOLS", [fail_with_sensitive_detail])
    llm = SequenceLLM(
        AIMessage(
            content="",
            tool_calls=[
                _call("fail_with_sensitive_detail", {"value": 1}, "fail-1")
            ],
        ),
        AIMessage(content="The requested operation succeeded."),
    )

    with caplog.at_level(logging.ERROR, logger="backend.agent"):
        with pytest.raises(AgentExecutionError, match="tool failed"):
            _invoke(llm)

    tool_result = next(
        message
        for message in llm.inputs[-1]
        if isinstance(message, ToolMessage)
    )
    assert tool_result.status == "error"
    assert tool_result.content == agent_module._TOOL_ERROR_MESSAGE
    assert "secret-token" not in tool_result.content
    assert "Agent tool execution failed (error_type=RuntimeError)" in caplog.text
    assert "secret-token" not in caplog.text


def test_tool_validation_result_cannot_be_reported_as_success():
    llm = SequenceLLM(
        AIMessage(
            content="",
            tool_calls=[
                _call(
                    "convert_currency",
                    {"amount": -1, "from_currency": "EUR", "to_currency": "INR"},
                    "invalid-amount-1",
                )
            ],
        ),
        AIMessage(content="The conversion succeeded."),
    )

    with pytest.raises(AgentExecutionError, match="tool failed"):
        _invoke(llm)


def test_run_agent_rejects_empty_or_incomplete_alternate_state(monkeypatch):
    class IncompleteAgent:
        def invoke(self, payload, config):
            return {
                "messages": [
                    *payload["messages"],
                    AIMessage(content="The trip is feasible."),
                ]
            }

    monkeypatch.setattr(agent_module, "agent", IncompleteAgent())

    with pytest.raises(AgentExecutionError, match="budget check"):
        agent_module.run_agent("Can I afford this trip?")


def test_run_agent_uses_a_bounded_graph_recursion_limit(monkeypatch):
    class CompleteAgent:
        def __init__(self):
            self.config = None

        def invoke(self, payload, config):
            self.config = config
            return {
                "messages": [
                    *payload["messages"],
                    ToolMessage(
                        content="Trip is feasible.",
                        tool_call_id="budget-1",
                        name="check_budget_feasibility",
                    ),
                    AIMessage(content="The trip is feasible."),
                ]
            }

    complete_agent = CompleteAgent()
    monkeypatch.setattr(agent_module, "agent", complete_agent)

    agent_module.run_agent("Can I afford this trip?")

    assert complete_agent.config == {
        "recursion_limit": agent_module.MAX_ITERATIONS * 3
    }


def test_normal_successful_agent_termination_is_preserved():
    llm = SequenceLLM(
        AIMessage(
            content="",
            tool_calls=[
                _call(
                    "check_budget_feasibility",
                    {
                        "total_cost": 500,
                        "cost_currency": "INR",
                        "budget": 1000,
                        "budget_currency": "INR",
                    },
                    "budget-1",
                )
            ],
        ),
        AIMessage(content="The trip is feasible."),
    )

    result = _invoke(llm)

    assert result["messages"][-1].content == "The trip is feasible."
    assert any(
        isinstance(message, ToolMessage)
        and message.name == "check_budget_feasibility"
        and message.status == "success"
        for message in result["messages"]
    )
