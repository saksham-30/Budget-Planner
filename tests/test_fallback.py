"""Deterministic guardrail tests — no Ollama or model required."""

from backend.fallback import (
    build_deterministic_result,
    parse_query,
    reconcile_agent_result,
)
from backend.tracing import build_trace


def _structured(**overrides):
    values = {
        "feasible": None,
        "total_cost": None,
        "budget": None,
        "currency": None,
        "remaining": None,
        "overage": None,
    }
    values.update(overrides)
    return values


def test_parse_extracts_explicit_cost_and_budget():
    parsed = parse_query(
        "I have ₹100,000 and my estimated trip cost is €900. Can I afford it?"
    )

    assert parsed.budget == 100000
    assert parsed.budget_currency == "INR"
    assert parsed.cost == 900
    assert parsed.cost_currency == "EUR"
    assert parsed.destination is None


def test_parse_destination_query_uses_defaults_and_style_is_not_the_budget_word():
    parsed = parse_query(
        "I have a budget of ₹30,000 and want to visit Manali. Is my trip feasible?"
    )

    assert parsed.budget == 30000
    assert parsed.destination == "Manali"
    assert parsed.days is None
    assert parsed.travelers is None
    assert parsed.style == "moderate"

    deterministic = build_deterministic_result(parsed, "")
    assert deterministic is not None
    assert deterministic["total_cost"] == 61600
    assert deterministic["feasible"] is False
    assert deterministic["overage"] == 31600


def test_parse_respects_days_travelers_and_luxury_style():
    query = "We have 40000 rupees for a 7 day luxury trip to Goa for 3 travelers"
    parsed = parse_query(query)

    assert parsed.days == 7
    assert parsed.travelers == 3
    assert parsed.style == "luxury"

    deterministic = build_deterministic_result(parsed, query)
    assert deterministic["budget"] == 40000
    assert deterministic["total_cost"] > 40000


def test_reconcile_keeps_consistent_model_output():
    query = "I have ₹100,000 and my estimated trip cost is €900. Can I afford it?"
    structured = _structured(
        feasible=True,
        total_cost=85500,
        budget=100000,
        currency="INR",
        remaining=14500,
    )
    trace = build_trace([])

    outcome = reconcile_agent_result(query, "Yes, you can afford it.", structured, trace)

    assert outcome.applied is False
    assert outcome.answer == "Yes, you can afford it."
    assert outcome.structured["total_cost"] == 85500


def test_reconcile_replaces_budget_used_as_trip_cost():
    query = "I have a budget of ₹30,000 and want to visit Manali. Is my trip feasible?"
    structured = _structured(
        feasible=True,
        total_cost=30000,
        budget=30000,
        currency="INR",
        remaining=0,
    )

    outcome = reconcile_agent_result(query, "Your trip is feasible.", structured, [])

    assert outcome.applied is True
    assert outcome.reason == "cost mismatch"
    assert outcome.structured["feasible"] is False
    assert outcome.structured["total_cost"] == 61600
    assert outcome.structured["overage"] == 31600
    assert "not feasible" in outcome.answer
    assert "recalculated" in outcome.answer


def test_reconcile_replaces_missing_tool_output():
    query = "budget 50000 INR and trip cost 900 EUR"
    structured = _structured()

    outcome = reconcile_agent_result(query, "I think it is fine.", structured, [])

    assert outcome.applied is True
    assert outcome.reason == "missing feasibility"
    assert outcome.structured == {
        "feasible": False,
        "total_cost": 85500,
        "budget": 50000,
        "remaining": None,
        "overage": 35500,
        "currency": "INR",
    }


def test_reconcile_keeps_model_output_when_query_cannot_be_recomputed():
    query = "Can I afford this trip?"
    structured = _structured(feasible=True, total_cost=80000, budget=100000, currency="INR")

    outcome = reconcile_agent_result(query, "Yes.", structured, [])

    assert outcome.applied is False
    assert outcome.answer == "Yes."


def test_reconcile_replaces_incorrect_explicit_cost():
    query = "My budget is 50000 INR and the trip cost is 900 EUR."
    structured = _structured(
        feasible=True,
        total_cost=50000,
        budget=50000,
        currency="INR",
        remaining=0,
    )

    outcome = reconcile_agent_result(query, "Feasible.", structured, [])

    assert outcome.applied is True
    assert outcome.structured["total_cost"] == 85500
    assert outcome.structured["overage"] == 35500


def test_fallback_trace_mirrors_real_tool_calls():
    query = "budget 50000 INR and trip cost 900 EUR"
    outcome = reconcile_agent_result(query, "Feasible.", _structured(), [])

    types = [event.type for event in outcome.trace]
    tools = [event.tool for event in outcome.trace if event.type == "tool_result"]

    assert types[0] == "user_request"
    assert types[-1] == "final_answer"
    assert [event.step for event in outcome.trace] == list(
        range(1, len(outcome.trace) + 1)
    )
    assert tools == ["convert_currency", "check_budget_feasibility"]
    assert "Over budget by 35500" in outcome.trace[-2].result
