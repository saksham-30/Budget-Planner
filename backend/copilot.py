"""
AI Travel Copilot — Phase 7.

Conversational assistant scoped to a single trip/itinerary.
Reuses ChatOllama (same model and host as itinerary.py) for multi-turn chat.
No new LLM provider. No duplicate LangGraph graph.

Design:
  - Context is assembled deterministically from itinerary, weather, feasibility,
    and destination data already available in the app.
  - Each turn is a ChatOllama.invoke() call with the full message history.
  - Conversation is persisted per trip in the copilot_conversations table.
  - The copilot NEVER modifies the itinerary directly.
    If a modification is requested, it returns a structured signal that
    tells the frontend to trigger the existing modify endpoint.
  - All data claims reference sourced context (FORECAST, CLIMATOLOGY, STATIC,
    ESTIMATED) — the copilot is told explicitly what data it has and doesn't have.

Data labels passed to the model:
  FORECAST    — Open-Meteo weather forecast data
  CLIMATOLOGY — Monthly climate averages (not a forecast)
  STATIC      — Curated transport / logistics guidance
  ESTIMATED   — Illustrative cost guide data
  UNAVAILABLE — Data that could not be retrieved
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_TEMPLATE = """You are a helpful AI travel copilot for a trip to {destination}.
You assist the user with questions about their itinerary, budget, weather, activities, and local transport.

Trip details:
- Destination: {destination}
- Dates: {start_date} to {end_date} ({day_count} days)
- Travelers: {travelers}
- Budget: {budget} {currency}
- Travel pace: {pace}
- Interests: {interests}

{itinerary_summary}

{weather_context}

{feasibility_context}

{logistics_context}

Rules you must follow:
1. Ground all answers in the trip context provided above. Do not invent fares, hotel prices, flight times, weather readings, or booking availability.
2. When asked about weather, use only the data labelled FORECAST or CLIMATOLOGY above. If that section is absent, say you do not have weather data for those dates.
3. When suggesting modifications (e.g. "make Day 2 more relaxed", "reduce cost"), describe the change clearly but always end with: "Would you like me to apply this change? I can preview it for you first."
4. Never claim to have booked or confirmed anything.
5. If the user asks about something outside the trip context (e.g. live stock prices, medical advice), politely decline and redirect to travel topics.
6. Keep answers concise and practical. Use bullet points for lists.
7. If the user asks for a modification, describe what you would change and ask for confirmation before anything happens. Do NOT output JSON.
"""

_SYSTEM_PROMPT_NO_ITIN = """You are a helpful AI travel copilot for a trip to {destination}.
No itinerary has been generated yet for this trip.

Trip details:
- Destination: {destination}
- Dates: {start_date} to {end_date} ({day_count} days)
- Travelers: {travelers}
- Budget: {budget} {currency}
- Travel pace: {pace}
- Interests: {interests}

{weather_context}

{feasibility_context}

{logistics_context}

You can answer general questions about {destination}, suggest activities, or help the user understand what to expect.
Once an itinerary is generated you will have more context to work with.

Rules:
1. Do not invent specific prices, hotel names, or availability.
2. Keep answers concise and practical.
3. Base answers on well-known public information about {destination}.
4. Never claim to have booked or confirmed anything.
"""


def _build_itinerary_summary(itinerary: dict | None) -> str:
    if not itinerary:
        return ""
    daily = itinerary.get("daily_plans", [])
    budget = itinerary.get("budget_breakdown", {})

    # Only return empty string if there's truly nothing to show
    if not daily and not budget:
        return ""

    lines = ["Current itinerary overview (ESTIMATED costs):"]
    for day in daily:
        acts = day.get("activities", [])
        act_names = [a.get("name", "") for a in acts if not a.get("is_break")]
        if act_names:
            lines.append(f"  Day {day.get('day_number')}: {', '.join(act_names[:4])}")

    if budget:
        total = budget.get("grand_total")
        remaining = budget.get("budget_remaining")
        currency = budget.get("currency", "")
        if total is not None:
            lines.append(f"  Estimated total: {total} {currency} (remaining: {remaining} {currency})")
        over = budget.get("is_over_budget")
        if over:
            lines.append("  ⚠ Current estimate exceeds budget.")
    return "\n".join(lines)


def _build_weather_context(weather: dict | None) -> str:
    if not weather or not weather.get("days"):
        return "Weather data: UNAVAILABLE for these dates."
    source = weather.get("source", "UNAVAILABLE")
    days = weather["days"][:7]  # cap to avoid huge prompts
    lines = [f"Weather ({source} — {'actual forecast' if source == 'FORECAST' else 'climate averages, NOT a forecast'}):"]
    for d in days:
        label = d.get("weather_label", "")
        tmin = d.get("temp_min")
        tmax = d.get("temp_max")
        prob = d.get("precipitation_probability")
        rain = " ⛈ Rain likely" if d.get("is_rain_likely") else ""
        temp = f" {tmin}–{tmax}°C" if tmin is not None and tmax is not None else ""
        prob_str = f" ({prob}% rain)" if prob is not None else ""
        lines.append(f"  {d['date']}: {label}{temp}{prob_str}{rain}")
    if weather.get("notes"):
        lines.append(f"  Note: {weather['notes']}")
    return "\n".join(lines)


def _build_feasibility_context(feasibility: dict | None) -> str:
    if not feasibility:
        return ""
    issues = feasibility.get("issues", [])
    if not issues:
        return "Schedule analysis: No issues detected."
    lines = ["Schedule issues detected:"]
    for issue in issues[:8]:  # cap to avoid prompt bloat
        sev = issue.get("severity", "INFO")
        msg = issue.get("message", "")
        sug = issue.get("suggestion", "")
        lines.append(f"  [{sev}] {msg}")
        if sug:
            lines.append(f"    Suggestion: {sug}")
    return "\n".join(lines)


def _build_logistics_context(logistics: dict | None) -> str:
    if not logistics:
        return ""
    t = logistics.get("transport", {})
    lines = ["Local transport (STATIC — illustrative, not live):"]
    if t.get("airport_transfer"):
        lines.append(f"  Airport transfer: {t['airport_transfer']}")
    if t.get("local_transport"):
        lines.append(f"  Local transport: {t['local_transport']}")
    return "\n".join(lines)


def build_system_prompt(
    destination: str,
    start_date: str,
    end_date: str,
    travelers: int,
    budget: float,
    currency: str,
    pace: str,
    interests: list[str],
    itinerary: dict | None = None,
    weather: dict | None = None,
    feasibility: dict | None = None,
    logistics: dict | None = None,
) -> str:
    """Build the system prompt for the copilot from available context."""
    from datetime import date
    try:
        days = (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days + 1
    except ValueError:
        days = "unknown number of"

    itin_summary = _build_itinerary_summary(itinerary)
    weather_ctx = _build_weather_context(weather)
    feasibility_ctx = _build_feasibility_context(feasibility)
    logistics_ctx = _build_logistics_context(logistics)

    template = _SYSTEM_PROMPT_TEMPLATE if itinerary else _SYSTEM_PROMPT_NO_ITIN
    return template.format(
        destination=destination,
        start_date=start_date,
        end_date=end_date,
        day_count=days,
        travelers=travelers,
        budget=budget,
        currency=currency,
        pace=pace,
        interests=", ".join(interests) if interests else "general sightseeing",
        itinerary_summary=itin_summary,
        weather_context=weather_ctx,
        feasibility_context=feasibility_ctx,
        logistics_context=logistics_ctx,
    )


def chat(
    system_prompt: str,
    history: list[dict[str, str]],
    user_message: str,
) -> str:
    """
    Send a message to the copilot and return the response text.

    history: list of {"role": "user"|"assistant", "content": "..."} dicts
    Returns the assistant reply as a plain string.
    Raises RuntimeError if Ollama is unavailable.
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
    from langchain_ollama import ChatOllama
    from .config import OLLAMA_HOST, OLLAMA_MODEL

    llm = ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_HOST,
        temperature=0.3,   # slightly warmer than generation for conversational tone
        num_ctx=4096,
    )

    messages = [SystemMessage(content=system_prompt)]
    for turn in history:
        role = turn.get("role", "")
        content = turn.get("content", "")
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))

    messages.append(HumanMessage(content=user_message))

    try:
        response = llm.invoke(messages)
        text = response.content if hasattr(response, "content") else str(response)
        return str(text).strip()
    except Exception as exc:
        logger.exception("Copilot Ollama call failed")
        raise RuntimeError(
            "Could not reach the AI model. Make sure Ollama is running."
        ) from exc


def _detect_modification_intent(text: str) -> bool:
    """
    Return True if the assistant response implies a modification suggestion.
    Used by the frontend to decide whether to show the 'Apply change' button.
    """
    lower = text.lower()
    signals = [
        "would you like me to apply",
        "preview it for you",
        "i can make that change",
        "shall i update",
        "want me to modify",
        "should i change",
        "i'll update day",
        "i would suggest changing",
    ]
    return any(s in lower for s in signals)
