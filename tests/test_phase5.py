"""
Phase 5 — Tests for Weather, POI, Geocoding, and Feasibility.

All external HTTP calls are mocked. No actual network traffic.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ─── Geocoding ─────────────────────────────────────────────────��──────────────

class TestGeocoding:
    def setup_method(self):
        from backend.geocoding import clear_cache
        clear_cache()

    def test_fallback_known_destination(self):
        from backend.geocoding import geocode
        coords = geocode("Paris")
        assert coords is not None
        lat, lon = coords
        assert 48.0 < lat < 49.5
        assert 1.5 < lon < 3.5

    def test_fallback_case_insensitive(self):
        from backend.geocoding import geocode
        assert geocode("GOA") == geocode("goa")

    def test_fallback_alias(self):
        from backend.geocoding import geocode
        coords = geocode("New York")
        assert coords is not None
        assert 40.0 < coords[0] < 41.5

    def test_nominatim_called_for_unknown_destination(self):
        """Unknown destinations trigger a Nominatim lookup."""
        from backend.geocoding import geocode
        mock_response = MagicMock()
        mock_response.json.return_value = [{"lat": "51.5074", "lon": "-0.1278"}]
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_response) as mock_get:
            coords = geocode("London UK test destination xyz")
            mock_get.assert_called_once()

        assert coords is not None
        assert 51.0 < coords[0] < 52.0

    def test_nominatim_failure_returns_none(self):
        from backend.geocoding import geocode
        import httpx
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            coords = geocode("completely_unknown_place_xyz_404")
        assert coords is None

    def test_cache_prevents_second_nominatim_call(self):
        from backend.geocoding import geocode
        mock_response = MagicMock()
        mock_response.json.return_value = [{"lat": "12.0", "lon": "77.0"}]
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_response) as mock_get:
            geocode("test_city_cache_check_abc")
            geocode("test_city_cache_check_abc")
            assert mock_get.call_count == 1  # cached after first call


# ─── Weather ──────────────────────────────────────────────────────────────────

MOCK_OPEN_METEO_RESPONSE = {
    "timezone": "Asia/Kolkata",
    "generationtime_ms": 1.5,
    "daily": {
        "time": ["2026-01-10", "2026-01-11", "2026-01-12"],
        "weather_code":           [0, 61, 2],
        "temperature_2m_max":     [33.0, 29.5, 31.0],
        "temperature_2m_min":     [22.0, 24.0, 23.0],
        "precipitation_sum":      [0.0, 12.5, 0.5],
        "precipitation_probability_max": [5, 85, 20],
        "wind_speed_10m_max":     [15.0, 25.0, 12.0],
    },
}


class TestWeather:
    def setup_method(self):
        from backend.weather import clear_cache
        from backend.geocoding import clear_cache as gc_clear
        clear_cache()
        gc_clear()

    def _mock_forecast_response(self):
        mock = MagicMock()
        mock.json.return_value = MOCK_OPEN_METEO_RESPONSE
        mock.raise_for_status = MagicMock()
        mock.__enter__ = lambda s: s
        mock.__exit__ = MagicMock(return_value=False)
        return mock

    def test_forecast_returns_correct_day_count(self):
        from backend.weather import get_weather
        sd = date.today() + timedelta(days=2)
        ed = sd + timedelta(days=2)
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            **MOCK_OPEN_METEO_RESPONSE,
            "daily": {k: v[:3] for k, v in MOCK_OPEN_METEO_RESPONSE["daily"].items()},
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_resp)

        with patch("httpx.Client", return_value=mock_client):
            result = get_weather("Goa", sd, ed)

        assert result.source == "FORECAST"
        assert len(result.days) == 3

    def test_climatology_returned_for_far_future_dates(self):
        from backend.weather import get_weather
        sd = date.today() + timedelta(days=60)
        ed = sd + timedelta(days=2)
        result = get_weather("Goa", sd, ed)
        assert result.source == "CLIMATOLOGY"
        assert len(result.days) == 3
        for d in result.days:
            assert d.source == "CLIMATOLOGY"

    def test_climatology_returned_when_geocode_fails(self):
        from backend.weather import get_weather
        sd = date.today() + timedelta(days=1)
        ed = sd + timedelta(days=1)
        with patch("backend.weather.geocode", return_value=None):
            result = get_weather("nowhere_xyz", sd, ed)
        assert result.source == "CLIMATOLOGY"

    def test_climatology_returned_on_network_error(self):
        import httpx
        from backend.weather import get_weather
        sd = date.today() + timedelta(days=1)
        ed = sd + timedelta(days=2)
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(side_effect=httpx.ConnectError("refused"))

        with patch("httpx.Client", return_value=mock_client):
            with patch("backend.weather.geocode", return_value=(15.3, 74.1)):
                result = get_weather("Goa", sd, ed)

        assert result.source == "CLIMATOLOGY"

    def test_rain_day_flagged_correctly(self):
        from backend.weather import get_weather
        sd = date.today() + timedelta(days=1)
        ed = sd + timedelta(days=2)
        mock_client = MagicMock()
        mock_resp = MagicMock()
        # Day 2 has code 61 (rain) and 85% probability
        mock_resp.json.return_value = {
            "timezone": "UTC",
            "daily": {
                "time": [sd.isoformat(), (sd + timedelta(days=1)).isoformat(), (sd + timedelta(days=2)).isoformat()],
                "weather_code":           [0, 61, 2],
                "temperature_2m_max":     [30.0, 28.0, 29.0],
                "temperature_2m_min":     [22.0, 23.0, 22.0],
                "precipitation_sum":      [0.0, 15.0, 0.2],
                "precipitation_probability_max": [5, 85, 15],
                "wind_speed_10m_max":     [10.0, 20.0, 12.0],
            },
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_resp)

        with patch("httpx.Client", return_value=mock_client):
            with patch("backend.weather.geocode", return_value=(15.3, 74.1)):
                result = get_weather("Goa", sd, ed)

        assert result.days[1].is_rain_likely is True
        assert result.days[0].is_rain_likely is False

    def test_weather_cache_prevents_second_http_call(self):
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
                "temperature_2m_max": [30.0, 30.0],
                "temperature_2m_min": [20.0, 20.0],
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
                get_weather("Paris", sd, ed)
                get_weather("Paris", sd, ed)
                assert mock_client.get.call_count == 1

    def test_climatology_has_all_months_for_known_destinations(self):
        from backend.weather import _CLIMATOLOGY
        for dest, table in _CLIMATOLOGY.items():
            assert len(table) == 12, f"{dest} missing months"
            for month, (lo, hi, days) in table.items():
                assert 1 <= month <= 12
                assert lo <= hi
                assert 0 <= days <= 31

    def test_wmo_label_known_codes(self):
        from backend.weather import _wmo_label
        assert _wmo_label(0) == "Clear sky"
        assert _wmo_label(61) == "Slight rain"
        assert _wmo_label(95) == "Thunderstorm"
        assert "Code" in _wmo_label(999)   # unknown code fallback

    def test_wmo_label_none(self):
        from backend.weather import _wmo_label
        assert _wmo_label(None) == "Unknown"


# ─── POI ─────────────────────────────────────────────────────────────────────

MOCK_OVERPASS_RESPONSE = {
    "elements": [
        {"type": "node", "id": 1, "lat": 48.858, "lon": 2.294,
         "tags": {"name": "Eiffel Tower", "tourism": "attraction"}},
        {"type": "node", "id": 2, "lat": 48.860, "lon": 2.337,
         "tags": {"name": "Louvre Museum", "tourism": "museum"}},
        {"type": "node", "id": 3, "lat": 48.873, "lon": 2.343,
         "tags": {"name": "Notre-Dame Cathedral", "historic": "monument"}},
        # Same name at a distinct location must be preserved so the UI can
        # detect ambiguity instead of selecting the first result silently.
        {"type": "node", "id": 4, "lat": 48.858, "lon": 2.295,
         "tags": {"name": "Eiffel Tower", "tourism": "attraction"}},
        # Repeated copy of the same OSM object should be discarded by ID.
        {"type": "node", "id": 4, "lat": 48.858, "lon": 2.295,
         "tags": {"name": "Eiffel Tower", "tourism": "attraction"}},
    ]
}


class TestPoi:
    def setup_method(self):
        from backend.poi import clear_cache
        from backend.geocoding import clear_cache as gc_clear
        clear_cache()
        gc_clear()

    def test_overpass_returns_pois(self):
        from backend.poi import get_pois
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_OVERPASS_RESPONSE
        mock_resp.raise_for_status = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = MagicMock(return_value=mock_resp)

        with patch("httpx.Client", return_value=mock_client):
            with patch("backend.poi.geocode", return_value=(48.86, 2.35)):
                result = get_pois("Paris")

        assert result.source == "OSM"
        assert len(result.pois) == 4  # distinct same-name places preserved

    def test_poi_duplicate_deduplication(self):
        from backend.poi import get_pois
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_OVERPASS_RESPONSE
        mock_resp.raise_for_status = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = MagicMock(return_value=mock_resp)

        with patch("httpx.Client", return_value=mock_client):
            with patch("backend.poi.geocode", return_value=(48.86, 2.35)):
                result = get_pois("Paris")

        ids = [(p.osm_type, p.osm_id) for p in result.pois]
        assert len(ids) == len(set(ids))  # duplicate OSM object IDs removed
        assert sum(p.name == "Eiffel Tower" for p in result.pois) == 2

    def test_static_fallback_when_geocode_fails(self):
        from backend.poi import get_pois
        with patch("backend.poi.geocode", return_value=None):
            result = get_pois("Goa")
        assert result.source == "STATIC_FALLBACK"
        assert len(result.pois) > 0
        assert all(p.source == "STATIC_FALLBACK" for p in result.pois)

    def test_static_fallback_on_overpass_error(self):
        import httpx
        from backend.poi import get_pois
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = MagicMock(side_effect=httpx.ConnectError("refused"))

        with patch("httpx.Client", return_value=mock_client):
            with patch("backend.poi.geocode", return_value=(15.3, 74.1)):
                result = get_pois("Goa")

        assert result.source == "STATIC_FALLBACK"

    def test_empty_overpass_result_uses_static_fallback(self):
        from backend.poi import get_pois
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"elements": []}
        mock_resp.raise_for_status = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = MagicMock(return_value=mock_resp)

        with patch("httpx.Client", return_value=mock_client):
            with patch("backend.poi.geocode", return_value=(15.3, 74.1)):
                result = get_pois("Goa")

        assert result.source == "STATIC_FALLBACK"

    def test_poi_cache(self):
        from backend.poi import get_pois
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_OVERPASS_RESPONSE
        mock_resp.raise_for_status = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = MagicMock(return_value=mock_resp)

        with patch("httpx.Client", return_value=mock_client):
            with patch("backend.poi.geocode", return_value=(48.86, 2.35)):
                get_pois("Paris")
                get_pois("Paris")
                assert mock_client.post.call_count == 1

    def test_missing_poi_coordinates_handled(self):
        """POIs with missing lat/lon should still parse without error."""
        from backend.poi import get_pois
        response_with_missing_coords = {
            "elements": [
                {"type": "way", "id": 10,
                 "tags": {"name": "Central Park", "leisure": "park"}},
                # no lat/lon and no center key
            ]
        }
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = response_with_missing_coords
        mock_resp.raise_for_status = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = MagicMock(return_value=mock_resp)

        with patch("httpx.Client", return_value=mock_client):
            with patch("backend.poi.geocode", return_value=(40.78, -73.97)):
                result = get_pois("New York")

        # Returned with None coordinates — no crash
        assert any(p.name == "Central Park" for p in result.pois if result.source == "OSM") or \
               result.source == "STATIC_FALLBACK"


# ─── Feasibility engine ───────────────────────────────────────────────────────

class TestFeasibility:
    def _make_itinerary(self, activities_per_day=3, pace="balanced"):
        """Build a minimal ItineraryDocument for testing."""
        from backend.models import (
            ItineraryActivity, ItineraryBudget, ItineraryConfig,
            ItineraryDocument, ItineraryPlanDay,
        )
        cfg = ItineraryConfig(
            destination="Paris",
            start_date="2026-05-01",
            end_date="2026-05-03",
            travelers=2,
            budget=1500,
            currency="EUR",
            pace=pace,
        )
        days = []
        for d in range(3):
            acts = []
            for i in range(activities_per_day):
                start_hour = 9 + i * 2
                acts.append(ItineraryActivity(
                    id=f"d{d+1}_a{i+1}",
                    time=f"{start_hour:02d}:00",
                    duration_minutes=90,
                    name=f"Activity {i+1}",
                    location="Paris Centre",
                    category="sightseeing",
                    estimated_cost_per_person=20.0,
                    currency="EUR",
                ))
            days.append(ItineraryPlanDay(
                day_number=d + 1,
                date=f"2026-05-0{d+1}",
                title=f"Day {d+1}",
                activities=acts,
            ))
        budget = ItineraryBudget(
            accommodation_total=300,
            food_total=120,
            transport_total=60,
            activities_total=360,
            miscellaneous_total=60,
            grand_total=900,
            per_person_total=450,
            currency="EUR",
            budget_remaining=600,
            is_over_budget=False,
        )
        return ItineraryDocument(id="test", config=cfg, daily_plans=days, budget_breakdown=budget)

    def test_no_issues_for_well_spaced_itinerary(self):
        from backend.feasibility import analyse
        doc = self._make_itinerary(activities_per_day=3)
        issues = analyse(doc)
        conflict_issues = [i for i in issues if i.code == "TIME_OVERLAP"]
        assert len(conflict_issues) == 0

    def test_overlap_detected(self):
        from backend.feasibility import analyse
        from backend.models import ItineraryActivity, ItineraryPlanDay, ItineraryConfig, ItineraryBudget, ItineraryDocument
        cfg = ItineraryConfig(destination="Paris", start_date="2026-05-01", end_date="2026-05-01",
                              travelers=1, budget=500, currency="EUR")
        # Two activities that overlap: 09:00+90min and 10:00
        acts = [
            ItineraryActivity(id="a1", time="09:00", duration_minutes=90, name="A1",
                              category="sightseeing", estimated_cost_per_person=10.0, currency="EUR"),
            ItineraryActivity(id="a2", time="10:00", duration_minutes=60, name="A2",
                              category="sightseeing", estimated_cost_per_person=10.0, currency="EUR"),
        ]
        day = ItineraryPlanDay(day_number=1, date="2026-05-01", activities=acts)
        doc = ItineraryDocument(id="t", config=cfg, daily_plans=[day],
                                budget_breakdown=ItineraryBudget(grand_total=20, per_person_total=20, currency="EUR"))
        issues = analyse(doc)
        overlap = [i for i in issues if i.code == "TIME_OVERLAP"]
        assert len(overlap) >= 1

    def test_tight_schedule_warning(self):
        from backend.feasibility import analyse
        from backend.models import ItineraryActivity, ItineraryPlanDay, ItineraryConfig, ItineraryBudget, ItineraryDocument
        cfg = ItineraryConfig(destination="Tokyo", start_date="2026-03-01", end_date="2026-03-01",
                              travelers=2, budget=500, currency="USD")
        # 09:00+60min, 10:05 — only 5 min gap
        acts = [
            ItineraryActivity(id="a1", time="09:00", duration_minutes=60, name="A1",
                              category="sightseeing", estimated_cost_per_person=15.0, currency="USD"),
            ItineraryActivity(id="a2", time="10:05", duration_minutes=60, name="A2",
                              category="sightseeing", estimated_cost_per_person=15.0, currency="USD"),
        ]
        day = ItineraryPlanDay(day_number=1, date="2026-03-01", activities=acts)
        doc = ItineraryDocument(id="t", config=cfg, daily_plans=[day],
                                budget_breakdown=ItineraryBudget(grand_total=60, per_person_total=30, currency="USD"))
        issues = analyse(doc)
        tight = [i for i in issues if i.code == "TIGHT_SCHEDULE"]
        assert len(tight) >= 1

    def test_no_meal_break_flagged(self):
        from backend.feasibility import analyse
        from backend.models import ItineraryActivity, ItineraryPlanDay, ItineraryConfig, ItineraryBudget, ItineraryDocument
        cfg = ItineraryConfig(destination="Rome", start_date="2026-04-01", end_date="2026-04-01",
                              travelers=2, budget=400, currency="EUR")
        # 5+ hour day with no food activity
        acts = [
            ItineraryActivity(id="a1", time="09:00", duration_minutes=120, name="A1",
                              category="sightseeing", estimated_cost_per_person=10.0, currency="EUR"),
            ItineraryActivity(id="a2", time="12:00", duration_minutes=120, name="A2",
                              category="sightseeing", estimated_cost_per_person=10.0, currency="EUR"),
        ]
        day = ItineraryPlanDay(day_number=1, date="2026-04-01", activities=acts)
        doc = ItineraryDocument(id="t", config=cfg, daily_plans=[day],
                                budget_breakdown=ItineraryBudget(grand_total=40, per_person_total=20, currency="EUR"))
        issues = analyse(doc)
        meal = [i for i in issues if i.code == "NO_MEAL_BREAK"]
        assert len(meal) >= 1

    def test_budget_overrun_flagged(self):
        from backend.feasibility import analyse
        from backend.models import ItineraryActivity, ItineraryPlanDay, ItineraryConfig, ItineraryBudget, ItineraryDocument
        cfg = ItineraryConfig(destination="Santorini", start_date="2026-09-01", end_date="2026-09-01",
                              travelers=2, budget=100, currency="EUR")
        acts = [ItineraryActivity(id="a1", time="09:00", duration_minutes=60, name="A1",
                                  category="sightseeing", estimated_cost_per_person=10.0, currency="EUR")]
        day = ItineraryPlanDay(day_number=1, date="2026-09-01", activities=acts)
        doc = ItineraryDocument(id="t", config=cfg, daily_plans=[day],
                                budget_breakdown=ItineraryBudget(
                                    grand_total=500, per_person_total=250,
                                    budget_remaining=-400, is_over_budget=True, currency="EUR"))
        issues = analyse(doc)
        over = [i for i in issues if i.code == "BUDGET_OVERRUN"]
        assert len(over) == 1
        assert over[0].severity == "WARNING"

    def test_missing_location_flagged(self):
        from backend.feasibility import analyse
        from backend.models import ItineraryActivity, ItineraryPlanDay, ItineraryConfig, ItineraryBudget, ItineraryDocument
        cfg = ItineraryConfig(destination="Bangkok", start_date="2026-11-01", end_date="2026-11-01",
                              travelers=1, budget=400, currency="USD")
        acts = [ItineraryActivity(id="a1", time="09:00", duration_minutes=60, name="A1",
                                  location="",  # intentionally empty
                                  category="sightseeing", estimated_cost_per_person=5.0, currency="USD")]
        day = ItineraryPlanDay(day_number=1, date="2026-11-01", activities=acts)
        doc = ItineraryDocument(id="t", config=cfg, daily_plans=[day],
                                budget_breakdown=ItineraryBudget(grand_total=5, per_person_total=5, currency="USD"))
        issues = analyse(doc)
        missing = [i for i in issues if i.code == "MISSING_LOCATION"]
        assert len(missing) == 1

    def test_high_density_flagged(self):
        from backend.feasibility import analyse
        # 8 activities for a relaxed pace → density warning
        doc = self._make_itinerary(activities_per_day=8, pace="relaxed")
        issues = analyse(doc)
        density = [i for i in issues if i.code == "HIGH_DENSITY"]
        assert len(density) > 0

    def test_summary_counts_correct(self):
        from backend.feasibility import analyse, summarise
        from backend.models import ItineraryActivity, ItineraryPlanDay, ItineraryConfig, ItineraryBudget, ItineraryDocument
        cfg = ItineraryConfig(destination="Barcelona", start_date="2026-04-01", end_date="2026-04-01",
                              travelers=2, budget=50, currency="EUR")
        # Overlapping activities + budget overrun
        acts = [
            ItineraryActivity(id="a1", time="09:00", duration_minutes=120, name="A1",
                              category="sightseeing", estimated_cost_per_person=10.0, currency="EUR"),
            ItineraryActivity(id="a2", time="10:00", duration_minutes=60, name="A2",
                              category="sightseeing", estimated_cost_per_person=10.0, currency="EUR"),
        ]
        day = ItineraryPlanDay(day_number=1, date="2026-04-01", activities=acts)
        doc = ItineraryDocument(id="t", config=cfg, daily_plans=[day],
                                budget_breakdown=ItineraryBudget(
                                    grand_total=200, per_person_total=100,
                                    budget_remaining=-150, is_over_budget=True, currency="EUR"))
        issues = analyse(doc)
        s = summarise(issues)
        assert s["total"] == len(issues)
        assert s["critical"] + s["warning"] + s["info"] == s["total"]

    def test_suitable_outdoor_false_for_rain(self):
        from backend.weather import DayWeather
        d = DayWeather(
            date="2026-06-15", temp_min=18.0, temp_max=24.0,
            precipitation_mm=12.0, precipitation_probability=80,
            weather_code=61, weather_label="Slight rain",
            is_rain_likely=True, wind_speed_max=20.0,
            source="FORECAST", is_suitable_outdoor=False,
        )
        assert d.is_suitable_outdoor is False


# ─── Models ───────────────────────────────────────────────────────────────────

class TestPhase5Models:
    def test_weather_response_out_serialises(self):
        from backend.models import WeatherResponseOut, DayWeatherOut
        resp = WeatherResponseOut(
            destination="Goa",
            days=[DayWeatherOut(date="2026-01-10", source="FORECAST")],
            source="FORECAST",
        )
        raw = resp.model_dump_json()
        parsed = WeatherResponseOut.model_validate_json(raw)
        assert parsed.destination == "Goa"
        assert parsed.days[0].source == "FORECAST"

    def test_poi_response_out_serialises(self):
        from backend.models import PoiResponseOut, PoiResultOut
        resp = PoiResponseOut(
            destination="Paris",
            pois=[PoiResultOut(name="Eiffel Tower", category="tourism:attraction", osm_id=1)],
            source="OSM",
        )
        raw = resp.model_dump_json()
        parsed = PoiResponseOut.model_validate_json(raw)
        assert parsed.pois[0].name == "Eiffel Tower"

    def test_feasibility_response_out_serialises(self):
        from backend.models import FeasibilityResponseOut, FeasibilityIssueOut
        resp = FeasibilityResponseOut(
            issues=[FeasibilityIssueOut(severity="WARNING", code="TIME_OVERLAP",
                                        message="Activities overlap.", suggestion="Adjust.")],
            summary={"critical": 0, "warning": 1, "info": 0, "total": 1},
            analysed_days=3,
        )
        raw = resp.model_dump_json()
        parsed = FeasibilityResponseOut.model_validate_json(raw)
        assert parsed.summary["warning"] == 1

    def test_feasibility_request_accepts_itinerary(self):
        from backend.models import FeasibilityRequest, ItineraryConfig, ItineraryDocument
        cfg = ItineraryConfig(destination="Tokyo", start_date="2026-03-01",
                              end_date="2026-03-03", travelers=2, budget=500, currency="USD")
        doc = ItineraryDocument(id="x", config=cfg)
        req = FeasibilityRequest(itinerary=doc)
        assert req.itinerary.id == "x"


# ─── API route registration ───────────────────────────────────────────────────

class TestPhase5Routes:
    def test_weather_route_registered(self):
        from backend.main import app
        paths = {getattr(r, "path", None) for r in app.routes}
        assert "/api/weather/{destination}" in paths

    def test_poi_route_registered(self):
        from backend.main import app
        paths = {getattr(r, "path", None) for r in app.routes}
        assert "/api/poi/{destination}" in paths

    def test_feasibility_route_registered(self):
        from backend.main import app
        paths = {getattr(r, "path", None) for r in app.routes}
        assert "/api/itinerary/feasibility" in paths


# ─── Legacy compatibility ─────────────────────────────────────────────────────

class TestLegacyCompatibility:
    def test_existing_itinerary_routes_still_registered(self):
        from backend.main import app
        paths = {getattr(r, "path", None) for r in app.routes}
        assert "/api/itinerary/generate" in paths
        assert "/api/trips/{trip_id}/itinerary" in paths
        assert "/api/trips/{trip_id}/itinerary/modify" in paths

    def test_existing_analyze_routes_still_registered(self):
        from backend.main import app
        paths = {getattr(r, "path", None) for r in app.routes}
        assert "/api/analyze" in paths
        assert "/api/analyze/stream" in paths

    def test_tripstore_has_itinerary_methods(self):
        from backend.persistence import TripStore
        store = TripStore()
        assert hasattr(store, "save_itinerary_for_owner")
        assert hasattr(store, "get_itinerary_for_owner")

    def test_trip_itineraries_table_exists(self):
        import sqlite3
        from backend.config import DATABASE_PATH
        from backend.persistence import TripStore
        TripStore()
        conn = sqlite3.connect(str(DATABASE_PATH))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "trip_itineraries" in tables

    def test_phase5_modules_import_cleanly(self):
        import backend.geocoding
        import backend.weather
        import backend.poi
        import backend.feasibility
        assert True  # no ImportError
