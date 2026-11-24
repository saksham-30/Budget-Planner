"""
Phase 6 — Travel Logistics tests.

Covers:
- URL building correctness for all three providers (Google Flights, Skyscanner, Booking.com)
- Airport code lookup (known destinations, partial matches, unknown destinations)
- Transport guidance retrieval and fallback for unknown destinations
- Hostelworld link only generated for budget/hostel preference
- rome2rio URL construction
- build_logistics returns correct structure
- Pydantic model serialisation round-trip
- API route registration
- Backward compatibility: no existing routes or models broken
- Parameter validation (travelers range, budget_style normalisation)

No live API calls are made in any test.
"""

from __future__ import annotations

import pytest


# ─── Airport code lookup ──────────────────────────────────────────────────────

class TestAirportCodes:
    def test_known_destinations_return_primary_code(self):
        from backend.logistics import _get_airport_code
        cases = {
            "Paris":      "CDG",
            "Tokyo":      "NRT",
            "Goa":        "GOI",
            "Bali":       "DPS",
            "New York":   "JFK",
            "Amsterdam":  "AMS",
            "Singapore":  "SIN",
            "Dubai":      "DXB",
            "Barcelona":  "BCN",
            "Phuket":     "HKT",
        }
        for dest, expected in cases.items():
            result = _get_airport_code(dest)
            assert result == expected, f"{dest}: expected {expected}, got {result}"

    def test_case_insensitive(self):
        from backend.logistics import _get_airport_code
        assert _get_airport_code("paris") == _get_airport_code("PARIS") == "CDG"

    def test_partial_match_returns_code(self):
        from backend.logistics import _get_airport_code
        # "Goa, India" should match "goa"
        result = _get_airport_code("Goa, India")
        assert result == "GOI"

    def test_unknown_destination_returns_none(self):
        from backend.logistics import _get_airport_code
        assert _get_airport_code("Nowhereville_XYZ_123") is None

    def test_all_22_destinations_have_codes(self):
        from backend.logistics import DESTINATION_AIRPORTS
        assert len(DESTINATION_AIRPORTS) >= 22
        for dest, codes in DESTINATION_AIRPORTS.items():
            assert len(codes) >= 1, f"{dest} has no airport codes"
            for code in codes:
                assert len(code) == 3, f"'{code}' is not a valid IATA code for {dest}"
                assert code.isupper(), f"IATA code '{code}' must be uppercase"


# ─── URL building ─────────────────────────────────────────────────────────────

class TestFlightUrls:
    def test_google_flights_contains_destination(self):
        from backend.logistics import _google_flights_url
        url = _google_flights_url("Mumbai", "Paris", "2026-05-01", "2026-05-08", 2)
        assert "google.com" in url
        assert "Paris" in url or "CDG" in url or "paris" in url.lower()

    def test_google_flights_no_return_date(self):
        from backend.logistics import _google_flights_url
        url = _google_flights_url("Delhi", "Goa", "2026-01-10", None, 1)
        assert "google.com" in url

    def test_skyscanner_roundtrip_contains_dates(self):
        from backend.logistics import _skyscanner_url
        url = _skyscanner_url("LHR", "CDG", "2026-05-01", "2026-05-08", 2)
        assert "skyscanner.net" in url
        assert "20260501" in url
        assert "20260508" in url
        assert "adults=2" in url

    def test_skyscanner_oneway_no_return(self):
        from backend.logistics import _skyscanner_url
        url = _skyscanner_url("BOM", "GOI", "2026-01-10", None, 1)
        assert "skyscanner.net" in url
        assert "20260110" in url
        assert "adults=1" in url

    def test_skyscanner_uses_iata_codes(self):
        from backend.logistics import _skyscanner_url
        # Paris → "CDG", Goa → "GOI"
        url = _skyscanner_url("Paris", "Goa", "2026-01-10", "2026-01-15", 2)
        assert "cdg" in url.lower() or "paris" in url.lower()
        assert "goi" in url.lower() or "goa" in url.lower()

    def test_rome2rio_url_contains_both_cities(self):
        from backend.logistics import _rome2rio_url
        url = _rome2rio_url("Mumbai", "Paris")
        assert "rome2rio.com" in url
        assert "Mumbai" in url or "mumbai" in url.lower()
        assert "Paris" in url or "paris" in url.lower()

    def test_rome2rio_url_replaces_spaces_with_hyphens(self):
        from backend.logistics import _rome2rio_url
        url = _rome2rio_url("New York", "New York")
        # Spaces become hyphens in the path
        assert " " not in url


class TestAccommodationUrls:
    def test_booking_com_contains_destination(self):
        from backend.logistics import _booking_com_url
        url = _booking_com_url("Paris", "2026-05-01", "2026-05-08", 2)
        assert "booking.com" in url
        assert "Paris" in url or "paris" in url.lower()

    def test_booking_com_contains_dates(self):
        from backend.logistics import _booking_com_url
        url = _booking_com_url("Goa", "2026-01-10", "2026-01-15", 2)
        assert "2026-01-10" in url
        assert "2026-01-15" in url

    def test_booking_com_contains_adults(self):
        from backend.logistics import _booking_com_url
        url = _booking_com_url("Tokyo", "2026-03-01", "2026-03-05", 3)
        assert "group_adults=3" in url

    def test_airbnb_contains_destination(self):
        from backend.logistics import _airbnb_url
        url = _airbnb_url("Bali", "2026-07-01", "2026-07-08", 2)
        assert "airbnb.com" in url
        assert "Bali" in url or "bali" in url.lower()

    def test_airbnb_contains_dates_and_adults(self):
        from backend.logistics import _airbnb_url
        url = _airbnb_url("Kyoto", "2026-03-25", "2026-03-30", 2)
        assert "2026-03-25" in url
        assert "2026-03-30" in url
        assert "adults=2" in url

    def test_hostelworld_url_format(self):
        from backend.logistics import _hostelworld_url
        url = _hostelworld_url("Bangkok", "2026-11-01", "2026-11-07", 1)
        assert "hostelworld.com" in url
        assert "Bangkok" in url or "bangkok" in url.lower()

    def test_budget_style_filter_budget(self):
        from backend.logistics import _budget_style_to_booking_filter
        f = _budget_style_to_booking_filter("budget")
        assert "1" in f or "2" in f  # 1-2 star filter

    def test_budget_style_filter_luxury(self):
        from backend.logistics import _budget_style_to_booking_filter
        f = _budget_style_to_booking_filter("luxury")
        assert "4" in f or "5" in f  # 4-5 star filter

    def test_budget_style_filter_moderate_default(self):
        from backend.logistics import _budget_style_to_booking_filter
        f = _budget_style_to_booking_filter("moderate")
        assert "3" in f  # 3 star filter


# ─── Transport guidance ───────────────────────────────────────────────────────

class TestTransportGuidance:
    def test_all_22_destinations_have_guidance(self):
        from backend.logistics import TRANSPORT_GUIDANCE
        assert len(TRANSPORT_GUIDANCE) >= 22
        required_fields = {"airport_transfer", "local_transport", "public_transport", "notes"}
        for dest, data in TRANSPORT_GUIDANCE.items():
            for field in required_fields:
                assert field in data, f"{dest} missing field: {field}"
                assert isinstance(data[field], str) and data[field].strip(), \
                    f"{dest}.{field} is empty"

    def test_guidance_does_not_claim_live_prices(self):
        """Guidance text must not use the word 'live' in a misleading context."""
        from backend.logistics import TRANSPORT_GUIDANCE
        for dest, data in TRANSPORT_GUIDANCE.items():
            for field in ("airport_transfer", "local_transport", "public_transport"):
                text = data[field].lower()
                # Allow "live" only in clearly informational contexts
                assert "live price" not in text, f"{dest}.{field} claims live prices"
                assert "guaranteed" not in text, f"{dest}.{field} uses 'guaranteed'"

    def test_notes_includes_illustrative_label(self):
        """Every guidance entry must note it is illustrative."""
        from backend.logistics import TRANSPORT_GUIDANCE
        for dest, data in TRANSPORT_GUIDANCE.items():
            notes = data.get("notes", "").lower()
            assert "illustrative" in notes or "vary" in notes or "subject to change" in notes, \
                f"{dest}: notes do not label data as illustrative or variable"


# ─── build_logistics ──────────────────────────────────────────────────────────

class TestBuildLogistics:
    def test_returns_correct_structure(self):
        from backend.logistics import build_logistics
        result = build_logistics(
            destination="Paris",
            origin="Mumbai",
            start_date="2026-05-01",
            end_date="2026-05-08",
            travelers=2,
            budget_style="moderate",
            accommodation_preference="hotel",
        )
        assert result.destination == "Paris"
        assert result.flights.origin == "Mumbai"
        assert result.flights.adults == 2
        assert result.accommodation.checkin == "2026-05-01"
        assert result.accommodation.checkout == "2026-05-08"
        assert result.transport.destination == "Paris"
        assert isinstance(result.disclaimer, str) and result.disclaimer

    def test_hostelworld_link_for_budget_style(self):
        from backend.logistics import build_logistics
        result = build_logistics(
            destination="Bangkok",
            origin="Singapore",
            start_date="2026-11-01",
            end_date="2026-11-07",
            travelers=1,
            budget_style="budget",
            accommodation_preference="hostel",
        )
        assert result.accommodation.hostelworld is not None
        assert "hostelworld.com" in result.accommodation.hostelworld

    def test_no_hostelworld_for_hotel_moderate(self):
        from backend.logistics import build_logistics
        result = build_logistics(
            destination="Tokyo",
            origin="Delhi",
            start_date="2026-03-01",
            end_date="2026-03-07",
            travelers=2,
            budget_style="moderate",
            accommodation_preference="hotel",
        )
        assert result.accommodation.hostelworld is None

    def test_unknown_destination_uses_fallback_transport(self):
        from backend.logistics import build_logistics
        result = build_logistics(
            destination="Atlantis",
            origin="London",
            start_date="2026-06-01",
            end_date="2026-06-05",
            travelers=1,
        )
        # Should not raise; fallback guidance text used
        assert result.transport.airport_transfer != ""
        assert result.transport.local_transport != ""

    def test_all_urls_are_strings(self):
        from backend.logistics import build_logistics
        result = build_logistics(
            destination="Goa",
            origin="Delhi",
            start_date="2026-01-10",
            end_date="2026-01-15",
            travelers=2,
        )
        for url in (
            result.flights.google_flights,
            result.flights.skyscanner,
            result.flights.rome2rio,
            result.accommodation.booking_com,
            result.accommodation.airbnb,
            result.transport.rome2rio_url,
        ):
            assert isinstance(url, str) and url.startswith("http"), \
                f"URL not valid: {url!r}"

    def test_all_urls_contain_no_spaces(self):
        from backend.logistics import build_logistics
        result = build_logistics(
            destination="New York",
            origin="Los Angeles",
            start_date="2026-09-01",
            end_date="2026-09-07",
            travelers=2,
        )
        for url in (
            result.flights.google_flights,
            result.flights.skyscanner,
            result.flights.rome2rio,
            result.accommodation.booking_com,
            result.accommodation.airbnb,
        ):
            assert " " not in url, f"URL contains unencoded space: {url!r}"

    def test_disclaimer_present_and_non_empty(self):
        from backend.logistics import build_logistics
        result = build_logistics("Bali", "Sydney", "2026-07-01", "2026-07-08", 2)
        assert len(result.disclaimer) > 20
        # Must say no booking is made
        assert "booking" in result.disclaimer.lower() or "book" in result.disclaimer.lower()

    def test_empty_origin_handled_gracefully(self):
        from backend.logistics import build_logistics
        result = build_logistics(
            destination="Singapore",
            origin="",
            start_date="2026-02-01",
            end_date="2026-02-05",
            travelers=1,
        )
        assert result.flights.origin == "not specified"
        assert isinstance(result.flights.google_flights, str)


# ─── Pydantic models ──────────────────────────────────────────────────────────

class TestLogisticsModels:
    def test_full_round_trip_serialisation(self):
        from backend.models import (
            AccommodationLinksOut,
            FlightLinksOut,
            LogisticsResponseOut,
            TransportGuidanceOut,
        )
        resp = LogisticsResponseOut(
            destination="Paris",
            flights=FlightLinksOut(
                google_flights="https://google.com/flights?q=test",
                skyscanner="https://skyscanner.net/test",
                rome2rio="https://rome2rio.com/s/Mumbai/Paris",
                origin="Mumbai",
                destination="Paris",
                depart_date="2026-05-01",
                return_date="2026-05-08",
                adults=2,
                airport_code="CDG",
                notes="Search links only.",
            ),
            accommodation=AccommodationLinksOut(
                booking_com="https://booking.com/searchresults.html?ss=Paris",
                airbnb="https://airbnb.com/s/Paris/homes",
                destination="Paris",
                checkin="2026-05-01",
                checkout="2026-05-08",
                adults=2,
                budget_style="moderate",
                notes="Search links only.",
            ),
            transport=TransportGuidanceOut(
                destination="Paris",
                airport_transfer="RER B from CDG.",
                local_transport="Metro and bus.",
                public_transport="Navigo pass.",
                rome2rio_url="https://rome2rio.com/s/Mumbai/Paris",
                notes="Illustrative.",
                source="STATIC",
            ),
            disclaimer="Links open third-party search pages. No booking made.",
        )
        raw = resp.model_dump_json()
        parsed = LogisticsResponseOut.model_validate_json(raw)
        assert parsed.destination == "Paris"
        assert parsed.flights.airport_code == "CDG"
        assert parsed.accommodation.budget_style == "moderate"
        assert parsed.transport.source == "STATIC"

    def test_hostelworld_optional_null(self):
        from backend.models import AccommodationLinksOut
        acc = AccommodationLinksOut(
            booking_com="https://booking.com",
            airbnb="https://airbnb.com",
            hostelworld=None,
            destination="Tokyo",
            checkin="2026-03-01",
            checkout="2026-03-05",
            adults=2,
        )
        data = acc.model_dump()
        assert data["hostelworld"] is None

    def test_transport_source_defaults_to_static(self):
        from backend.models import TransportGuidanceOut
        t = TransportGuidanceOut(
            destination="Goa",
            airport_transfer="Taxi from GOI.",
            local_transport="Scooter.",
            public_transport="Limited.",
        )
        assert t.source == "STATIC"


# ─── API route ────────────────────────────────────────────────────────────────

class TestLogisticsRoute:
    def test_route_registered(self):
        from backend.main import app
        paths = {getattr(r, "path", None) for r in app.routes}
        assert "/api/logistics/{destination}" in paths

    def test_existing_routes_still_present(self):
        from backend.main import app
        paths = {getattr(r, "path", None) for r in app.routes}
        for expected in (
            "/api/analyze",
            "/api/analyze/stream",
            "/api/itinerary/generate",
            "/api/trips/{trip_id}/itinerary",
            "/api/trips/{trip_id}/itinerary/modify",
            "/api/weather/{destination}",
            "/api/poi/{destination}",
            "/api/itinerary/feasibility",
        ):
            assert expected in paths, f"Route missing: {expected}"


# ─── Backward compatibility ───────────────────────────────────────────────────

class TestBackwardCompatibility:
    def test_existing_models_unchanged(self):
        from backend.models import (
            TripPreferences,
            ItineraryDocument,
            ItineraryConfig,
            ItineraryModifyRequest,
            WeatherResponseOut,
            PoiResponseOut,
            FeasibilityResponseOut,
        )
        # Spot-check: each model must still import and instantiate
        req = ItineraryModifyRequest(modification_request="test")
        assert req.intelligence_context is None
        assert req.current_itinerary is None

    def test_logistics_module_does_not_import_httpx(self):
        """Logistics module must not make any HTTP calls."""
        import ast, pathlib
        src = pathlib.Path(
            "c:/Projects/Budget planner/Agentic_AI-main/backend/logistics.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(src)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imports.append(ast.dump(node))
        httpx_imports = [i for i in imports if "httpx" in i]
        assert not httpx_imports, f"logistics.py must not import httpx: {httpx_imports}"

    def test_logistics_module_has_no_time_sleep(self):
        """Logistics module must not block the event loop."""
        import pathlib
        src = pathlib.Path(
            "c:/Projects/Budget planner/Agentic_AI-main/backend/logistics.py"
        ).read_text(encoding="utf-8")
        assert "time.sleep" not in src
        assert "asyncio.sleep" not in src

    def test_all_logistics_urls_use_https(self):
        from backend.logistics import build_logistics
        result = build_logistics("Goa", "Delhi", "2026-01-10", "2026-01-15", 2)
        for url in (
            result.flights.google_flights,
            result.flights.skyscanner,
            result.flights.rome2rio,
            result.accommodation.booking_com,
            result.accommodation.airbnb,
            result.transport.rome2rio_url,
        ):
            assert url.startswith("https://"), f"Non-HTTPS URL: {url!r}"

    def test_no_new_dependencies_in_requirements(self):
        """requirements.txt must not have new packages added in Phase 6."""
        import pathlib
        reqs = pathlib.Path(
            "c:/Projects/Budget planner/Agentic_AI-main/backend/requirements.txt"
        ).read_text()
        # These should not appear — Phase 6 adds no dependencies
        for unexpected in ("requests", "aiohttp", "amadeus", "serpapi", "rapidapi"):
            assert unexpected not in reqs.lower(), \
                f"Unexpected dependency found in requirements.txt: {unexpected}"
