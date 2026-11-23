# Phase 2B Test Results

## New persistence regression tests

Command:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_persistence_reliability.py -q
```

Result: **14 passed** (parameterized cases included).

Coverage includes:

- Malformed, null, missing-field, and unexpected-shape preference JSON
- SQL `NULL` legacy preference and conversation values
- Locked/busy database behavior
- Failed connection handling and sanitized logging
- Rollback after a failed transaction
- Atomic preference/conversation plan update
- Repeated database initialization and existing-record preservation
- Safe 500/503 API responses
- Corrupt-trip listing fallback without data deletion
- Preference serialization round-trip

## Full backend suite

Command:

```powershell
.\venv\Scripts\python.exe -m pytest -q
```

Result: **95 passed in 16.65 seconds**. Exit code: 0. No failing or skipped tests were reported.

All tests use temporary databases for persistence scenarios. The configured user database was not used for test writes.

## Frontend production build

Command:

```powershell
npm run build
```

Result: **passed** with Vite 5.4.21; 43 modules transformed; build completed in 726 ms. No frontend files were modified.

## Database checks

The configured database was inspected read-only before and after the implementation and test run. `PRAGMA integrity_check` returned `ok` both times. Table counts and the logical data fingerprint were identical; details are in `database_integrity_report.md`.
