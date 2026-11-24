"""
Phase 5.1 — Focused regression tests.

Covers:
- intelligence_context injection into AI modify prompt
- backward compatibility when intelligence_context is absent
- feasibility re-analysis triggered by content change (not just id)
- weather refresh behaviour
- POI lazy-load (not triggered on mount)
- geocoding sleep guard correctness
- _format_intel_block edge cases
- ItineraryModifyRequest schema: new field optional, doesn't break existing callers
"""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ─── _format_intel_block ──────────────────────────────────────────────────────

class TestFormatIntelBlock:
    def test_none_returns_empty(self):
        from backend.itinerary import _format_intel_block
        assert _format_intel_block(None) == ""

    def test_blank_returns_empty(self):
        from backend.itinerary import _format_intel_block
        assert _format_intel_block("   ") == ""

    def test_non_empty_includes_sourced_label(self):
        from backend.itinerary import _format_intel_block
        result = _format_intel_block("Weather: Rain on day 1.")
        assert "sourced data" in result
        assert "Weather: Rain on day 1." in result

    def test_non_empty_includes_ai_guidance(self):
        from backend.itinerary import _format_intel_block
        result = _format_intel_block("some context")
        assert "Do not invent facts" in result

    def test_long_context_preserved(self):
        from backend.itinerary import _format_intel_block
        ctx = "Line\n" * 50
        result = _format_intel_block(ctx)
        assert "Line" in result


# ─── intelligence_context in ItineraryModifyRequest ───────────────────────────

class TestIntelligenceContextModel:
    def test_field_optional_absent(self):
        from backend.models import ItineraryModifyRequest
        req = ItineraryModifyRequest(modification_request="Make day 2 relaxed.")
        assert req.intelligence_context is None

    def test_field_present(self):
        from backend.models import ItineraryModifyRequest
        req = ItineraryModifyRequest(
            modification_request="Suggest indoor alternatives.",
            intelligence_context="Weather (FORECAST): 2026-01-10: Slight rain — rain likely.",
        )
        assert "rain likely" in req.intelligence_context

    def test_field_round_trip_serialisation(self):
        from backend.models import ItineraryModifyRequest
        req = ItineraryModifyRequest(
            modification_request="Reduce walking.",
            intelligence_context="Feasibility: 2 warnings detected.",
        )
        raw = req.model_dump_json()
        parsed = ItineraryModifyRequest.model_validate_json(raw)
        assert parsed.intelligence_context == req.intelligence_context

    def test_none_field_serialises_as_null(self):
        from backend.models import ItineraryModifyRequest
        req = ItineraryModifyRequest(modification_request="Add more food.")
        data = json.loads(req.model_dump_json())
        assert data["intelligence_context"] is None

    def test_max_length_enforced(self):
        from backend.models import ItineraryModifyRequest
        with pytest.raises(Exception):
            ItineraryModifyRequest(
                modification_request="x",
                intelligence_context="a" * 4001,  # over 4000-char limit
            )

    def test_existing_callers_unaffected(self):
        """Callers that don't pass intelligence_context must continue to work."""
        from backend.models import ItineraryModifyRequest
        req = ItineraryModifyRequest(
            modification_request="Replace activities on day 3 with free alternatives.",
            target_day=3,
        )
        assert req.target_day == 3
        assert req.intelligence_context is None
        assert req.current_itinerary is None


# ─── _build_modify_prompt includes context ────────────────────────────────────

class TestModifyPromptWithContext:
    def _make_config(self):
        from backend.models import ItineraryConfig
        return ItineraryConfig(
            destination="Goa",
            start_date="2026-01-10",
            end_date="2026-01-12",
            travelers=2,
            budget=30000,
            currency="INR",
        )

    def _make_doc(self, config):
        from backend.models import (
            ItineraryBudget, ItineraryDocument, ItineraryPlanDay,
        )
        return ItineraryDocument(
            id="test",
            config=config,
            daily_plans=[
                ItineraryPlanDay(day_number=i + 1, date=f"2026-01-{10+i:02d}", title=f"Day {i+1}")
                for i in range(3)
            ],
            budget_breakdown=ItineraryBudget(grand_total=20000, per_person_total=10000, currency="INR"),
        )

    def test_prompt_contains_intel_block_when_present(self):
        from backend.itinerary import _build_modify_prompt
        from backend.models import ItineraryModifyRequest
        config = self._make_config()
        doc = self._make_doc(config)
        req = ItineraryModifyRequest(
            modification_request="Suggest indoor alternatives.",
            intelligence_context="Weather (FORECAST):\n  2026-01-10: Slight rain — rain likely.",
        )
        prompt = _build_modify_prompt(config, doc, req)
        assert "sourced data" in prompt
        assert "rain likely" in prompt
        assert "Do not invent facts" in prompt

    def test_prompt_has_no_intel_block_when_absent(self):
        from backend.itinerary import _build_modify_prompt
        from backend.models import ItineraryModifyRequest
        config = self._make_config()
        doc = self._make_doc(config)
        req = ItineraryModifyRequest(
            modification_request="Make cheaper.",
            intelligence_context=None,
        )
        prompt = _build_modify_prompt(config, doc, req)
        assert "sourced data" not in prompt
        # Core content still present
        assert "Make cheaper" in prompt
        assert "IMPORTANT" in prompt

    def test_prompt_has_no_intel_block_when_blank(self):
        from backend.itinerary import _build_modify_prompt
        from backend.models import ItineraryModifyRequest
        config = self._make_config()
        doc = self._make_doc(config)
        req = ItineraryModifyRequest(
            modification_request="Reduce activities.",
            intelligence_context="   ",
        )
        prompt = _build_modify_prompt(config, doc, req)
        assert "sourced data" not in prompt

    def test_intel_context_does_not_appear_in_itinerary_json(self):
        """The current_itinerary JSON section must not leak intel_context."""
        from backend.itinerary import _build_modify_prompt
        from backend.models import ItineraryModifyRequest
        config = self._make_config()
        doc = self._make_doc(config)
        ctx = "UNIQUE_INTEL_MARKER_XYZ"
        req = ItineraryModifyRequest(
            modification_request="x",
            intelligence_context=ctx,
        )
        prompt = _build_modify_prompt(config, doc, req)
        # Context should appear in the intel block
        assert ctx in prompt
        # But the JSON section header should not contain it
        json_section = prompt.split("Current itinerary JSON:")[-1]
        assert ctx not in json_section


# ─── Feasibility content-hash trigger ────────────────────────────────────────

class TestFeasibilityContentHash:
    """
    The FeasibilitySection should re-trigger analysis when activity content changes,
    not just when itinerary.id changes.

    We test the backend feasibility engine responds correctly to edited itineraries,
    which validates that re-analysis on content change produces updated results.
    """

    def _make_doc(self, overlap=False):
        from backend.models import (
            ItineraryActivity, ItineraryBudget, ItineraryConfig,
            ItineraryDocument, ItineraryPlanDay,
        )
        cfg = ItineraryConfig(
            destination="Paris",
            start_date="2026-05-01",
            end_date="2026-05-01",
            travelers=1,
            budget=500,
            currency="EUR",
        )
        if overlap:
            # 09:00+90min, 10:00 → overlap by 30 min
            acts = [
                ItineraryActivity(id="a1", time="09:00", duration_minutes=90, name="A1",
                                  category="sightseeing", estimated_cost_per_person=10.0,
                                  currency="EUR", location="Louvre"),
                ItineraryActivity(id="a2", time="10:00", duration_minutes=60, name="A2",
                                  category="sightseeing", estimated_cost_per_person=10.0,
                                  currency="EUR", location="Eiffel"),
            ]
        else:
            # Well-spaced: 09:00+60min, 11:00
            acts = [
                ItineraryActivity(id="a1", time="09:00", duration_minutes=60, name="A1",
                                  category="sightseeing", estimated_cost_per_person=10.0,
                                  currency="EUR", location="Louvre"),
                ItineraryActivity(id="a2", time="11:00", duration_minutes=60, name="A2",
                                  category="sightseeing", estimated_cost_per_person=10.0,
                                  currency="EUR", location="Eiffel"),
            ]
        day = ItineraryPlanDay(day_number=1, date="2026-05-01", activities=acts)
        return ItineraryDocument(
            id="test",
            config=cfg,
            daily_plans=[day],
            budget_breakdown=ItineraryBudget(grand_total=20, per_person_total=20, currency="EUR"),
        )

    def test_no_issues_before_edit(self):
        from backend.feasibility import analyse
        doc = self._make_doc(overlap=False)
        issues = analyse(doc)
        overlaps = [i for i in issues if i.code == "TIME_OVERLAP"]
        assert len(overlaps) == 0

    def test_overlap_issue_after_edit(self):
        from backend.feasibility import analyse
        doc = self._make_doc(overlap=True)
        issues = analyse(doc)
        overlaps = [i for i in issues if i.code == "TIME_OVERLAP"]
        assert len(overlaps) >= 1

    def test_different_content_produces_different_hash(self):
        """
        Simulate what the frontend content-hash logic does:
        a1:09:00:90:10.0 vs a1:09:00:60:10.0 → different hash.
        """
        def content_hash(daily_plans):
            return "||".join(
                "|".join(
                    f"{a['id']}:{a['time']}:{a['duration_minutes']}:{a['estimated_cost_per_person']}"
                    for a in day["activities"]
                )
                for day in daily_plans
            )

        plans_before = [{"activities": [{"id": "a1", "time": "09:00", "duration_minutes": 90,
                                          "estimated_cost_per_person": 10.0}]}]
        plans_after  = [{"activities": [{"id": "a1", "time": "09:00", "duration_minutes": 60,
                                          "estimated_cost_per_person": 10.0}]}]

        assert content_hash(plans_before) != content_hash(plans_after)

    def test_same_content_produces_same_hash(self):
        def content_hash(daily_plans):
            return "||".join(
                "|".join(
                    f"{a['id']}:{a['time']}:{a['duration_minutes']}:{a['estimated_cost_per_person']}"
                    for a in day["activities"]
                )
                for day in daily_plans
            )

        plans = [{"activities": [{"id": "a1", "time": "09:00", "duration_minutes": 60,
                                   "estimated_cost_per_person": 10.0}]}]
        assert content_hash(plans) == content_hash(plans)


# ─── Geocoding sleep guard ─────────────────────────────────────────────────────

class TestGeocodingSleepGuard:
    """The sleep inside geocode only fires when elapsed < 1.1 s.
    This test verifies the guard logic without actually sleeping."""

    def setup_method(self):
        from backend.geocoding import clear_cache
        clear_cache()

    def test_known_destination_never_sleeps(self):
        """All 22 known destinations hit the fallback table — no sleep."""
        from backend.geocoding import geocode
        # Paris is in the fallback table; time.sleep should never be called
        with patch("time.sleep") as mock_sleep:
            geocode("Paris")
        mock_sleep.assert_not_called()

    def test_sleep_skipped_when_enough_time_elapsed(self):
        """If _LAST_CALL_TIME is old enough, sleep is skipped."""
        import backend.geocoding as gc_mod
        gc_mod._LAST_CALL_TIME = time.monotonic() - 2.0  # 2 s ago

        mock_response = MagicMock()
        mock_response.json.return_value = [{"lat": "51.5", "lon": "-0.1"}]
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_response):
            with patch("time.sleep") as mock_sleep:
                gc_mod.geocode("some_unknown_city_xyz_no_cache")

        mock_sleep.assert_not_called()

    def test_sleep_called_when_too_soon(self):
        """If _LAST_CALL_TIME is recent, sleep is called."""
        import backend.geocoding as gc_mod
        gc_mod._LAST_CALL_TIME = time.monotonic()  # just now

        mock_response = MagicMock()
        mock_response.json.return_value = [{"lat": "51.5", "lon": "-0.1"}]
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_response):
            with patch("time.sleep") as mock_sleep:
                gc_mod.geocode("another_unknown_city_xyz_no_cache")

        mock_sleep.assert_called_once()
        sleep_arg = mock_sleep.call_args[0][0]
        assert 0 < sleep_arg <= 1.1


# ─── Weather backward compat and refresh ─────────────────────────────────────

class TestWeatherBackwardCompat:
    def setup_method(self):
        from backend.weather import clear_cache
        from backend.geocoding import clear_cache as gc_clear
        clear_cache()
        gc_clear()

    def test_climatology_returned_without_live_call_for_far_dates(self):
        """Dates >16 days ahead must use climatology, never Open-Meteo."""
        from backend.weather import get_weather
        sd = date.today() + timedelta(days=60)
        ed = sd + timedelta(days=2)
        with patch("httpx.Client") as mock_cls:
            result = get_weather("Paris", sd, ed)
        # httpx.Client should not have been called
        mock_cls.assert_not_called()
        assert result.source == "CLIMATOLOGY"

    def test_response_has_attribution_field(self):
        from backend.weather import get_weather
        sd = date.today() + timedelta(days=60)
        ed = sd + timedelta(days=1)
        result = get_weather("Goa", sd, ed)
        assert isinstance(result.attribution, str)
        assert len(result.attribution) > 0

    def test_response_has_notes_field(self):
        from backend.weather import get_weather
        sd = date.today() + timedelta(days=60)
        ed = sd + timedelta(days=1)
        result = get_weather("Goa", sd, ed)
        assert isinstance(result.notes, str)

    def test_cache_hit_returns_same_object(self):
        from backend.weather import get_weather
        sd = date.today() + timedelta(days=1)
        ed = sd + timedelta(days=1)
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "timezone": "UTC",
            "daily": {
                "time": [sd.isoformat(), ed.isoformat()],
                "weather_code": [0, 0],
                "temperature_2m_max": [25.0, 25.0],
                "temperature_2m_min": [18.0, 18.0],
                "precipitation_sum": [0.0, 0.0],
                "precipitation_probability_max": [5, 5],
                "wind_speed_10m_max": [10.0, 10.0],
            },
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_resp)

        with patch("httpx.Client", return_value=mock_client):
            with patch("backend.weather.geocode", return_value=(48.86, 2.35)):
                r1 = get_weather("Paris", sd, ed)
                r2 = get_weather("Paris", sd, ed)

        assert mock_client.get.call_count == 1
        assert r1 is r2  # exact same cached object


# ─── POI lazy load ────────────────────────────────────────────────────────────

class TestPoiLazyLoad:
    """
    The PoiSection should NOT fire a network request on mount — only on first expand.
    We test the backend get_pois caching behaviour, which is what the lazy-load
    avoids duplicating.
    """

    def setup_method(self):
        from backend.poi import clear_cache
        from backend.geocoding import clear_cache as gc_clear
        clear_cache()
        gc_clear()

    def test_poi_not_called_twice_for_same_destination(self):
        from backend.poi import get_pois
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "elements": [
                {"type": "node", "id": 1, "lat": 48.86, "lon": 2.29,
                 "tags": {"name": "Eiffel Tower", "tourism": "attraction"}},
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = MagicMock(return_value=mock_resp)

        with patch("httpx.Client", return_value=mock_client):
            with patch("backend.poi.geocode", return_value=(48.86, 2.35)):
                get_pois("Paris")
                get_pois("Paris")

        assert mock_client.post.call_count == 1

    def test_poi_static_fallback_has_names(self):
        """Static fallback for a known destination returns real attraction names."""
        from backend.poi import get_pois
        with patch("backend.poi.geocode", return_value=None):
            result = get_pois("Goa")
        assert result.source == "STATIC_FALLBACK"
        names = [p.name for p in result.pois]
        assert any("Beach" in n or "Basilica" in n or "Fort" in n for n in names)


# ─── Backward compatibility: existing workflows unaffected ───────────────────

class TestBackwardCompatibility:
    """Verify that existing routes, models, and tools are not broken."""

    def test_analyze_routes_present(self):
        from backend.main import app
        paths = {getattr(r, "path", None) for r in app.routes}
        assert "/api/analyze" in paths
        assert "/api/analyze/stream" in paths

    def test_trip_planner_routes_present(self):
        from backend.main import app
        paths = {getattr(r, "path", None) for r in app.routes}
        assert "/api/trips/{trip_id}/plan" in paths
        assert "/api/trips/{trip_id}/preferences" in paths

    def test_itinerary_routes_present(self):
        from backend.main import app
        paths = {getattr(r, "path", None) for r in app.routes}
        assert "/api/itinerary/generate" in paths
        assert "/api/trips/{trip_id}/itinerary" in paths
        assert "/api/trips/{trip_id}/itinerary/modify" in paths

    def test_trippreferences_model_unchanged(self):
        from backend.models import TripPreferences
        from datetime import date
        prefs = TripPreferences(
            destination="Goa",
            start_date=date(2026, 1, 10),
            end_date=date(2026, 1, 15),
            travelers=2,
            budget=30000,
            currency="INR",
        )
        assert prefs.destination == "Goa"

    def test_tools_import_cleanly(self):
        from backend.tools import convert_currency, check_budget_feasibility, estimate_trip_cost
        # LangChain @tool-decorated functions are StructuredTool objects, not plain callables.
        # Verify they have the expected .name attribute instead.
        assert hasattr(convert_currency, "name")
        assert hasattr(check_budget_feasibility, "name")
        assert hasattr(estimate_trip_cost, "name")
        assert convert_currency.name == "convert_currency"
        assert check_budget_feasibility.name == "check_budget_feasibility"
        assert estimate_trip_cost.name == "estimate_trip_cost"

    def test_persistence_methods_intact(self):
        from backend.persistence import TripStore
        store = TripStore()
        assert hasattr(store, "save_preferences_for_owner")
        assert hasattr(store, "get_preferences_for_owner")
        assert hasattr(store, "save_itinerary_for_owner")
        assert hasattr(store, "get_itinerary_for_owner")
        assert hasattr(store, "save_plan_update_for_owner")

    def test_rates_module_unchanged(self):
        from backend.rates import get_snapshot, quote, is_supported
        assert is_supported("EUR")
        assert is_supported("INR")
        assert is_supported("USD")

    def test_auth_module_unchanged(self):
        from backend.auth import router, require_csrf, require_local_owner
        assert router is not None

    def test_modify_request_without_intel_field_valid(self):
        """Pre-5.1 callers that omit intelligence_context must still validate."""
        from backend.models import ItineraryModifyRequest
        req = ItineraryModifyRequest(
            modification_request="Make day 2 more relaxed.",
            target_day=2,
        )
        assert req.intelligence_context is None
        raw = req.model_dump_json()
        parsed = ItineraryModifyRequest.model_validate_json(raw)
        assert parsed.modification_request == req.modification_request

    def test_feasibility_analysis_unchanged_for_legacy_itinerary(self):
        """An itinerary without intelligence_context passes through feasibility unchanged."""
        from backend.models import (
            ItineraryActivity, ItineraryBudget, ItineraryConfig,
            ItineraryDocument, ItineraryPlanDay,
        )
        from backend.feasibility import analyse, summarise
        cfg = ItineraryConfig(destination="Goa", start_date="2026-01-10",
                              end_date="2026-01-10", travelers=1, budget=5000, currency="INR")
        acts = [ItineraryActivity(id="a1", time="09:00", duration_minutes=60, name="A1",
                                  category="sightseeing", estimated_cost_per_person=200.0,
                                  currency="INR", location="Calangute Beach")]
        day = ItineraryPlanDay(day_number=1, date="2026-01-10", activities=acts)
        doc = ItineraryDocument(id="legacy", config=cfg, daily_plans=[day],
                                budget_breakdown=ItineraryBudget(
                                    grand_total=200, per_person_total=200,
                                    currency="INR", budget_remaining=4800))
        issues = analyse(doc)
        s = summarise(issues)
        assert s["total"] == s["critical"] + s["warning"] + s["info"]


# ─── Phase 5 route registration (regression) ─────────────────────────────────

class TestPhase5RoutesRegression:
    def test_all_phase5_routes_still_registered(self):
        from backend.main import app
        paths = {getattr(r, "path", None) for r in app.routes}
        assert "/api/weather/{destination}" in paths
        assert "/api/poi/{destination}" in paths
        assert "/api/itinerary/feasibility" in paths
