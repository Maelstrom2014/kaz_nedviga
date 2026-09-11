"""TTL disk cache for detail-page HTML (cache/html/, gzip-compressed).

Detail pages are the most expensive part of a run: photos, phones and
coordinates each re-fetch the same URL, and repeated searches / scheduler
runs hit the same listings again. A short-TTL cache removes those repeat
fetches without serving stale data to the user (1 h default).

Storage: entries are gzip-compressed (level 6) — HTML shrinks ~5-8x at a
cost of a few ms per page, negligible next to a network fetch. Files are
``<sha1(url)>.html.gz``. Plain-text ``.html`` entries written by older
versions are still read (and transparently migrated to .gz on hit).

Fail-open: any error reading/writing the cache is swallowed — the fetch
path must keep working even if the disk is full or the dir is unwritable.
"""
from __future__ import annotations

import gzip
import hashlib
import logging
import time
from pathlib import Path

log = logging.getLogger("parsers")

_CACHE_DIR = Path(__file__).parent.parent / "cache" / "html"
_TTL_SECONDS = 3600.0  # 1 hour
_COMPRESS_LEVEL = 6
# Expired entries are swept on writes, at most this often (burst writes
# would otherwise rescan the whole dir on every page).
_SWEEP_INTERVAL = 600.0  # 10 min
_last_sweep = 0.0


def _path_for(url: str) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return _CACHE_DIR / f"{digest}.html.gz"


def _legacy_path_for(url: str) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return _CACHE_DIR / f"{digest}.html"


def get(url: str, ttl: float = _TTL_SECONDS) -> str | None:
    """Return cached HTML for ``url`` if present and younger than TTL."""
    try:
        for p, gzipped in ((_path_for(url), True), (_legacy_path_for(url), False)):
            if not p.is_file():
                continue
            age = time.time() - p.stat().st_mtime
            if age > ttl:
                continue
            if gzipped:
                html = gzip.decompress(p.read_bytes()).decode("utf-8")
            else:
                html = p.read_text(encoding="utf-8")
                # Old plain-text entry: migrate to gzip, drop the original.
                try:
                    _path_for(url).write_bytes(
                        gzip.compress(html.encode("utf-8"), _COMPRESS_LEVEL))
                    p.unlink()
                except Exception:
                    pass
            log.debug("[htmlcache] hit (%.0fs old): %s", age, url[:80])
            return html
        return None
    except Exception:
        return None


def put(url: str, html: str) -> None:
    """Store ``html`` for ``url`` (gzip). Never raises."""
    global _last_sweep
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data = gzip.compress((html or "").encode("utf-8"), _COMPRESS_LEVEL)
        p = _path_for(url)
        p.write_bytes(data)
        _legacy_path_for(url).unlink(missing_ok=True)
    except Exception as exc:
        log.debug("[htmlcache] write failed: %s", exc)
    now = time.time()
    if now - _last_sweep > _SWEEP_INTERVAL:
        _last_sweep = now
        _sweep_expired(now)


def _sweep_expired(now: float) -> None:
    """Best-effort removal of entries older than the default TTL."""
    try:
        if not _CACHE_DIR.is_dir():
            return
        for p in _CACHE_DIR.glob("*"):
            try:
                if p.is_file() and now - p.stat().st_mtime > _TTL_SECONDS:
                    p.unlink()
            except Exception:
                pass
    except Exception:
        pass


def clear() -> int:
    """Delete all cached HTML files. Returns the number removed."""
    removed = 0
    try:
        if _CACHE_DIR.is_dir():
            for pattern in ("*.html.gz", "*.html"):
                for p in _CACHE_DIR.glob(pattern):
                    try:
                        if p.is_file():
                            p.unlink()
                            removed += 1
                    except Exception:
                        pass
    except Exception:
        pass
    return removed
