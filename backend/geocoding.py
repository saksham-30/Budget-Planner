"""
Geocoding service using Nominatim (OpenStreetMap).

No API key required. Attribution: © OpenStreetMap contributors.
Rate limit: 1 request/second per Nominatim policy.
Caches results in-process (dict) for the application lifetime to avoid
repeated calls for the same destination.

Fallback: bundled coordinate table for the 22 destinations in the app.
Coordinates are approximate city-centre points, accurate to ~0.5 km.
"""

from __future__ import annotations

import logging
import math
import re
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Nominatim endpoint — no key required; User-Agent identifies the app.
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "TravelBudgetPlanner/1.0 (educational demo; github.com/saksham-30/Trip-Planner)"
_TIMEOUT_SECONDS = 6.0
_LAST_CALL_TIME: float = 0.0  # module-level rate-limit guard

# In-process cache: destination_name (casefold) → (lat, lon) | None
_CACHE: dict[str, tuple[float, float] | None] = {}

# Bundled fallback coordinates — approximate city centres.
# These are used when Nominatim is unavailable or rate-limited.
# Source: Wikidata / OpenStreetMap — public domain.
FALLBACK_COORDS: dict[str, tuple[float, float]] = {
    "goa":          (15.2993, 74.1240),
    "paris":        (48.8566,  2.3522),
    "tokyo":        (35.6762, 139.6503),
    "bali":         (-8.3405, 115.0920),
    "manali":       (32.2432,  77.1892),
    "new york":     (40.7128, -74.0060),
    "new york city":(40.7128, -74.0060),
    "rome":         (41.9028,  12.4964),
    "bangkok":      (13.7563, 100.5018),
    "santorini":    (36.3932,  25.4615),
    "amsterdam":    (52.3676,   4.9041),
    "maldives":     ( 3.2028,  73.2207),
    "dubai":        (25.2048,  55.2708),
    "kerala":       ( 9.9312,  76.2673),
    "prague":       (50.0755,  14.4378),
    "singapore":    ( 1.3521, 103.8198),
    "barcelona":    (41.3851,   2.1734),
    "kyoto":        (35.0116, 135.7681),
    "istanbul":     (41.0082,  28.9784),
    "jaipur":       (26.9124,  75.7873),
    "queenstown":   (-45.0312, 168.6626),
    "marrakech":    (31.6295,  -7.9811),
    "phuket":       ( 7.8804,  98.3923),
    # Common aliases
    "bali, indonesia":   (-8.3405, 115.0920),
    "goa, india":        (15.2993, 74.1240),
    "kerala, india":     ( 9.9312,  76.2673),
    "jaipur, india":     (26.9124,  75.7873),
    "manali, india":     (32.2432,  77.1892),
}


def _normalise(name: str) -> str:
    """Lowercase and strip punctuation for cache/fallback lookup."""
    return re.sub(r"[,.]", " ", name.strip().casefold()).split("  ")[0].strip()


def _normalise_exact(name: str) -> str:
    return re.sub(r"[^\w]+", " ", name.strip().casefold(), flags=re.UNICODE).strip()


def _known_destination_center(destination: str) -> tuple[float, float] | None:
    """Use the bundled centre only for exact curated destination names/aliases."""
    query = _normalise_exact(destination)
    if not query:
        return None
    return next(
        (coords for name, coords in FALLBACK_COORDS.items() if _normalise_exact(name) == query),
        None,
    )


def _strict_nominatim_match(destination: str, results: list[dict]) -> tuple[float, float] | None:
    """Return a reliable, unambiguous place match; never guess a result."""
    requested_words = re.findall(r"[^\W_]+", destination.casefold(), flags=re.UNICODE)
    requested_tokens = set(requested_words)

    def ordered_subsequence(needle: list[str], haystack: list[str]) -> bool:
        cursor = iter(haystack)
        return all(any(word == candidate for candidate in cursor) for word in needle)

    allowed_types = {
        "city", "town", "village", "hamlet", "municipality", "island",
        "administrative", "state", "region", "county", "suburb",
    }
    candidates: list[tuple[tuple[int, int], str, float, float]] = []

    for item in results:
        feature_class = item.get("class") or item.get("category")
        if feature_class not in {"place", "boundary"} or item.get("type") not in allowed_types:
            continue
        name = str(item.get("name") or "")
        display = str(item.get("display_name") or "")
        display_words = re.findall(r"[^\W_]+", display.casefold(), flags=re.UNICODE)
        if not requested_tokens or not ordered_subsequence(requested_words, display_words):
            continue

        try:
            lat, lon = float(item["lat"]), float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            continue

        name_words = re.findall(r"[^\W_]+", name.casefold(), flags=re.UNICODE)
        exact_name = int(name_words == requested_words)
        matching_name = int(bool(name_words) and ordered_subsequence(name_words, requested_words))
        name_quality = 3 if exact_name else 2 if matching_name else 1
        place_quality = int(feature_class == "place") + int(item.get("type") in {"city", "town", "village", "island"})
        candidates.append(((name_quality, place_quality), name.casefold(), lat, lon))

    if not candidates:
        return None

    exact_candidates = [item for item in candidates if item[0][0] == 3]
    if exact_candidates:
        exact_anchor = exact_candidates[0]
        if any(_distance_km(exact_anchor[2], exact_anchor[3], item[2], item[3]) > 2 for item in exact_candidates[1:]):
            return None

    best_score = max(item[0] for item in candidates)
    best = [item for item in candidates if item[0] == best_score]
    # Identical or near-identical representations of one named place are safe;
    # separate locations with the same name are ambiguous and must be rejected.
    anchor = best[0]
    if any(_distance_km(anchor[2], anchor[3], item[2], item[3]) > 2 for item in best[1:]):
        return None
    return anchor[2], anchor[3]


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    value = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 6371 * 2 * asin(sqrt(min(1.0, value)))


def geocode(
    destination: str,
    timeout: float = _TIMEOUT_SECONDS,
    *,
    strict: bool = False,
) -> tuple[float, float] | None:
    """
    Return (latitude, longitude) for a destination name, or None on failure.

    Resolution order:
      1. In-process cache
      2. Bundled approximate centre (default mode only)
      3. Nominatim live query (rate-limited to 1 req/s)

    Strict mode accepts an exact curated destination centre for known planner
    destinations. Other names require an unambiguous matching Nominatim result.
    The centre is used only for the map/query area; POIs always retain their
    own Overpass coordinates or remain unmapped.
    """
    global _LAST_CALL_TIME

    key = _normalise(destination)
    cache_key = f"strict:{key}" if strict else key

    # 1. Cache hit
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    if strict:
        known_center = _known_destination_center(destination)
        if known_center is not None:
            _CACHE[cache_key] = known_center
            return known_center

    # 2. Fallback table
    if not strict and key in FALLBACK_COORDS:
        result = FALLBACK_COORDS[key]
        _CACHE[key] = result
        return result

    # 3. Live Nominatim (honour rate limit — only applies here, not for cached/fallback paths)
    elapsed = time.monotonic() - _LAST_CALL_TIME
    if elapsed < 1.1:
        # Only sleep the needed remainder; skip entirely if already enough time has passed
        wait = 1.1 - elapsed
        if wait > 0:
            time.sleep(wait)

    try:
        response = httpx.get(
            NOMINATIM_URL,
            params={
                "q": destination,
                "format": "jsonv2" if strict else "json",
                "limit": 10 if strict else 1,
                **({"addressdetails": 1} if strict else {}),
            },
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout,
        )
        _LAST_CALL_TIME = time.monotonic()
        response.raise_for_status()
        results = response.json()
        if results:
            if strict:
                resolved = _strict_nominatim_match(destination, results)
                _CACHE[cache_key] = resolved
                return resolved
            lat = float(results[0]["lat"])
            lon = float(results[0]["lon"])
            _CACHE[cache_key] = (lat, lon)
            return lat, lon
    except Exception as exc:
        logger.warning(
            "Nominatim geocoding failed for %r (error_type=%s)",
            destination,
            type(exc).__name__,
        )

    _CACHE[cache_key] = None
    return None


def clear_cache() -> None:
    """Clear the in-process geocoding cache (useful in tests)."""
    _CACHE.clear()
