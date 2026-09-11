"""Composition root for the Flask app.

Thin on purpose: all shared helpers/state live in ``webapp.core`` (the single
monkeypatch target for tests) and HTTP routes live in ``webapp/routes/*``.
This module wires them together, keeps the historical module-level names for
backwards compatibility, and owns the startup side effects.
"""
from __future__ import annotations

import logging
import sys
import threading

from webapp import create_app
# Re-export the historical helper names so `from app import X` keeps working.
# NOTE: tests that MONKEYPATCH shared state must target `webapp.core.*` —
# patching this module's re-exported references would not affect the routes.
from webapp.core import (  # noqa: F401
    SETTINGS_PATH,
    THEMES,
    _RESULTS_CACHE,
    _apply_parser_max_pages,
    _build_listings_from_request,
    _compute_price_change,
    _dict,
    _is_apartment_listing,
    _load_prev_results,
    _parse_params,
    _point_in_polygon,
    _port_is_free,
    _port_owner_pids,
    _save_results,
    _to_dict,
    free_port,
    load_settings,
    save_settings,
)

# --- Logging setup: rotating file (shared with "parsers") + console ---
from logsetup import console_handler, errors_file_handler

# The file keeps errors only; the console gets the full INFO stream so
# search activity is visible. Both this logger and the "parsers" logger
# write to the same rotating file.
_fh = errors_file_handler()
_ch = console_handler()
log = logging.getLogger("app")
log.setLevel(logging.DEBUG)
if not log.handlers:
    log.addHandler(_fh)
    log.addHandler(_ch)
# Console output must not depend on werkzeug's root handler (which is not
# always present, e.g. under `flask run`); also prevents double printing
# when the dev server does add one.
log.propagate = False

from db import init_db  # noqa: E402
import rates as rates_module  # noqa: E402
from parsers.proxy import get_pool as get_proxy_pool  # noqa: E402
from webapp.core import load_settings as _load_settings_startup  # noqa: E402,F401

app = create_app()

init_db()

# Pre-warm exchange rates in the background: the first /api/rates (and the
# first RUB/USD conversion) must not pay for a network fetch. If the file
# cache is fresh this returns instantly; otherwise the fetch runs while the
# server is coming up.
threading.Thread(target=rates_module.get_rates, daemon=True,
                 name="rates-prewarm").start()

# The background scheduler is started below (after load_settings is
# available) — see "Start the background scheduler" near the
# if __name__ == "__main__" block.

# Start the background scheduler if it was enabled in settings.
# Skip the autostart when running under pytest — tests that need the
# scheduler configure it explicitly (and would falsely trigger real parser
# network runs at import time otherwise).
if "pytest" not in sys.modules:
    try:
        import scheduler
        scheduler.start_from_settings(_load_settings_startup())
    except Exception as exc:
        log.warning("[scheduler] failed to start: %s", exc)

# Start the embedded telegram bot if enabled in settings (needs
# bot_token.txt or the BOT_TOKEN env var; missing token = warning only).
if "pytest" not in sys.modules:
    try:
        import telegram_bot
        telegram_bot.start_from_settings(_load_settings_startup())
    except Exception as exc:
        log.warning("[bot] failed to start: %s", exc)

# Sync the shared free-proxy pool with persisted settings at startup so a
# restart picks up the enabled flag + sources without a manual settings call.
try:
    get_proxy_pool().configure(_load_settings_startup().get("proxy"))
except Exception as _exc:  # pragma: no cover - startup best-effort
    log.warning("[proxy] startup configure failed: %s", _exc)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Guard against a previously started instance that is still alive on the
    # port. Werkzeug binds with SO_REUSEADDR, so a second `python app.py`
    # would "start" fine while the old process keeps answering the requests —
    # its logs then go to the old (often closed) terminal and the new one
    # shows nothing. AUTO-FIX: kill the stale instance automatically (only
    # processes whose command line is app.py are eligible); if the port is
    # still busy after that — e.g. an unrelated program owns it — fail fast
    # with the manual hint instead of killing the wrong thing.
    _PORT = 5000
    was_busy = not _port_is_free(_PORT)
    if not free_port(_PORT):
        owner = _port_owner_pids(_PORT)
        import os
        if os.name == "nt":
            kill_hint = f"taskkill /F /PID {owner or '<PID>'}"
        else:
            kill_hint = f"kill -9 {owner or '<PID>'}"
        print(
            f"\nПорт {_PORT} занят (PID {owner or 'неизвестен'}) и не был "
            f"освобождён автоматически: процесс не похож на этот сервер.\n"
            f"Освободите порт вручную:\n"
            f"    {kill_hint}\n",
            file=sys.stderr,
        )
        sys.exit(1)
    if was_busy:
        log.info("[app] порт %d был занят — старый экземпляр завершён "
                 "автоматически", _PORT)
    # debug=False: the Werkzeug debugger allows arbitrary code execution and
    # the app binds to 0.0.0.0, so a debug instance must never be exposed.
    # threaded=True so a long PDF export (which downloads many photos) does
    # not block the rest of the app for other requests.
    app.run(debug=False, host="0.0.0.0", port=_PORT, threaded=True)
