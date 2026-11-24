"""
POI (Point of Interest) retrieval using the Overpass API (OpenStreetMap).

No API key required.
Attribution: Data © OpenStreetMap contributors, ODbL 1.0. openstreetmap.org/copyright
Rate limits: Overpass public instance — no strict per-IP limit but courtesy
throttle applied (1 request per destination, cached for 24h).

Fallback: returns the static attraction list from data_helpers.py when
Overpass is unavailable, rate-limited, or the location is unknown.
"""

from __future__ import annotations

import logging
from math import asin, cos, radians, sin, sqrt
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from .geocoding import geocode
from .data_helpers import destination_attractions

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_TIMEOUT = 10.0
_RADIUS_METERS = 10_000     # 10 km radius around city centre
_MAX_RESULTS = 30           # cap to avoid massive responses

# Cache: destination_name → (fetched_at_monotonic, [PoiResult])
_POI_CACHE: dict[str, tuple[float, list]] = {}
_POI_TTL = 24 * 3600        # 24 hours

# OSM amenity/tourism/leisure tags to query
_TAGS = [
    'tourism~"attraction|museum|gallery|viewpoint|theme_park|zoo|aquarium|monument|artwork"',
    'amenity~"place_of_worship|arts_centre|theatre|cinema"',
    'historic~"castle|monument|ruins|archaeological_site|memorial|fort"',
    'leisure~"park|nature_reserve|garden"',
]


@dataclass
class PoiResult:
    name: str
    category: str           # e.g. tourism, historic, leisure
    osm_type: str           # node | way | relation
    osm_id: int
    latitude: float | None
    longitude: float | None
    source: str = "OSM"     # always OSM for Overpass results
    wikipedia: str | None = None
    website: str | None = None
    description: str | None = None


@dataclass
class PoiResponse:
    destination: str
    latitude: float | None
    longitude: float | None
    pois: list[PoiResult]
    source: str             # OSM | STATIC_FALLBACK | UNAVAILABLE
    attribution: str
    fetched_at: str | None
    notes: str


def _build_overpass_query(lat: float, lon: float, radius: int = _RADIUS_METERS) -> str:
    tag_filters = "\n  ".join(
        f'nwr(around:{radius},{lat},{lon})[{tag}]["name"];'
        for tag in _TAGS
    )
    return f"""[out:json][timeout:25];
(
  {tag_filters}
);
out center {_MAX_RESULTS};"""


def _distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    value = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 6_371_000 * 2 * asin(sqrt(min(1.0, value)))


def _parse_overpass(destination: str, lat: float, lon: float, data: dict) -> list[PoiResult]:
    seen_elements: set[tuple[str, int]] = set()
    results: list[PoiResult] = []
    for elem in data.get("elements", []):
        tags = elem.get("tags", {})
        name = tags.get("name") or tags.get("name:en") or ""
        element_id = (str(elem.get("type", "node")), int(elem.get("id", 0)))
        if not name or element_id in seen_elements:
            continue
        seen_elements.add(element_id)

        # Determine category
        if "tourism" in tags:
            category = f"tourism:{tags['tourism']}"
        elif "historic" in tags:
            category = f"historic:{tags['historic']}"
        elif "leisure" in tags:
            category = f"leisure:{tags['leisure']}"
        elif "amenity" in tags:
            category = f"amenity:{tags['amenity']}"
        else:
            category = "other"

        # Coordinates — nodes have lat/lon; ways/relations have center
        node_lat = elem.get("lat")
        node_lon = elem.get("lon")
        if node_lat is None or node_lon is None:
            center = elem.get("center") or {}
            node_lat = center.get("lat") if node_lat is None else node_lat
            node_lon = center.get("lon") if node_lon is None else node_lon
        try:
            poi_lat = float(node_lat) if node_lat is not None else None
            poi_lon = float(node_lon) if node_lon is not None else None
        except (TypeError, ValueError):
            poi_lat = poi_lon = None
        if (
            poi_lat is None
            or poi_lon is None
            or not -90 <= poi_lat <= 90
            or not -180 <= poi_lon <= 180
        ):
            poi_lat = poi_lon = None
        elif _distance_meters(lat, lon, poi_lat, poi_lon) > _RADIUS_METERS:
            # Overpass can return a large way whose bounding-box centre is
            # well outside the search radius. Keep its name but don't place a
            # misleading marker far from the destination.
            poi_lat = poi_lon = None

        results.append(PoiResult(
            name=name,
            category=category,
            osm_type=elem.get("type", "node"),
            osm_id=int(elem.get("id", 0)),
            latitude=poi_lat,
            longitude=poi_lon,
            wikipedia=tags.get("wikipedia"),
            website=tags.get("website") or tags.get("contact:website"),
            description=tags.get("description") or tags.get("description:en"),
        ))
        if len(results) >= _MAX_RESULTS:
            break
    return results


def _static_fallback(destination: str, lat: float | None, lon: float | None) -> PoiResponse:
    """Return POIs from the bundled attraction list when Overpass is unavailable."""
    names = destination_attractions(destination)
    pois = [
        PoiResult(
            name=n,
            category="static_attraction",
            osm_type="node",
            osm_id=0,
            # Names in the guide have no per-attraction coordinates. Never
            # copy the destination centre onto them as if it were their pin.
            latitude=None,
            longitude=None,
            source="STATIC_FALLBACK",
        )
        for n in names
    ]
    return PoiResponse(
        destination=destination,
        latitude=lat,
        longitude=lon,
        pois=pois,
        source="STATIC_FALLBACK",
        attribution="Bundled attraction names (not from OpenStreetMap).",
        fetched_at=None,
        notes="Live POI data unavailable. Showing bundled attraction list.",
    )


def get_pois(
    destination: str,
    timeout: float = _TIMEOUT,
) -> PoiResponse:
    """
    Return up to MAX_RESULTS POIs near the destination from OpenStreetMap.

    Falls back to the static attraction list if the destination cannot be
    geocoded or Overpass is unavailable.
    """
    from datetime import datetime, timezone

    cache_key = destination.strip().casefold()
    if cache_key in _POI_CACHE:
        cached_at, cached_resp = _POI_CACHE[cache_key]
        if time.monotonic() - cached_at < _POI_TTL:
            return cached_resp

    # POI searches need a verified place result. The general geocoder's
    # approximate bundled centres are intentionally not accepted here.
    coords = geocode(destination, timeout=timeout, strict=True)
    if coords is None:
        resp = _static_fallback(destination, None, None)
        resp = PoiResponse(**{**resp.__dict__,
            "notes": f"Coordinates for {destination!r} could not be resolved. "
                     "Showing bundled attraction list."})
        return resp

    lat, lon = coords

    try:
        query = _build_overpass_query(lat, lon)
        with httpx.Client(timeout=timeout) as client:
            response = client.post(OVERPASS_URL, data={"data": query})
        response.raise_for_status()
        data = response.json()
        pois = _parse_overpass(destination, lat, lon, data)

        if not pois:
            # Overpass returned nothing; use static fallback
            result = _static_fallback(destination, lat, lon)
        else:
            result = PoiResponse(
                destination=destination,
                latitude=lat,
                longitude=lon,
                pois=pois,
                source="OSM",
                attribution="Data © OpenStreetMap contributors, ODbL 1.0. openstreetmap.org/copyright",
                fetched_at=datetime.now(timezone.utc).isoformat(),
                notes="",
            )

        _POI_CACHE[cache_key] = (time.monotonic(), result)
        return result

    except Exception as exc:
        logger.warning(
            "Overpass POI query failed for %r (error_type=%s)",
            destination,
            type(exc).__name__,
        )
        resp = _static_fallback(destination, lat, lon)
        resp = PoiResponse(**{**resp.__dict__,
            "notes": "Live POI data unavailable. Showing bundled attraction list."})
        return resp


def clear_cache() -> None:
    """Clear POI cache (for tests)."""
    _POI_CACHE.clear()
