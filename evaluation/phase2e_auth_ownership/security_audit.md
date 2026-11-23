# Phase 2E — Stage 1 Read-Only Security Audit

## Status and scope

This is the Stage 1 read-only audit and proposed design only. No application source, authentication state, database schema, database records, or LangGraph checkpoints were changed.

## Executive summary

The application has no authenticated identity or trip ownership enforcement. `GET /api/trips` exposes all stored trip IDs and summaries. Trip-specific routes use the path-provided ID directly to read preferences, conversations, and plans or to update preferences and create follow-ups. A caller can therefore enumerate IDs and access or modify trips without proving ownership.

Legacy ownership cannot be inferred safely. Existing IDs were created in browser-local storage, while SQLite and LangGraph checkpoint records contain no user identifier. Assigning these records to the first account or treating an old UUID as proof would grant ownership without evidence. A product decision is required before implementing authentication or migration.

## Findings summary

| ID | Severity | Classification | Evidence | Impact |
|---|---|---|---|---|
| AUTH-001 | High | Confirmed missing authentication/authorization | `backend/main.py:236-357`; `backend/persistence.py:171-207, 327-341` | Unauthenticated clients can enumerate trips, read preferences/conversations/plans, replace preferences, or create follow-ups for any known trip ID. |
| AUTH-002 | High | Confirmed missing ownership mapping | `backend/persistence.py:171-193`; checkpoint schema inspected read-only | No trip, conversation, or checkpoint row is associated with an authenticated user. |
| AUTH-003 | High | Confirmed indirect access path | `backend/supervisor.py:138-150, 188-231`; `backend/main.py:263-357` | LangGraph reads, invokes, and streams checkpoints using `trip_id` as `thread_id`, without an owner check. |
| AUTH-004 | Medium | Confirmed frontend trust weakness | `frontend/src/components/TripPlanningPanel.jsx:32-34, 540-548, 570-574, 622-715, 782-909` | Browser-local random IDs and localStorage support restore/listing but do not authenticate a user or prove ownership. The global list currently exposes the IDs. |

## Endpoint inventory

| Method | Endpoint | Trip ID source | Data/operation | Current authorization |
|---|---|---|---|---|
| GET | `/api/trips` | None | Lists every trip summary and its ID | None |
| PUT | `/api/trips/{trip_id}/preferences` | Path | Creates or replaces preferences | None |
| POST | `/api/trips/{trip_id}/preferences` | Path | Creates or replaces preferences | None |
| GET | `/api/trips/{trip_id}/preferences` | Path | Reads preferences | None |
| GET | `/api/trips/{trip_id}/conversation` | Path | Reads conversation history | None |
| GET | `/api/trips/{trip_id}/plan` | Path | Restores saved plan/checkpoint state | None |
| POST | `/api/trips/{trip_id}/plan` | Path | Creates or updates plan and conversation | None |
| POST | `/api/trips/{trip_id}/plan/stream` | Path | Streams plan and persists completed result | None |
| POST | `/api/analyze` | None | Runs an analysis without trip persistence | None |
| POST | `/api/analyze/stream` | None | Streams an analysis without trip persistence | None |
| GET | `/api/health`, `/api/destinations`, `/api/rates` | None | Health or public reference data | No trip authorization currently required |

There are no current trip delete or dedicated restore endpoints. `GET /api/trips/{trip_id}/plan` is the plan restoration path. Any future delete/export/share/restore endpoint must use the same ownership dependency.

## Persistence and checkpoint findings

Read-only inspection found:

- `trip_preferences`: keyed by `trip_id`; no owner column.
- `trip_conversations`: keyed by conversation turn and `trip_id`; no owner column or user foreign key.
- LangGraph `checkpoints` and `writes`: keyed by `thread_id` and checkpoint metadata; no owner column.
- No users or sessions table exists.
- Counts at audit time: 12 distinct preference trips, 11 conversation trips, 11 checkpoint threads, 54 conversation rows, 162 checkpoints, and 486 checkpoint writes. One preference trip had no checkpoint. These counts establish existing data volume, not ownership.

Preferences/conversations and LangGraph checkpoints are persisted through separate operations. A graph invocation may advance its checkpoint even if a later preferences/conversation write fails. Ownership enforcement must therefore happen before every persistence lookup and before any checkpoint `get_state`, `invoke`, or `stream` operation.

## Frontend and trust boundaries

The frontend creates a random trip ID and stores the active ID in `localStorage`. It sends that ID to read and update routes and uses the global trip list to restore trips. There is no login flow, session cookie, authorization header, CSRF token, or current-user request.

`localStorage` is client-controlled state and is not an identity credential. A UUID may reduce accidental collisions but cannot establish authorization. The server must derive identity from verified authentication context; it must never accept `owner_user_id` or equivalent from request payloads.

## Threat model

### Assets

- Preferences, budgets, dates, and traveler details
- Conversation content
- Plans, bids, itinerary, cost evidence, and prior decisions
- LangGraph checkpoint state and message history
- Future account credentials and active sessions

### Threat actors and paths

- An unauthenticated caller can request `/api/trips`, obtain IDs, and call trip routes.
- A user with an obtained or guessed trip ID can access another user's trip.
- Cross-site callers may attempt mutations after cookie-based sessions are introduced.
- A same-origin script or browser extension may access JavaScript-readable localStorage; it must not be trusted for identity.

### Security boundary required

Authentication must establish a server-verified principal. Every trip operation must then verify that the trip's owner matches that principal before reading or mutating preferences, conversations, plans, or LangGraph state. Unknown and non-owned IDs should return the same safe 404 response to reduce enumeration.

## Proposed minimal authentication design

1. Add account registration/login only if the product intends private user accounts; store password hashes, never plaintext. Prefer Argon2id with a maintained implementation if no suitable password-hashing dependency is already present.
2. Use opaque, cryptographically random server-side sessions. Store a hash of the session token, user ID, creation/expiry/last-use metadata, and revocation state in SQLite. Rotate on login; revoke on logout; enforce expiry.
3. Set the session cookie `HttpOnly`, `SameSite=Lax`, and `Secure` in HTTPS deployments. Define local-development behavior separately; do not weaken production settings.
4. Protect unsafe cookie-authenticated requests with strict Origin validation and a CSRF token. Keep credentialed CORS origins explicit and production-specific.
5. Add a FastAPI `current_user` dependency. Identity comes exclusively from the validated session, never from request bodies, query parameters, trip IDs, or localStorage.
6. Add `/api/auth/login`, `/api/auth/logout`, and `/api/auth/me`; keep public health and non-sensitive static reference data unauthenticated. Protect trip and planning routes consistently. Decide explicitly whether `/api/analyze*` is public or requires login; these routes do not currently persist trip data but can consume model resources.
7. Prefer an authoritative ownership registry keyed by trip ID, rather than altering LangGraph's internal tables. Every trip storage and checkpoint access must first verify the registry owner.

## Proposed migration and legacy-data strategy

### Required stop decision

No evidence links any existing trip to a person or account. The existing browser-local trip UUID is not proof of identity. **Do not automatically assign, expose, re-key, delete, or migrate legacy rows until the product owner selects a policy.**

Recommended default: quarantine legacy records as unassigned and keep them inaccessible through authenticated APIs. Preserve existing trip IDs and checkpoint thread IDs.

### Additive migration outline, after approval

1. Take a verified, restorable backup of the SQLite database before migration. Record its integrity check and table/row counts.
2. Add `users` and server-side `sessions` tables.
3. Add a `trip_ownership` table such as `(trip_id PRIMARY KEY, owner_user_id NULL, status)`; legacy rows receive `owner_user_id=NULL` and an explicit unassigned/quarantined status. New trip rows get the authenticated owner from the server.
4. Do not drop or rewrite preference, conversation, checkpoint, or writes tables. Do not immediately add non-null foreign keys that would reject unassigned legacy records.
5. Make initialization repeatable and transactional. Use additive tables/indexes; use schema-version tracking and safe rollback/backup instructions.
6. Before every trip read/write and checkpoint operation, require an ownership row with `owner_user_id == current_user.id`. Lists must return only that user's owned trips.
7. Preserve `thread_id == trip_id`. Do not re-key checkpoints as part of the initial ownership migration; that creates unnecessary consistency risk.
8. Verify counts, integrity, ownership joins, orphan handling, and checkpoint-to-trip mapping against the backup before enabling authenticated traffic.

### Legacy policy options requiring product approval

- **Quarantine (recommended):** preserve all rows but do not expose them to accounts until a verified claim process exists.
- **Verified owner claim:** provide a single-use claim mechanism that proves control through an out-of-band channel. An old UUID or possession of browser localStorage alone is insufficient proof.
- **Explicit public demo:** retain shared access only if the product is explicitly designated and disclosed as public, not a private-user system.
- **Archive/delete:** only after a separately approved retention decision and verified backup; no deletion is performed by this audit.

## Stage 1 conclusion

Authentication and ownership enforcement are required before the application can safely be treated as multi-user/private. The safe additive design is feasible without modifying LangGraph checkpoint schema, but existing records have no verifiable owners. Stage 2 implementation must remain blocked until the legacy-data policy and authentication product choices are reviewed and approved.
