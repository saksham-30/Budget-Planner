"""
AI Itinerary Builder for Travel Budget Planner.

Uses ChatOllama directly (not the LangGraph tool-calling loop) with a
structured prompt to generate JSON itinerary data, then validates it
with Pydantic.  The existing LangGraph agent, tools, and supervisor
are untouched.

Design:
  - Single ChatOllama call with a JSON-schema prompt.
  - Server-side Pydantic validation of LLM output.
  - Fallback: return a structured error message rather than crash.
  - Itinerary stored in trip_itineraries table (additive migration).
  - Modification uses the same path: send current itinerary in the
    prompt, ask for targeted changes, validate, return preview.
"""

from __future__ import annotations

import json
import logging
import math
import re
import uuid
from datetime import date, timedelta
from typing import Any

from pydantic import ValidationError

from . import rates as fx
from .config import OLLAMA_HOST, OLLAMA_MODEL
from .models import (
    ItineraryActivity,
    ItineraryBudget,
    ItineraryConfig,
    ItineraryPlanDay,
    ItineraryDocument,
    ItineraryModifyRequest,
)
from .rag import DestinationCostGuide
from .tools import _normalize_currency

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cost guide helper
# ---------------------------------------------------------------------------

def _get_guide_costs(destination: str, style: str, currency: str) -> dict[str, float]:
    """Return per-traveler per-day guide estimates in the requested currency."""
    guide = DestinationCostGuide()
    evidence = guide.retrieve(destination, fallback=True)
    if evidence is None:
        return {}
    estimates: dict[str, float] = {}
    safe_style = style if style in ("budget", "moderate", "luxury") else "moderate"
    for category in ("hotel", "food", "transport", "activities"):
        levels = evidence.estimates.get(category, {})
        raw = levels.get(safe_style, levels.get("moderate", 0.0))
        if evidence.currency != currency:
            quote = fx.quote(evidence.currency, currency)
            if quote:
                raw = raw * quote.rate
        estimates[category] = round(raw, 2)
    return estimates


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_PACE_DESC = {
    "relaxed": "2–3 activities per day with generous breaks",
    "balanced": "3–4 activities per day with meal breaks",
    "packed": "4–5 activities per day, early starts, minimal downtime",
}

_BASE_INSTRUCTIONS = """You are a travel itinerary planner. Respond ONLY with a valid JSON object. No prose, no markdown fences, no extra keys.

The JSON must match this exact schema:
{{
  "summary": "2-3 sentence trip overview",
  "daily_plans": [
    {{
      "day_number": 1,
      "date": "YYYY-MM-DD",
      "title": "Day title",
      "description": "1-2 sentence day overview",
      "activities": [
        {{
          "id": "unique_string_id",
          "time": "HH:MM",
          "duration_minutes": 60,
          "name": "Activity name",
          "description": "1-2 sentences",
          "location": "Place name",
          "category": "sightseeing|food|transport|accommodation|leisure|shopping|nature",
          "estimated_cost_per_person": 0.0,
          "currency": "INR",
          "notes": "optional tip or note",
          "is_break": false,
          "is_optional": false
        }}
      ]
    }}
  ],
  "budget_breakdown": {{
    "accommodation_total": 0.0,
    "food_total": 0.0,
    "transport_total": 0.0,
    "activities_total": 0.0,
    "miscellaneous_total": 0.0,
    "grand_total": 0.0,
    "per_person_total": 0.0,
    "currency": "INR",
    "daily_average": 0.0,
    "budget_remaining": 0.0,
    "is_over_budget": false,
    "notes": "1-2 sentences about budget assumptions"
  }}
}}

Rules:
- daily_plans must have exactly {day_count} entries, one per day from {start_date} to {end_date}.
- Each activity must have a unique id (e.g. "d1_a1", "d1_a2").
- Include realistic meal breaks (category "food") and at least one rest or transit activity per day.
- Do NOT invent live prices, booking status, exact opening hours, or transport distances.
- Budget estimates are illustrative; label them accordingly in notes.
- All monetary values must use currency code {currency}.
- grand_total must equal accommodation_total + food_total + transport_total + activities_total + miscellaneous_total.
- per_person_total = grand_total / {travelers}.
- budget_remaining = {budget} - grand_total (can be negative if over budget).
- is_over_budget = true if grand_total > {budget}.
- Use guide cost hints provided below — do not invent widely different numbers.
- For pace "{pace}": aim for {pace_desc}.
"""

def _build_prompt(config: ItineraryConfig, guide_costs: dict[str, float]) -> str:
    start = date.fromisoformat(config.start_date)
    end = date.fromisoformat(config.end_date)
    day_count = (end - start).days + 1

    hints = "\n".join(
        f"  {k}: approx {v} {config.currency} per traveler per day"
        for k, v in guide_costs.items()
        if v > 0
    )
    if not hints:
        hints = "  No specific guide data — use illustrative regional estimates."

    attractions = ""
    if config.destination:
        from .data_helpers import destination_attractions
        attrs = destination_attractions(config.destination)
        if attrs:
            attractions = "Known attractions to consider: " + "; ".join(attrs[:8]) + "."

    base = _BASE_INSTRUCTIONS.format(
        day_count=day_count,
        start_date=config.start_date,
        end_date=config.end_date,
        currency=config.currency,
        travelers=config.travelers,
        budget=config.budget,
        pace=config.pace,
        pace_desc=_PACE_DESC.get(config.pace, _PACE_DESC["balanced"]),
    )

    user_block = f"""
Trip details:
- Destination: {config.destination}
- Origin: {config.origin or 'not specified'}
- Dates: {config.start_date} to {config.end_date} ({day_count} days)
- Travelers: {config.travelers}
- Budget: {config.budget} {config.currency} total
- Travel pace: {config.pace}
- Accommodation: {config.accommodation_preference}
- Interests: {', '.join(config.interests) if config.interests else 'general sightseeing'}
- Dietary / accessibility notes: {config.special_requirements or 'none'}
- Extra instructions: {config.extra_instructions or 'none'}

{attractions}

Illustrative guide cost hints (per traveler per day, in {config.currency}):
{hints}

Generate the itinerary now. Return ONLY the JSON object.
"""
    return base.strip() + "\n\n" + user_block.strip()


def _format_intel_block(intel_context: str | None) -> str:
    """
    Format optional intelligence context (weather/feasibility/POI) for the modify prompt.
    Returns an empty string if no context provided.
    The block is clearly labelled as sourced data, not AI assumptions.
    """
    if not intel_context or not intel_context.strip():
        return ""
    return (
        "\nTravel intelligence context (sourced data — not AI assumptions):\n"
        + intel_context.strip()
        + "\n"
        "Use this context to inform suggestions (e.g. suggest indoor alternatives on "
        "rain days, reduce density if feasibility warnings exist). "
        "Do not invent facts beyond what is stated above.\n"
    )


def _build_modify_prompt(
    config: ItineraryConfig,
    current: ItineraryDocument,
    request: ItineraryModifyRequest,
) -> str:
    scope_desc = (
        f"only day {request.target_day} (day number {request.target_day})"
        if request.target_day
        else "the entire itinerary"
    )
    current_json = json.dumps(
        {
            "summary": current.summary,
            "daily_plans": [
                {
                    "day_number": d.day_number,
                    "date": d.date,
                    "title": d.title,
                    "activities": [
                        {
                            "id": a.id,
                            "time": a.time,
                            "name": a.name,
                            "category": a.category,
                            "estimated_cost_per_person": a.estimated_cost_per_person,
                            "is_optional": a.is_optional,
                        }
                        for a in d.activities
                    ],
                }
                for d in current.daily_plans
            ],
            "budget_breakdown": current.budget_breakdown.model_dump(),
        },
        default=str,
    )

    start = date.fromisoformat(config.start_date)
    end = date.fromisoformat(config.end_date)
    day_count = (end - start).days + 1

    return f"""You are a travel itinerary editor. Respond ONLY with a valid JSON object matching the same schema as below.

The user wants to modify {scope_desc}.
Modification request: "{request.modification_request}"

IMPORTANT:
- If modifying only day {request.target_day or 'N/A'}, keep all other days IDENTICAL to the input.
- Keep the total number of days at {day_count}.
- Preserve all activity IDs that remain unchanged.
- Recalculate the budget_breakdown accurately after changes.
- Return the COMPLETE itinerary (all days), not just the changed day.
- All monetary values in {config.currency}.
- grand_total = sum of all five budget categories.
- per_person_total = grand_total / {config.travelers}.
- budget_remaining = {config.budget} - grand_total.
- is_over_budget = grand_total > {config.budget}.
{_format_intel_block(request.intelligence_context)}
Current itinerary JSON:
{current_json}

Apply the modification and return the full updated JSON now."""


# ---------------------------------------------------------------------------
# LLM call + JSON extraction
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict[str, Any]:
    """Extract the first valid JSON object from LLM output."""
    # Strip markdown fences if present
    text = re.sub(r"```(?:json)?", "", text).strip()
    # Try to find a JSON object
    start = text.find("{")
    if start == -1:
        raise ValueError("LLM returned no JSON object.")
    # Scan for matching closing brace
    depth = 0
    end = start
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if depth != 0:
        raise ValueError("LLM JSON object is not properly closed.")
    return json.loads(text[start:end])


def _call_ollama(prompt: str) -> str:
    """Call ChatOllama and return the response text."""
    from langchain_ollama import ChatOllama
    from langchain_core.messages import HumanMessage

    llm = ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_HOST,
        temperature=0,
        num_ctx=4096,
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    if hasattr(response, "content"):
        return str(response.content)
    return str(response)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_dates(daily_plans: list[dict], config: ItineraryConfig) -> None:
    start = date.fromisoformat(config.start_date)
    end = date.fromisoformat(config.end_date)
    expected_count = (end - start).days + 1
    if len(daily_plans) != expected_count:
        raise ValueError(
            f"Expected {expected_count} day(s) but LLM returned {len(daily_plans)}."
        )


def _validate_budget(budget: dict, config: ItineraryConfig) -> None:
    keys = ("accommodation_total", "food_total", "transport_total",
            "activities_total", "miscellaneous_total", "grand_total")
    for k in keys:
        v = budget.get(k)
        if v is None or not math.isfinite(float(v)):
            budget[k] = 0.0
    # Recompute grand_total to ensure consistency
    computed = sum(
        float(budget.get(k, 0))
        for k in ("accommodation_total", "food_total", "transport_total",
                  "activities_total", "miscellaneous_total")
    )
    budget["grand_total"] = round(computed, 2)
    travelers = max(1, config.travelers)
    budget["per_person_total"] = round(computed / travelers, 2)
    budget["budget_remaining"] = round(config.budget - computed, 2)
    budget["is_over_budget"] = computed > config.budget
    budget["currency"] = config.currency
    if not budget.get("daily_average"):
        start = date.fromisoformat(config.start_date)
        end = date.fromisoformat(config.end_date)
        days = max((end - start).days + 1, 1)
        budget["daily_average"] = round(computed / days, 2)


def _ensure_activity_ids(daily_plans: list[dict]) -> None:
    seen: set[str] = set()
    for di, day in enumerate(daily_plans):
        for ai, act in enumerate(day.get("activities", [])):
            aid = str(act.get("id") or "")
            if not aid or aid in seen:
                aid = f"d{di+1}_a{ai+1}_{uuid.uuid4().hex[:6]}"
            act["id"] = aid
            seen.add(aid)


def _coerce_activity_costs(daily_plans: list[dict], currency: str) -> None:
    for day in daily_plans:
        for act in day.get("activities", []):
            try:
                act["estimated_cost_per_person"] = float(
                    act.get("estimated_cost_per_person") or 0
                )
            except (TypeError, ValueError):
                act["estimated_cost_per_person"] = 0.0
            act["currency"] = act.get("currency") or currency
            if "is_break" not in act:
                act["is_break"] = act.get("category") in ("food",) and act.get("duration_minutes", 60) <= 45
            if "is_optional" not in act:
                act["is_optional"] = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_itinerary(config: ItineraryConfig) -> ItineraryDocument:
    """
    Generate a full AI itinerary for the given trip configuration.

    Raises:
        RuntimeError: If Ollama is unavailable or the LLM returns unrecoverable output.
        ValueError: If the configuration is invalid.
    """
    currency = _normalize_currency(config.currency)
    guide_costs = _get_guide_costs(config.destination, config.budget_style, currency)

    prompt = _build_prompt(config, guide_costs)

    try:
        raw = _call_ollama(prompt)
    except Exception as exc:
        logger.exception("Ollama call failed during itinerary generation")
        raise RuntimeError(
            "Could not reach the AI model. Make sure Ollama is running and the model is pulled."
        ) from exc

    try:
        data = _extract_json(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "Itinerary LLM returned invalid JSON (error_type=%s raw_length=%d)",
            type(exc).__name__,
            len(raw),
        )
        raise RuntimeError(
            "The AI model returned a response that could not be parsed as an itinerary. "
            "Please try again."
        ) from exc

    daily_plans = data.get("daily_plans", [])
    budget = data.get("budget_breakdown", {})

    # Validate and repair
    try:
        _validate_dates(daily_plans, config)
    except ValueError as exc:
        # Attempt to recover by padding or trimming
        start = date.fromisoformat(config.start_date)
        end = date.fromisoformat(config.end_date)
        expected = (end - start).days + 1
        while len(daily_plans) < expected:
            offset = len(daily_plans)
            day_date = start + timedelta(days=offset)
            daily_plans.append({
                "day_number": offset + 1,
                "date": day_date.isoformat(),
                "title": f"Day {offset + 1}",
                "description": "Flexible day — activities to be confirmed.",
                "activities": [
                    {
                        "id": f"d{offset+1}_a1",
                        "time": "09:00",
                        "duration_minutes": 60,
                        "name": "Free exploration",
                        "description": "Explore at leisure.",
                        "location": config.destination,
                        "category": "leisure",
                        "estimated_cost_per_person": 0.0,
                        "currency": currency,
                        "notes": "AI did not generate a full plan for this day.",
                        "is_break": False,
                        "is_optional": True,
                    }
                ],
            })
        daily_plans = daily_plans[:expected]
        logger.warning("Itinerary day count mismatch recovered: %s", exc)

    _ensure_activity_ids(daily_plans)
    _coerce_activity_costs(daily_plans, currency)
    _validate_budget(budget, config)

    # Build the document
    try:
        doc = ItineraryDocument(
            id=str(uuid.uuid4()),
            config=config,
            summary=str(data.get("summary") or f"AI-generated itinerary for {config.destination}."),
            daily_plans=[ItineraryPlanDay(**d) for d in daily_plans],
            budget_breakdown=ItineraryBudget(**budget),
            is_ai_generated=True,
            generation_notes=(
                "Estimates are illustrative only. "
                "Costs are approximations based on guide data and AI suggestions. "
                "No bookings, live prices, or availability are implied."
            ),
        )
    except (ValidationError, TypeError) as exc:
        logger.exception("Itinerary Pydantic validation failed after LLM generation")
        raise RuntimeError(
            "The AI-generated itinerary failed validation. Please try again."
        ) from exc

    return doc


def modify_itinerary(
    config: ItineraryConfig,
    current: ItineraryDocument,
    request: ItineraryModifyRequest,
) -> ItineraryDocument:
    """
    Apply a natural-language modification to an existing itinerary.
    Returns the PREVIEW (not automatically saved).
    """
    prompt = _build_modify_prompt(config, current, request)

    try:
        raw = _call_ollama(prompt)
    except Exception as exc:
        logger.exception("Ollama call failed during itinerary modification")
        raise RuntimeError(
            "Could not reach the AI model. Make sure Ollama is running."
        ) from exc

    try:
        data = _extract_json(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "Modification LLM returned invalid JSON (error_type=%s)", type(exc).__name__
        )
        raise RuntimeError(
            "The AI model returned an unparseable response. The original itinerary is preserved."
        ) from exc

    daily_plans = data.get("daily_plans") or []
    budget = data.get("budget_breakdown") or {}

    # For day-specific modifications, replace only the targeted day
    if request.target_day and 1 <= request.target_day <= len(current.daily_plans):
        original_plans = [d.model_dump(mode="json") for d in current.daily_plans]
        updated_plan = next(
            (p for p in daily_plans if p.get("day_number") == request.target_day),
            None,
        )
        if updated_plan is not None:
            original_plans[request.target_day - 1] = updated_plan
        daily_plans = original_plans
        # Recompute budget from scratch
        budget = {}

    start = date.fromisoformat(config.start_date)
    end = date.fromisoformat(config.end_date)
    expected = (end - start).days + 1
    while len(daily_plans) < expected:
        offset = len(daily_plans)
        day_date = start + timedelta(days=offset)
        daily_plans.append({
            "day_number": offset + 1,
            "date": day_date.isoformat(),
            "title": f"Day {offset + 1}",
            "description": "Unchanged day.",
            "activities": [],
        })
    daily_plans = daily_plans[:expected]

    _ensure_activity_ids(daily_plans)
    _coerce_activity_costs(daily_plans, config.currency)
    _validate_budget(budget, config)

    try:
        doc = ItineraryDocument(
            id=current.id,
            config=config,
            summary=str(data.get("summary") or current.summary),
            daily_plans=[ItineraryPlanDay(**d) for d in daily_plans],
            budget_breakdown=ItineraryBudget(**budget),
            is_ai_generated=True,
            generation_notes=current.generation_notes,
        )
    except (ValidationError, TypeError) as exc:
        logger.exception("Modified itinerary Pydantic validation failed")
        raise RuntimeError(
            "The AI modification failed validation. The original itinerary is preserved."
        ) from exc

    return doc
