# Phase 2E — Local Authentication and Trip Ownership

## Status

Implemented the approved single-local-owner workflow. Authentication initialization remains an explicit operator step because it creates the password and a verified database backup. The legacy migration assigns the existing local planner records to that one local owner; it does not infer among multiple accounts.

## Changes

- Added the local auth router to the FastAPI application. Password login uses Argon2id hashes, opaque random session and CSRF tokens, hashed token storage, a 12-hour expiry, failed-login lockout, strict Origin checks, `HttpOnly` session cookies, and logout revocation. A new successful login rotates and invalidates the prior session.
- Protected trip listing and all preference, conversation, plan restore, plan, and streaming routes. Mutations require the session-bound CSRF cookie/header and an allowed Origin. Unknown and non-owned trip IDs share the same 404 from the ownership store.
- Route handlers verify trip ownership before reading or invoking the LangGraph checkpoint. New trip ownership is created server-side when preferences are first saved; request bodies cannot set an owner.
- Added a same-origin frontend sign-in gate and CSRF header helper for planner API mutations.
- Added `argon2-cffi` as an explicit backend dependency.
- Kept `/api/analyze*`, health, destinations, and rates public. `/api/analyze*` remains non-persistent and may still consume model resources.

## Initialization and data handling

From the project root, stop the backend normally, close any SQLite tools that may write to the database, then run `python -m backend.manage_auth`. The command validates that the configured database is the project's existing `backend/data/travel_planner.sqlite3`, checks the configured backend port and matching application processes, probes SQLite for an active writer, and performs a read-only integrity/snapshot preflight. It then creates a verified SQLite backup under the current Windows user's local application data directory and compares row counts, per-table payload fingerprints, and the unique IDs in preferences, conversations, checkpoints, and writes. Only after those checks pass does the command prompt for a 12-character-minimum password and apply the additive migration in a transaction. It verifies persisted state again after migration and writes a safety report beside the backup. If backup verification or migration checks fail, authentication initialization stops without applying the migration. Repeated initialization does not replace the existing local owner, password, or ownership rows.

For a normal Windows shutdown, press `Ctrl+C` in the terminal running Uvicorn and wait for it to exit. Verify the configured port is no longer listening with `Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue`. Close any database browser or other process that may have the SQLite file open. Do not terminate Python processes by PID; if the CLI finds an active service or cannot verify process state, leave the database untouched and resolve that condition before retrying.

After initialization, start the backend and frontend as usual, then sign in with the local password. A database without initialized auth fails closed for trip APIs and presents the setup instruction in the frontend.

## Verification status and limits

Stage 3 verification results are recorded in `stage3_verification.md`. The initial read-only audit and its findings remain available in `security_audit.md`; this report records the selected single-owner implementation path. This is local single-owner authentication, not a multi-account identity system. Cookie `Secure` is disabled for the current local HTTP development workflow; any HTTPS deployment needs an environment-aware secure-cookie setting and production Origin configuration before exposure.
