"""
FastAPI application — Travel Budget Planner backend.

Endpoints:
  POST /api/analyze   — runs the LangGraph agent and returns a structured response.
  GET  /api/health    — checks Ollama reachability and model availability.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, suppress
from typing import TypeVar

import anyio
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from . import rates
from .config import CORS_ORIGINS, FX_ENABLED, OLLAMA_HOST, OLLAMA_MODEL
from .models import (
    AnalyzeRequest,
    AnalyzeResponse,
    AccommodationLinksOut,
    CopilotRequest,
    CopilotResponse,
    CopilotTurn,
    DayWeatherOut,
    ErrorResponse,
    FeasibilityIssueOut,
    FeasibilityRequest,
    FeasibilityResponseOut,
    FlightLinksOut,
    HealthResponse,
    ItineraryDocument,
    ItineraryGenerateRequest,
    ItineraryModifyRequest,
    ItinerarySaveRequest,
    LogisticsResponseOut,
    PoiResponseOut,
    PoiResultOut,
    RateInfo,
    RatesResponse,
    TraceEvent,
    TransportGuidanceOut,
    TripPlanRequest,
    TripPlanResponse,
    TripPreferences,
    TripSummary,
    WeatherResponseOut,
)
from .fallback import reconcile_agent_result
from .tracing import build_trace, extract_structured_data
from .persistence import PersistenceError, TripStore
from .rag import DestinationCostGuide
from .auth import (
    LocalPrincipal,
    initialize_deployment_owner,
    require_csrf,
    require_local_owner,
    router as auth_router,
)

logger = logging.getLogger(__name__)

_StreamItem = TypeVar("_StreamItem")
_SyncResult = TypeVar("_SyncResult")


class _SynchronousIteratorExhausted(Exception):
    pass


def _next_or_exhausted(iterator: Iterator[_StreamItem]) -> _StreamItem:
    try:
        return next(iterator)
    except StopIteration as exc:
        raise _SynchronousIteratorExhausted from exc


async def _run_sync_to_completion(
    function: Callable[..., _SyncResult],
    *args: object,
) -> _SyncResult:
    operation = asyncio.create_task(run_in_threadpool(function, *args))
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        with anyio.CancelScope(shield=True):
            try:
                await operation
            except Exception as exc:
                logger.warning(
                    "Synchronous stream operation failed during cancellation (error_type=%s)",
                    type(exc).__name__,
                )
        raise


async def _iterate_sync_and_close(
    iterator: Iterator[_StreamItem],
) -> AsyncIterator[_StreamItem]:
    try:
        while True:
            try:
                item = await _run_sync_to_completion(_next_or_exhausted, iterator)
            except _SynchronousIteratorExhausted:
                break
            yield item
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            await _run_sync_to_completion(close)


class ClosingStreamingResponse(StreamingResponse):
    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            close = getattr(self.body_iterator, "aclose", None)
            if close is not None:
                try:
                    with anyio.CancelScope(shield=True):
                        await close()
                except Exception:
                    logger.exception("Unable to close streaming response body")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    TripStore()
    initialize_deployment_owner()
    rates.initialize_rates()
    refresh_task = None
    if FX_ENABLED:
        await rates.refresh_rates(force=True)
        refresh_task = asyncio.create_task(rates.refresh_rates_forever())
    try:
        yield
    finally:
        if refresh_task is not None:
            refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await refresh_task


app = FastAPI(
    title="Travel Budget Planner API",
    description="Autonomous AI agent that answers travel-budget questions via two-tool reasoning.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=5),
    retry=retry_if_exception_type(httpx.HTTPError),
    reraise=True,
)
async def _check_ollama() -> tuple[bool, bool]:
    """Returns (ollama_reachable, model_available) with retry on network errors."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(f"{OLLAMA_HOST}/api/tags")
        if r.status_code != 200:
            return False, False
        data = r.json()
        models = [m.get("name", "") for m in data.get("models", [])]
        model_available = OLLAMA_MODEL in models
        return True, model_available


async def _ollama_status() -> tuple[bool, bool]:
    try:
        return await _check_ollama()
    except (httpx.HTTPError, ValueError):
        logger.warning("Unable to check Ollama health at %s", OLLAMA_HOST, exc_info=True)
        return False, False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/api/health", response_model=HealthResponse)
async def health():
    """Check whether Ollama is running and the required model is pulled."""
    reachable, available = await _ollama_status()

    if not reachable:
        msg = (
            "Unable to connect to Ollama. Make sure Ollama is running "
            f"at {OLLAMA_HOST} (run: ollama serve)."
        )
    elif not available:
        msg = (
            f"Ollama is running but model '{OLLAMA_MODEL}' is not pulled. "
            f"Run: ollama pull {OLLAMA_MODEL}"
        )
    else:
        msg = f"Ollama is running and model '{OLLAMA_MODEL}' is available."

    return HealthResponse(
        ollama_reachable=reachable,
        model_available=available,
        model=OLLAMA_MODEL,
        message=msg,
    )


@app.get("/api/destinations", response_model=list[str])
async def list_destinations():
    return DestinationCostGuide().destinations()


@app.get("/api/rates", response_model=RatesResponse)
async def get_rates():
    """Current exchange-rate snapshot with its provenance and freshness."""
    snapshot = rates.get_snapshot()
    return RatesResponse(
        base=rates.BASE_CURRENCY,
        pairs=[
            RateInfo(
                from_currency=pair["from"],
                to_currency=pair["to"],
                rate=pair["rate"],
            )
            for pair in rates.rate_pairs()
        ],
        source=snapshot.source,
        label=snapshot.label,
        as_of=snapshot.as_of,
        fetched_at=snapshot.fetched_at,
        is_live=snapshot.is_live,
        stale=snapshot.stale,
        note=snapshot.note,
    )


@app.get("/api/trips", response_model=list[TripSummary])
async def list_trips(principal: LocalPrincipal = Depends(require_local_owner)):
    return TripStore().list_trips_for_owner(principal.owner_id)


@app.put("/api/trips/{trip_id}/preferences", response_model=TripPreferences)
@app.post("/api/trips/{trip_id}/preferences", response_model=TripPreferences)
async def save_trip_preferences(
    trip_id: str,
    preferences: TripPreferences,
    principal: LocalPrincipal = Depends(require_csrf),
):
    if not trip_id.strip() or len(trip_id) > 100:
        raise HTTPException(status_code=422, detail="trip_id must contain 1 to 100 characters.")
    TripStore().save_preferences_for_owner(trip_id, preferences, principal.owner_id)
    return preferences


@app.get("/api/trips/{trip_id}/preferences", response_model=TripPreferences)
async def get_trip_preferences(
    trip_id: str,
    principal: LocalPrincipal = Depends(require_local_owner),
):
    preferences = TripStore().get_preferences_for_owner(trip_id, principal.owner_id)
    if preferences is None:
        raise HTTPException(status_code=404, detail="No saved preferences for this trip.")
    return preferences


@app.get("/api/trips/{trip_id}/conversation", response_model=list[dict[str, str]])
async def get_trip_conversation(
    trip_id: str,
    principal: LocalPrincipal = Depends(require_local_owner),
):
    return TripStore().get_conversation_for_owner(trip_id, principal.owner_id)


@app.get("/api/trips/{trip_id}/plan", response_model=TripPlanResponse)
async def get_trip_plan(
    trip_id: str,
    principal: LocalPrincipal = Depends(require_local_owner),
):
    store = TripStore()
    if store.get_preferences_for_owner(trip_id, principal.owner_id) is None:
        raise HTTPException(status_code=404, detail="No saved preferences for this trip.")
    from .supervisor import get_saved_plan

    result = await run_in_threadpool(get_saved_plan, trip_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No plan has been generated for this trip.")
    result.conversation = store.get_conversation_for_owner(trip_id, principal.owner_id)
    return result


@app.post("/api/trips/{trip_id}/plan", response_model=TripPlanResponse)
async def plan_trip(
    trip_id: str,
    request: TripPlanRequest,
    principal: LocalPrincipal = Depends(require_csrf),
):
    if not trip_id.strip() or len(trip_id) > 100:
        raise HTTPException(status_code=422, detail="trip_id must contain 1 to 100 characters.")
    store = TripStore()
    preferences = store.get_preferences_for_owner(trip_id, principal.owner_id)
    if preferences is None:
        raise HTTPException(
            status_code=404,
            detail="Save trip preferences before requesting a plan.",
        )
    try:
        from .supervisor import run_planning_thread

        result = await run_in_threadpool(
            run_planning_thread, trip_id, request.query, preferences
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Persistent trip planner failed for trip %s", trip_id)
        raise HTTPException(
            status_code=500,
            detail="Trip planning failed; no result was saved.",
        ) from exc
    result.conversation = store.save_plan_update_for_owner(
        trip_id,
        result.preferences,
        request.query,
        result.answer,
        principal.owner_id,
    )
    return result


@app.post("/api/trips/{trip_id}/plan/stream")
async def plan_trip_stream(
    trip_id: str,
    request: TripPlanRequest,
    principal: LocalPrincipal = Depends(require_csrf),
):
    """Stream planning stages as Server-Sent Events, ending with the complete plan."""
    if not trip_id.strip() or len(trip_id) > 100:
        raise HTTPException(status_code=422, detail="trip_id must contain 1 to 100 characters.")
    store = TripStore()
    preferences = store.get_preferences_for_owner(trip_id, principal.owner_id)
    if preferences is None:
        raise HTTPException(
            status_code=404,
            detail="Save trip preferences before requesting a plan.",
        )

    async def event_stream():
        completed = False
        events = None
        try:
            from .supervisor import run_planning_thread_stream

            events = _iterate_sync_and_close(
                run_planning_thread_stream(trip_id, request.query, preferences)
            )
            async for event in events:
                if event.get("type") == "complete":
                    plan = TripPlanResponse.model_validate(event["plan"])
                    plan.conversation = store.save_plan_update_for_owner(
                        trip_id,
                        plan.preferences,
                        request.query,
                        plan.answer,
                        principal.owner_id,
                    )
                    event["plan"] = json.loads(plan.model_dump_json())
                    completed = True
                yield f"data: {json.dumps(event)}\n\n"
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Streaming trip planner failed for trip %s", trip_id)
            yield f"data: {json.dumps({'type': 'error', 'message': 'Trip planning failed; no result was saved.'})}\n\n"
        else:
            if not completed:
                logger.error("Trip planning stream ended without a result")
                yield f"data: {json.dumps({'type': 'error', 'message': 'Trip planning ended before a plan was produced.'})}\n\n"
        finally:
            if events is not None:
                try:
                    with anyio.CancelScope(shield=True):
                        await events.aclose()
                except Exception:
                    logger.exception("Unable to close trip-planning event iterator")

        yield "data: [DONE]\n\n"

    return ClosingStreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest):
    """
    Run the autonomous LangGraph agent on the user's travel-budget query.

    The agent decides which tools to call and in what order.
    Returns a structured response with the final answer, feasibility data,
    and the full execution trace derived from real LangGraph messages.
    """
    # Pre-flight Ollama check
    reachable, available = await _ollama_status()
    if not reachable:
        raise HTTPException(
            status_code=503,
            detail=(
                "Unable to connect to Ollama. Make sure Ollama is running "
                f"at {OLLAMA_HOST} (run: ollama serve)."
            ),
        )
    if not available:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Model '{OLLAMA_MODEL}' is not available in Ollama. "
                f"Run: ollama pull {OLLAMA_MODEL}"
            ),
        )

    # Run agent (import here to avoid build-time Ollama requirement)
    try:
        from .agent import run_agent
        messages = await run_in_threadpool(run_agent, request.query)
    except httpx.HTTPError as exc:
        logger.exception("Unable to communicate with Ollama")
        raise HTTPException(
            status_code=502,
            detail="Unable to communicate with Ollama while processing the request.",
        ) from exc
    except Exception as exc:
        logger.exception("Travel budget agent failed")
        raise HTTPException(
            status_code=500,
            detail="The travel budget agent failed while processing the request.",
        ) from exc

    # Build trace from real messages
    trace = build_trace(messages)

    # Extract structured data from tool results
    structured = extract_structured_data(trace)

    # Collect the final answer text
    final_answer = ""
    from langchain_core.messages import AIMessage
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", []):
            final_answer = msg.content if isinstance(msg.content, str) else str(msg.content)
            break

    if not final_answer:
        raise HTTPException(
            status_code=502,
            detail="The agent did not produce a final text response.",
        )

    outcome = reconcile_agent_result(request.query, final_answer, structured, trace)

    return AnalyzeResponse(
        answer=outcome.answer,
        feasible=outcome.structured["feasible"],
        total_cost=outcome.structured["total_cost"],
        budget=outcome.structured["budget"],
        remaining=outcome.structured["remaining"],
        overage=outcome.structured["overage"],
        currency=outcome.structured["currency"],
        trace=outcome.trace,
    )


@app.post("/api/analyze/stream")
async def analyze_stream(request: AnalyzeRequest):
    """
    Streaming version of /api/analyze using Server-Sent Events.
    Pushes trace events progressively as the agent processes them.
    """
    reachable, available = await _ollama_status()
    if not reachable or not available:
        message = (
            f"Ollama is unavailable at {OLLAMA_HOST}."
            if not reachable
            else f"Model '{OLLAMA_MODEL}' is not available. Run: ollama pull {OLLAMA_MODEL}"
        )
        raise HTTPException(status_code=503, detail=message)

    async def event_stream():
        agent_events = None
        try:
            from langchain_core.messages import HumanMessage
            from .agent import agent as _agent

            completed = False
            agent_events = _agent.astream_events(
                {"messages": [HumanMessage(content=request.query)]},
                version="v2",
            )
            async for event in agent_events:
                kind = event.get("event", "")
                data = event.get("data", {})

                if kind == "on_tool_start":
                    payload = {
                        "type": "tool_start",
                        "tool": event.get("name", ""),
                        "arguments": data.get("input", {}),
                    }
                    yield f"data: {json.dumps(payload)}\n\n"

                elif kind == "on_tool_end":
                    output = data.get("output", "")
                    if hasattr(output, "content"):
                        output = output.content
                    payload = {
                        "type": "tool_end",
                        "tool": event.get("name", ""),
                        "result": output if isinstance(output, str) else str(output),
                    }
                    yield f"data: {json.dumps(payload)}\n\n"

                elif kind == "on_chain_end" and event.get("name") == "LangGraph":
                    # Final state — emit the complete response
                    output = data.get("output", {})
                    msgs = output.get("messages", [])
                    trace = build_trace(msgs)
                    structured = extract_structured_data(trace)
                    final_answer = ""
                    from langchain_core.messages import AIMessage
                    for msg in reversed(msgs):
                        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", []):
                            final_answer = msg.content if isinstance(msg.content, str) else str(msg.content)
                            break
                    if not final_answer:
                        logger.error("Agent stream ended without a final assistant response")
                        yield f"data: {json.dumps({'type': 'error', 'message': 'The agent did not produce a final text response.'})}\n\n"
                        completed = True
                        continue
                    outcome = reconcile_agent_result(
                        request.query, final_answer, structured, trace
                    )
                    payload = {
                        "type": "complete",
                        "answer": outcome.answer,
                        **outcome.structured,
                        "trace": [t.model_dump() for t in outcome.trace],
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
                    completed = True

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Streaming travel budget agent failed")
            yield f"data: {json.dumps({'type': 'error', 'message': 'The travel budget agent failed while processing the request.'})}\n\n"
        else:
            if not completed:
                logger.error("Agent event stream ended without a final graph response")
                yield f"data: {json.dumps({'type': 'error', 'message': 'The agent stream ended before a result was produced.'})}\n\n"
        finally:
            close = getattr(agent_events, "aclose", None)
            if close is not None:
                try:
                    with anyio.CancelScope(shield=True):
                        await close()
                except Exception:
                    logger.exception("Unable to close agent event iterator")

        yield "data: [DONE]\n\n"

    return ClosingStreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Itinerary Builder — Phase 3
# ---------------------------------------------------------------------------


@app.post("/api/itinerary/generate", response_model=ItineraryDocument)
async def generate_itinerary_endpoint(
    request: ItineraryGenerateRequest,
    principal: LocalPrincipal = Depends(require_csrf),
):
    """
    Generate an AI itinerary from a trip configuration.
    Does NOT save automatically — use PUT /api/trips/{trip_id}/itinerary to persist.
    """
    reachable, available = await _ollama_status()
    if not reachable:
        raise HTTPException(
            status_code=503,
            detail=f"Ollama is not reachable at {OLLAMA_HOST}. Run: ollama serve",
        )
    if not available:
        raise HTTPException(
            status_code=503,
            detail=f"Model '{OLLAMA_MODEL}' is not pulled. Run: ollama pull {OLLAMA_MODEL}",
        )

    try:
        from .itinerary import generate_itinerary
        doc = await run_in_threadpool(generate_itinerary, request.config)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Itinerary generation failed")
        raise HTTPException(status_code=500, detail="Itinerary generation failed.") from exc

    return doc


@app.post("/api/trips/{trip_id}/itinerary/modify", response_model=ItineraryDocument)
async def modify_itinerary_endpoint(
    trip_id: str,
    request: ItineraryModifyRequest,
    principal: LocalPrincipal = Depends(require_csrf),
):
    """
    Apply a natural-language modification to the saved itinerary.
    Returns a PREVIEW — does not save automatically.
    If current_itinerary is provided in the request body it is used directly
    (allowing modification before the first save).
    """
    if not trip_id.strip() or len(trip_id) > 100:
        raise HTTPException(status_code=422, detail="trip_id must contain 1 to 100 characters.")

    # Resolve the current itinerary: prefer in-request body, fall back to DB
    if request.current_itinerary is not None:
        current = request.current_itinerary
    else:
        store = TripStore()
        raw = store.get_itinerary_for_owner(trip_id, principal.owner_id)
        if raw is None:
            raise HTTPException(status_code=404, detail="No saved itinerary for this trip.")
        import json as _json
        from .models import ItineraryDocument as _ItinDoc
        try:
            current = _ItinDoc.model_validate(_json.loads(raw))
        except Exception as exc:
            logger.warning("Corrupt itinerary data for trip %s: %s", trip_id, exc)
            raise HTTPException(status_code=500, detail="Stored itinerary data is invalid.") from exc

    reachable, available = await _ollama_status()
    if not reachable or not available:
        raise HTTPException(
            status_code=503,
            detail="Ollama is unavailable. Run: ollama serve",
        )

    try:
        from .itinerary import modify_itinerary
        preview = await run_in_threadpool(
            modify_itinerary, current.config, current, request
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Itinerary modification failed for trip %s", trip_id)
        raise HTTPException(status_code=500, detail="Itinerary modification failed.") from exc

    return preview


@app.put("/api/trips/{trip_id}/itinerary", response_model=ItineraryDocument)
async def save_itinerary_endpoint(
    trip_id: str,
    request: ItinerarySaveRequest,
    principal: LocalPrincipal = Depends(require_csrf),
):
    """Save (or overwrite) the itinerary for a trip."""
    if not trip_id.strip() or len(trip_id) > 100:
        raise HTTPException(status_code=422, detail="trip_id must contain 1 to 100 characters.")

    store = TripStore()
    # Ensure trip exists and is owned before saving
    if store.get_preferences_for_owner(trip_id, principal.owner_id) is None:
        raise HTTPException(
            status_code=404,
            detail="Save trip preferences before saving an itinerary.",
        )

    import json as _json
    from datetime import datetime, timezone
    doc = request.itinerary
    doc = doc.model_copy(
        update={"updated_at": datetime.now(timezone.utc).isoformat()}
    )
    store.save_itinerary_for_owner(
        trip_id, doc.model_dump_json(), principal.owner_id
    )
    return doc


@app.get("/api/trips/{trip_id}/itinerary", response_model=ItineraryDocument)
async def get_itinerary_endpoint(
    trip_id: str,
    principal: LocalPrincipal = Depends(require_local_owner),
):
    """Retrieve the saved itinerary for a trip."""
    if not trip_id.strip() or len(trip_id) > 100:
        raise HTTPException(status_code=422, detail="trip_id must contain 1 to 100 characters.")

    store = TripStore()
    raw = store.get_itinerary_for_owner(trip_id, principal.owner_id)
    if raw is None:
        raise HTTPException(status_code=404, detail="No saved itinerary for this trip.")

    import json as _json
    from .models import ItineraryDocument as _ItinDoc
    try:
        return _ItinDoc.model_validate(_json.loads(raw))
    except Exception as exc:
        logger.warning("Corrupt itinerary data for trip %s: %s", trip_id, exc)
        raise HTTPException(status_code=500, detail="Stored itinerary data is invalid.") from exc


# ---------------------------------------------------------------------------
# Phase 5 — Weather / POI / Feasibility
# ---------------------------------------------------------------------------


@app.get("/api/weather/{destination}", response_model=WeatherResponseOut)
async def get_weather_endpoint(
    destination: str,
    start_date: str,
    end_date: str,
    _principal: LocalPrincipal = Depends(require_local_owner),
):
    """
    Return weather forecast or climatology for a destination and date range.
    Uses Open-Meteo (no key required, CC BY 4.0) with in-process cache.
    Falls back to long-run monthly averages when outside the 16-day window.
    """
    from datetime import date as _date
    from .weather import get_weather

    try:
        sd = _date.fromisoformat(start_date)
        ed = _date.fromisoformat(end_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="start_date and end_date must be ISO dates.") from exc

    if ed < sd:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date.")

    try:
        result = await run_in_threadpool(get_weather, destination, sd, ed)
        # Convert dataclass to Pydantic response
        return WeatherResponseOut(
            destination=result.destination,
            latitude=result.latitude,
            longitude=result.longitude,
            timezone=result.timezone,
            days=[DayWeatherOut(**{
                "date": d.date,
                "temp_min": d.temp_min,
                "temp_max": d.temp_max,
                "precipitation_mm": d.precipitation_mm,
                "precipitation_probability": d.precipitation_probability,
                "weather_code": d.weather_code,
                "weather_label": d.weather_label,
                "is_rain_likely": d.is_rain_likely,
                "wind_speed_max": d.wind_speed_max,
                "source": d.source,
                "is_suitable_outdoor": d.is_suitable_outdoor,
            }) for d in result.days],
            source=result.source,
            fetched_at=result.fetched_at,
            forecast_generated_at=result.forecast_generated_at,
            attribution=result.attribution,
            notes=result.notes,
        )
    except Exception as exc:
        logger.exception("Weather endpoint failed for %s", destination)
        raise HTTPException(status_code=502, detail="Weather data could not be retrieved.") from exc


@app.get("/api/poi/{destination}", response_model=PoiResponseOut)
async def get_poi_endpoint(
    destination: str,
    _principal: LocalPrincipal = Depends(require_local_owner),
):
    """
    Return real POIs near a destination using OpenStreetMap / Overpass API.
    Falls back to bundled attraction list when unavailable.
    Attribution: Data © OpenStreetMap contributors, ODbL 1.0.
    """
    from .poi import get_pois

    try:
        result = await run_in_threadpool(get_pois, destination)
        return PoiResponseOut(
            destination=result.destination,
            latitude=result.latitude,
            longitude=result.longitude,
            pois=[PoiResultOut(
                name=p.name,
                category=p.category,
                osm_type=p.osm_type,
                osm_id=p.osm_id,
                latitude=p.latitude,
                longitude=p.longitude,
                source=p.source,
                wikipedia=p.wikipedia,
                website=p.website,
                description=p.description,
            ) for p in result.pois],
            source=result.source,
            attribution=result.attribution,
            fetched_at=result.fetched_at,
            notes=result.notes,
        )
    except Exception as exc:
        logger.exception("POI endpoint failed for %s", destination)
        raise HTTPException(status_code=502, detail="POI data could not be retrieved.") from exc


@app.post("/api/itinerary/feasibility", response_model=FeasibilityResponseOut)
async def feasibility_endpoint(
    request: FeasibilityRequest,
    _principal: LocalPrincipal = Depends(require_local_owner),
):
    """
    Analyse an itinerary for schedule conflicts, missing breaks, budget overruns, etc.
    Returns issues with severity levels and improvement suggestions.
    Deterministic — no AI or external calls.
    """
    from .feasibility import analyse, summarise

    try:
        issues = analyse(request.itinerary)
        summary = summarise(issues)
        return FeasibilityResponseOut(
            issues=[FeasibilityIssueOut(
                severity=i.severity,
                day_number=i.day_number,
                activity_id=i.activity_id,
                code=i.code,
                message=i.message,
                suggestion=i.suggestion,
            ) for i in issues],
            summary=summary,
            analysed_days=len(request.itinerary.daily_plans),
        )
    except Exception as exc:
        logger.exception("Feasibility analysis failed")
        raise HTTPException(status_code=500, detail="Feasibility analysis failed.") from exc


@app.get("/api/logistics/{destination}", response_model=LogisticsResponseOut)
async def get_logistics_endpoint(
    destination: str,
    origin: str = "",
    start_date: str = "",
    end_date: str = "",
    travelers: int = 1,
    budget_style: str = "moderate",
    accommodation_preference: str = "hotel",
    _principal: LocalPrincipal = Depends(require_local_owner),
):
    """
    Return parameterised flight search links, accommodation search links,
    and static local transport guidance for a destination.

    No live API calls. No bookings. All links open third-party search pages.
    """
    if travelers < 1 or travelers > 20:
        raise HTTPException(status_code=422, detail="travelers must be between 1 and 20.")
    if budget_style not in ("budget", "moderate", "luxury"):
        budget_style = "moderate"

    from .logistics import build_logistics

    try:
        result = await run_in_threadpool(
            build_logistics,
            destination,
            origin,
            start_date,
            end_date,
            travelers,
            budget_style,
            accommodation_preference,
        )
        return LogisticsResponseOut(
            destination=result.destination,
            flights=FlightLinksOut(
                google_flights=result.flights.google_flights,
                skyscanner=result.flights.skyscanner,
                rome2rio=result.flights.rome2rio,
                origin=result.flights.origin,
                destination=result.flights.destination,
                depart_date=result.flights.depart_date,
                return_date=result.flights.return_date,
                adults=result.flights.adults,
                airport_code=result.flights.airport_code,
                notes=result.flights.notes,
            ),
            accommodation=AccommodationLinksOut(
                booking_com=result.accommodation.booking_com,
                airbnb=result.accommodation.airbnb,
                hostelworld=result.accommodation.hostelworld,
                destination=result.accommodation.destination,
                checkin=result.accommodation.checkin,
                checkout=result.accommodation.checkout,
                adults=result.accommodation.adults,
                budget_style=result.accommodation.budget_style,
                notes=result.accommodation.notes,
            ),
            transport=TransportGuidanceOut(
                destination=result.transport.destination,
                airport_transfer=result.transport.airport_transfer,
                local_transport=result.transport.local_transport,
                public_transport=result.transport.public_transport,
                rome2rio_url=result.transport.rome2rio_url,
                notes=result.transport.notes,
                source=result.transport.source,
            ),
            disclaimer=result.disclaimer,
        )
    except Exception as exc:
        logger.exception("Logistics endpoint failed for %s", destination)
        raise HTTPException(status_code=500, detail="Logistics data could not be built.") from exc


# ---------------------------------------------------------------------------
# Phase 7 — AI Travel Copilot
# ---------------------------------------------------------------------------


@app.post("/api/trips/{trip_id}/copilot", response_model=CopilotResponse)
async def copilot_chat(
    trip_id: str,
    request: CopilotRequest,
    principal: LocalPrincipal = Depends(require_csrf),
):
    """
    Send a message to the AI travel copilot for the current trip.
    Returns the assistant reply, modification intent flag, and updated history.
    Never modifies the itinerary automatically.
    """
    if not trip_id.strip() or len(trip_id) > 100:
        raise HTTPException(status_code=422, detail="trip_id must be 1–100 characters.")

    reachable, available = await _ollama_status()
    if not reachable:
        raise HTTPException(status_code=503, detail=f"Ollama is not reachable at {OLLAMA_HOST}.")
    if not available:
        raise HTTPException(
            status_code=503,
            detail=f"Model '{OLLAMA_MODEL}' is not pulled. Run: ollama pull {OLLAMA_MODEL}",
        )

    store = TripStore()
    prefs = store.get_preferences_for_owner(trip_id, principal.owner_id)
    if prefs is None:
        raise HTTPException(
            status_code=404,
            detail="No saved preferences for this trip. Save preferences first.",
        )

    # Load existing conversation (last 20 turns = 10 exchanges)
    history = store.get_copilot_history_for_owner(trip_id, principal.owner_id, limit=20)

    # Load saved itinerary if available
    itin_raw = store.get_itinerary_for_owner(trip_id, principal.owner_id)
    itinerary_dict: dict | None = None
    if itin_raw:
        try:
            import json as _json
            itinerary_dict = _json.loads(itin_raw)
        except Exception:
            itinerary_dict = None

    # Build system prompt
    from .copilot import build_system_prompt, chat, _detect_modification_intent

    system_prompt = await run_in_threadpool(
        build_system_prompt,
        prefs.destination,
        prefs.start_date.isoformat(),
        prefs.end_date.isoformat(),
        prefs.travelers,
        prefs.budget,
        prefs.currency,
        prefs.budget_style,
        list(prefs.interests),
        itinerary_dict,
        request.weather_context,
        request.feasibility_context,
        request.logistics_context,
    )

    # Get AI reply
    try:
        reply = await run_in_threadpool(chat, system_prompt, history, request.message)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Copilot chat failed for trip %s", trip_id)
        raise HTTPException(status_code=500, detail="Copilot failed to respond.") from exc

    # Persist the exchange
    store.append_copilot_turn(trip_id, request.message, reply, principal.owner_id)

    # Reload history for response
    updated_history = store.get_copilot_history_for_owner(trip_id, principal.owner_id, limit=20)

    return CopilotResponse(
        reply=reply,
        suggests_modification=_detect_modification_intent(reply),
        history=[CopilotTurn(role=t["role"], content=t["content"]) for t in updated_history],
    )


@app.get("/api/trips/{trip_id}/copilot/history", response_model=list[CopilotTurn])
async def get_copilot_history(
    trip_id: str,
    principal: LocalPrincipal = Depends(require_local_owner),
):
    """Return the copilot conversation history for a trip."""
    if not trip_id.strip() or len(trip_id) > 100:
        raise HTTPException(status_code=422, detail="trip_id must be 1–100 characters.")
    store = TripStore()
    history = store.get_copilot_history_for_owner(trip_id, principal.owner_id, limit=40)
    return [CopilotTurn(role=t["role"], content=t["content"]) for t in history]


@app.delete("/api/trips/{trip_id}/copilot/history", status_code=204)
async def clear_copilot_history(
    trip_id: str,
    principal: LocalPrincipal = Depends(require_csrf),
):
    """Clear the copilot conversation history for a trip."""
    if not trip_id.strip() or len(trip_id) > 100:
        raise HTTPException(status_code=422, detail="trip_id must be 1–100 characters.")
    store = TripStore()
    store.clear_copilot_history_for_owner(trip_id, principal.owner_id)


# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------

@app.exception_handler(PersistenceError)
async def persistence_exception_handler(request: Request, exc: PersistenceError):
    logger.warning(
        "Persistence request failed (kind=%s)",
        exc.kind,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(error=exc.public_message).model_dump(),
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled application exception for %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(error="Internal server error").model_dump(),
    )
