"""
LangGraph agent construction for the Travel Budget Planner.

Uses a LangGraph StateGraph with ChatOllama, ToolNode, and conditional
edges to let the model decide when to finish or request another tool.

The loop ends when the model stops requesting tools — that's the model's decision,
not a fixed step count. MAX_ITERATIONS is a defensive safety cap only.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langgraph.graph.message import add_messages
from pydantic import BaseModel, ValidationError

from .config import OLLAMA_HOST, OLLAMA_MODEL, MAX_ITERATIONS
from .tools import convert_currency, estimate_trip_cost, check_budget_feasibility

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    iterations: int


class AgentExecutionError(RuntimeError):
    """Raised when an agent run cannot safely produce a completed result."""


# ---------------------------------------------------------------------------
# System prompt (§5 of the spec).
# Tool order is inferred by the model via clear guidance.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a strict Travel Budget Planner. Follow these rules in order:

1. Find the trip cost in the user's message.
   - If the user states an explicit trip cost (e.g. "trip cost is 900 EUR"), use it. Do NOT call estimate_trip_cost.
   - If no trip cost is stated but a destination is named, call estimate_trip_cost once and use its estimate.
   - NEVER use the budget as the trip cost.
2. If the trip cost currency differs from the budget currency, call convert_currency once and use the converted amount.
3. Call check_budget_feasibility exactly once with the final cost and the budget, using matching currency codes.
4. Answer using only the tool result. Never do arithmetic yourself.

Example A — "budget 50000 INR and trip cost 900 EUR":
- convert_currency(900, "EUR", "INR") returns 85500 INR.
- check_budget_feasibility(total_cost=85500, cost_currency="INR", budget=50000, budget_currency="INR").

Example B — "budget 30000 INR and I want to visit Manali":
- estimate_trip_cost(destination="Manali", days=5, travelers=2, budget_style="moderate", target_currency="INR") returns an estimate.
- check_budget_feasibility(total_cost=<that estimate>, cost_currency="INR", budget=30000, budget_currency="INR").

Tools:
- `estimate_trip_cost(destination, days, travelers, budget_style, target_currency)`: ONLY when no trip cost is stated.
- `convert_currency(amount, from_currency, to_currency)`: ONLY when currencies differ.
- `check_budget_feasibility(total_cost, cost_currency, budget, budget_currency)`: ONLY when currencies match.

Reply using the tools provided."""

TOOLS = [estimate_trip_cost, convert_currency, check_budget_feasibility]
MAX_TOOL_CALLS = MAX_ITERATIONS
_TOOL_ERROR_MESSAGE = "Tool execution failed; this result cannot be relied on."
_TOOL_FAILURE_PREFIXES = (
    "validation error:",
    "unsupported currency:",
    "error:",
    "no exchange rate is available",
)


def _handle_tool_error(error: Exception) -> str:
    logger.error(
        "Agent tool execution failed (error_type=%s)",
        type(error).__name__,
    )
    return _TOOL_ERROR_MESSAGE


def _validate_tool_call(
    tool_call: object,
    tools_by_name: dict[str, BaseTool],
) -> str:
    if not isinstance(tool_call, dict):
        raise AgentExecutionError("Agent produced a malformed tool call.")

    name = tool_call.get("name")
    call_id = tool_call.get("id")
    arguments = tool_call.get("args")
    if (
        not isinstance(name, str)
        or not name
        or not isinstance(call_id, str)
        or not call_id
    ):
        raise AgentExecutionError("Agent produced a malformed tool call.")

    tool = tools_by_name.get(name)
    if tool is None:
        raise AgentExecutionError("Agent requested an unsupported tool.")
    if not isinstance(arguments, dict):
        raise AgentExecutionError("Agent supplied invalid tool arguments.")

    schema = tool.args_schema
    if not isinstance(schema, type) or not issubclass(schema, BaseModel):
        raise AgentExecutionError("Agent tool validation is unavailable.")

    fields = schema.model_fields
    missing = {
        field_name
        for field_name, field_info in fields.items()
        if field_info.is_required() and field_name not in arguments
    }
    extra = set(arguments) - set(fields)
    if missing or extra:
        raise AgentExecutionError("Agent supplied invalid tool arguments.")

    try:
        schema.model_validate(arguments, strict=True)
        encoded_arguments = json.dumps(
            arguments,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, ValidationError):
        raise AgentExecutionError("Agent supplied invalid tool arguments.") from None

    return f"{name}:{encoded_arguments}"


def _tool_message_failed(message: ToolMessage) -> bool:
    if message.status == "error":
        return True
    if not isinstance(message.content, str):
        return False
    return message.content.lstrip().lower().startswith(_TOOL_FAILURE_PREFIXES)


def _has_tool_failure(messages: list[BaseMessage]) -> bool:
    return any(
        isinstance(message, ToolMessage) and _tool_message_failed(message)
        for message in messages
    )


def _has_successful_feasibility_result(messages: list[BaseMessage]) -> bool:
    return any(
        isinstance(message, ToolMessage)
        and message.name == "check_budget_feasibility"
        and message.status == "success"
        and not _tool_message_failed(message)
        for message in messages
    )


def _build_llm() -> ChatOllama:
    return ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_HOST,
        temperature=0,
        num_ctx=2048,
    )


def build_agent(llm: ChatOllama | None = None):
    """
    Build and return the LangGraph agent for Travel Budget Planner.
    Uses StateGraph with ToolNode and autonomous loop.
    """
    if llm is None:
        llm = _build_llm()

    tool_node = ToolNode(TOOLS, handle_tool_errors=_handle_tool_error)
    llm_with_tools = llm.bind_tools(TOOLS)

    def llm_node(state: AgentState) -> AgentState:
        messages = state.get("messages")
        iterations = state.get("iterations", 0)
        if (
            not isinstance(messages, list)
            or not messages
            or not all(isinstance(message, BaseMessage) for message in messages)
            or not any(isinstance(message, HumanMessage) for message in messages)
            or isinstance(iterations, bool)
            or not isinstance(iterations, int)
            or iterations < 0
        ):
            raise AgentExecutionError("Agent state is invalid.")

        # Prepend system prompt if not present
        has_system = any(isinstance(m, SystemMessage) for m in messages)
        msgs = (
            messages
            if has_system
            else [SystemMessage(content=SYSTEM_PROMPT)] + messages
        )
        response = llm_with_tools.invoke(msgs)
        if not isinstance(response, AIMessage):
            raise AgentExecutionError("Agent returned an invalid model response.")
        return {
            "messages": [response],
            "iterations": iterations + 1,
        }

    def should_continue(state: AgentState) -> str:
        messages = state.get("messages")
        iterations = state.get("iterations", 0)
        if (
            not isinstance(messages, list)
            or not messages
            or not all(isinstance(message, BaseMessage) for message in messages)
            or not isinstance(iterations, int)
            or isinstance(iterations, bool)
            or iterations < 0
        ):
            raise AgentExecutionError("Agent state is invalid.")

        last = messages[-1]
        if not isinstance(last, AIMessage):
            raise AgentExecutionError("Agent returned an invalid model response.")
        if getattr(last, "invalid_tool_calls", []):
            raise AgentExecutionError("Agent returned a malformed tool call.")

        tool_calls = last.tool_calls or []
        if tool_calls and state.get("iterations", 0) >= MAX_ITERATIONS:
            raise AgentExecutionError("Agent exceeded the model iteration limit.")
        if tool_calls:
            all_tool_calls = [
                call
                for message in messages
                if isinstance(message, AIMessage)
                for call in (message.tool_calls or [])
            ]
            if len(all_tool_calls) > MAX_TOOL_CALLS:
                raise AgentExecutionError("Agent exceeded the tool-call limit.")

            tools_by_name = {tool.name: tool for tool in TOOLS}
            seen_calls: set[str] = set()
            for call in all_tool_calls:
                signature = _validate_tool_call(call, tools_by_name)
                if signature in seen_calls:
                    raise AgentExecutionError("Agent repeated an identical tool call.")
                seen_calls.add(signature)
            return "tools"

        if _has_tool_failure(messages):
            raise AgentExecutionError("Agent could not complete because a tool failed.")
        if not isinstance(last.content, str) or not last.content.strip():
            raise AgentExecutionError("Agent stopped without a final text response.")
        if not _has_successful_feasibility_result(messages):
            raise AgentExecutionError("Agent stopped before completing the budget check.")
        return "end"

    graph = StateGraph(AgentState)
    graph.add_node("llm", llm_node)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("llm")
    graph.add_conditional_edges("llm", should_continue, {"tools": "tools", "end": END})
    graph.add_edge("tools", "llm")

    return graph.compile()


# Singleton agent — built once at import time
agent = build_agent()


def _invoke_agent(messages: list) -> dict:
    """Invoke the compiled LangGraph agent with retry on transient errors."""
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
    import httpx

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=5),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    def _inner(messages: list) -> dict:
        return agent.invoke(messages, config={"recursion_limit": MAX_ITERATIONS * 3})

    return _inner(messages)


def run_agent(query: str) -> list:
    """Run the agent on a user query and return the full message list with retry handling."""
    from langchain_core.messages import AIMessage, HumanMessage

    # Prepare the input payload for the agent
    payload = {"messages": [HumanMessage(content=query)]}
    result = _invoke_agent(payload)
    if not isinstance(result, dict):
        raise AgentExecutionError("Agent returned an invalid state.")
    messages = result.get("messages")
    if (
        not isinstance(messages, list)
        or not messages
        or not all(isinstance(message, BaseMessage) for message in messages)
    ):
        raise AgentExecutionError("Agent returned an invalid state.")
    if _has_tool_failure(messages):
        raise AgentExecutionError("Agent could not complete because a tool failed.")
    last = messages[-1]
    if (
        not isinstance(last, AIMessage)
        or getattr(last, "tool_calls", [])
        or getattr(last, "invalid_tool_calls", [])
    ):
        raise AgentExecutionError(
            "Agent stopped without producing a final assistant response."
        )
    if not isinstance(last.content, str) or not last.content.strip():
        raise AgentExecutionError("Agent stopped without producing a final assistant response.")
    if not _has_successful_feasibility_result(messages):
        raise AgentExecutionError("Agent stopped before completing the budget check.")
    return messages
