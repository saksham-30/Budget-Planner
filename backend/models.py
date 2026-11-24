"""
Pydantic request/response schemas for the Travel Budget Planner API.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class AnalyzeRequest(BaseModel):
    query: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="Natural-language travel-budget question from the user.",
        examples=["I have a budget of ₹100000 and my estimated trip cost is €900. Can I afford it?"],
    )

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value


class TripPreferences(BaseModel):
    destination: str = Field(min_length=2, max_length=100)
    start_date: date
    end_date: date
    travelers: int = Field(ge=1, le=20)
    budget: float = Field(gt=0, le=1_000_000_000)
    currency: str = Field(min_length=3, max_length=3)
    budget_style: Literal["budget", "moderate", "luxury"] = "moderate"
    preferred_accommodation_type: Literal["hotel", "hostel", "rental"] = "hotel"
    food_preferences: list[str] = Field(default_factory=list, max_length=20)
    interests: list[str] = Field(default_factory=list, max_length=30)
    hard_constraints: list[
        Literal["keep_accommodation", "no_activity_reduction"]
    ] = Field(default_factory=list, max_length=20)

    @field_validator("destination")
    @classmethod
    def normalize_destination(cls, value: str) -> str:
        return value.strip()

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        aliases = {"₹": "INR", "€": "EUR", "$": "USD", "£": "GBP"}
        normalized = aliases.get(value.strip(), normalized)
        if normalized not in {"INR", "EUR", "USD", "GBP"}:
            raise ValueError("currency must be one of INR, EUR, USD, or GBP")
        return normalized

    @field_validator("food_preferences", "interests", "hard_constraints")
    @classmethod
    def normalize_list_values(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]

    @field_validator("budget")
    @classmethod
    def validate_finite_budget(cls, value: float) -> float:
        import math

        if not math.isfinite(value):
            raise ValueError("budget must be a finite amount")
        return value

    @model_validator(mode="after")
    def validate_dates(self) -> "TripPreferences":
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self


class TripPlanRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value


class TripSummary(BaseModel):
    trip_id: str
    destination: str
    budget: float
    currency: str
    travelers: int
    updated_at: str


class CostEvidence(BaseModel):
    destination: str
    currency: str
    section: str
    source_file: str
    estimates: dict[str, dict[str, float]]
    assumptions: list[str]
    is_generic: bool = False


class Bid(BaseModel):
    bid_id: str
    agent_name: Literal["baseline", "budget_agent", "itinerary_agent"]
    proposed_changes: list[str]
    itemized_costs: dict[str, float]
    original_itemized_costs: dict[str, float] = Field(default_factory=dict)
    original_currency: str
    comparison_currency: str
    exchange_rates: dict[str, float] = Field(default_factory=dict)
    baseline_cost: float = Field(ge=0)
    proposed_total_cost: float = Field(ge=0)
    savings_amount: float
    savings_percentage: float
    budget_limit: float = Field(ge=0)
    feasible: bool
    constraint_violations: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    rationale: str
    decision_reason: str = ""

    @field_validator("itemized_costs")
    @classmethod
    def validate_cost_items(cls, values: dict[str, float]) -> dict[str, float]:
        import math

        if not values or any(not math.isfinite(v) or v < 0 for v in values.values()):
            raise ValueError("itemized costs must be non-empty finite non-negative amounts")
        return values

    @field_validator("exchange_rates")
    @classmethod
    def validate_exchange_rates(cls, values: dict[str, float]) -> dict[str, float]:
        import math

        if any(not math.isfinite(value) or value <= 0 for value in values.values()):
            raise ValueError("exchange rates must be finite positive amounts")
        return values

    @model_validator(mode="after")
    def validate_totals(self) -> "Bid":
        import math

        if not math.isfinite(self.proposed_total_cost) or not math.isfinite(self.baseline_cost):
            raise ValueError("bid totals must be finite")
        if round(sum(self.itemized_costs.values()), 2) != round(self.proposed_total_cost, 2):
            raise ValueError("itemized costs must sum to proposed_total_cost")
        return self


class ItineraryDay(BaseModel):
    day: int
    date: date
    title: str = ""
    plan: str
    highlights: list[str] = Field(default_factory=list)
    estimated_activity_cost: float = Field(ge=0)
    currency: str


class TripPlanResponse(BaseModel):
    trip_id: str
    preferences: TripPreferences
    conversation: list[dict[str, str]]
    evidence: list[CostEvidence]
    bids: list[Bid]
    selected_bid: Optional[Bid] = None
    rejected_bids: list[Bid] = Field(default_factory=list)
    answer: str
    feasibility: Literal["feasible", "infeasible", "unavailable"]
    budget_gap: Optional[float] = None
    rationale: str
    trace: list[dict[str, str]]
    comparison: dict[str, Any] = Field(default_factory=dict)
    itinerary: list[ItineraryDay] = Field(default_factory=list)
    prior_decisions: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------


class TraceEvent(BaseModel):
    step: int
    type: str  # user_request | agent_decision | tool_call | tool_result | final_answer
    title: str
    description: str
    tool: Optional[str] = None
    arguments: Optional[dict[str, Any]] = None
    result: Optional[str] = None
    timestamp: str  # ISO 8601
    status: str  # complete | error


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class AnalyzeResponse(BaseModel):
    answer: str
    feasible: Optional[bool] = None  # None if the model couldn't determine
    total_cost: Optional[float] = None
    budget: Optional[float] = None
    remaining: Optional[float] = None
    overage: Optional[float] = None
    currency: Optional[str] = None
    trace: list[TraceEvent] = Field(default_factory=list)


class HealthResponse(BaseModel):
    ollama_reachable: bool
    model_available: bool
    model: str
    message: str


class RateInfo(BaseModel):
    from_currency: str
    to_currency: str
    rate: float


class RatesResponse(BaseModel):
    base: str
    pairs: list[RateInfo]
    source: str
    label: str
    as_of: Optional[str] = None
    fetched_at: Optional[str] = None
    is_live: bool
    stale: bool
    note: str


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None


# ---------------------------------------------------------------------------
# Itinerary Builder — Phase 3
# ---------------------------------------------------------------------------


class ItineraryConfig(BaseModel):
    """User-supplied trip configuration for the AI itinerary builder."""
    destination: str = Field(min_length=2, max_length=120)
    origin: Optional[str] = Field(default=None, max_length=120)
    start_date: str  # ISO date string
    end_date: str    # ISO date string
    travelers: int = Field(ge=1, le=20)
    budget: float = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    pace: str = Field(default="balanced")           # relaxed | balanced | packed
    budget_style: str = Field(default="moderate")   # budget | moderate | luxury
    accommodation_preference: str = Field(default="hotel")
    interests: list[str] = Field(default_factory=list)
    special_requirements: Optional[str] = Field(default=None, max_length=500)
    extra_instructions: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        from datetime import date as _date
        try:
            _date.fromisoformat(v)
        except ValueError as exc:
            raise ValueError(f"Invalid date format: {v}") from exc
        return v

    @field_validator("pace")
    @classmethod
    def validate_pace(cls, v: str) -> str:
        if v not in ("relaxed", "balanced", "packed"):
            return "balanced"
        return v

    @field_validator("budget_style")
    @classmethod
    def validate_budget_style(cls, v: str) -> str:
        if v not in ("budget", "moderate", "luxury"):
            return "moderate"
        return v

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        normalized = v.strip().upper()
        if normalized not in {"INR", "EUR", "USD", "GBP"}:
            raise ValueError("currency must be one of INR, EUR, USD, GBP")
        return normalized

    @model_validator(mode="after")
    def validate_date_range(self) -> "ItineraryConfig":
        from datetime import date as _date
        s = _date.fromisoformat(self.start_date)
        e = _date.fromisoformat(self.end_date)
        if e < s:
            raise ValueError("end_date must be on or after start_date")
        if (e - s).days > 30:
            raise ValueError("Trip duration cannot exceed 30 days")
        return self


class ItineraryActivity(BaseModel):
    """A single activity in the daily itinerary."""
    id: str
    time: str = "09:00"           # HH:MM
    duration_minutes: int = Field(default=60, ge=5, le=720)
    name: str
    description: str = ""
    location: str = ""
    category: str = "sightseeing"
    estimated_cost_per_person: float = Field(default=0.0, ge=0)
    currency: str = "INR"
    notes: str = ""
    is_break: bool = False
    is_optional: bool = False
    is_edited: bool = False        # set to True when user manually edits


class ItineraryPlanDay(BaseModel):
    """One day in the AI-generated itinerary (Phase 3 builder)."""
    day_number: int = Field(ge=1)
    date: str                      # ISO date string
    title: str = ""
    description: str = ""
    activities: list[ItineraryActivity] = Field(default_factory=list)


class ItineraryBudget(BaseModel):
    """Budget breakdown for the full trip."""
    accommodation_total: float = Field(default=0.0, ge=0)
    food_total: float = Field(default=0.0, ge=0)
    transport_total: float = Field(default=0.0, ge=0)
    activities_total: float = Field(default=0.0, ge=0)
    miscellaneous_total: float = Field(default=0.0, ge=0)
    grand_total: float = Field(default=0.0, ge=0)
    per_person_total: float = Field(default=0.0, ge=0)
    currency: str = "INR"
    daily_average: float = Field(default=0.0, ge=0)
    budget_remaining: float = 0.0  # can be negative
    is_over_budget: bool = False
    notes: str = ""


class ItineraryDocument(BaseModel):
    """Complete itinerary produced by the AI builder."""
    id: str
    config: ItineraryConfig
    summary: str = ""
    daily_plans: list[ItineraryPlanDay] = Field(default_factory=list)
    budget_breakdown: ItineraryBudget = Field(default_factory=ItineraryBudget)
    is_ai_generated: bool = True
    generation_notes: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ItineraryGenerateRequest(BaseModel):
    """Request body for POST /api/itinerary/generate."""
    config: ItineraryConfig


class ItineraryModifyRequest(BaseModel):
    """Request body for POST /api/trips/{trip_id}/itinerary/modify."""
    modification_request: str = Field(min_length=1, max_length=2000)
    target_day: Optional[int] = Field(default=None, ge=1)
    # Optional: pass the current in-memory itinerary so modification works
    # before the first save.  If omitted the endpoint loads from the database.
    current_itinerary: Optional["ItineraryDocument"] = None
    # Optional: weather, feasibility and POI context to include in the AI prompt.
    # Keeps the prompt grounded in real data rather than AI assumptions.
    intelligence_context: Optional[str] = Field(default=None, max_length=4000)

    @field_validator("modification_request")
    @classmethod
    def validate_request_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("modification_request must not be blank")
        return v


class ItinerarySaveRequest(BaseModel):
    """Request body for PUT /api/trips/{trip_id}/itinerary."""
    itinerary: ItineraryDocument


# ---------------------------------------------------------------------------
# Phase 5 — Weather / POI / Feasibility
# ---------------------------------------------------------------------------


class DayWeatherOut(BaseModel):
    """Serialisable version of weather.DayWeather."""
    date: str
    temp_min: Optional[float] = None
    temp_max: Optional[float] = None
    precipitation_mm: Optional[float] = None
    precipitation_probability: Optional[int] = None
    weather_code: Optional[int] = None
    weather_label: str = ""
    is_rain_likely: bool = False
    wind_speed_max: Optional[float] = None
    source: str = "UNAVAILABLE"
    is_suitable_outdoor: bool = True


class WeatherResponseOut(BaseModel):
    destination: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    timezone: str = "UTC"
    days: list[DayWeatherOut] = Field(default_factory=list)
    source: str = "UNAVAILABLE"
    fetched_at: Optional[str] = None
    forecast_generated_at: Optional[str] = None
    attribution: str = ""
    notes: str = ""


class PoiResultOut(BaseModel):
    name: str
    category: str = ""
    osm_type: str = "node"
    osm_id: int = 0
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    source: str = "OSM"
    wikipedia: Optional[str] = None
    website: Optional[str] = None
    description: Optional[str] = None


class PoiResponseOut(BaseModel):
    destination: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    pois: list[PoiResultOut] = Field(default_factory=list)
    source: str = "UNAVAILABLE"
    attribution: str = ""
    fetched_at: Optional[str] = None
    notes: str = ""


class FeasibilityIssueOut(BaseModel):
    severity: str              # INFO | WARNING | CRITICAL
    day_number: Optional[int] = None
    activity_id: Optional[str] = None
    code: str = ""
    message: str = ""
    suggestion: str = ""


class FeasibilityResponseOut(BaseModel):
    issues: list[FeasibilityIssueOut] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)
    analysed_days: int = 0


class FeasibilityRequest(BaseModel):
    """Request body for POST /api/itinerary/feasibility."""
    itinerary: ItineraryDocument


# ---------------------------------------------------------------------------
# Phase 6 — Logistics
# ---------------------------------------------------------------------------


class FlightLinksOut(BaseModel):
    google_flights: str
    skyscanner: str
    rome2rio: str
    origin: str
    destination: str
    depart_date: str
    return_date: Optional[str] = None
    adults: int
    airport_code: Optional[str] = None
    notes: str = ""


class AccommodationLinksOut(BaseModel):
    booking_com: str
    airbnb: str
    hostelworld: Optional[str] = None
    destination: str
    checkin: str
    checkout: str
    adults: int
    budget_style: str = "moderate"
    notes: str = ""


class TransportGuidanceOut(BaseModel):
    destination: str
    airport_transfer: str = ""
    local_transport: str = ""
    public_transport: str = ""
    rome2rio_url: str = ""
    notes: str = ""
    source: str = "STATIC"


class LogisticsResponseOut(BaseModel):
    destination: str
    flights: FlightLinksOut
    accommodation: AccommodationLinksOut
    transport: TransportGuidanceOut
    disclaimer: str = ""


# ---------------------------------------------------------------------------
# Phase 7 — Copilot
# ---------------------------------------------------------------------------


class CopilotTurn(BaseModel):
    role: str                    # "user" | "assistant"
    content: str


class CopilotRequest(BaseModel):
    """Request body for POST /api/trips/{trip_id}/copilot."""
    message: str = Field(min_length=1, max_length=2000)
    # Optional context snapshots — if supplied, used to build the system prompt.
    # If omitted, the backend falls back to what's stored (preferences + itinerary).
    weather_context: Optional[dict[str, Any]] = None
    feasibility_context: Optional[dict[str, Any]] = None
    logistics_context: Optional[dict[str, Any]] = None

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message must not be blank")
        return v


class CopilotResponse(BaseModel):
    reply: str
    suggests_modification: bool = False
    history: list[CopilotTurn] = Field(default_factory=list)
