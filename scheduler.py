"""Background scheduler: runs all parsers periodically and caches results.

The scheduler mirrors what ``app.api_search()`` does for the on-demand
search path, but runs in a daemon thread so listings are refreshed
automatically (default: every 4 hours). This way when the user opens the
UI later, ``/api/get_results`` already has fresh cached listings.

The scheduler is **opt-in** (``scheduler_enabled = False`` by default —
explicit opt-in prevents surprise network load on a fresh install).
``parser_interval_hours`` controls the interval (clamped to 1..168).

Thread model:
  - One daemon thread, lazily started when settings are applied or when
    the app boots if enabled.
  - Reconfigurable: changing ``parser_interval_hours`` / ``scheduler_enabled``
    in settings signals the thread via a refresh event so it recomputes the
    sleep duration or exits.
  - Manual "run now" via ``run_now()`` pokes the event and wakes the thread
    immediately (also usable from a Flask endpoint).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger("scheduler")

# Module-level state: a single global scheduler instance, since the app is
# single-process. Tests reset this with `reset()`.


@dataclass
class SchedulerState:
    enabled: bool = False
    interval_hours: int = 4
    last_run_at: Optional[str] = None       # ISO timestamp
    last_run_status: Optional[str] = None   # "ok" | "empty" | "error" | None
    last_run_results: int = 0
    last_error: Optional[str] = None
    running: bool = False
    next_run_at: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        # Include "now" so the UI can compute remaining time consistently.
        d["now"] = datetime.now().isoformat(sep=" ", timespec="seconds")
        return d


_state = SchedulerState()
_lock = threading.Lock()
_wakeup_event = threading.Event()
_stop_event = threading.Event()
_thread: Optional[threading.Thread] = None


# Bounds (must match app.py; mirrored to avoid a circular import).
INTERVAL_HOURS_MIN = 1
INTERVAL_HOURS_MAX = 168
INTERVAL_HOURS_DEFAULT = 4


def get_state() -> dict:
    """Snapshot of the scheduler state. Safe for JSON response."""
    with _lock:
        return _state.to_dict()


def configure(enabled: Optional[bool] = None,
              interval_hours: Optional[int] = None) -> dict:
    """Update scheduler configuration.

    - ``enabled`` toggles whether the daemon should run.
    - ``interval_hours`` is clamped to [INTERVAL_HOURS_MIN, INTERVAL_HOURS_MAX].

    The change wakes the running thread so it picks up the new interval or
    exits. Returns the new state snapshot.
    """
    global _state
    with _lock:
        if enabled is not None:
            _state.enabled = bool(enabled)
        if interval_hours is not None:
            try:
                ih = int(interval_hours)
            except (TypeError, ValueError):
                ih = INTERVAL_HOURS_DEFAULT
            ih = max(INTERVAL_HOURS_MIN,
                     min(INTERVAL_HOURS_MAX, ih))
            _state.interval_hours = ih
        state_for_resp = _state.to_dict()
    # Wake the thread outside the lock — it just signals.
    _wakeup_event.set()
    # Start the thread lazily if enabling.
    if enabled is True:
        _ensure_thread()
    return state_for_resp


def run_now() -> dict:
    """Trigger a one-off scheduler run immediately (manual trigger).

    Returns the state snapshot immediately; the run happens asynchronously.
    The caller should poll ``get_state()`` afterwards to see ``running=True`` and
    then ``last_run_status``.
    """
    # Set the trigger flag FIRST, then start/wake the thread — avoids the
    # race where the thread starts and immediately sleeps (sees no trigger).
    with _lock:
        _state._manual_trigger = True  # type: ignore[attr-defined]
    _ensure_thread()
    _wakeup_event.set()
    return get_state()


def _ensure_thread() -> None:
    """Create the daemon thread if it's not already running."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    _wakeup_event.clear()
    _thread = threading.Thread(target=_run_loop, name="parser-scheduler",
                               daemon=True)
    _thread.start()


def _run_loop() -> None:
    """Main scheduler loop. Runs while enabled, plus any pending manual
    triggers even when disabled."""
    log.info("[scheduler] thread started (enabled=%s, interval=%dh)",
             _state.enabled, _state.interval_hours)
    while not _stop_event.is_set():
        with _lock:
            enabled = _state.enabled
            manual = getattr(_state, "_manual_trigger", False)
            if manual:
                _state._manual_trigger = False  # type: ignore[attr-defined]
                do_run = True
            else:
                do_run = enabled

        if do_run and not _stop_event.is_set():
            _execute_once()

        with _lock:
            now = datetime.now()
            # If disabled but we just did a manual run, exit the thread;
            # we'll be restarted on the next manual trigger or enable.
            if not enabled and not getattr(_state, "_manual_trigger", False):
                _state.next_run_at = None
                break
            next_dt = now + timedelta(hours=_state.interval_hours)
            _state.next_run_at = next_dt.isoformat(sep=" ",
                                                   timespec="seconds")
            sleep_s = _state.interval_hours * 3600

        # Sleep, but wake early on configure()/run_now() signal.
        # Offline-bound interval (>=1h) — re-check periodically so the thread
        # notices config changes / shutdown within ~30s, not at the next run.
        deadline = time.monotonic() + sleep_s
        while time.monotonic() < deadline and not _stop_event.is_set():
            if _wakeup_event.wait(timeout=30):
                _wakeup_event.clear()
                break

    log.info("[scheduler] thread exiting")


def _execute_once() -> None:
    """Run all parsers once with current settings, save results, update state.

    Reuses the search pipeline so behaviour matches the on-demand
    ``/api/search`` path (parser stats, merge with previous results, etc.).
    Imports ``app`` lazily — otherwise a circular import at module load.
    """
    with _lock:
        _state.running = True

    started_at = datetime.now().isoformat(sep=" ", timespec="seconds")
    try:
        # Import here to avoid circulars: app imports us (scheduler), so we
        # must not import app/webapp at module load.
        from webapp import core  # type: ignore
        from parsers.factory import get_all_parsers  # type: ignore
        from parsers.models import SearchParams  # type: ignore
        _run_all_parsers = core._run_all_parsers
        _save_results = core._save_results
        _to_dict = core._to_dict
        _check_cached_cards = core._check_cached_cards
        _write_results_cache = core._write_results_cache

        settings = core.load_settings()
        # Use the user's saved search defaults so the scheduler's runs match
        # what they'd see in the UI. Defaults are: price_min/max, rooms,
        # district, floor_min/max, area_min/max, limit.
        sd = settings.get("search_defaults", {})
        params = SearchParams(
            price_min=sd.get("price_min"),
            price_max=sd.get("price_max"),
            floor_min=sd.get("floor_min"),
            floor_max=sd.get("floor_max"),
            area_min=sd.get("area_min"),
            area_max=sd.get("area_max"),
            district=sd.get("district", ""),
            rooms=sd.get("rooms", []) or [],
            limit=0,  # scheduler collects all so UI sees full cache
        )

        parsers = get_all_parsers()
        # Apply per-parser max_pages overrides the same way /api/search does.
        try:
            core._apply_parser_max_pages(parsers)
        except Exception:
            pass  # non-critical

        results = _run_all_parsers(parsers, params)
        result_dicts = [_to_dict(r) for r in results]
        merged = _save_results(result_dicts)
        # Re-check cached cards (not re-fetched this run) for validity so the
        # UI can mark removed/expired ads. Bounded to keep runs responsive.
        current_keys = {f"{r.get('source','')}|{r.get('url','')}" for r in result_dicts}
        cached_cards = [r for r in merged.values()
                        if f"{r.get('source','')}|{r.get('url','')}" not in current_keys]
        if cached_cards:
            _check_cached_cards(cached_cards, {p.name: p for p in get_all_parsers()})
            _write_results_cache(merged)

        with _lock:
            _state.last_run_at = started_at
            _state.last_run_status = "ok" if results else "empty"
            _state.last_run_results = len(results)
            _state.last_error = None
        log.info("[scheduler] run completed: %d listings", len(results))
    except Exception as exc:
        log.warning("[scheduler] run failed: %s", exc, exc_info=True)
        with _lock:
            _state.last_run_at = started_at
            _state.last_run_status = "error"
            _state.last_run_results = 0
            _state.last_error = str(exc)[:300]


def start_from_settings(settings: dict) -> None:
    """Boot the scheduler (if enabled in settings) — called at app start."""
    enabled = bool(settings.get("scheduler_enabled", False))
    interval = settings.get("parser_interval_hours", INTERVAL_HOURS_DEFAULT)
    configure(enabled=enabled, interval_hours=interval)
    if enabled:
        log.info("[scheduler] started at boot (interval=%dh)", interval)


def reset() -> None:
    """Test helper: stop the scheduler and clear state. Signals the daemon
    thread to exit (via _stop_event) and waits briefly for it to finish."""
    global _state, _thread
    _stop_event.set()
    _wakeup_event.set()
    t = _thread
    if t is not None and t.is_alive():
        t.join(timeout=5)
    with _lock:
        _state = SchedulerState()
    _stop_event.clear()
    _wakeup_event.clear()
    _thread = None
