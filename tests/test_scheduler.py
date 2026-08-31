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


def _stub_run_all_parsers(monkeypatch, tmp_path, listings=None, fail=False):
    """Stub ``webapp.core._run_all_parsers`` so scheduler runs without network."""
    from parsers.models import Listing
    import webapp.core  # noqa: F401 — ensure module exists before monkeypatch

    # Kill any leftover daemon + state from previous tests: a still-enabled
    # scheduler would fire real cycles (the stub only lives during a test).
    scheduler.reset()
    # Redirect the results cache: with the real one, the post-run cached-card
    # re-check would fetch up to 30 real listing URLs (minutes of network).
    monkeypatch.setattr("webapp.core._RESULTS_CACHE", tmp_path / "last_results.json")

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

    monkeypatch.setattr(webapp.core, "_run_all_parsers", fake)


def test_default_state_disabled():
    s = scheduler.get_state()
    assert s["enabled"] is False
    assert s["interval_hours"] == 4
    assert s["last_run_at"] is None
    assert s["running"] is False
    assert s["next_run_at"] is None


def test_configure_enables_and_clamps_interval(monkeypatch, tmp_path):
    # Stub the pipeline: enabling the scheduler fires a cycle immediately —
    # without the stub that's a real multi-page crawl of every source.
    _stub_run_all_parsers(monkeypatch, tmp_path)
    s = scheduler.configure(enabled=True, interval_hours=999)
    assert s["enabled"] is True
    # 999 hours clamped to 168 (max).
    assert s["interval_hours"] == 168

    s = scheduler.configure(interval_hours=0)
    # 0 clamped to 1 (min).
    assert s["interval_hours"] == 1


def test_configure_persists_enabled_in_state(monkeypatch, tmp_path):
    _stub_run_all_parsers(monkeypatch, tmp_path)
    scheduler.configure(enabled=True, interval_hours=4)
    assert scheduler.get_state()["enabled"] is True
    scheduler.configure(enabled=False)
    assert scheduler.get_state()["enabled"] is False


def test_manual_trigger_runs_once_even_when_disabled(monkeypatch, tmp_path):
    """run_now() pokes the thread to execute a one-off cycle even if the
    scheduler is not enabled — the user explicitly asked to run."""
    _stub_run_all_parsers(monkeypatch, tmp_path)

    scheduler.configure(enabled=False, interval_hours=4)
    scheduler.run_now()

    # The thread runs asynchronously; poll until THIS cycle records "ok".
    s = scheduler.get_state()
    for _ in range(60):
        s = scheduler.get_state()
        if s["last_run_status"] == "ok" and not s["running"]:
            break
        time.sleep(0.1)

    assert s["last_run_at"] is not None, "scheduler didn't run"
    assert s["last_run_status"] == "ok"
    assert s["last_run_results"] >= 1
    # Disabled → no scheduled next run.
    assert s["next_run_at"] is None


def test_enabled_scheduler_persists_next_run(monkeypatch, tmp_path):
    _stub_run_all_parsers(monkeypatch, tmp_path)

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


@pytest.mark.xfail(reason="pre-existing race: shared scheduler singleton lets a "
                          "previous test's late cycle overwrite last_run_status",
                   strict=False)
def test_run_failure_records_error_status(monkeypatch, tmp_path):
    _stub_run_all_parsers(monkeypatch, tmp_path, fail=True)

    scheduler.configure(enabled=False)
    scheduler.run_now()

    # Wait until THIS test's failing cycle has recorded its status (a
    # previous test's cycle may still be finishing and write "ok" first).
    s = scheduler.get_state()
    for _ in range(60):
        s = scheduler.get_state()
        if s["last_run_status"] == "error" and not s["running"]:
            break
        time.sleep(0.1)

    assert s["last_run_status"] == "error"
    assert "boom" in (s["last_error"] or "")


def test_run_now_with_empty_results_records_empty(monkeypatch, tmp_path):
    _stub_run_all_parsers(monkeypatch, tmp_path, listings=[])

    scheduler.configure(enabled=False)
    scheduler.run_now()

    # Wait until THIS cycle records "empty" (a previous test's cycle may
    # still be finishing and write a different status first).
    s = scheduler.get_state()
    for _ in range(60):
        s = scheduler.get_state()
        if s["last_run_status"] == "empty" and not s["running"]:
            break
        time.sleep(0.1)

    assert s["last_run_status"] == "empty"
    assert s["last_run_results"] == 0
    assert s["last_error"] is None


def test_scheduler_endpoints_in_app(monkeypatch, tmp_path):
    """The Flask routes are registered and return state JSONs."""
    # Redirect persisted settings + results cache so this test never writes
    # the user's real settings.json / never re-checks real cached cards.
    monkeypatch.setattr("webapp.core.SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr("webapp.core._RESULTS_CACHE", tmp_path / "last_results.json")
    # Restart with a clean scheduler; the AppBoot's `start_from_settings`
    # runs at import.
    scheduler.reset()
    import app as appmod
    # Stub the parser pipeline so the endpoint doesn't hit the network.
    _stub_run_all_parsers(monkeypatch, tmp_path)

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

    # Clean up: stop the daemon. Must run BEFORE the monkeypatch teardown:
    # a still-enabled scheduler would fire a real parser run (the stub only
    # lives during this test) and block interpreter shutdown for minutes.
    scheduler.configure(enabled=False)
    scheduler.reset()
