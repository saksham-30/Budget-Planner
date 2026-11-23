# Test Results

## Backend test suite

Command:

```powershell
.\venv\Scripts\python.exe -m pytest -q
```

Outcome:

- **81 passed**
- Duration: 13.65 seconds
- Exit code: 0
- Failing tests: none
- Collection or runtime errors: none

The test suite ran under the live environment. Ollama was reachable and `qwen2.5:3b` was available, so Ollama integration tests were executed rather than skipped. No test failures were silently suppressed.

### Coverage areas represented

- API health and Ollama status
- Currency and budget tool behavior
- Deterministic fallback reconciliation
- SSE API responses
- Trip preference persistence
- Trip listing
- LangGraph thread persistence and isolation
- Destination cost retrieval
- Generic benchmark fallback
- Cross-currency bid calculations
- Hard constraints
- Bid ranking
- Exchange-rate cache, live refresh, stale snapshot, and demo mode

### Material coverage gaps

- No frontend test suite
- No browser automation
- No real database-corruption test
- No client-disconnect/SSE cancellation test
- No authentication/authorization test
- No concurrent trip-plan race test
- No malformed Ollama tool-call test
- No test for same-day trip behavior
- No test for unsupported follow-up sentences
- No test for past dates or post-normalization destination length
- No test for restart/restoration of live exchange-rate provenance

## Frontend production build

Command:

```powershell
npm run build
```

Outcome:

- Exit code: 0
- Vite: 5.4.21
- Modules transformed: 42
- Build time: 827 ms
- Main JS bundle: 203.30 kB / 62.13 kB gzip
- Main CSS bundle: 43.52 kB / 40.52 kB gzip
- HTML bundle: 0.69 kB

No build warnings or errors were reported.

## Existing frontend tests

No frontend test files or test script were found:

- No `test`, `vitest`, `jest`, or e2e script in `frontend/package.json`
- No frontend test/config files discovered
- Production build is the only existing frontend verification command

## Direct validation

Same-day preference validation was reproduced with Pydantic:

```text
start_date=2026-11-01
end_date=2026-11-01
```

Actual result:

```text
ValidationError: end_date must be after start_date
```

Follow-up parser probes showed:

- `Increase my budget to 0 INR` is rejected by Pydantic
- `Increase my budget to -5000 INR` does not change the budget
- `What if ten more people join?` does not change travelers
- `Add two travelers` does not change travelers

These are parser gaps; no source changes were made.
