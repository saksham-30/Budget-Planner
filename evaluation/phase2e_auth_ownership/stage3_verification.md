# Phase 2E — Stage 3 Pre-Migration Verification

## Scope and result

Reviewed the local single-owner authentication implementation, route and persistence ownership checks, browser session/CSRF handling, and backup/migration path. Added focused security and migration tests and corrected the migration check/lock race, repeat-initialization behavior, and frontend handling of expired sessions. Authentication initialization and the real database migration were not run.

## Test and build results

- Backend: `python -m pytest -p no:cacheprovider --basetemp <workspace-temp> -m "not integration" -q` — **122 passed, 4 deselected**. `TRAVEL_PLANNER_DATABASE` pointed to a temporary file inside the workspace; tests with explicit databases used pytest's temporary workspace directories.
- Frontend: `npm.cmd test` — **8 passed**.
- Production build: `npm.cmd run build` — **passed**, Vite transformed 44 modules.
- The four deselected backend tests are marked `integration` and require a live Ollama/model setup.

The added focused backend coverage exercises authentication on trip routes, exact Origin and CSRF checks, hashed passwords, session expiration, login session rotation, logout revocation, failed-login throttling, legacy IDs from all four persisted state tables, checkpoint/write payload preservation, backup verification failures, stale-backup refusal, transactional rollback, and repeat initialization. Frontend coverage checks same-origin credentials, CSRF header creation, and the expired-session sign-in event.

## Route and session review

- `/api/trips` and all trip preference, conversation, plan restore, plan, and plan-stream routes require a validated local session.
- Preference writes require ownership or create a server-side owner mapping only when no existing trip/checkpoint data exists. Plan and conversation operations assert the mapping before reading or writing those records. Plan restoration and planning assert ownership before calling LangGraph checkpoint access.
- Unsafe cookie-authenticated operations validate an exact configured Origin and compare the CSRF header with both the readable CSRF cookie and its stored session hash. Login checks Origin; logout also requires CSRF.
- Session tokens are random and stored only as hashes. Sessions expire after 12 hours. Successful login invalidates earlier sessions; logout revokes the current session. Password hashes use Argon2id. Five failed attempts trigger a 15-minute lockout.
- Frontend requests use same-origin credentials. Mutations add the CSRF header from the readable cookie. A 401 triggers a sign-in state transition; this is covered by a Node test.

## Backup and migration review

- Backup creation uses SQLite's `Connection.backup` API. It checks source integrity, hashes and counts source tables before and after copying, checks backup integrity, and compares the backup's complete logical snapshot and counts. Windows ACL or POSIX permission checks must also succeed before a `VerifiedBackup` is returned.
- The migration collects IDs from `trip_preferences.trip_id`, `trip_conversations.trip_id`, `checkpoints.thread_id`, and `writes.thread_id`. It creates only auth/ownership/schema-version tables and an index; original trip, conversation, checkpoint, and writes rows are not updated or re-keyed.
- The migration now acquires `BEGIN IMMEDIATE` before checking the source against the verified backup and holds that reservation through schema changes. SQLite DDL and data changes commit together or roll back together.
- Re-running setup takes a fresh backup. The existing migration is accepted when every persisted ID is already mapped to the existing local owner, even though the CLI has generated a new candidate owner UUID. It does not replace the password or ownership rows.
- Backup verification or snapshot mismatch raises before schema changes. Tests confirm unchanged source rows for ACL-check failure, stale backup, and migration SQL rollback. Checkpoint and writes BLOB payloads remain byte-for-byte unchanged in the success case.

## Deviations and remaining deployment limits

- No separate Stage 2 plan document was present in the workspace, so comparison used the implementation report's approved single-local-owner policy. That policy assigns legacy IDs to the sole local owner; it differs from the Stage 1 audit's recommended default of quarantining legacy data. The approved Stage 2 choice is recorded in the implementation report.
- Cookies currently set `Secure=False` for local HTTP. HTTPS deployment requires environment-aware Secure cookies and production Origin/CORS configuration. The current static allowed-Origin list is for local development.
- The implementation is a single local owner, not a multi-account identity system. `/api/analyze*` remains public and can consume model resources, as documented in the implementation report.
- The four Ollama integration tests were not run. No test failure remains in the executed non-integration suite.

## Changed files

- `backend/auth.py`
- `backend/main.py`
- `backend/migrations.py`
- `backend/requirements.txt`
- `frontend/src/App.jsx`
- `frontend/src/auth.js`
- `frontend/src/components/TripPlanningPanel.jsx`
- `frontend/src/styles/index.css`
- `frontend/tests/auth.test.js`
- `tests/conftest.py`
- `tests/test_api.py`
- `tests/test_auth_migration.py`
- `tests/test_auth_security.py`
- `tests/test_persistence_reliability.py`
- `tests/test_stream_cancellation.py`
- `evaluation/phase2e_auth_ownership/implementation_report.md`
- `evaluation/phase2e_auth_ownership/stage3_verification.md`
- Generated production build: `frontend/dist/index.html`, `frontend/dist/assets/index-DUSPuret.css`, and `frontend/dist/assets/index-Dz26le5q.js`.

## Database safety confirmation

`python -m backend.manage_auth` was not run. The configured application database was redirected to temporary workspace files for backend test execution; test databases and pytest temporary folders were removed afterward. The real `backend/data/travel_planner.sqlite3` was not opened or modified by the verification commands.
