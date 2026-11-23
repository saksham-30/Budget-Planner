# Phase 1 Comprehensive Stability Audit

**Scope:** `C:\Projects\Budget planner\Agentic_AI-main`

**Audit mode:** Read-only. No application source code or database records were modified.

**Audit date:** 2026-11-21

## Executive summary

The project has a strong deterministic core and a healthy automated backend baseline: **81 backend tests passed**, and the **Vite production build completed successfully**. Ollama was reachable during the audit and the `qwen2.5:3b` model was available.

The highest-risk issue is that saved trips have no authentication or ownership boundary. Any caller that knows or obtains a trip ID can list, read, replace, plan, and restore it. The next major reliability concern is that planning and follow-up SSE requests are not abortable or request-generation guarded in the React UI, allowing an older response to overwrite a newly created or selected trip.

Other confirmed problems include contradictory same-day-trip validation, no recovery for malformed persisted preference JSON, and a follow-up parser that silently ignores unsupported natural-language changes. The current suite is broad for deterministic planning and API behavior but has no frontend test suite or end-to-end browser tests.

## Audit findings summary

| ID | Classification | Severity | Summary |
|---|---|---:|---|
| AUTH-001 | Confirmed bug | Critical | Trip resources are not authenticated or owned |
| SSE-001 | Reproduced failure risk | High | Planning SSE requests cannot be aborted or guarded by request generation |
| DATE-001 | Reproduced failure | Medium | Same-day trips are allowed by the UI but rejected by backend schema |
| SSE-002 | Potential weakness | High | Backend SSE work may continue after client disconnect |
| DB-001 | Confirmed bug | Medium | Corrupt preference JSON is not converted to a safe API error |
| FOLLOW-001 | Confirmed bug | Medium | Unsupported follow-up numbers and traveler syntax are silently ignored |
| MODEL-001 | Potential weakness | High | Safety cap does not bound all agent failure modes or malformed tool output |
| DATA-001 | Potential weakness | Medium | Unknown destinations use generic INR benchmarks |
| FX-001 | Potential weakness | Medium | Reconciliation cannot distinguish stale/reference rates from live decisions |
| VALID-001 | Potential weakness | Medium | Destination normalization and date validation are incomplete |
| TEST-001 | Missing feature | High | No frontend test runner or frontend tests exist |
| TEST-002 | Potential weakness | Medium | Integration tests are environment-dependent and no frontend coverage exists |
| API-001 | Improvement opportunity | Medium | Health checks have no frontend timeout and can overlap |
| SSE-003 | Potential weakness | Medium | Frontend streams release resources inconsistently and replayable malformed events |
| DOC-001 | Improvement opportunity | Low | Package scripts and setup documentation do not cover test/build automation |

## Testing performed

### Backend

Command:

```powershell
.\venv\Scripts\python.exe -m pytest -q
```

Result: **81 passed in 13.65 seconds**.

The Ollama integration tests were not skipped: Ollama was available and `qwen2.5:3b` was installed. The suite therefore exercised the integration-marked tests in this environment.

### Frontend

Command:

```powershell
npm run build
```

Result: **successful**.

Vite 5.4.21 built 42 modules and produced the production bundle.

### Direct reproduction

The following Pydantic input was validated directly:

```text
start_date=2026-11-01
end_date=2026-11-01
```

Actual result: `ValidationError: end_date must be after start_date`.

The follow-up parser was probed with zero, negative, unsupported large-number, and unsupported traveler syntax. Unsupported valid-looking requests may leave preferences unchanged or raise a schema validation error instead of producing an explicit correction.

## Frontend audit

The React application is organized reasonably, with named components and accessible tab structures in the main areas. It has:

- Tab keyboard navigation
- Skip navigation
- Reduced-motion support
- Responsive breakpoints
- Several live-region/status announcements
- Abort handling for the currency analysis request

Weaknesses remain around:

- Trip-plan request cancellation
- Request-generation/race protection
- Browser-storage lifecycle
- Frontend test coverage
- Health/rate-fetch overlap
- Large component complexity

## Backend audit

The FastAPI schemas, SSE endpoints, and SQLite store generally follow a consistent shape. The backend includes:

- Pydantic request validation
- CORS restrictions
- Deterministic planning fallbacks
- SQLite persistence
- Retries for Ollama health checks
- Exchange-rate cache and stale-state handling

Reliability risks include the lack of authentication, broad global exception handling, disconnected-client SSE work, and unprotected database corruption.

## AI architecture audit

The persistent trip planner is substantially more deterministic than the original Ollama tool-calling agent. It does not rely on an LLM to perform arithmetic for trip bids, and it reconciles the original agent's financial result against deterministic tool calculations.

The Ollama loop has a maximum iteration cap and a deterministic mismatch guardrail. However, prompt and tool output remain untrusted inputs, and the frontend must treat every stream as incomplete until a final `complete` event is received.

## Financial audit

The core bid calculations are deterministic and pass unit coverage for:

- Direct and inverse demo currency conversion
- Cross-currency itemization
- Hard constraints
- Cheapest feasible bid selection
- Budget gaps
- Single-day and multi-day itinerary construction

The system clearly identifies guide costs as illustrative. Unknown destinations do not fail planning: they receive generic INR benchmarks, which is appropriate only if this behavior is clearly communicated and accepted as a demo limitation.

## Five most important issues to fix first

1. **AUTH-001:** Add authentication and trip ownership before exposing the API beyond a local demo.
2. **SSE-001:** Add abort controllers and stale-request guards to trip planning and follow-up streams.
3. **SSE-002:** Detect client disconnect and cancel backend planning work/checkpointing.
4. **DB-001:** Add safe persistence error handling and database migration/repair behavior.
5. **MODEL-001:** Harden tool-output and agent-loop validation while preserving the deterministic reconciler.
