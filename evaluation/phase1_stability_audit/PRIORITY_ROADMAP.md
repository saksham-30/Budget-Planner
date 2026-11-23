# Priority Roadmap

## P0 — Before any non-local deployment

### 1. AUTH-001 — Add authentication and trip ownership

- Generate opaque trip IDs server-side.
- Add user/anonymous session ownership.
- Scope list, preferences, plans, and conversations by owner.
- Do not accept caller-selected IDs for stored trips.
- Risks: local demo workflow and recent-trip storage require migration.

### 2. SSE-001 — Cancel stale planning requests

- Add `AbortController` to plan and follow-up requests.
- Abort the old request on trip selection/new-trip actions/unmount.
- Track request generations.
- Ignore stale stage and completion events.
- Risks: React closure and SSE reader lifecycle need systematic testing.

### 3. SSE-002 — Cancel disconnected planning work

- Detect Starlette/client disconnect.
- Stop LangGraph work where possible.
- Avoid persisting plans after cancellation unless explicitly saved.
- Risks: graph cancellation and checkpoint state require concurrency testing.

## P1 — Data integrity and consistency

### 4. DB-001 — Harden SQLite and JSON persistence

- Validate loaded JSON and report controlled errors.
- Add migration/version support.
- Add useful indexes.
- Add transaction and concurrent-access tests.
- Risks: migration should preserve existing SQLite data.

### 5. DATE-001 — Define same-day trip behavior

- Match frontend and backend date rules.
- If same-day trips are supported, represent nights and days consistently.
- If not supported, reject with a clear UI message.
- Risks: itinerary, hotel, and activity calculations all use date shape.

### 6. MODEL-001 — Harden agent tool execution

- Detect repeated or conflicting tool calls.
- Validate tool names and argument types.
- Bound total graph execution independently of provider behavior.
- Preserve deterministic reconciliation.
- Risks: provider-specific tool structures can vary.

### 7. FOLLOW-001 — Replace fragile follow-up parsing

- Add a structured follow-up request schema or typed intent parser.
- Return an explicit unsupported-change response.
- Validate every mutation against Pydantic.
- Risks: moving beyond demo-only regex parsing may require an LLM.

## P2 — Quality and testability

### 8. TEST-001 — Add frontend tests

- Test preference validation
- Test SSE completion/error/malformed data
- Test restore behavior
- Test trip switching and stale responses
- Test recent trips and localStorage
- Add component tests and Playwright-style browser tests.

### 9. TEST-002 — Make integration tests deterministic

- Provide clear unit-only and Ollama-required commands.
- Add CI Ollama setup or mocks.
- Report integration skips explicitly.
- Add SSE disconnect and cancellation tests.

### 10. FX-001 — Persist exchange-rate provenance

- Store the exact snapshot/rate set used in each plan.
- Display source, as-of, and stale status with every saved plan.
- Risks: API and migration schema changes.

## P3 — UX and maintainability

### 11. Split TripPlanningPanel

- Extract hooks for preference, persistence, streaming, and recent-trip state.
- Keep SSE parsing in a tested utility.
- Risks: moderate React refactor but low backend risk.

### 12. Improve accessibility and mobile verification

- Add automated accessibility checks.
- Test keyboard behavior and focus restoration.
- Test at 320 px and 200% zoom.

### 13. Improve data confidence

- Differentiate destination-specific evidence from generic benchmarks.
- Add confidence labels and warnings.
- Prevent one unverified benchmark from being treated as a booking quote.

### 14. Add operational observability

- Add request IDs and structured logs.
- Track SSE duration, cancellation, graph node failures, and Ollama retry outcomes.
- Add health endpoints for database and rate-cache state.

## Top five fixes

1. AUTH-001
2. SSE-001
3. SSE-002
4. DB-001
5. MODEL-001
