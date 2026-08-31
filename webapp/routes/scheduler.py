"""Background scheduler status/config routes."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import core

bp = Blueprint("scheduler", __name__)


@bp.route("/api/scheduler", methods=["GET"])
def api_get_scheduler():
    """Status snapshot: enabled, interval, last/next run timestamps."""
    try:
        import scheduler
        return jsonify(scheduler.get_state())
    except Exception as exc:
        core.log.warning("[scheduler] status failed: %s", exc)
        return jsonify({"error": str(exc)[:200]}), 500


@bp.route("/api/scheduler", methods=["POST"])
def api_set_scheduler():
    """Update scheduler configuration: {enabled, interval_hours}.

    Persists to settings.json so the change survives restarts, then
    signals the running daemon thread immediately.
    """
    args = request.get_json(silent=True) or request.form
    data = core.load_settings()
    if "enabled" in args:
        data["scheduler_enabled"] = bool(args["enabled"])
    if "interval_hours" in args:
        try:
            pih = int(args["interval_hours"])
        except (TypeError, ValueError):
            pih = core._DEFAULT_SETTINGS["parser_interval_hours"]
        data["parser_interval_hours"] = max(core._SCHEDULER_INTERVAL_MIN,
                                            min(core._SCHEDULER_INTERVAL_MAX, pih))
    core.save_settings(data)
    try:
        import scheduler
        state = scheduler.configure(
            enabled=data["scheduler_enabled"],
            interval_hours=data["parser_interval_hours"],
        )
        return jsonify(state)
    except Exception as exc:
        core.log.warning("[scheduler] configure failed: %s", exc)
        return jsonify({"error": str(exc)[:200]}), 500


@bp.route("/api/scheduler/run", methods=["POST"])
def api_run_scheduler_now():
    """Trigger an immediate scheduler run (manual trigger).

    Returns the state snapshot immediately; the run happens asynchronously.
    The UI should poll ``GET /api/scheduler`` to see ``running=true`` →
    ``last_run_status`` afterwards.
    """
    try:
        import scheduler
        return jsonify(scheduler.run_now())
    except Exception as exc:
        core.log.warning("[scheduler] manual run failed: %s", exc)
        return jsonify({"error": str(exc)[:200]}), 500
