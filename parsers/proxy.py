"""Free-proxy pipeline: fetch proxy lists from public sites, test which ones
actually route traffic, and serve working proxies to the parsers with rotation.

Design notes
------------
* ``ProxyPool`` is a process-wide singleton (``get_pool``) so every parser
  created via ``get_all_parsers()`` shares the same pool without each one
  needing the settings plumbed through.
* List fetching uses the environment's normal connectivity (``trust_env``
  left at its default) so it works whether or not the host has a system proxy.
* Liveness tests use ``trust_env=False`` so the *specific* proxy under test is
  what routes the request — an ambient system proxy would otherwise mask a
  dead free proxy.
* Everything is defensive: a source that is down or changes layout simply
  contributes zero entries and the remaining sources fill the pool.
"""
from __future__ import annotations

import ipaddress
import logging
import random
import re
import threading
import time
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover - bs4 is a hard dep of the parsers
    BeautifulSoup = None

log = logging.getLogger("parsers")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_SCHEME_RE = re.compile(r"^(?P<scheme>https?|socks[45](h)?|sockss)://(?P<rest>.+)$")
_HOSTPORT_RE = re.compile(r"^(?P<host>[^\s:/]+):(?P<port>\d{1,5})$")

# Parser ``name`` attributes that can use proxies (per-site selection in settings).
# These MUST match the ``name`` class attribute of each parser in
# ``parsers/factory.py`` (PARSER_CLASSES), otherwise ``get_proxies(site)``
# never matches and that parser will not route through a proxy.
KNOWN_SITES = ["krisha.kz", "olx.kz", "kn.kz", "etagi.com", "telegram", "twogis"]

# Legacy site keys from an earlier KNOWN_SITES (before it matched parser
# ``name`` attributes). Migrated to current keys in sanitize_proxy_settings so
# existing saved settings keep working. Keys with no current equivalent are dropped.
_LEGACY_SITE_KEYS = {
    "etagi": "etagi.com",
    "krisha": "krisha.kz",
    "olx": "olx.kz",
    "2gis": "twogis",
}


# --------------------------------------------------------------------------- #
# Proxy entry
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ProxyEntry:
    host: str
    port: int
    scheme: str = "http"   # how we connect TO the proxy: http/https/socks4/socks5
    source: str = ""

    def as_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    def key(self) -> tuple:
        return (self.host, self.port, self.scheme)

    def to_dict(self) -> dict:
        return {"host": self.host, "port": self.port,
                "scheme": self.scheme, "source": self.source,
                "url": self.as_url()}


def _valid_ipv4(host: str) -> bool:
    try:
        ipaddress.IPv4Address(host)
        return True
    except Exception:
        return False


def _valid_port(port: str) -> Optional[int]:
    s = (port or "").strip()
    if not s.isdigit():
        return None
    p = int(s)
    return p if 1 <= p <= 65535 else None


# --------------------------------------------------------------------------- #
# Extractors (pure: raw body -> list of entries)
# --------------------------------------------------------------------------- #
def parse_text(text: str, source: str = "") -> list[ProxyEntry]:
    """Parse a newline-delimited list. Each line may be ``scheme://host:port``
    or a bare ``host:port`` (defaulting to http)."""
    out: list[ProxyEntry] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        scheme = "http"
        m = _SCHEME_RE.match(line)
        if m:
            scheme = m.group("scheme").lower()
            line = m.group("rest").strip()
            # strip trailing junk / user:pass@
            if "@" in line:
                line = line.rsplit("@", 1)[-1]
        hp = _HOSTPORT_RE.match(line)
        if not hp:
            continue
        host, port = hp.group("host"), hp.group("port")
        if not _valid_ipv4(host):
            continue
        p = _valid_port(port)
        if p is None:
            continue
        out.append(ProxyEntry(host, p, scheme, source))
    return out


def parse_html(html: str, source: str = "", scheme: str = "http") -> list[ProxyEntry]:
    """Generic HTML-table extractor: for every table row, find a cell holding a
    valid IPv4 and another cell holding a valid port, and emit an entry. This
    works across the common proxy-list layouts (first two columns, IP/port)."""
    out: list[ProxyEntry] = []
    if BeautifulSoup is None or not html:
        return out
    soup = BeautifulSoup(html, "lxml")
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = [c.get_text().strip() for c in row.find_all(["td", "th"])]
            ip_cell = None
            port_cell = None
            for idx, cell in enumerate(cells):
                if ip_cell is None and _valid_ipv4(cell):
                    ip_cell = cell
                elif cell.isdigit() and 1 <= int(cell) <= 65535 and ip_cell is not None:
                    # ignore the cell that is the IP itself
                    if cell != ip_cell:
                        port_cell = cell
                        break
            if ip_cell and port_cell:
                out.append(ProxyEntry(ip_cell, int(port_cell), scheme, source))
    return out


def parse_json(text: str, source: str = "", scheme: str = "http") -> list[ProxyEntry]:
    """Parse a JSON body that is either a list of objects or an object wrapping
    a list under one of several common keys."""
    import json
    out: list[ProxyEntry] = []
    try:
        data = json.loads(text)
    except Exception:
        return out
    items = data
    if isinstance(data, dict):
        for key in ("proxies", "proxy_list", "data", "list", "results"):
            if isinstance(data.get(key), list):
                items = data[key]
                break
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        host = str(item.get("ip") or item.get("host") or item.get("ip_address") or "").strip()
        port_raw = item.get("port")
        if not host:
            # fall back to a combined "host:port" field
            combined = str(item.get("proxy") or item.get("address") or "").strip()
            hp = _HOSTPORT_RE.match(combined.rsplit("@", 1)[-1])
            if hp:
                host, port_raw = hp.group("host"), hp.group("port")
        p = _valid_port(str(port_raw)) if port_raw is not None else None
        if not host or p is None:
            continue
        if not _valid_ipv4(host):
            continue
        sch = str(item.get("scheme") or item.get("protocol") or item.get("type") or scheme).lower()
        if sch not in ("http", "https", "socks4", "socks5", "socks5h"):
            sch = scheme
        out.append(ProxyEntry(host, p, sch, source))
    return out


# --------------------------------------------------------------------------- #
# Source definitions
# --------------------------------------------------------------------------- #
PROXY_SOURCES: list[dict] = [
    {
        "key": "proxyscrape",
        "name": "Proxyscrape",
        "url": ("https://api.proxyscrape.com/v4/free-proxy-list/get?"
                "request=display_proxies&proxy_format=protocolipport&format=text"
                "&timeout=10000"),
        "kind": "text",
    },
    {
        "key": "freeproxylist",
        "name": "free-proxy-list.net",
        "url": "https://free-proxy-list.net/",
        "kind": "html",
    },
    {
        "key": "openproxylist",
        "name": "openproxylist.w0lf.me",
        "url": "http://openproxylist.w0lf.me/api/proxies/http/last/1000/",
        "kind": "json",
    },
    {
        "key": "a8",
        "name": "open-proxy-list.a8.net",
        "url": "http://www.open-proxy-list.a8.net/http-proxy-list/1.php",
        "kind": "html",
    },
    {
        "key": "proxylistde",
        "name": "proxy-list.de",
        "url": "https://proxy-list.de/en/proxy-list/http-proxy/list/1",
        "kind": "html",
    },
]

_SOURCE_KEYS = {s["key"] for s in PROXY_SOURCES}


def default_sources() -> list[str]:
    return [s["key"] for s in PROXY_SOURCES]


DEFAULT_PROXY_SETTINGS: dict = {
    "enabled": False,
    "rotation": "random",          # random | round_robin
    "max_proxies": 50,
    "max_candidates": 120,
    "test_workers": 20,
    "test_url": "https://www.bing.com",
    "test_timeout": 6,
    "proxy_max_retries": 2,
    "sources": default_sources(),
    "proxy_sites": list(KNOWN_SITES),
}


def sanitize_proxy_settings(raw) -> dict:
    """Coerce arbitrary user input into a clean proxy-settings dict."""
    base = {k: (v.copy() if isinstance(v, list) else v) for k, v in DEFAULT_PROXY_SETTINGS.items()}
    if not isinstance(raw, dict):
        return base
    try:
        base["enabled"] = bool(raw.get("enabled", base["enabled"]))
    except Exception:
        pass
    rot = str(raw.get("rotation", base["rotation"]))
    base["rotation"] = rot if rot in ("random", "round_robin") else "random"
    for num_key, lo, hi in (("max_proxies", 1, 2000),
                            ("max_candidates", 1, 5000),
                            ("test_workers", 1, 64),
                            ("test_timeout", 1, 60),
                            ("proxy_max_retries", 0, 10)):
        try:
            val = int(raw.get(num_key, base[num_key]))
        except (TypeError, ValueError):
            continue
        base[num_key] = max(lo, min(hi, val))
    url = str(raw.get("test_url", base["test_url"])).strip()
    if url.startswith("http://") or url.startswith("https://"):
        base["test_url"] = url
    srcs = raw.get("sources")
    if isinstance(srcs, list):
        kept = [s for s in srcs if s in _SOURCE_KEYS]
        base["sources"] = kept or default_sources()
    sites = raw.get("proxy_sites")
    if isinstance(sites, list):
        migrated = [_LEGACY_SITE_KEYS.get(s, s) for s in sites]
        base["proxy_sites"] = [s for s in migrated if s in KNOWN_SITES]
    return base


def fetch_source(source: dict, timeout: int = 15) -> list[ProxyEntry]:
    """Fetch one source and return its entries. Never raises."""
    key, kind, url = source["key"], source["kind"], source["url"]
    try:
        session = requests.Session()
        session.headers.update({"User-Agent": _UA})
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        body = resp.text or ""
    except Exception as exc:
        log.info("[proxy] source %s fetch failed: %s", key, exc)
        return []
    try:
        if kind == "text":
            return parse_text(body, key)
        if kind == "json":
            return parse_json(body, key)
        return parse_html(body, key)
    except Exception as exc:
        log.info("[proxy] source %s parse failed: %s", key, exc)
        return []


def test_proxy(entry: ProxyEntry, test_url: str, timeout: float) -> bool:
    """Return True if the proxy routes a request to ``test_url`` (status < 500)."""
    proxies = {"http": entry.as_url(), "https": entry.as_url()}
    try:
        session = requests.Session()
        session.trust_env = False
        session.headers.update({"User-Agent": _UA})
        session.proxies = proxies
        resp = session.get(test_url, timeout=timeout, allow_redirects=True)
        return 200 <= resp.status_code < 500
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Pool
# --------------------------------------------------------------------------- #
class ProxyPool:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.enabled: bool = False
        self.rotation: str = "random"
        self.max_proxies: int = DEFAULT_PROXY_SETTINGS["max_proxies"]
        self.proxy_max_retries: int = DEFAULT_PROXY_SETTINGS["proxy_max_retries"]
        self.proxy_sites: set = set(KNOWN_SITES)
        self._proxies: list[ProxyEntry] = []
        self._cursor: int = 0
        self.last_refresh: Optional[float] = None
        self.refreshing: bool = False
        self.stats: dict = {}
        self._use: dict = {}
        self._errors: list = []

    # -- configuration ------------------------------------------------------
    def configure(self, settings: Optional[dict]) -> None:
        cfg = sanitize_proxy_settings(settings or {})
        with self._lock:
            self.enabled = bool(cfg["enabled"])
            self.rotation = cfg["rotation"]
            self.max_proxies = int(cfg["max_proxies"])
            self.proxy_max_retries = int(cfg["proxy_max_retries"])
            self.proxy_sites = set(cfg.get("proxy_sites", KNOWN_SITES))

    def set_proxies(self, proxies: list[ProxyEntry]) -> None:
        with self._lock:
            self._proxies = list(proxies)
            self._cursor = 0
            self.last_refresh = time.time()

    def clear(self) -> None:
        with self._lock:
            self._proxies = []
            self._cursor = 0
            self.last_refresh = None
            self.stats = {}
            self._use = {}

    def snapshot(self) -> list[ProxyEntry]:
        with self._lock:
            return list(self._proxies)

    def __len__(self) -> int:
        with self._lock:
            return len(self._proxies)

    # -- selection ----------------------------------------------------------
    def next(self) -> Optional[ProxyEntry]:
        with self._lock:
            if not self._proxies:
                return None
            if self.rotation == "round_robin":
                entry = self._proxies[self._cursor % len(self._proxies)]
                self._cursor += 1
            else:
                entry = random.choice(self._proxies)
        return entry

    def get_proxies(self, site: Optional[str] = None) -> dict:
        """Return ``{"http": url, "https": url}`` for the current rotation step,
        or ``{}`` when proxies are disabled, ``site`` is not selected, or the
        pool is empty (direct)."""
        if not self.enabled:
            return {}
        if site is not None and site not in self.proxy_sites:
            return {}
        entry = self.next()
        if entry is None:
            return {}
        url = entry.as_url()
        return {"http": url, "https": url}

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": self.enabled,
                "rotation": self.rotation,
                "max_proxies": self.max_proxies,
                "proxy_sites": sorted(self.proxy_sites),
                "count": len(self._proxies),
                "last_refresh": self.last_refresh,
                "refreshing": self.refreshing,
                "error_count": len(self._errors),
                "stats": dict(self.stats),
            }

    # -- usage + error log --------------------------------------------------
    def note_use(self, site: str, url: str) -> None:
        """Record that ``site`` just routed a request through proxy ``url``."""
        with self._lock:
            self._use[site] = {"url": url, "ts": time.time()}

    def usage(self) -> dict:
        """``{site: {"url": proxy_url, "ts": ts}}`` for the current parse run."""
        with self._lock:
            return {s: {"url": v["url"], "ts": v["ts"]} for s, v in self._use.items()}

    def clear_usage(self) -> None:
        with self._lock:
            self._use = {}

    def log_error(self, site: str, url: str, proxy_url: str, error: str) -> None:
        """Append a proxy request failure to the capped error log."""
        with self._lock:
            self._errors.append({
                "ts": time.time(),
                "site": site,
                "proxy": proxy_url,
                "url": (url or "")[:300],
                "error": (error or "")[:300],
            })
            if len(self._errors) > 200:
                self._errors = self._errors[-200:]

    def errors(self, limit: int = 100) -> list:
        with self._lock:
            return list(reversed(self._errors[-limit:]))

    def clear_errors(self) -> None:
        with self._lock:
            self._errors = []

    # -- refresh (fetch + test) --------------------------------------------
    def refresh(self, settings: Optional[dict], on_progress=None) -> dict:
        cfg = sanitize_proxy_settings(settings or {})
        self.configure(cfg)
        enabled_keys = set(cfg["sources"]) or default_sources()
        sources = [s for s in PROXY_SOURCES if s["key"] in enabled_keys]
        with self._lock:
            self.refreshing = True
        try:
            t0 = time.time()
            per_source: dict[str, int] = {}
            combined: list[ProxyEntry] = []
            for src in sources:
                entries = fetch_source(src)
                per_source[src["key"]] = len(entries)
                combined.extend(entries)
            log.info("[proxy] fetched %d raw proxies from %d sources",
                     len(combined), len(sources))
            # de-dupe (keep first occurrence order)
            seen = set()
            unique: list[ProxyEntry] = []
            for e in combined:
                k = e.key()
                if k not in seen:
                    seen.add(k)
                    unique.append(e)
            # cap candidates for testing
            max_cand = int(cfg["max_candidates"])
            if len(unique) > max_cand:
                random.shuffle(unique)
                unique = unique[:max_cand]
            # test concurrently, stop early once enough are working
            max_proxies = int(cfg["max_proxies"])
            workers = int(cfg["test_workers"])
            test_url = cfg["test_url"]
            test_timeout = float(cfg["test_timeout"])
            working: list[ProxyEntry] = []

            def _check(e: ProxyEntry):
                return e, test_proxy(e, test_url, test_timeout)

            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = {ex.submit(_check, e): e for e in unique}
                for fut in as_completed(futures):
                    try:
                        entry, ok = fut.result()
                    except Exception:
                        continue
                    if ok:
                        working.append(entry)
                        if len(working) >= max_proxies:
                            for f in futures:
                                f.cancel()
                            break
                    if on_progress:
                        try:
                            on_progress(len(working), len(unique))
                        except Exception:
                            pass

            working = working[:max_proxies]
            self.set_proxies(working)
            stats = {
                "sources": per_source,
                "raw": len(combined),
                "unique": len(seen),
                "tested": min(len(unique), len(working) + self.stats.get("failed", 0)),
                "working": len(working),
                "elapsed_s": round(time.time() - t0, 1),
            }
            with self._lock:
                self.stats = stats
            log.info("[proxy] refresh complete: %d working (from %d unique, %ds)",
                     len(working), len(seen), stats["elapsed_s"])
            return stats
        finally:
            with self._lock:
                self.refreshing = False


_POOL = ProxyPool()


def get_pool() -> ProxyPool:
    return _POOL
