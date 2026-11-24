"""Unit tests for Phase 3 itinerary logic (no Ollama required)."""

import json
import pytest
from backend.itinerary import (
    _extract_json,
    _validate_dates,
    _validate_budget,
    _ensure_activity_ids,
    _coerce_activity_costs,
    _build_prompt,
)
from backend.models import (
    ItineraryConfig,
    ItineraryActivity,
    ItineraryPlanDay,
    ItineraryBudget,
    ItineraryDocument,
    ItineraryGenerateRequest,
    ItineraryModifyRequest,
)


@pytest.fixture
def goa_config():
    return ItineraryConfig(
        destination="Goa",
        start_date="2026-01-10",
        end_date="2026-01-12",
        travelers=2,
        budget=30000,
        currency="INR",
    )


# ── Schema validation ──────────────────────────────────────────────────────

def test_config_valid_dates(goa_config):
    assert goa_config.destination == "Goa"
    assert goa_config.travelers == 2


def test_config_rejects_end_before_start():
    with pytest.raises(Exception):
        ItineraryConfig(
            destination="X",
            start_date="2026-01-10",
            end_date="2026-01-09",
            travelers=1,
            budget=1000,
            currency="EUR",
        )


def test_config_rejects_trip_over_30_days():
    with pytest.raises(Exception):
        ItineraryConfig(
            destination="X",
            start_date="2026-01-01",
            end_date="2026-02-15",
            travelers=1,
            budget=1000,
            currency="EUR",
        )


def test_config_rejects_bad_currency():
    with pytest.raises(Exception):
        ItineraryConfig(
            destination="X",
            start_date="2026-01-01",
            end_date="2026-01-05",
            travelers=1,
            budget=1000,
            currency="XYZ",
        )


def test_itinerary_document_round_trip(goa_config):
    act = ItineraryActivity(
        id="d1_a1",
        time="09:00",
        duration_minutes=90,
        name="Calangute Beach",
        category="leisure",
        estimated_cost_per_person=0.0,
        currency="INR",
    )
    day = ItineraryPlanDay(
        day_number=1,
        date="2026-01-10",
        title="Arrival",
        activities=[act],
    )
    budget = ItineraryBudget(
        accommodation_total=5000,
        food_total=2000,
        transport_total=1000,
        activities_total=500,
        miscellaneous_total=500,
        grand_total=9000,
        per_person_total=4500,
        currency="INR",
        daily_average=3000,
        budget_remaining=21000,
        is_over_budget=False,
    )
    doc = ItineraryDocument(
        id="test-goa",
        config=goa_config,
        daily_plans=[day],
        budget_breakdown=budget,
    )
    raw = doc.model_dump_json()
    parsed = ItineraryDocument.model_validate_json(raw)
    assert parsed.id == "test-goa"
    assert parsed.daily_plans[0].activities[0].name == "Calangute Beach"
    assert parsed.budget_breakdown.grand_total == 9000.0


def test_modify_request_validation():
    req = ItineraryModifyRequest(
        modification_request="Make day 2 more relaxed.",
        target_day=2,
    )
    assert req.target_day == 2


def test_modify_request_rejects_blank():
    with pytest.raises(Exception):
        ItineraryModifyRequest(modification_request="   ")


# ── JSON extraction ────────────────────────────────────────────────────────

def test_extract_json_clean():
    obj = {"key": "value", "nested": {"a": 1}}
    raw = json.dumps(obj)
    result = _extract_json(raw)
    assert result["key"] == "value"
    assert result["nested"]["a"] == 1


def test_extract_json_strips_markdown_fences():
    obj = {"summary": "test trip"}
    raw = "```json\n" + json.dumps(obj) + "\n```"
    result = _extract_json(raw)
    assert result["summary"] == "test trip"


def test_extract_json_ignores_leading_prose():
    obj = {"daily_plans": [], "summary": "ok"}
    raw = "Here is your itinerary: " + json.dumps(obj) + " Hope that helps!"
    result = _extract_json(raw)
    assert result["summary"] == "ok"


def test_extract_json_raises_no_object():
    with pytest.raises(ValueError, match="no JSON"):
        _extract_json("No JSON here at all.")


# ── Date validation ────────────────────────────────────────────────────────

def test_validate_dates_correct_count(goa_config):
    # 3-day trip: Jan 10, 11, 12
    days = [
        {"day_number": i + 1, "date": f"2026-01-{10 + i:02d}", "title": "", "description": "", "activities": []}
        for i in range(3)
    ]
    _validate_dates(days, goa_config)  # should not raise


def test_validate_dates_wrong_count(goa_config):
    days = [{"day_number": 1, "date": "2026-01-10", "title": "", "description": "", "activities": []}]
    with pytest.raises(ValueError, match="Expected 3"):
        _validate_dates(days, goa_config)


# ── Budget validation & recalc ─────────────────────────────────────────────

def test_validate_budget_computes_grand_total(goa_config):
    budget = {
        "accommodation_total": 10000,
        "food_total": 5000,
        "transport_total": 3000,
        "activities_total": 4000,
        "miscellaneous_total": 1000,
    }
    _validate_budget(budget, goa_config)
    assert budget["grand_total"] == 23000
    assert budget["per_person_total"] == 11500
    assert budget["budget_remaining"] == 7000
    assert budget["is_over_budget"] is False
    assert budget["currency"] == "INR"


def test_validate_budget_flags_overspend():
    cfg = ItineraryConfig(
        destination="Paris",
        start_date="2026-05-01",
        end_date="2026-05-07",
        travelers=1,
        budget=500,
        currency="EUR",
    )
    budget = {
        "accommodation_total": 600,
        "food_total": 200,
        "transport_total": 100,
        "activities_total": 150,
        "miscellaneous_total": 50,
    }
    _validate_budget(budget, cfg)
    assert budget["is_over_budget"] is True
    assert budget["budget_remaining"] < 0


def test_validate_budget_handles_missing_keys(goa_config):
    budget = {}
    _validate_budget(budget, goa_config)
    assert budget["grand_total"] == 0.0
    assert budget["is_over_budget"] is False


# ── Activity ID dedup ──────────────────────────────────────────────────────

def test_ensure_activity_ids_fills_blanks():
    plans = [{
        "day_number": 1,
        "date": "2026-01-10",
        "title": "",
        "description": "",
        "activities": [
            {"id": "", "time": "09:00", "name": "A"},
            {"id": "", "time": "10:00", "name": "B"},
        ],
    }]
    _ensure_activity_ids(plans)
    ids = [a["id"] for a in plans[0]["activities"]]
    assert len(set(ids)) == 2
    assert all(ids)


def test_ensure_activity_ids_resolves_duplicates():
    plans = [{
        "day_number": 1,
        "date": "2026-01-10",
        "title": "",
        "description": "",
        "activities": [
            {"id": "same", "time": "09:00", "name": "A"},
            {"id": "same", "time": "10:00", "name": "B"},
        ],
    }]
    _ensure_activity_ids(plans)
    ids = [a["id"] for a in plans[0]["activities"]]
    assert len(set(ids)) == 2


# ── Cost coercion ──────────────────────────────────────────────────────────

def test_coerce_activity_costs_converts_strings():
    plans = [{
        "day_number": 1,
        "date": "2026-01-10",
        "title": "",
        "description": "",
        "activities": [{"id": "x", "estimated_cost_per_person": "25.5", "currency": None}],
    }]
    _coerce_activity_costs(plans, "INR")
    act = plans[0]["activities"][0]
    assert act["estimated_cost_per_person"] == 25.5
    assert act["currency"] == "INR"


def test_coerce_activity_costs_handles_none():
    plans = [{
        "day_number": 1,
        "date": "2026-01-10",
        "title": "",
        "description": "",
        "activities": [{"id": "x", "estimated_cost_per_person": None}],
    }]
    _coerce_activity_costs(plans, "USD")
    assert plans[0]["activities"][0]["estimated_cost_per_person"] == 0.0


# ── Prompt building ────────────────────────────────────────────────────────

def test_build_prompt_contains_key_fields(goa_config):
    prompt = _build_prompt(goa_config, {"hotel": 2500.0, "food": 900.0})
    assert "Goa" in prompt
    assert "INR" in prompt
    assert "3" in prompt          # day count
    assert "2026-01-10" in prompt
    assert "2026-01-12" in prompt
    assert "2" in prompt          # travelers


def test_build_prompt_includes_guide_hints(goa_config):
    prompt = _build_prompt(goa_config, {"hotel": 2500.0, "food": 900.0})
    assert "2500" in prompt
    assert "900" in prompt


def test_build_prompt_includes_attractions(goa_config):
    prompt = _build_prompt(goa_config, {})
    # Should include at least one known Goa attraction
    assert "Beach" in prompt or "Basilica" in prompt or "Fort" in prompt


# ── Persistence round-trip ─────────────────────────────────────────────────

def test_itinerary_persistence_methods_exist():
    from backend.persistence import TripStore
    store = TripStore()
    assert hasattr(store, "save_itinerary_for_owner")
    assert hasattr(store, "get_itinerary_for_owner")


def test_trip_itineraries_table_exists():
    import sqlite3
    from backend.config import DATABASE_PATH
    from backend.persistence import TripStore
    TripStore()  # ensure tables created
    conn = sqlite3.connect(str(DATABASE_PATH))
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    conn.close()
    assert "trip_itineraries" in tables


# ── API endpoint registration ──────────────────────────────────────────────

def test_itinerary_routes_registered():
    from backend.main import app
    routes = [getattr(r, 'path', None) for r in app.routes]
    assert "/api/itinerary/generate" in routes
    assert "/api/trips/{trip_id}/itinerary" in routes
    assert "/api/trips/{trip_id}/itinerary/modify" in routes
