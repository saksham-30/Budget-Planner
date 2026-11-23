# Phase 2B — SQLite & Persistence Reliability

## Summary

Implemented focused persistence reliability improvements without changing the database schema, introducing a migration, changing planning calculations, or modifying frontend files.

## Root causes found

1. `TripStore.get_preferences` passed database text directly to `json.loads` and Pydantic. Malformed JSON, SQL `NULL`, JSON `null`, or missing required values could escape as unclassified exceptions.
2. `list_trips` skipped invalid preference records without logging why, making damaged rows difficult to diagnose.
3. `get_conversation` trusted role/content values and could return null or invalid persisted values to the API.
4. SQLite connection creation occurred outside the protected transaction block. Operational errors (including locked/busy databases) were exposed as raw SQLite exceptions.
5. Plan endpoints wrote updated preferences and the two conversation turns in separate transactions, so failure after one write could leave application records inconsistent.
6. SQLite cursors relied on implicit cleanup rather than explicit closure.
7. There is no migration system; current schema setup uses repeatable `CREATE TABLE IF NOT EXISTS`.

## Fixes implemented

- Added sanitized `PersistenceError`, `PersistenceUnavailableError`, and `CorruptPersistenceError` types.
- Wrapped connection setup, transaction commit, SQLite operational errors, and other SQLite errors. Busy/locked/open failures produce a controlled 503; other persistence failures produce a controlled 500.
- Added a bounded SQLite timeout parameter (default remains 10 seconds); no retry loop was introduced.
- Ensured rollback is attempted after failures and connections/cursors are closed.
- Validated persisted preference JSON as an object before Pydantic validation. Malformed and incomplete values are logged by table/field/error type only; stored values remain unchanged.
- `list_trips` continues returning valid trips, explicitly logs invalid rows, and leaves the original rows intact.
- Validated conversation roles and content; invalid records produce a controlled persistence error.
- Added `save_plan_update`, which atomically saves preferences, both conversation turns, and the conversation snapshot returned by the planning endpoints.
- Added a FastAPI persistence exception handler with safe messages and no database paths, SQL text, stored contents, or stack traces in responses.

## Files changed

- `backend/persistence.py`
- `backend/main.py`
- `tests/test_persistence_reliability.py`
- `evaluation/phase2b_persistence_reliability/implementation_report.md`
- `evaluation/phase2b_persistence_reliability/test_results.md`
- `evaluation/phase2b_persistence_reliability/database_integrity_report.md`

No frontend, configuration, secret, schema, or database files were changed.

## Tests added

Added isolated temporary-database coverage for malformed JSON, JSON `null`, missing required fields, unexpected JSON shape, SQL `NULL` legacy values, corrupt conversations, locked databases, connection failures, rollback, atomic plan persistence, repeated initialization, preservation of existing rows, sanitized API errors, corrupt-list fallback, and successful serialization round-trip.

## Remaining issues and risks

- LangGraph checkpoint writes use `SqliteSaver` independently of `TripStore` transactions. The planner checkpoint and application preference/conversation rows therefore cannot be committed as one atomic transaction without changing the checkpoint integration. This remains an architectural limitation; no LangGraph changes were made in Phase 2B.
- Corrupt preference rows are retained and logged, but omitted from `/api/trips`; a user-facing repair/export path is outside this phase.
- The default SQLite busy timeout is 10 seconds before an unavailable error is returned.
- No migration was needed, so no backup or schema change was performed.
