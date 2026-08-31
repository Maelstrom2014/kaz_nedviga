"""Tests for Flask app endpoints (mocked, no real parsing)."""
import json
import socket
from unittest.mock import patch, MagicMock

import pytest

from app import app, _port_is_free, _port_owner_pids


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Isolate the results cache so /api/search writes can never touch the
    # real data/last_results.json
    monkeypatch.setattr("webapp.core._RESULTS_CACHE", tmp_path / "last_results.json")
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_index_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"\xd0\x90\xd0\xbb\xd0\xbc\xd0\xb0\xd1\x82\xd1\x8b" in resp.data  # "Алматы"


def test_api_districts(client):
    resp = client.get("/api/districts")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 8
    assert data[0]["name"] == "Алмалинский"


def test_api_search_empty(client, monkeypatch):
    # Stub the parser factory: an unpatched /api/search would run every real
    # parser (live network) — this test only checks the response shape.
    class _FakeParser:
        name = "krisha.kz"

        def run(self, params):
            return []

    monkeypatch.setattr("webapp.core.get_all_parsers", lambda: [_FakeParser()])
    resp = client.post("/api/search", json={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "total" in data
    assert "results" in data


def test_api_export_txt(client):
    payload = {"results": [
        {"title": "test apt", "price": 150000, "source": "krisha.kz", "rooms": 1, "area": 45.0},
    ]}
    resp = client.post("/api/export/txt", json=payload)
    assert resp.status_code == 200
    assert b"test apt" in resp.data


def test_api_export_pdf_portrait(client):
    payload = {"results": [
        {"title": "test apt", "price": 150000, "source": "krisha.kz"},
    ]}
    resp = client.post("/api/export/pdf", json={**payload, "orientation": "portrait"})
    assert resp.status_code == 200
    assert resp.data[:4] == b"%PDF"


def test_api_export_pdf_landscape(client):
    payload = {"results": [
        {"title": "test apt", "price": 150000, "source": "krisha.kz"},
    ]}
    resp = client.post("/api/export/pdf", json={**payload, "orientation": "landscape"})
    assert resp.status_code == 200
    assert resp.data[:4] == b"%PDF"


def test_port_is_free_true_when_unbound():
    # Grab a free port from the OS, release it, then the guard must agree.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert _port_is_free(port) is True


def test_port_is_free_false_when_listening():
    # A listener bound with SO_REUSEADDR (like Werkzeug's dev server) must
    # still be detected — that is the exact orphan-instance scenario.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.listen()
        assert _port_is_free(port) is False


def test_port_owner_pids_finds_listener():
    me = __import__("os").getpid()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.listen()
        assert str(me) in _port_owner_pids(port)
