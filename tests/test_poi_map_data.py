from unittest.mock import MagicMock, patch

from backend.models import PoiResultOut, PoiResponseOut
from backend.poi import _parse_overpass, clear_cache as clear_poi_cache, get_pois
from backend.geocoding import _strict_nominatim_match, clear_cache, geocode


def test_overpass_parser_keeps_optional_description():
    pois = _parse_overpass("Paris", 48.85, 2.35, {
        "elements": [{
            "type": "node",
            "id": 42,
            "lat": 48.86,
            "lon": 2.34,
            "tags": {"name": "Example Museum", "tourism": "museum", "description": "A listed museum."},
        }],
    })

    assert pois[0].description == "A listed museum."


def test_poi_api_models_expose_description_and_coordinates():
    response = PoiResponseOut(
        destination="Paris",
        latitude=48.85,
        longitude=2.35,
        pois=[PoiResultOut(
            name="Example Museum",
            category="tourism:museum",
            latitude=48.86,
            longitude=2.34,
            description="A listed museum.",
        )],
        source="OSM",
    )

    assert response.pois[0].description == "A listed museum."
    assert response.latitude == 48.85


def test_strict_geocoder_selects_a_matching_place_candidate():
    result = _strict_nominatim_match("Paris, France", [{
        "category": "place",
        "type": "city",
        "name": "Paris",
        "display_name": "Paris, Île-de-France, France",
        "lat": "48.8566",
        "lon": "2.3522",
    }])

    assert result == (48.8566, 2.3522)


def test_strict_geocoder_rejects_ambiguous_same_name_places():
    candidates = [
        {"class": "place", "type": "city", "name": "Paris", "display_name": "Paris, France", "lat": "48.8566", "lon": "2.3522"},
        {"class": "place", "type": "city", "name": "Paris", "display_name": "Paris, Texas, United States", "lat": "33.6609", "lon": "-95.5555"},
    ]

    assert _strict_nominatim_match("Paris", candidates) is None


def test_strict_geocoder_rejects_non_place_and_invalid_coordinate_results():
    candidates = [
        {"class": "tourism", "type": "attraction", "name": "Paris", "display_name": "Paris, France", "lat": "48.8566", "lon": "2.3522"},
        {"class": "place", "type": "city", "name": "Paris", "display_name": "Paris, France", "lat": "999", "lon": "2.3522"},
    ]

    assert _strict_nominatim_match("Paris", candidates) is None


def test_strict_geocode_uses_only_exact_curated_destination_centres():
    clear_cache()
    from backend.geocoding import FALLBACK_COORDS
    with patch("backend.geocoding.httpx.get") as request:
        assert geocode("New York", strict=True) == FALLBACK_COORDS["new york"]
    request.assert_not_called()
    clear_cache()


def test_strict_geocode_does_not_partially_match_curated_destination_names():
    clear_cache()
    response = MagicMock()
    response.json.return_value = [{
        "class": "place", "type": "city", "name": "Paris",
        "display_name": "Paris, France", "lat": "48.8566", "lon": "2.3522",
    }]
    response.raise_for_status = MagicMock()

    with patch("backend.geocoding.httpx.get", return_value=response) as request:
        with patch("backend.geocoding.time.sleep"):
            assert geocode("Paris, Texas", strict=True) is None

    assert request.call_args.kwargs["params"]["limit"] == 10
    clear_cache()


def test_overpass_parser_does_not_treat_invalid_or_missing_coordinates_as_locations():
    pois = _parse_overpass("Origin", 0, 0, {"elements": [
        {"type": "node", "id": 1, "lat": 0, "lon": 0, "tags": {"name": "Null Island place", "tourism": "attraction"}},
        {"type": "node", "id": 2, "lat": 91, "lon": 2, "tags": {"name": "Invalid place", "tourism": "attraction"}},
    ]})

    assert (pois[0].latitude, pois[0].longitude) == (0.0, 0.0)
    assert pois[1].latitude is None
    assert pois[1].longitude is None


def test_overpass_parser_does_not_place_way_centres_outside_search_radius():
    pois = _parse_overpass("Paris", 48.85, 2.35, {"elements": [{
        "type": "way",
        "id": 3,
        "center": {"lat": 40.78, "lon": -73.96},
        "tags": {"name": "Central Park", "leisure": "park"},
    }]})

    assert pois[0].name == "Central Park"
    assert pois[0].latitude is None
    assert pois[0].longitude is None


def test_static_fallback_never_copies_destination_centre_to_attraction_items():
    clear_poi_cache()
    with patch("backend.poi.geocode", return_value=(48.85, 2.35)):
        with patch("backend.poi.httpx.Client", side_effect=RuntimeError("offline")):
            result = get_pois("Paris")

    assert result.source == "STATIC_FALLBACK"
    assert result.latitude == 48.85
    assert result.longitude == 2.35
    assert result.pois
    assert all(poi.latitude is None and poi.longitude is None for poi in result.pois)
    clear_poi_cache()


def test_known_destination_keeps_map_centre_when_overpass_is_offline():
    from backend.geocoding import FALLBACK_COORDS

    clear_cache()
    clear_poi_cache()
    with patch("backend.poi.httpx.Client", side_effect=RuntimeError("offline")):
        result = get_pois("New York")

    assert (result.latitude, result.longitude) == FALLBACK_COORDS["new york"]
    assert result.source == "STATIC_FALLBACK"
    assert all(poi.latitude is None and poi.longitude is None for poi in result.pois)
    clear_cache()
    clear_poi_cache()
