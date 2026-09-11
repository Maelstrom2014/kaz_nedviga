"""Shared pytest fixtures.

The detail-HTML disk cache (parsers/htmlcache) must never touch the real
cache/html/ dir during tests: patched fetches would write fixture HTML to
disk and poison later test runs within the TTL (same URL, different
expected content). Every test gets its own throwaway cache dir.
"""
from pathlib import Path

import pytest

from parsers import htmlcache


@pytest.fixture(autouse=True)
def _isolated_html_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(htmlcache, "_CACHE_DIR", Path(tmp_path) / "htmlcache")


@pytest.fixture(autouse=True)
def _no_bot_autostart(monkeypatch):
    """The embedded telegram bot must never start polling from tests.

    Block the token source (the repo has a real bot_token.txt) so the real
    start_from_settings() is a safe no-op; lifecycle tests override this
    with their own fake token.
    """
    import telegram_bot
    monkeypatch.setattr(telegram_bot, "load_token_quiet", lambda: None)
    monkeypatch.setattr(telegram_bot, "configure", lambda enabled: None)


@pytest.fixture(autouse=True)
def _clean_bot_search_cache():
    """The shared search cache is process-global — tests must not leak
    results into each other (same filter key would hit a stale entry)."""
    import telegram_bot
    telegram_bot._SEARCH_CACHE.clear()
