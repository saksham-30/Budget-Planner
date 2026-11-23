# Architecture Review

## System shape

```text
React/Vite frontend
        |
        | REST + SSE
        v
FastAPI backend
        |
        +-- Ollama / LangGraph tool-calling agent
        +-- Deterministic trip planning LangGraph supervisor
        +-- Local destination cost guide
        +-- Exchange-rate service
        +-- SQLite preferences/checkpoints/conversations
```

## Strengths

### Clear separation of deterministic and probabilistic logic

Trip bid ranking, conversion, itemization, hard constraints, and feasibility are deterministic. The UI and persisted plan clearly distinguish guide estimates from live bookings.

### Defensive reconciliation

The original Ollama agent result is checked against deterministic parsing, tool calls, and fallback calculation. Missing or inconsistent model values can be replaced with the reconciled answer.

### Persistent graph state

LangGraph checkpoints and application preferences share SQLite-backed persistence. The thread test demonstrates that separate thread IDs remain isolated.

### Graceful exchange-rate degradation

The exchange-rate service supports live ECB/Frankfurter rates, cache fallback, stale labeling, and demo fallback. It is architecturally suitable for a demo-only environment.

### Streamed UI states

Both the currency and trip planners expose stage or tool events incrementally. The frontend waits for a complete event before considering the stream successful.

## Weaknesses

### No identity boundary

Every persistence route is addressable by `trip_id` alone. SQLite rows, LangGraph checkpoints, preferences, and conversations do not include a user owner.

### Shared state is not strongly scoped

SQLite stores application preferences and LangGraph checkpoints in the same database. Thread isolation depends on the caller-provided ID and is not protected by authentication or server-generated opaque ownership.

### Broad exception handling

The API has route-specific exceptions and a global exception handler, but persistence and schema corruption can still fall through to a generic 500 response.

### SSE lifecycle is incomplete

The backend has no explicit client-disconnect cancellation. The frontend has abort support for the currency agent but not the trip planner stream. Request ownership is not tracked by generation.

### Large frontend component

`TripPlanningPanel.jsx` contains state, persistence, SSE parsing, result tabs, export, and forms in one component. This increases race and test difficulty.

### No frontend automation

The frontend build validates syntax and bundling but not state synchronization, browser APIs, SSE parsing, or accessibility behavior.

## AI architecture evaluation

### Tool-calling agent

The original agent uses:

- System prompt rules
- Bound tools
- LangGraph conditional edges
- A defensive iteration cap
- Tool trace generation
- Deterministic reconciliation

Main risks are provider-specific malformed tool calls, repeated tool calls, hallucinated numeric outputs, and a retry policy that is not aware of every provider exception.

### Persistent supervisor

The supervisor graph is more reliable because:

- Retrieval is deterministic
- Currency conversion is deterministic
- Bid generation is deterministic
- Supervisor ranking is arithmetic
- No LLM call is required to produce the plan

The `apply_follow_up` natural-language parser is the weakest probabilistic-adjacent point because it is regex-based. It may silently ignore valid requests outside its narrow grammar.

## Persistence evaluation

SQLite is appropriate for this local demo, but:

- No migration system is present
- JSON corruption is not handled
- No ownership columns exist
- No database indexes beyond primary keys exist
- Checkpoint and application preference writes are not transactionally grouped
- The backend can serve concurrent requests through separate SQLite connections

## Reliability priorities

1. Introduce authentication and ownership.
2. Bound and cancel SSE work.
3. Add request-generation guards and reader cancellation in React.
4. Add persistence migration and corruption handling.
5. Add frontend tests and browser E2E tests.
