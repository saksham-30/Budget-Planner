# Phase 2D — Agent Loop Verification

## Existing loop observed

1. `llm_node` prepends the existing system prompt and invokes the tool-bound model.
2. `should_continue` routes messages with `tool_calls` to `ToolNode`; messages without calls end the graph.
3. ToolNode invokes the requested registered tools and appends ToolMessages.
4. The graph returns to the model after tool execution.
5. `run_agent` invokes the graph with `recursion_limit = MAX_ITERATIONS * 3`.

The configured default observed at runtime is `MAX_ITERATIONS=6`. Before hardening, the guard counted model iterations but imposed no total tool-call cap, and repeated identical calls were not detected. Runtime probes showed ToolNode ignored extra fields, returned detailed errors for invalid calls, and re-raised an ordinary tool `RuntimeError` by default.

## Current loop safeguards

1. Validate the incoming state contains non-empty BaseMessages including a HumanMessage, and a non-negative integer iteration count.
2. Require an AIMessage from the model; reject parser-reported `invalid_tool_calls`.
3. Before routing to ToolNode, validate every call's name, ID, exact argument keys, and strict Pydantic argument types.
4. Enforce at most six total tool calls and reject duplicate canonical name/argument signatures. Distinct arguments are not deduplicated.
5. Return a fixed generic ToolMessage for raised tool exceptions and log only the exception type.
6. On termination, reject any failed/error-shaped tool result, empty/non-text final output, or absence of a successful feasibility result.
7. Recheck final state in `run_agent` before returning messages to API callers.

## Termination evidence

- `MAX_ITERATIONS=6`: at most six model invocations. A model response requesting more tools at the limit is rejected instead of routed.
- `MAX_TOOL_CALLS=6`: an oversized batch or aggregate count is rejected before that batch executes.
- `recursion_limit=18`: confirmed by the focused test of the actual `run_agent` invocation configuration. The iteration guard is tested to raise before the configured recursion limit is exhausted.
- Focused agent tests: **19 passed**.
- Full backend suite: **117 passed**.
- Normal planning/follow-up SSE checks: **2 passed**.

## Important boundary

These safeguards validate model output after generation; they do not make an LLM obey its instructions. Valid-looking but semantically incorrect arguments remain possible, so deterministic tool results and the existing downstream reconciliation remain important.
