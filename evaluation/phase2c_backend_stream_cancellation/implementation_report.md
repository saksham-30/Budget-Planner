# Phase 2C — Backend Stream Cancellation & Cleanup

## Summary

Backend SSE streams now close their response iterators when streaming ends or the client disconnects. Cancellation is propagated rather than converted into an SSE application error or a success/completion event. The synchronous planning graph and its checkpointer are closed after any currently executing synchronous step finishes.

The existing SSE payload formats and successful planning behavior are preserved. No frontend source, planning calculations, prompts, model configuration, schema, or persistence behavior was changed.

## Root causes

- The response layer detected client disconnects, but cleanup depended on the body iterator being explicitly closed.
- The planning graph uses synchronous iteration inside an async SSE generator. Closing only the outer response did not explicitly close the graph iterator and its enclosing checkpointer context.
- The async agent stream also needed explicit `aclose()` cleanup and cancellation had to remain distinct from an application failure.
- A direct task-cancellation regression test showed that cancellation can arrive before an in-flight thread operation returns. Closing the synchronous generator at that point races with `next()` and can raise `ValueError: generator already executing`.

## Changes implemented

- `backend/main.py`
  - Added `ClosingStreamingResponse`, which closes the response body iterator in a shielded `finally` block.
  - Added cancellation-safe synchronous iterator advancement and cleanup. If a synchronous `next()` is already running, cancellation waits for that call to finish before closing the iterator; no subsequent graph step is started.
  - Trip-planning and agent SSE handlers explicitly close their iterators and re-raise `asyncio.CancelledError`. Cancellation does not generate an SSE error, completion, or `[DONE]` event.
  - Normal event serialization and successful completion behavior are unchanged.
- `backend/supervisor.py`
  - Closes the underlying `graph.stream()` iterator in `finally`, allowing the graph generator and SQLite saver context to unwind.
- `tests/test_stream_cancellation.py`
  - Added isolated tests for planning/follow-up disconnects, async agent cancellation, graph iterator cleanup, in-flight synchronous step cancellation, and normal follow-up completion.
- `evaluation/phase2c_backend_stream_cancellation/`
  - Added implementation, test-results, and cancellation-verification reports.

## Cancellation behavior and limitations

- Starlette's streaming response handles client disconnect detection. The application does not concurrently poll `Request.is_disconnected()`, avoiding competing reads from the ASGI receive channel.
- Async agent-stream cancellation closes the local async event iterator. The application cannot guarantee that the Ollama server stops model generation after the client disconnects; server-side cancellation was not verified.
- A synchronous LangGraph node cannot be interrupted mid-call. Its current `next()` operation is allowed to finish, after which the iterator is closed and later graph steps are not run. Disconnect cleanup may therefore wait for that operation.
- The non-streaming `/api/analyze` and `/api/trips/{trip_id}/plan` routes still execute synchronous work in a threadpool and are not made interruptible by this streaming-focused change.

## Scope and risks

Changes are limited to the two backend streaming paths, supervisor graph-iterator cleanup, focused regression tests, and these reports. Tests use temporary databases; no production database records or Ollama settings were modified.

The project directory did not contain Git metadata, so a Git diff/status check was unavailable. The touched implementation and test sections were reviewed directly. The frontend build generated files under `frontend/dist`; no frontend source files were changed.
