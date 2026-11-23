# Phase 2D — Agent & Tool-Loop Reliability Hardening

## Summary

Hardened the LangGraph agent boundary against malformed tool calls, excessive/repeated calls, tool failures, invalid state, and incomplete model output. The agent now refuses to complete successfully unless it has a non-empty final response and a successful feasibility-tool result. Prompts, tool calculations, API schemas, frontend code, and persistence were not changed.

## Findings

| ID | Classification | Finding and evidence |
|---|---|---|
| AGT-001 | Reproduced failure | ToolNode returned detailed error ToolMessages for unknown, missing, and wrongly typed arguments; an extra argument was silently ignored and the tool still executed. Runtime checks reproduced all four cases. |
| AGT-002 | Confirmed weakness | `MAX_ITERATIONS` bounded model calls, but did not bound the number of tool calls in a single model response or detect repeated identical calls. ToolNode executes calls from a response as a batch. |
| AGT-003 | Reproduced failure | The default ToolNode error handler re-raised a tool `RuntimeError`; a controlled test exception containing a secret-like path propagated through graph execution. The API had a generic outer error response, but the error was not returned to the model as a safe result. |
| AGT-004 | Confirmed weakness | `invalid_tool_calls` were not checked by the routing logic. A final AI message with text but no valid `tool_calls` could be treated as an ordinary final response. `run_agent` also accepted empty text and did not verify that feasibility checking had succeeded. |
| AGT-005 | Confirmed weakness | Tool validation errors returned by the existing tools are strings with successful ToolMessage status. A model could continue and produce a final answer after such a failed operation unless completion checked tool results. |
| AGT-006 | Confirmed weakness | Agent state and model response shape were used without validating the required message list, iteration counter, or response type. |

## Safeguards implemented

- `backend/agent.py`
  - Validates agent state and requires a valid AIMessage response.
  - Rejects malformed `invalid_tool_calls`, unknown tool names, absent IDs, missing required arguments, extra arguments, and type-invalid arguments before ToolNode runs. Validation uses each registered tool's Pydantic schema in strict mode.
  - Rejects repeated calls using a canonical tool-name/JSON-arguments signature. Calls with distinct arguments remain permitted.
  - Caps total tool calls and model iterations at the existing `MAX_ITERATIONS` setting.
  - Configures ToolNode to return a fixed, non-sensitive tool error to the model and logs only the exception type.
  - Prevents graph completion after a failed tool result, an empty final answer, or without a successful `check_budget_feasibility` result.
  - Revalidates the final `run_agent` state so malformed or incomplete states cannot bypass graph checks.
- `tests/test_agent_hardening.py`
  - Adds deterministic fake-model coverage for malformed/unknown calls, missing/extra/wrong arguments, duplicate and distinct calls, iteration and call limits, sanitized tool exceptions, invalid state, empty output, and successful/incomplete termination.
- Created the requested Phase 2D reports.

## Actual limits

- Default `MAX_ITERATIONS` is **6** (`backend/config.py`); it limits model invocations.
- `MAX_TOOL_CALLS` is **6**, using the same existing setting; the total is checked before any calls in an over-limit batch execute.
- `run_agent` supplies a LangGraph `recursion_limit` of **18** (`MAX_ITERATIONS * 3`) as a separate graph-level guard.

## Risks and remaining limitations

- Syntactically valid arguments can still be semantically wrong. The application validates shape/types and tool outcomes; it cannot guarantee that an LLM selected the correct trip amounts or interpretation.
- If any tool result is marked failed or matches the existing tool validation/error prefixes, the agent will not return a successful final result, even if a later model response claims recovery. This deliberately favors avoiding false success over automatic recovery.
- Error-string detection relies on the current tool error prefixes plus ToolMessage status. If future tools use different error conventions, their result classification must be updated.
- The six-call cap is intentionally conservative; a future legitimate workflow requiring more tool calls would need an explicit, reviewed limit change.
- Git metadata is absent from the project directory, so repository diff/status could not be used. The changed application/test files were reviewed directly. No frontend source, prompts, tool calculations, supervisor planning logic, database schema, or API contracts were changed.
