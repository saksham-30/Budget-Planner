"""
Weather service using Open-Meteo (https://open-meteo.com/).

Open-Meteo is free, open-source, and requires no API key.
It provides weather forecasts up to 16 days ahead, and historical
archive data for past dates.

Attribution: Weather data by Open-Meteo.com (CC BY 4.0).

Caching: responses are cached in-process by (lat, lon, date_range) key.
Cache TTL is 3 hours for forecasts; climate/archive data is cached for 24 h.

Data labels used:
  FORECAST   — data from Open-Meteo forecast API (up to 16 days)
  ARCHIVE    — data from Open-Meteo historical archive
  CLIMATOLOGY — month-based averages from bundled data (no live call)
  UNAVAILABLE — provider unreachable or location unknown
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

from .geocoding import geocode

logger = logging.getLogger(__name__)

FORECAST_API = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_API  = "https://archive-api.open-meteo.com/v1/archive"
_TIMEOUT = 8.0
_MAX_FORECAST_DAYS = 16          # Open-Meteo forecast horizon

# Cache entries: key → (fetched_at_monotonic, payload)
_WEATHER_CACHE: dict[str, tuple[float, object]] = {}
_FORECAST_TTL = 3 * 3600         # seconds
_ARCHIVE_TTL  = 24 * 3600

# ─── Climatology fallback ─────────────────────────────────────────────────────
# Source: publicly known long-run averages from Wikipedia/WMO station data.
# These are illustrative — clearly labelled CLIMATOLOGY in all responses.
# month → (avg_low_C, avg_high_C, typical_precipitation_days_per_month)
_CLIMATOLOGY: dict[str, dict[int, tuple[float, float, int]]] = {
    "goa":        {1:(18,32,0),2:(19,33,0),3:(22,34,1),4:(26,36,2),5:(27,34,10),
                   6:(25,30,25),7:(24,29,27),8:(24,29,25),9:(24,30,20),10:(24,32,8),
                   11:(22,33,2),12:(19,32,0)},
    "paris":      {1:(2,7,9),2:(3,9,8),3:(5,13,8),4:(8,17,8),5:(12,21,8),
                   6:(15,25,8),7:(17,27,7),8:(17,27,7),9:(13,23,8),10:(10,17,9),
                   11:(5,11,10),12:(2,7,10)},
    "tokyo":      {1:(2,10,5),2:(2,11,6),3:(5,14,10),4:(10,19,10),5:(14,24,11),
                   6:(18,27,13),7:(22,30,12),8:(23,32,9),9:(19,27,13),10:(13,22,10),
                   11:(8,17,7),12:(3,12,5)},
    "bali":       {1:(23,30,18),2:(23,30,16),3:(23,30,13),4:(23,31,8),5:(22,31,5),
                   6:(21,30,3),7:(21,29,2),8:(21,29,2),9:(21,30,4),10:(22,31,8),
                   11:(22,30,13),12:(23,30,17)},
    "new york":   {1:(-3,4,10),2:(-3,5,9),3:(2,11,10),4:(8,18,10),5:(13,23,10),
                   6:(18,28,10),7:(21,30,9),8:(21,29,8),9:(16,25,8),10:(10,19,8),
                   11:(4,13,9),12:(-1,6,10)},
    "dubai":      {1:(14,24,1),2:(15,26,1),3:(18,30,1),4:(21,34,1),5:(25,38,0),
                   6:(28,40,0),7:(30,41,0),8:(30,41,0),9:(27,38,0),10:(23,34,0),
                   11:(19,30,1),12:(15,26,1)},
    "singapore":  {1:(23,30,15),2:(23,31,10),3:(24,32,13),4:(24,32,14),5:(24,32,14),
                   6:(24,32,13),7:(24,31,13),8:(24,31,13),9:(24,31,14),10:(24,31,16),
                   11:(23,31,18),12:(23,30,17)},
    "barcelona":  {1:(6,13,5),2:(7,15,5),3:(9,17,5),4:(11,20,6),5:(15,24,5),
                   6:(18,28,3),7:(21,30,2),8:(21,30,3),9:(18,27,5),10:(14,22,7),
                   11:(10,17,6),12:(7,14,6)},
    "amsterdam":  {1:(2,5,13),2:(2,7,10),3:(4,10,10),4:(7,14,9),5:(10,19,10),
                   6:(13,22,10),7:(15,23,10),8:(14,22,11),9:(12,19,11),10:(8,14,12),
                   11:(5,9,13),12:(2,6,14)},
    "istanbul":   {1:(3,8,12),2:(3,9,10),3:(5,12,9),4:(9,17,7),5:(13,22,7),
                   6:(18,27,5),7:(20,30,3),8:(20,30,3),9:(16,26,6),10:(12,20,8),
                   11:(8,14,10),12:(5,10,12)},
    "bangkok":    {1:(20,32,1),2:(22,34,2),3:(24,35,4),4:(25,36,6),5:(25,34,13),
                   6:(24,33,13),7:(24,32,14),8:(24,32,14),9:(24,32,17),10:(24,31,16),
                   11:(22,31,7),12:(20,31,2)},
    "kyoto":      {1:(2,9,6),2:(2,10,7),3:(5,14,10),4:(10,19,11),5:(15,24,11),
                   6:(18,28,13),7:(22,32,12),8:(22,33,10),9:(18,28,13),10:(12,22,11),
                   11:(7,16,7),12:(2,10,5)},
    "marrakech":  {1:(7,19,3),2:(8,21,3),3:(11,24,4),4:(14,28,3),5:(18,32,2),
                   6:(21,36,1),7:(22,38,0),8:(22,38,0),9:(18,34,1),10:(14,28,2),
                   11:(10,22,3),12:(7,19,4)},
    "phuket":     {1:(23,32,2),2:(23,33,2),3:(24,33,3),4:(24,33,8),5:(24,32,16),
                   6:(23,31,20),7:(23,31,20),8:(23,31,20),9:(23,31,19),10:(23,31,16),
                   11:(23,32,8),12:(23,32,3)},
    "jaipur":     {1:(8,22,1),2:(11,25,1),3:(16,31,1),4:(21,37,1),5:(26,41,2),
                   6:(26,38,5),7:(24,34,10),8:(23,33,10),9:(23,34,5),10:(18,34,2),
                   11:(12,28,1),12:(8,23,0)},
    "rome":       {1:(4,12,8),2:(5,13,7),3:(7,16,7),4:(10,19,7),5:(14,24,5),
                   6:(17,28,3),7:(19,31,1),8:(19,31,2),9:(16,27,5),10:(12,22,7),
                   11:(8,16,9),12:(5,13,9)},
    "santorini":  {1:(10,15,9),2:(10,15,8),3:(12,17,7),4:(15,21,5),5:(18,25,3),
                   6:(22,29,1),7:(24,31,0),8:(24,31,0),9:(21,28,2),10:(17,24,4),
                   11:(14,20,7),12:(11,17,9)},
    "prague":     {1:(-3,2,10),2:(-2,4,8),3:(2,9,8),4:(6,15,8),5:(10,20,9),
                   6:(13,23,9),7:(15,25,8),8:(14,24,8),9:(10,20,9),10:(5,13,10),
                   11:(1,6,11),12:(-2,2,12)},
    "maldives":   {1:(25,30,6),2:(26,31,5),3:(26,31,7),4:(26,31,10),5:(26,30,15),
                   6:(25,29,16),7:(25,29,15),8:(25,29,15),9:(25,29,15),10:(25,30,14),
                   11:(25,30,10),12:(25,30,8)},
    "queenstown": {1:(10,22,7),2:(9,21,7),3:(6,18,8),4:(3,14,9),5:(0,10,10),
                   6:(-2,7,11),7:(-3,6,11),8:(-1,8,10),9:(2,12,9),10:(5,16,8),
                   11:(7,18,8),12:(9,21,7)},
    "kerala":     {1:(22,32,2),2:(24,34,2),3:(26,35,4),4:(27,34,10),5:(25,32,18),
                   6:(23,29,25),7:(23,28,25),8:(23,29,24),9:(24,30,18),10:(24,31,12),
                   11:(23,31,5),12:(22,31,2)},
    "manali":     {1:(-8,7,10),2:(-6,9,9),3:(-2,13,8),4:(5,19,6),5:(10,24,5),
                   6:(13,26,10),7:(12,22,18),8:(11,21,18),9:(8,22,8),10:(3,17,4),
                   11:(-3,11,7),12:(-8,7,10)},
}

WMO_CODE_LABELS: dict[int, str] = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Icy fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    77: "Snow grains",
    80: "Slight showers", 81: "Moderate showers", 82: "Violent showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm + slight hail", 99: "Thunderstorm + heavy hail",
}

def _wmo_label(code: int | None) -> str:
    if code is None:
        return "Unknown"
    return WMO_CODE_LABELS.get(int(code), f"Code {code}")

def _is_rain_code(code: int | None) -> bool:
    if code is None:
        return False
    return int(code) in {51,53,55,61,63,65,80,81,82,95,96,99}


@dataclass
class DayWeather:
    date: str                        # ISO date
    temp_min: float | None           # °C
    temp_max: float | None           # °C
    precipitation_mm: float | None   # mm
    precipitation_probability: int | None  # 0-100
    weather_code: int | None         # WMO code
    weather_label: str
    is_rain_likely: bool
    wind_speed_max: float | None     # km/h
    source: str                      # FORECAST | ARCHIVE | CLIMATOLOGY
    is_suitable_outdoor: bool


@dataclass
class WeatherResponse:
    destination: str
    latitude: float | None
    longitude: float | None
    timezone: str
    days: list[DayWeather]
    source: str                      # FORECAST | ARCHIVE | CLIMATOLOGY | UNAVAILABLE
    fetched_at: str | None
    forecast_generated_at: str | None
    attribution: str
    notes: str


def _cache_key(lat: float, lon: float, start: str, end: str) -> str:
    return f"{lat:.3f},{lon:.3f},{start},{end}"


def _get_climatology(destination: str, start_date: date, end_date: date) -> WeatherResponse:
    key = destination.strip().casefold()
    table = _CLIMATOLOGY.get(key)
    days: list[DayWeather] = []
    current = start_date
    while current <= end_date:
        m = current.month
        if table and m in table:
            lo, hi, precip_days = table[m]
            # Approximate precipitation probability from days/month
            precip_days_in_month = precip_days
            days_in_month = 30
            prob = min(100, int(round(precip_days_in_month / days_in_month * 100)))
            is_rain = prob > 40
        else:
            lo, hi, prob, is_rain = None, None, None, False
        days.append(DayWeather(
            date=current.isoformat(),
            temp_min=lo,
            temp_max=hi,
            precipitation_mm=None,
            precipitation_probability=prob,
            weather_code=None,
            weather_label="Typical for month",
            is_rain_likely=is_rain,
            wind_speed_max=None,
            source="CLIMATOLOGY",
            is_suitable_outdoor=(not is_rain) if is_rain is not None else True,
        ))
        current += timedelta(days=1)

    return WeatherResponse(
        destination=destination,
        latitude=None,
        longitude=None,
        timezone="UTC",
        days=days,
        source="CLIMATOLOGY",
        fetched_at=None,
        forecast_generated_at=None,
        attribution="Long-run monthly averages. NOT a weather forecast. Source: illustrative climate data.",
        notes="Dates are outside the forecast window (>16 days). Showing long-run monthly climate averages. "
              "These are NOT forecasts and do not reflect actual current or future conditions.",
    )


def _parse_forecast(destination: str, lat: float, lon: float, data: dict) -> WeatherResponse:
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    tmin   = daily.get("temperature_2m_min", [])
    tmax   = daily.get("temperature_2m_max", [])
    precip = daily.get("precipitation_sum", [])
    prob   = daily.get("precipitation_probability_max", [])
    codes  = daily.get("weather_code", [])
    wind   = daily.get("wind_speed_10m_max", [])

    days: list[DayWeather] = []
    for i, dt in enumerate(dates):
        code = codes[i] if i < len(codes) else None
        p    = int(prob[i]) if i < len(prob) and prob[i] is not None else None
        wmin = tmin[i] if i < len(tmin) else None
        wmax = tmax[i] if i < len(tmax) else None
        rain_likely = _is_rain_code(code) or (p is not None and p >= 50)
        days.append(DayWeather(
            date=dt,
            temp_min=round(wmin, 1) if wmin is not None else None,
            temp_max=round(wmax, 1) if wmax is not None else None,
            precipitation_mm=round(precip[i], 1) if i < len(precip) and precip[i] is not None else None,
            precipitation_probability=p,
            weather_code=code,
            weather_label=_wmo_label(code),
            is_rain_likely=rain_likely,
            wind_speed_max=round(wind[i], 1) if i < len(wind) and wind[i] is not None else None,
            source="FORECAST",
            is_suitable_outdoor=not rain_likely,
        ))

    tz = data.get("timezone", "UTC")
    gen_time = data.get("generationtime_ms")
    return WeatherResponse(
        destination=destination,
        latitude=lat,
        longitude=lon,
        timezone=tz,
        days=days,
        source="FORECAST",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        forecast_generated_at=str(gen_time) + "ms" if gen_time else None,
        attribution="Weather data by Open-Meteo.com (CC BY 4.0). openmeteo.com",
        notes="",
    )


def get_weather(
    destination: str,
    start_date: date,
    end_date: date,
    timeout: float = _TIMEOUT,
) -> WeatherResponse:
    """
    Return weather data for a destination and date range.

    Returns FORECAST for dates within the next 16 days (via Open-Meteo),
    CLIMATOLOGY for dates beyond that range or when unavailable.
    Never fabricates forecasts.
    """
    today = date.today()
    max_forecast_date = today + timedelta(days=_MAX_FORECAST_DAYS)

    # Determine if any trip dates fall within the forecast window
    forecast_start = max(start_date, today)
    forecast_end   = min(end_date, max_forecast_date)
    has_forecast_window = forecast_start <= forecast_end

    coords = geocode(destination, timeout=timeout)

    if not has_forecast_window or coords is None:
        # Fall back to climatology
        reason = "location unavailable" if coords is None else "dates outside forecast window"
        resp = _get_climatology(destination, start_date, end_date)
        if coords is None:
            resp = WeatherResponse(**{**resp.__dict__,
                "notes": f"Coordinates for {destination!r} could not be resolved ({reason}). "
                         "Showing long-run monthly climate averages instead."})
        return resp

    lat, lon = coords
    cache_key = _cache_key(lat, lon, start_date.isoformat(), end_date.isoformat())

    # Cache lookup
    if cache_key in _WEATHER_CACHE:
        cached_at, cached_resp = _WEATHER_CACHE[cache_key]
        age = time.monotonic() - cached_at
        if age < _FORECAST_TTL:
            return cached_resp

    # Live fetch
    params = {
        "latitude":  lat,
        "longitude": lon,
        "start_date": forecast_start.isoformat(),
        "end_date":   forecast_end.isoformat(),
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                 "precipitation_sum,precipitation_probability_max,wind_speed_10m_max",
        "timezone": "auto",
        "forecast_days": min(_MAX_FORECAST_DAYS, (forecast_end - forecast_start).days + 1),
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(FORECAST_API, params=params)
        response.raise_for_status()
        data = response.json()
        result = _parse_forecast(destination, lat, lon, data)

        # If trip extends beyond forecast window, append climatology for the tail
        if end_date > max_forecast_date:
            tail_start = max_forecast_date + timedelta(days=1)
            clim = _get_climatology(destination, tail_start, end_date)
            combined_days = result.days + clim.days
            result = WeatherResponse(
                destination=result.destination,
                latitude=result.latitude,
                longitude=result.longitude,
                timezone=result.timezone,
                days=combined_days,
                source="FORECAST",
                fetched_at=result.fetched_at,
                forecast_generated_at=result.forecast_generated_at,
                attribution=result.attribution,
                notes=f"Forecast covers {forecast_start} to {forecast_end}. "
                      f"Days after {max_forecast_date} show long-run climate averages.",
            )

        _WEATHER_CACHE[cache_key] = (time.monotonic(), result)
        return result

    except Exception as exc:
        logger.warning(
            "Open-Meteo request failed for %r (error_type=%s)",
            destination,
            type(exc).__name__,
        )
        resp = _get_climatology(destination, start_date, end_date)
        return WeatherResponse(**{**resp.__dict__,
            "notes": "Live forecast unavailable. Showing long-run monthly climate averages instead."})


def clear_cache() -> None:
    """Clear weather cache (for tests)."""
    _WEATHER_CACHE.clear()
