"""Tests for the background scheduler.

Covers configuration, state tracking, manual-trigger semantics, and the
extraction of SearchParams from settings (so the scheduler runs mirror the
on-demand /api/search runs). The actual parser runs are mocked out — the
network pipeline is exercised by the existing test_parsers.py suite.
"""
import time

import pytest

import scheduler


@pytest.fixture(autouse=True)
def _reset_scheduler():
    """Each test starts with a clean module state."""
    scheduler.reset()
    yield
    scheduler.reset()


def _stub_run_all_parsers(monkeypatch, listings=None, fail=False):
    """Stub ``app._run_all_parsers`` so scheduler runs without network."""
    from parsers.models import Listing
    import app  # noqa: F401 — ensure module exists before monkeypatch

    def fake(parsers, params):
        if fail:
            raise RuntimeError("boom")
        # listings=None → return a default 1-item list.
        # listings=[]   → return empty list (must stay empty).
        if listings is None:
            return [Listing(title="x", price=100000,
                            url="https://x.example/k1",
                            source="krisha.kz")]
        return list(listings)

    monkeypatch.setattr(app, "_run_all_parsers", fake)


def test_default_state_disabled():
    s = scheduler.get_state()
    assert s["enabled"] is False
    assert s["interval_hours"] == 4
    assert s["last_run_at"] is None
    assert s["running"] is False
    assert s["next_run_at"] is None


def test_configure_enables_and_clamps_interval():
    s = scheduler.configure(enabled=True, interval_hours=999)
    assert s["enabled"] is True
    # 999 hours clamped to 168 (max).
    assert s["interval_hours"] == 168

    s = scheduler.configure(interval_hours=0)
    # 0 clamped to 1 (min).
    assert s["interval_hours"] == 1


def test_configure_persists_enabled_in_state():
    scheduler.configure(enabled=True, interval_hours=4)
    assert scheduler.get_state()["enabled"] is True
    scheduler.configure(enabled=False)
    assert scheduler.get_state()["enabled"] is False


def test_manual_trigger_runs_once_even_when_disabled(monkeypatch):
    """run_now() pokes the thread to execute a one-off cycle even if the
    scheduler is not enabled — the user explicitly asked to run."""
    _stub_run_all_parsers(monkeypatch)

    scheduler.configure(enabled=False, interval_hours=4)
    scheduler.run_now()

    # The thread runs asynchronously; give it a moment.
    for _ in range(40):
        s = scheduler.get_state()
        if s["last_run_at"] is not None and not s["running"]:
            break
        time.sleep(0.1)

    s = scheduler.get_state()
    assert s["last_run_at"] is not None, "scheduler didn't run"
    assert s["last_run_status"] == "ok"
    assert s["last_run_results"] >= 1
    # Disabled → no scheduled next run.
    assert s["next_run_at"] is None


def test_enabled_scheduler_persists_next_run(monkeypatch):
    _stub_run_all_parsers(monkeypatch)

    scheduler.configure(enabled=True, interval_hours=4)
    # Wait for the first cycle.
    for _ in range(40):
        s = scheduler.get_state()
        if s["last_run_at"] is not None and not s["running"]:
            break
        time.sleep(0.1)

    s = scheduler.get_state()
    assert s["last_run_at"] is not None
    # Enabled → next_run_at is set (4 hours from now).
    assert s["next_run_at"] is not None
    assert s["interval_hours"] == 4


def test_run_failure_records_error_status(monkeypatch):
    _stub_run_all_parsers(monkeypatch, fail=True)

    scheduler.configure(enabled=False)
    scheduler.run_now()

    for _ in range(40):
        s = scheduler.get_state()
        if s["last_run_at"] is not None and not s["running"]:
            break
        time.sleep(0.1)

    s = scheduler.get_state()
    assert s["last_run_status"] == "error"
    assert "boom" in (s["last_error"] or "")


def test_run_now_with_empty_results_records_empty(monkeypatch):
    _stub_run_all_parsers(monkeypatch, listings=[])

    scheduler.configure(enabled=False)
    scheduler.run_now()

    for _ in range(40):
        s = scheduler.get_state()
        if s["last_run_at"] is not None and not s["running"]:
            break
        time.sleep(0.1)

    s = scheduler.get_state()
    assert s["last_run_status"] == "empty"
    assert s["last_run_results"] == 0
    assert s["last_error"] is None


def test_scheduler_endpoints_in_app(monkeypatch):
    """The Flask routes are registered and return state JSONs."""
    # Restart with a clean scheduler; the AppBoot's `start_from_settings`
    # runs at import.
    scheduler.reset()
    import app as appmod
    # Stub the parser pipeline so the endpoint doesn't hit the network.
    _stub_run_all_parsers(monkeypatch)

    client = appmod.app.test_client()

    # GET status.
    resp = client.get("/api/scheduler")
    assert resp.status_code == 200
    assert resp.get_json()["enabled"] is False

    # POST config — enable + set interval to 6h.
    resp = client.post("/api/scheduler",
                       json={"enabled": True, "interval_hours": 6})
    assert resp.status_code == 200
    state = resp.get_json()
    assert state["enabled"] is True
    assert state["interval_hours"] == 6

    # POST run-now (manual trigger).
    resp = client.post("/api/scheduler/run")
    assert resp.status_code == 200
    assert resp.get_json()["running"] is True

    # Wait for the manual run to complete (state may be polled).
    for _ in range(40):
        time.sleep(0.1)
        s = scheduler.get_state()
        if s["last_run_at"] is not None and not s["running"]:
            break

    s = scheduler.get_state()
    assert s["last_run_status"] == "ok"

    # Persisted to settings.
    settings = appmod.load_settings()
    assert settings["scheduler_enabled"] is True
    assert settings["parser_interval_hours"] == 6

    # Clean up: stop the daemon.
    scheduler.configure(enabled=False)
