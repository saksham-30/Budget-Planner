"""Exchange-rate service: live ECB reference rates with graceful degradation.

Resolution order is live feed → last-known cached snapshot → bundled demo table.
A failed refresh never blocks a request: the previous snapshot keeps serving and
is labelled stale, so every converted amount carries an honest provenance note.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from . import config

logger = logging.getLogger(__name__)

BASE_CURRENCY = "EUR"
QUOTED_CURRENCIES = ("INR", "USD", "GBP")
SUPPORTED_CURRENCIES = (BASE_CURRENCY, *QUOTED_CURRENCIES)
LIVE_SOURCE = "frankfurter"
DEMO_SOURCE = "demo"

# The bundled table the app shipped with. Kept verbatim so that demo-mode
# results stay reproducible (the rates are illustrative, not market-derived).
DEMO_RATES: dict[str, dict[str, float]] = {
    "EUR": {"INR": 95.0, "USD": 1.08, "GBP": 0.86},
    "USD": {"INR": 88.0, "EUR": 0.93, "GBP": 0.79},
    "INR": {"USD": 0.011, "EUR": 0.0105, "GBP": 0.0092},
    "GBP": {"INR": 108.5, "USD": 1.27, "EUR": 1.16},
}


@dataclass(frozen=True)
class RateSnapshot:
    rates: dict[str, dict[str, float]]
    source: str
    as_of: str | None
    fetched_at: str | None
    is_live: bool
    stale: bool

    @property
    def label(self) -> str:
        if not self.is_live:
            return "Bundled demonstration rates"
        if self.stale:
            return "Last-known ECB reference rates (live refresh unavailable)"
        return "Live ECB reference rates"

    @property
    def note(self) -> str:
        if not self.is_live:
            return (
                "Bundled demonstration rates, not live market data. "
                "Do not use them for real financial decisions."
            )
        if self.stale:
            return (
                f"Last-known ECB reference rates dated {self.as_of}, served from the local "
                "cache because the live feed is unavailable. Values may be out of date."
            )
        return (
            f"ECB reference rates published by Frankfurter, as of {self.as_of}. "
            "Reference rates only — bank and card rates will differ."
        )


@dataclass(frozen=True)
class RateQuote:
    source: str
    target: str
    rate: float
    source_name: str
    as_of: str | None
    is_live: bool
    stale: bool

    @property
    def description(self) -> str:
        """Phrase usable inside a sentence, e.g. 'using the live rate 1 EUR = ...'."""
        if not self.is_live:
            return (
                f"the demonstration rate 1 {self.source} = "
                f"{_format_rate(self.rate)} {self.target}"
            )
        if self.stale:
            return (
                f"the last-known rate 1 {self.source} = {_format_rate(self.rate)} "
                f"{self.target} (dated {self.as_of}; live refresh unavailable)"
            )
        return (
            f"the live rate 1 {self.source} = {_format_rate(self.rate)} "
            f"{self.target} (as of {self.as_of})"
        )


def _format_rate(rate: float) -> str:
    return f"{round(rate, 6)}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _demo_snapshot() -> RateSnapshot:
    return RateSnapshot(
        rates=DEMO_RATES,
        source=DEMO_SOURCE,
        as_of=None,
        fetched_at=None,
        is_live=False,
        stale=False,
    )


_snapshot: RateSnapshot = _demo_snapshot()
_refreshing = False


def get_snapshot() -> RateSnapshot:
    return _snapshot


def is_supported(currency: str) -> bool:
    return currency.strip().upper() in _snapshot.rates


def quote(source: str, target: str) -> RateQuote | None:
    """Rate for one currency pair, or None when it cannot be priced."""
    source_code = source.strip().upper()
    target_code = target.strip().upper()
    if source_code not in _snapshot.rates or target_code not in _snapshot.rates:
        return None
    rate = _snapshot.rates[source_code].get(target_code)
    if rate is None and source_code == target_code:
        rate = 1.0
    if rate is None or not math.isfinite(rate) or rate <= 0:
        return None
    return RateQuote(
        source=source_code,
        target=target_code,
        rate=rate,
        source_name=_snapshot.source,
        as_of=_snapshot.as_of,
        is_live=_snapshot.is_live,
        stale=_snapshot.stale,
    )


def rate_pairs() -> list[dict[str, Any]]:
    """Every ordered supported pair, for the rates panel."""
    snapshot = _snapshot
    pairs: list[dict[str, Any]] = []
    for source in SUPPORTED_CURRENCIES:
        for target in SUPPORTED_CURRENCIES:
            if source == target:
                continue
            rate = snapshot.rates.get(source, {}).get(target)
            if rate is not None and math.isfinite(rate) and rate > 0:
                pairs.append({"from": source, "to": target, "rate": rate})
    return pairs


def _has_all_pairs(rates: dict[str, dict[str, float]]) -> bool:
    return all(
        source in rates
        and all(
            target in rates[source]
            for target in SUPPORTED_CURRENCIES
            if target != source
        )
        for source in SUPPORTED_CURRENCIES
    )


def _parse_rates(payload: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Expand a EUR-based provider payload into every ordered pair."""
    quoted = payload.get("rates")
    if not isinstance(quoted, dict):
        raise ValueError("rate payload is missing a rates object")
    units = {BASE_CURRENCY: 1.0}
    for currency in QUOTED_CURRENCIES:
        value = quoted.get(currency)
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"rate payload is missing {currency}")
        if float(value) <= 0:
            raise ValueError(f"rate payload has a non-positive {currency}")
        units[currency] = float(value)
    return {
        source: {
            target: round(units[target] / units[source], 8)
            for target in SUPPORTED_CURRENCIES
            if target != source
        }
        for source in SUPPORTED_CURRENCIES
    }


async def _fetch_live_rates() -> RateSnapshot:
    async with httpx.AsyncClient(
        timeout=config.FX_TIMEOUT_SECONDS, follow_redirects=True
    ) as client:
        response = await client.get(
            config.FX_RATES_URL,
            params={"base": BASE_CURRENCY, "symbols": ",".join(QUOTED_CURRENCIES)},
        )
        response.raise_for_status()
        payload = response.json()
    as_of = payload.get("date")
    return RateSnapshot(
        rates=_parse_rates(payload),
        source=LIVE_SOURCE,
        as_of=as_of if isinstance(as_of, str) else None,
        fetched_at=_utc_now(),
        is_live=True,
        stale=False,
    )


def _load_cache() -> RateSnapshot | None:
    path = Path(config.FX_CACHE_PATH)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    raw_rates = payload.get("rates")
    if not isinstance(raw_rates, dict):
        return None
    rates: dict[str, dict[str, float]] = {}
    for source, targets in raw_rates.items():
        if not isinstance(source, str) or not isinstance(targets, dict):
            continue
        for target, value in targets.items():
            if not isinstance(target, str) or not isinstance(value, (int, float)):
                continue
            amount = float(value)
            if math.isfinite(amount) and amount > 0:
                rates.setdefault(source.upper(), {})[target.upper()] = amount
    if not _has_all_pairs(rates):
        return None
    as_of = payload.get("as_of")
    fetched_at = payload.get("fetched_at")
    return RateSnapshot(
        rates=rates,
        source=LIVE_SOURCE,
        as_of=as_of if isinstance(as_of, str) else None,
        fetched_at=fetched_at if isinstance(fetched_at, str) else None,
        is_live=True,
        stale=True,
    )


def _write_cache(snapshot: RateSnapshot) -> None:
    path = Path(config.FX_CACHE_PATH)
    payload = {
        "source": snapshot.source,
        "as_of": snapshot.as_of,
        "fetched_at": snapshot.fetched_at,
        "rates": snapshot.rates,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            json.dump(payload, handle, indent=2)
            temp_name = handle.name
        os.replace(temp_name, path)
    except OSError:
        logger.warning("Unable to write the exchange-rate cache to %s", path, exc_info=True)


def _is_fresh(snapshot: RateSnapshot) -> bool:
    if not snapshot.is_live or snapshot.stale or not snapshot.fetched_at:
        return False
    try:
        fetched = datetime.fromisoformat(snapshot.fetched_at)
    except ValueError:
        return False
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - fetched).total_seconds() / 3600
    return age_hours < config.FX_REFRESH_HOURS


def initialize_rates() -> RateSnapshot:
    """Load the last-known snapshot from disk. Safe to call without a network."""
    global _snapshot
    if config.FX_ENABLED:
        cached = _load_cache()
        if cached is not None:
            _snapshot = cached
    return _snapshot


async def refresh_rates(force: bool = False) -> RateSnapshot:
    """Refresh from the provider; on any failure keep serving the old snapshot."""
    global _snapshot, _refreshing
    if not config.FX_ENABLED:
        return _snapshot
    if not force and _is_fresh(_snapshot):
        return _snapshot
    if _refreshing:
        return _snapshot
    _refreshing = True
    try:
        fresh = await _fetch_live_rates()
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Exchange-rate refresh failed: %s", exc)
        if _snapshot.is_live and not _snapshot.stale:
            _snapshot = replace(_snapshot, stale=True)
        return _snapshot
    finally:
        _refreshing = False
    _snapshot = fresh
    _write_cache(fresh)
    logger.info("Exchange rates refreshed (as of %s)", fresh.as_of)
    return fresh


async def refresh_rates_forever() -> None:
    """Refresh at startup and then once per FX_REFRESH_HOURS."""
    while True:
        try:
            await refresh_rates()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Unexpected exchange-rate refresh error", exc_info=True)
        await asyncio.sleep(max(config.FX_REFRESH_HOURS, 0.25) * 3600)
