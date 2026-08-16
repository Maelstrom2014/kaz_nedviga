"""Tests for the exchange rates module and API endpoints."""
import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import rates as rates_module
from app import app


SAMPLE_API = {
    "result": "success",
    "time_last_update_utc": "Fri, 14 Aug 2026 00:02:32 +0000",
    "time_next_update_utc": "Sat, 15 Aug 2026 00:23:42 +0000",
    "rates": {
        "KZT": 1,
        "RUB": 0.179,
        "USD": 0.002148,
        "EUR": 0.001864,
    },
}

SAMPLE_FETCHED = {
    "KZT": 1.0, "RUB": 0.179, "USD": 0.002148, "EUR": 0.001864,
    "updated": "test", "fetched_at": int(time.time()),
}


@pytest.fixture
def isolated_rates(tmp_path, monkeypatch):
    """Pre-seed module cache + temp file path so no network call happens."""
    monkeypatch.setattr(rates_module, "_RATES_PATH", tmp_path / "rates.json")
    monkeypatch.setattr(rates_module, "_cached", dict(SAMPLE_FETCHED))
    return tmp_path


@pytest.fixture
def client(isolated_rates):
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestRatesModule:

    def test_get_rates_from_cache(self, isolated_rates):
        r = rates_module.get_rates()
        assert r["RUB"] == 0.179

    def test_fallback_rates_reasonable(self):
        r = rates_module._FALLBACK
        assert 0.1 < r["RUB"] < 0.3
        assert 0.001 < r["USD"] < 0.005

    @patch("requests.get")
    def test_fetch_online_parses_api(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_API
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp
        r = rates_module._fetch_online()
        assert r is not None
        assert r["RUB"] == 0.179
        assert r["USD"] == 0.002148
        assert r["KZT"] == 1.0
        assert "fetched_at" in r

    @patch("requests.get", side_effect=Exception("network error"))
    def test_fetch_online_returns_none_on_error(self, mock_get):
        assert rates_module._fetch_online() is None

    @patch("rates._fetch_online")
    def test_get_rates_caches_to_file(self, mock_fetch, tmp_path, monkeypatch):
        monkeypatch.setattr(rates_module, "_RATES_PATH", tmp_path / "rates.json")
        mock_fetch.return_value = dict(SAMPLE_FETCHED)
        monkeypatch.setattr(rates_module, "_cached", None)

        r1 = rates_module.get_rates()
        assert r1["RUB"] == 0.179
        assert (tmp_path / "rates.json").exists()

        # Second call should not re-fetch (cache fresh)
        mock_fetch.reset_mock()
        r2 = rates_module.get_rates()
        assert r2["RUB"] == 0.179
        mock_fetch.assert_not_called()

    @patch("rates._fetch_online")
    def test_refresh_forces_new_fetch(self, mock_fetch, isolated_rates):
        # API returns new rates
        mock_fetch.return_value = {
            "KZT": 1.0, "RUB": 0.185, "USD": 0.0022,
            "updated": "new", "fetched_at": int(time.time()),
        }

        r = rates_module.refresh_rates()  # forced refresh
        assert r["RUB"] == 0.185
        assert mock_fetch.call_count == 1

    @patch("rates._fetch_online", return_value=None)
    @patch("rates._load_cache", return_value=None)
    def test_uses_fallback_when_no_cache(self, mock_cache, mock_fetch, tmp_path, monkeypatch):
        monkeypatch.setattr(rates_module, "_RATES_PATH", tmp_path / "nonexistent.json")
        monkeypatch.setattr(rates_module, "_cached", None)

        r = rates_module.get_rates()
        assert r["RUB"] == rates_module._FALLBACK["RUB"]
        assert r["USD"] == rates_module._FALLBACK["USD"]

    @patch("rates._fetch_online", return_value=None)
    def test_uses_stale_cache_when_api_fails(self, mock_fetch, tmp_path, monkeypatch):
        cache_file = tmp_path / "rates.json"
        cache_file.write_text(json.dumps({
            "RUB": 0.15, "USD": 0.002,
            "fetched_at": int(time.time()) - 7200,  # 2 hours old (stale TTL)
            "updated": "old",
        }), encoding="utf-8")
        monkeypatch.setattr(rates_module, "_RATES_PATH", cache_file)
        monkeypatch.setattr(rates_module, "_cached", None)

        r = rates_module.get_rates()
        # API fails, stale cache used
        assert r["RUB"] == 0.15

    def test_rub_per_kzt_from_cache(self, isolated_rates):
        assert rates_module.rub_per_kzt() == 0.179

    def test_usd_per_kzt_from_cache(self, isolated_rates):
        assert rates_module.usd_per_kzt() == 0.002148


class TestRatesApi:

    def test_get_rates_endpoint(self, client):
        resp = client.get("/api/rates")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["base"] == "KZT"
        assert "RUB" in data
        assert "USD" in data
        assert "updated" in data

    @patch("rates._fetch_online")
    def test_refresh_endpoint(self, mock_fetch, client):
        mock_fetch.return_value = {
            "KZT": 1.0, "RUB": 0.182, "USD": 0.0022,
            "updated": "test", "fetched_at": int(time.time()),
        }
        resp = client.post("/api/rates/refresh")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["RUB"] == 0.182

    @patch("rates._fetch_online", return_value=None)
    def test_refresh_fallback_on_failure(self, mock_fetch, client):
        # _cached is pre-seeded by isolated_rates fixture, so no network call
        resp = client.post("/api/rates/refresh")
        assert resp.status_code == 200
        data = resp.get_json()
        # refresh_rates: _fetch_online returns None → falls back to get_rates()
        # which returns fresh _cached (set by fixture)
        assert data["RUB"] > 0
        assert data["USD"] > 0


class TestListingPriceConversion:

    def test_price_rub_uses_live_rate(self):
        from parsers.models import Listing
        with patch("rates.rub_per_kzt", return_value=0.179):
            l = Listing(title="test", price=100000)
            assert l.price_rub() == 17900.0

    def test_price_usd_uses_live_rate(self):
        from parsers.models import Listing
        with patch("rates.usd_per_kzt", return_value=0.002148):
            l = Listing(title="test", price=100000)
            assert l.price_usd() == 214.8

    def test_price_rub_none(self):
        from parsers.models import Listing
        l = Listing(title="test", price=None)
        assert l.price_rub() is None

    def test_price_all_str_includes_all_currencies(self):
        from parsers.models import Listing
        with patch("rates.rub_per_kzt", return_value=0.179), \
             patch("rates.usd_per_kzt", return_value=0.002148):
            l = Listing(title="test", price=100000)
            s = l.price_all_str()
            assert "тг" in s
            assert "руб" in s
            assert "$" in s
