"""HTTP layer for parsers: TLS-impersonated fetches, WAF handling, proxies.

``HttpMixin`` is mixed into ``BaseParser`` (see parsers/base.py) so every
parser shares one browser-like fetch stack. Module-level header pools are
re-exported by parsers/base.py for backwards compatibility.
"""
from __future__ import annotations

import logging
import random
import time
from typing import ClassVar

import requests
from bs4 import BeautifulSoup  # noqa: F401  (kept for import compatibility)

log = logging.getLogger("parsers")

# --- Pool of User-Agent strings (browsers + devices) ---
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
]

ACCEPT_LANGUAGES = [
    "ru-RU,ru;q=0.9,en;q=0.8",
    "ru,en-US;q=0.9,en;q=0.8",
    "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "kk-RU,ru;q=0.9,en;q=0.8",
    "ru,be;q=0.9,en;q=0.8",
]

ACCEPT_VALUES = [
    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
]

_SEC_FETCH_COMBOS = [
    {"Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1"},
    {"Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "cross-site", "Sec-Fetch-User": "?1"},
    {"Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "same-origin"},
    {"Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "same-site", "Sec-Fetch-User": "?1"},
]

_DNT_VALUES = ["1", "0", None]
_CACHE_CONTROL = ["max-age=0", "no-cache", None]
_PRAGMA = ["no-cache", None]
_REFERER_VALUES = [
    "https://www.google.com/",
    "https://yandex.ru/",
    "https://go.mail.ru/",
    None,
]


def _random_headers() -> dict:
    """Generate a randomized set of HTTP headers to avoid bot detection."""
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": random.choice(ACCEPT_VALUES),
        "Accept-Language": random.choice(ACCEPT_LANGUAGES),
        "Accept-Encoding": random.choice(["gzip, deflate, br", "gzip, deflate", "identity"]),
        "Connection": random.choice(["keep-alive", "close"]),
        "Upgrade-Insecure-Requests": "1",
    }
    headers.update(random.choice(_SEC_FETCH_COMBOS))
    headers["DNT"] = random.choice(_DNT_VALUES) or "1"
    headers["Cache-Control"] = random.choice(_CACHE_CONTROL) or "no-cache"
    headers["Pragma"] = random.choice(_PRAGMA) or "no-cache"
    referer = random.choice(_REFERER_VALUES)
    if referer:
        headers["Referer"] = referer
    return headers


HEADERS = _random_headers()


class HttpMixin:
    """Browser-like HTTP fetching with WAF handling and proxy rotation."""

    # ---- HTTP ---------------------------------------------------------
    # Some sites (e.g. olx.kz) sit behind a WAF that fingerprints the TLS
    # handshake and 403s plain python-requests traffic while accepting
    # browser-like TLS. When enabled, fetch() uses curl_cffi impersonation.
    use_cffi: ClassVar[bool] = False
    cffi_impersonate: ClassVar[str] = "chrome"
    # A page smaller than this is treated as the last one (stops paging).
    # Sites with small per-page counts (etagi: 6) override this.
    min_page_size: ClassVar[int] = 10
    # Browser TLS profiles to rotate through when a WAF blocks us. A single
    # profile can get rate-limited/blocked while others still pass.
    cffi_profiles: ClassVar[tuple[str, ...]] = (
        "chrome", "safari184", "edge101", "firefox147",
    )
    # One persistent browser-TLS session across page fetches: cookies
    # persist and the TLS fingerprint never changes — like a real tab.
    session_sticky: ClassVar[bool] = False
    # (min, max) seconds of human-like pause between page fetches.
    page_delay: ClassVar[tuple[float, float] | None] = None
    # When set, a WAF block raises immediately instead of burning more
    # requests on profile rotation / fallbacks — retrying a block only
    # escalates it (the block lifts on its own).
    fail_fast_on_waf: ClassVar[bool] = False
    # Bounded WAF cooldown (fail-fast path only): on a 403/429/503, sleep and
    # retry up to waf_max_retries times. 0 = fail immediately (default).
    waf_max_retries: ClassVar[int] = 0
    waf_cooldown: ClassVar[float] = 30.0
    waf_max_cooldown: ClassVar[float] = 120.0

    def __init__(self):
        super().__init__()
        self._cffi_session = None

    def _proxy_kwargs(self) -> dict:
        """Return ``{"http": url, "https": url}`` for the current rotation proxy,
        or ``{}`` when proxies are disabled, the site is not selected, or the
        pool is empty (direct)."""
        from .proxy import get_pool
        try:
            pk = get_pool().get_proxies(self.name)
            return pk
        except Exception as exc:
            log.debug("[%s] proxy lookup failed, using direct: %s", self.name, exc)
            return {}

    def _note_proxy_error(self, url: str, pk: dict, exc: Exception) -> None:
        """Log a proxy request failure to the shared pool's error log."""
        try:
            from .proxy import get_pool
            if pk and get_pool().enabled:
                get_pool().log_error(self.name, url, pk.get("https", ""),
                                     "%s: %s" % (type(exc).__name__, exc))
        except Exception:
            pass

    def _note_proxy_used(self, pk: dict) -> None:
        """Record that a request for this site went through the given proxy."""
        try:
            from .proxy import get_pool
            if pk and get_pool().enabled:
                get_pool().note_use(self.name, pk.get("https", ""))
        except Exception:
            pass

    def _proxy_tries(self) -> list:
        """Ordered proxy kwargs to try for this site: up to
        ``proxy_max_retries`` rotated proxies, then one direct (``{}``) attempt.
        Always ends with ``{}`` so a site still parses when every proxy is dead.
        Returns ``[{}]`` when proxies are disabled or not selected for the site."""
        from .proxy import get_pool
        try:
            max_retries = int(get_pool().proxy_max_retries)
        except Exception:
            max_retries = 2
        first = self._proxy_kwargs()
        if not first:
            return [{}]
        tries = []
        cur = first
        for _ in range(max(0, max_retries)):
            tries.append(cur)
            cur = self._proxy_kwargs()
        tries.append({})  # direct fallback
        return tries

    def _fetch_cffi(self, url: str, profile: str | None = None) -> str:
        from curl_cffi import requests as cffi_requests
        profile = profile or self.cffi_impersonate
        log.debug("[%s] cffi.GET %s (profile=%s)", self.name, url, profile)
        if self.session_sticky and profile == self.cffi_impersonate:
            if self._cffi_session is None:
                self._cffi_session = cffi_requests.Session(impersonate=profile)
            session = self._cffi_session
        else:
            session = cffi_requests.Session(impersonate=profile)
        resp = None
        last_exc = None
        for pk in self._proxy_tries():
            try:
                tmo = self.timeout if not pk else min(self.timeout, 5)
                resp = session.get(url, timeout=tmo, proxies=pk)
                self._note_proxy_used(pk)
                break
            except Exception as exc:
                last_exc = exc
                self._note_proxy_error(url, pk, exc)
        if resp is None:
            raise last_exc if last_exc else RuntimeError("cffi fetch failed")
        log.debug("[%s] cffi HTTP %s — %d bytes", self.name, resp.status_code, len(resp.text or ""))
        try:
            resp.raise_for_status()
        except cffi_requests.exceptions.HTTPError as cexc:
            # Normalize to the requests exception class so run()'s
            # HTTPError handling (partial results, WAF detection) applies.
            raise requests.exceptions.HTTPError(response=resp) from cexc
        return resp.text

    def _fetch_cffi_any(self, url: str) -> str | None:
        """Try each browser TLS profile in turn; return the first 200 body."""
        try:
            import curl_cffi  # noqa: F401
        except ImportError:
            return None
        for profile in self.cffi_profiles:
            try:
                return self._fetch_cffi(url, profile)
            except Exception as exc:
                log.debug("[%s] cffi profile %s failed: %s", self.name, profile, exc)
        return None

    def _is_waf_block(self, exc: Exception) -> bool:
        if isinstance(exc, requests.exceptions.HTTPError):
            return getattr(exc.response, "status_code", None) in (403, 429, 503)
        return isinstance(exc, (requests.exceptions.ConnectionError,
                                requests.exceptions.SSLError))

    def fetch(self, url: str) -> str:
        time.sleep(random.uniform(0.2, 0.8))
        if self.use_cffi:
            if self.fail_fast_on_waf:
                # One consistent session, no rotation: a WAF block is
                # IP-based and lifts on its own. Fail fast by default; when
                # waf_max_retries > 0, back off and retry a bounded number of
                # times so a short block clears within the run.
                attempt = 0
                while True:
                    attempt += 1
                    try:
                        return self._fetch_cffi(url)
                    except Exception as exc:
                        if (self._is_waf_block(exc) and self.waf_max_retries > 0
                                and attempt <= self.waf_max_retries):
                            delay = min(self.waf_cooldown * attempt,
                                        self.waf_max_cooldown)
                            delay += random.uniform(0.0, 1.0)
                            log.warning(
                                "[%s] WAF block (%s); cooling down %.0fs "
                                "(retry %d/%d)",
                                self.name, type(exc).__name__, delay,
                                attempt, self.waf_max_retries)
                            time.sleep(delay)
                            continue
                        raise
            # Preferred path: rotate browser profiles — a WAF may allow one
            # fingerprint and block another after rate limiting.
            html = self._fetch_cffi_any(url)
            if html is not None:
                return html
            log.debug("[%s] all cffi profiles failed, falling back to requests",
                      self.name)
        try:
            return self._fetch_requests(url)
        except Exception as exc:
            # WAF blocked plain requests — give browser-like TLS a chance.
            if self._is_waf_block(exc):
                html = self._fetch_cffi_any(url)
                if html is not None:
                    log.info("[%s] requests blocked (%s), browser TLS passed",
                             self.name, type(exc).__name__)
                    return html
            raise

    def fetch_session(self, url: str) -> tuple[requests.Session, str]:
        """GET ``url`` and return ``(session, text)``.

        Some sites (kn.kz) gate the phone behind an XHR reveal endpoint
        that only answers for the session which loaded the detail page
        (cookie + Referer). The page and the endpoint must therefore
        share one browser session — use this instead of fetch().
        """
        return self._fetch_requests_session(url)

    def _fetch_requests(self, url: str) -> str:
        _, text = self._fetch_requests_session(url)
        return text

    def _fetch_requests_session(self, url: str) -> tuple[requests.Session, str]:
        """Like _fetch_requests, but returns (session, text) so follow-up
        calls can reuse the same browser session (cookies persist).

        Proxies are best-effort: up to ``proxy_max_retries`` rotated proxies
        are tried, then one direct request, so the site still parses when
        every proxy is dead. Connection-level failures and repeated 403/429
        rotate to the next proxy; a genuine server error raises immediately."""
        session = requests.Session()
        # Fresh randomized headers for each request
        session.headers.update({**_random_headers(), **{k: v for k, v in self.headers.items() if k not in _random_headers()}})
        last_exc = None
        for pk in self._proxy_tries():
            session.proxies = pk
            tmo = self.timeout if not pk else min(self.timeout, 5)
            for attempt, verify in [(0, True), (1, False)]:
                try:
                    log.debug("[%s] requests.GET %s (verify=%s, proxy=%s)",
                              self.name, url, verify, bool(pk))
                    resp = session.get(url, timeout=tmo, allow_redirects=True, verify=verify)
                    log.debug("[%s] HTTP %s — %d bytes (Content-Type: %s)",
                              self.name, resp.status_code, len(resp.content),
                              resp.headers.get("Content-Type", "?")[:60])
                    resp.raise_for_status()
                    # Fix encoding: prefer content-type header, then apparent, then utf-8
                    ct = resp.headers.get("Content-Type", "")
                    if "charset=" in ct:
                        resp.encoding = ct.split("charset=")[-1].strip()
                    elif resp.apparent_encoding:
                        resp.encoding = resp.apparent_encoding
                    else:
                        resp.encoding = "utf-8"
                    text = resp.text
                    # Fallback: if text looks like mojibake, try utf-8 directly
                    if "\\u0" in repr(text)[:200] or "Ä" in text[:100]:
                        try:
                            text = resp.content.decode("utf-8")
                        except Exception:
                            pass
                    self._note_proxy_used(pk)
                    return session, text
                except requests.exceptions.SSLError as exc:
                    if attempt == 0:
                        log.debug("[%s] SSL error, retrying verify=False", self.name)
                        continue
                    last_exc = exc
                    break
                except requests.exceptions.HTTPError as exc:
                    status = getattr(exc.response, "status_code", None)
                    if status == 403 and attempt == 0:
                        log.debug("[%s] 403, retrying with fresh headers", self.name)
                        session.headers.update(_random_headers())
                        time.sleep(random.uniform(0.5, 1.5))
                        continue
                    if status in (403, 429) and pk:
                        # The proxy's IP was likely blocked; rotate to the next.
                        last_exc = exc
                        self._note_proxy_error(url, pk, exc)
                        break
                    raise
                except requests.exceptions.RequestException as exc:
                    last_exc = exc
                    self._note_proxy_error(url, pk, exc)
                    break
            if not pk:
                # Direct request failed; no more fallbacks.
                if last_exc is not None:
                    raise last_exc
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("requests fetch failed")
