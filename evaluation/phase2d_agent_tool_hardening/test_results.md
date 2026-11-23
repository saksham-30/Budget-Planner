# Phase 2D — Test Results

## Executed commands

| Command | Result |
|---|---|
| `.\venv\Scripts\python.exe -m pytest tests/test_agent_hardening.py tests/test_agent_unit.py -q` | **19 passed** |
| `.\venv\Scripts\python.exe -m pytest -q` | **117 passed** |
| `.\venv\Scripts\python.exe -m pytest tests/test_api.py::test_trip_plan_stream_emits_stages_then_saves_the_plan tests/test_stream_cancellation.py::test_normal_follow_up_stream_still_emits_completion -q` | **2 passed** |
| `npm test` (from `frontend`) | **6 passed, 0 failed** |
| `npm run build` (from `frontend`) | **Passed**; Vite transformed 43 modules |

## Verified behavior

- Unknown tools and missing, extra, or invalidly typed arguments are rejected before execution.
- Malformed model tool-call output and invalid agent state do not become successful responses.
- Identical calls are rejected while different arguments for the same tool remain valid.
- Model-iteration and total tool-call limits terminate with controlled agent errors.
- Tool exceptions produce a generic ToolMessage; logs record the exception type without its message, path, or secret-like content.
- Tool errors and validation-error results cannot be followed by false-success completion.
- A normal tool sequence and final answer still complete successfully.
- Existing normal trip-planning SSE and follow-up SSE still emit successful completion events.

No new dependencies were introduced. Agent-specific tests use fake models; full suite results are reported as executed, without assuming model compliance with its prompt.
