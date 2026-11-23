"""Exchange-rate service: demo default, live refresh, cache fallback, API provenance."""

import asyncio
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import main, rates

client = TestClient(main.app)


def _live_snapshot(*, stale=False, fetched_at=None):
    return rates.RateSnapshot(
        rates=rates._parse_rates({"rates": {"INR": 100.0, "USD": 1.25, "GBP": 0.8}}),
        source=rates.LIVE_SOURCE,
        as_of="2026-11-02",
        fetched_at=fetched_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        is_live=True,
        stale=stale,
    )


@pytest.fixture(autouse=True)
def demo_snapshot(monkeypatch):
    monkeypatch.setattr(rates, "_snapshot", rates._demo_snapshot())
    monkeypatch.setattr(rates, "_refreshing", False)


def test_default_snapshot_is_demo_and_labelled_as_such():
    snapshot = rates.get_snapshot()

    assert snapshot.is_live is False
    assert snapshot.stale is False
    assert snapshot.label == "Bundled demonstration rates"

    quote = rates.quote("EUR", "INR")
    assert quote is not None
    assert quote.rate == 95.0
    assert "demonstration rate" in quote.description


def test_quote_handles_direct_inverse_same_and_unknown_pairs():
    assert rates.quote("EUR", "INR").rate == 95.0
    assert rates.quote("USD", "INR").rate == 88.0
    assert rates.quote("INR", "USD").rate == 0.011
    assert rates.quote("EUR", "EUR").rate == 1.0
    assert rates.quote("JPY", "INR") is None


def test_parse_rates_expands_every_ordered_pair():
    table = rates._parse_rates(
        {"date": "2026-11-02", "rates": {"INR": 100.0, "USD": 1.25, "GBP": 0.8}}
    )

    assert table["USD"]["INR"] == pytest.approx(80.0)
    assert table["INR"]["GBP"] == pytest.approx(0.008)
    assert table["GBP"]["USD"] == pytest.approx(1.5625)
    assert set(table["EUR"]) == {"INR", "USD", "GBP"}


def test_parse_rates_rejects_incomplete_payloads():
    with pytest.raises(ValueError, match="missing INR"):
        rates._parse_rates({"rates": {"USD": 1.0, "GBP": 0.8}})


def test_refresh_stores_live_snapshot_and_reloads_it_from_cache(monkeypatch, tmp_path):
    cache_path = tmp_path / "exchange_rates.json"
    monkeypatch.setattr(rates.config, "FX_CACHE_PATH", cache_path)
    live = _live_snapshot()

    async def fake_fetch():
        return live

    monkeypatch.setattr(rates, "_fetch_live_rates", fake_fetch)

    snapshot = asyncio.run(rates.refresh_rates(force=True))

    assert snapshot.is_live is True
    assert snapshot.stale is False
    assert rates.quote("EUR", "INR").rate == 100.0
    assert cache_path.exists()

    monkeypatch.setattr(rates, "_snapshot", rates._demo_snapshot())
    rates.initialize_rates()
    restored = rates.get_snapshot()
    assert restored.is_live is True
    assert restored.stale is True
    assert restored.as_of == "2026-11-02"


def test_refresh_failure_marks_last_known_rates_stale(monkeypatch):
    live = _live_snapshot()
    monkeypatch.setattr(rates, "_snapshot", live)

    async def failing_fetch():
        raise httpx.ConnectError("feed down")

    monkeypatch.setattr(rates, "_fetch_live_rates", failing_fetch)

    snapshot = asyncio.run(rates.refresh_rates(force=True))

    assert snapshot.rates == live.rates
    assert snapshot.stale is True
    assert "Last-known" in snapshot.label
    assert "dated 2026-11-02" in snapshot.note
    assert rates.quote("EUR", "INR").description.startswith("the last-known rate")


def test_refresh_failure_before_any_live_fetch_keeps_demo_rates(monkeypatch):
    async def failing_fetch():
        raise httpx.ConnectError("feed down")

    monkeypatch.setattr(rates, "_fetch_live_rates", failing_fetch)

    snapshot = asyncio.run(rates.refresh_rates(force=True))

    assert snapshot.is_live is False
    assert snapshot.stale is False
    assert snapshot.rates == rates.DEMO_RATES


def test_refresh_skips_a_fresh_live_snapshot(monkeypatch):
    monkeypatch.setattr(rates, "_snapshot", _live_snapshot())
    calls = []

    async def fake_fetch():
        calls.append(True)
        return _live_snapshot()

    monkeypatch.setattr(rates, "_fetch_live_rates", fake_fetch)

    snapshot = asyncio.run(rates.refresh_rates())

    assert snapshot.is_live is True
    assert calls == []


def test_disabled_fx_keeps_demo_rates(monkeypatch):
    monkeypatch.setattr(rates.config, "FX_ENABLED", False)

    async def fake_fetch():
        raise AssertionError("FX_ENABLED=false must not fetch")

    monkeypatch.setattr(rates, "_fetch_live_rates", fake_fetch)

    snapshot = asyncio.run(rates.refresh_rates(force=True))

    assert snapshot.is_live is False
    assert snapshot.rates == rates.DEMO_RATES


def test_currency_tool_reports_live_provenance(monkeypatch):
    from backend.tools import convert_currency

    monkeypatch.setattr(rates, "_snapshot", _live_snapshot())

    result = convert_currency.invoke(
        {"amount": 10, "from_currency": "EUR", "to_currency": "INR"}
    )

    assert "1000.0" in result
    assert "live rate" in result
    assert "2026-11-02" in result


def test_rates_endpoint_reports_demo_provenance():
    response = client.get("/api/rates")

    assert response.status_code == 200
    payload = response.json()
    assert payload["is_live"] is False
    assert payload["source"] == rates.DEMO_SOURCE
    assert payload["label"] == "Bundled demonstration rates"
    assert payload["as_of"] is None
    assert payload["base"] == "EUR"
    assert "not live market data" in payload["note"]

    pairs = {
        (pair["from_currency"], pair["to_currency"]): pair["rate"]
        for pair in payload["pairs"]
    }
    assert len(pairs) == 12
    assert pairs[("EUR", "INR")] == 95.0


def test_rates_endpoint_reports_stale_live_snapshot(monkeypatch):
    monkeypatch.setattr(rates, "_snapshot", _live_snapshot(stale=True))

    payload = client.get("/api/rates").json()

    assert payload["is_live"] is True
    assert payload["stale"] is True
    assert payload["as_of"] == "2026-11-02"
    assert "Last-known" in payload["label"]
    pairs = {
        (pair["from_currency"], pair["to_currency"]): pair["rate"]
        for pair in payload["pairs"]
    }
    assert pairs[("USD", "INR")] == pytest.approx(80.0)
