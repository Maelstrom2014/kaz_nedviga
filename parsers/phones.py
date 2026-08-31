"""Phone extraction for parsers: page-source mining + reveal-API rotation.

``PhonesMixin`` is mixed into ``BaseParser`` (see parsers/base.py). It mines
the page source for the advertiser's number and, failing that, calls the
site's click-to-reveal API while rotating exit IPs (the reveal budget is
per client IP) and skipping proxies whose budget was just spent.
"""
from __future__ import annotations

import base64
import itertools
import logging
import random
import re
import threading
import time
from typing import ClassVar

import requests

from .http import _random_headers

# Same logger objects parsers/base.py configures (getLogger is a singleton);
# handlers attach when base is imported, which always happens in the app.
log = logging.getLogger("parsers")
phone_log = logging.getLogger("parsers.phone")

_PHONE_DIGITS_RE = re.compile(r"\d")
_WA_PHONE_RE = re.compile(
    r"(?:wa\.me|api\.whatsapp\.com/send\?phone=)(\d{10,15})", re.I)


def _canonical_phone_digits(s: str) -> str:
    """Collapse a digits-only string to ``+7XXXXXXXXXX``; ``""`` if not a phone.

    Accepts the 11-digit international forms (+7/8/007 — KZ and RU) and the
    bare 10-digit national form (KZ ``7XXXXXXXXX``, RU mobile/landline).
    """
    if s.startswith("00"):  # international dialing prefix: 007...
        s = s[2:]
    if len(s) == 11 and s[0] in "78":
        s = s[1:]
    if len(s) != 10:
        return ""
    # National 10 digits: 7 = KZ (mobile 70X/74X/75X/77X, landline 71XX-79XX);
    # 9 / 3-6 / 8 = RU mobile/landline.  0/1/2 are not phone prefixes.
    if s[0] not in "3456789":
        return ""
    return "+7" + s


def normalize_phone(raw: str | None) -> str:
    """Normalize a KZ/RU phone to ``+7XXXXXXXXXX`` (10 digits after +7).

    Accepts ``8707...``, ``+7 707 ...``, ``8 (707) 123-45-67``, bare KZ
    ``707 123 45 67``, ``wa.me/77071234567`` etc. Returns ``""`` if it can't
    be normalized to a plausible KZ mobile/landline.
    """
    if not raw:
        return ""
    # Extract a wa.me/api.whatsapp phone if present (highest priority).
    wm = _WA_PHONE_RE.search(str(raw))
    if wm:
        return _canonical_phone_digits(wm.group(1))
    digits = "".join(_PHONE_DIGITS_RE.findall(str(raw)))
    if not digits:
        return ""
    return _canonical_phone_digits(digits)


class PhonesMixin:
    """Page-source + reveal-endpoint phone extraction."""

    # Site support/hotline numbers that leak into the page source (footer
    # tel: links). They must never be reported as the advertiser's phone.
    phone_blacklist: ClassVar[frozenset[str]] = frozenset()
    # Click-to-reveal phone endpoint throttling. The reveal API (OLX /phones,
    # kn.kz /card/phone, krisha.kz ajaxPhones) rate-limits rapid successive
    # calls — once _enrich_photos fans out to a 6-thread pool, concurrent
    # calls burst-fire it and the site soft-blocks (OLX: 400). The instance
    # lock (_phone_endpoint_lock) serializes calls across threads; the min
    # interval caps throughput.
    phone_endpoint_cooldown: ClassVar[float] = 2.0
    phone_endpoint_min_interval: ClassVar[float] = 0.0
    # Jitter (fraction) applied to the min interval so the call cadence
    # is not perfectly regular (a metronomic cadence is a bot signal).
    phone_endpoint_jitter: ClassVar[float] = 0.3
    # Per-proxy post-success cooldown. The reveal API budget is per client
    # IP (~1 success per 2-3 min on OLX), so a proxy that just yielded a
    # number is skipped for this long and calls rotate to a fresh exit IP.
    phone_proxy_cooldown: ClassVar[float] = 150.0
    # Block detection: after this many CONSECUTIVE 4xx/5xx responses the
    # endpoint is treated as soft-blocked for the IP and calls pause for
    # phone_block_cooldown seconds (the block outlasts short retries).
    phone_block_threshold: ClassVar[int] = 4
    phone_block_cooldown: ClassVar[float] = 120.0
    # Page-only phone mode. When False, _enrich_phone skips the click-to-reveal
    # XHR endpoints (OLX /phones, kn.kz /card/phone, krisha.kz ajaxPhones) and
    # relies solely on the page source (tel:/wa.me links, JSON state, visible
    # text). OLX uses this to avoid the reveal-API rate limits entirely: the
    # masked number is never present in the page HTML.
    phone_endpoint_enabled: ClassVar[bool] = True

    def __init__(self):
        # Per-run phone outcomes: listing.url -> "found:<stage>" or a
        # missing-reason. Reset at the start of every run().
        self._phone_outcomes: dict[str, str] = {}
        # Serializes click-to-reveal phone-API calls so concurrent detail
        # fetches (ThreadPoolExecutor in _enrich_photos) cannot burst-fire
        # the endpoint and trigger a soft rate-limit (OLX returns 400).
        self._phone_endpoint_lock = threading.Lock()
        self._phone_last_call = 0.0
        # Block detection: OLX's /phones API has a limited per-IP budget
        # of clean calls, then returns 400 for the rest of the window.
        # Track consecutive 4xx responses and back off (the block
        # outlasts short retries, so hammering only extends it).
        # _phone_consec_fail resets on a 200; _phone_block_until pauses
        # all endpoint calls until it passes.
        self._phone_consec_fail = 0
        self._phone_block_until = 0.0
        # Proxy URL -> monotonic time of its last successful reveal call.
        # The reveal budget is per client IP, so a proxy that just returned
        # a number is skipped until its budget can plausibly have refilled.
        self._phone_proxy_ok: dict[str, float] = {}

    # ---- Phone extraction ---------------------------------------------
    # KZ/RU numbers: 11 digits starting with 7 or 8 (network code 7XX KZ
    # mobile, 9XX/8XX RU mobile, 3XX-6XX/8XX RU landline) or the bare
    # 10-digit KZ national form ``7XX XXX XX XX`` without the leading 7/8.
    # Detail pages hide the number behind a "Показать телефон" button, but
    # the raw value still sits in the source: on the button itself, tel: /
    # WhatsApp links, data-* attributes, embedded JSON state, inline JS
    # (sometimes base64- or charCode-obfuscated), visible text — or it is
    # served from a click-to-reveal XHR endpoint (phonesUrl & co).
    _PHONE_CAND_RE = re.compile(
        r"(?<!\d)(?:"
        r"(?:\+|00)?[78][\s\-()]?\(?\d{3}\)?[\s\-()]?"
        r"\d{3}[\s\-()]?\d{2}[\s\-()]?\d{2}"
        r"|"
        r"7[\s\-()]?\(?\d{2}\)?[\s\-()]?"
        r"\d{3}[\s\-()]?\d{2}[\s\-()]?\d{2}"
        r")(?!\d)"
    )
    # JSON state keys: "phone"/"phoneNumber"/"tel"/"mobile"/... with a
    # quoted string, a bare number, or a single-element array value.
    _PHONE_KEY_RE = re.compile(
        r'["\'](?:phone\w*|tel\w*|mobile\w*|whatsapp\w*)["\']'
        r'\s*:\s*'
        r'(?:"([^"]{4,40})"|\'([^\']{4,40})\'|(\d{10,13})|\[\s*"([^"]{4,40})")',
        re.I,
    )
    _PHONE_KW_RE = re.compile(
        r"(?:tel|phone|телефон|контакт|связ|звон|whatsapp|вацап|viber)"
        r"[^0-9+\-]{0,20}"
        r"((?:\+|00)?[78][\s\-()]?\(?\d{3}\)?[\s\-()]?"
        r"\d{3}[\s\-()]?\d{2}[\s\-()]?\d{2}"
        r"|"
        r"7[\s\-()]?\(?\d{2}\)?[\s\-()]?"
        r"\d{3}[\s\-()]?\d{2}[\s\-()]?\d{2})(?!\d)", re.I,
    )
    _PHONE_ATTR_RE = re.compile(
        r"^data[-_]?phone(?:number)?$|^data[-_]?tel(?:ephone)?$", re.I,
    )
    # WhatsApp deep links: wa.me/7705..., api.whatsapp.com, whatsapp://
    _WA_HREF_RE = re.compile(
        r"(?:wa\.me/|api\.whatsapp\.com/send\?phone=|whatsapp://send\?phone=)"
        r"(\d{10,15})", re.I,
    )
    # The button that hides the number: "Показать телефон / показать номер
    # / показать свой номер / show phone number". Its own markup (data-*
    # attributes, onclick handler, plain text) is the most reliable source.
    _REVEAL_BTN_TEXT_RE = re.compile(
        r"(?:показать|посмотреть|show)\s+(?:свой\s+)?(?:телефон|номер|контакт)|"
        r"телефон\s+(?:скрыт|защищ)|скрыт(?:ый|ая)?\s+(?:телефон|номер)",
        re.I,
    )
    # Classic JS obfuscation of the number: base64 blobs and
    # String.fromCharCode(43,56,55,...) sequences.
    _B64_BLOB_RE = re.compile(r"[A-Za-z0-9+/]{12,56}={0,2}")
    _CHARCODE_RE = re.compile(r"fromCharCode\(([\d,\s]{10,400})\)")
    # Click-to-reveal endpoint URLs embedded in the page source (the button's
    # JS calls them): data-show-phone-url-value (kn.kz), "phonesUrl" state
    # keys (krisha.kz), /ajax/...phone paths.
    _PHONE_ENDPOINT_RES = (
        re.compile(r'data-(?:show-)?phone(?:s)?[-_]?url(?:-value)?="([^"]+)"',
                   re.I),
        re.compile(
            r'["\'](?:phones?_?url|showphoneurl|revealphoneurl|phoneendpoint)'
            r'["\']\s*:\s*["\']([^"\']+)["\']', re.I),
        re.compile(r'["\']([^"\']*?/ajax[^"\']{0,40}phone[^"\']{0,40})["\']',
                   re.I),
        re.compile(r'["\'](/[^"\']{0,80}/phones?[^"\']{0,40})["\']', re.I),
    )

    @staticmethod
    def _valid_phone(raw: str) -> str:
        """Normalize a candidate to +7XXXXXXXXXX; '' when invalid/masked."""
        if not raw or any(c in raw for c in "*?•."):
            return ""
        return _canonical_phone_digits(re.sub(r"\D", "", raw))

    def _first_valid(self, text: str) -> str:
        """First phone candidate in *text* that passes validation + blacklist."""
        for m in self._PHONE_CAND_RE.finditer(text or ""):
            cand = m.group(0)
            digits = re.sub(r"\D", "", cand)
            # A bare unseparated 10-digit run in free text is more often an
            # ID than a phone number — accept it only when formatted.
            if len(digits) == 10 and not re.search(r"[\s\-()]", cand):
                continue
            phone = self._valid_phone(cand)
            if phone and phone not in self.phone_blacklist:
                return phone
        return ""

    def _reveal_buttons(self, soup):
        """Yield elements whose text reads like a "Показать телефон" button."""
        seen: set[int] = set()
        for match in soup.find_all(string=self._REVEAL_BTN_TEXT_RE):
            node = match.parent
            while (node is not None
                   and node.name not in ("button", "a", "span", "div", "p")):
                node = node.parent
            if node is None or id(node) in seen:
                continue
            seen.add(id(node))
            yield node

    def _phone_from_scripts(self, soup) -> list[str]:
        """Phone numbers hidden inside <script> JavaScript.

        The value may sit in a plain assignment (``phone: "7 705 ..."``),
        be base64-encoded, or be built with String.fromCharCode(...) — all
        three shapes are decoded and the results validated.
        """
        found: list[str] = []
        for script in soup.find_all("script"):
            js = script.get_text() or ""
            if not js:
                continue
            for m in self._PHONE_KW_RE.finditer(js):
                phone = self._valid_phone(m.group(1))
                if phone and phone not in self.phone_blacklist:
                    found.append(phone)
            for m in itertools.islice(self._B64_BLOB_RE.finditer(js), 200):
                try:
                    blob = m.group(0)
                    dec = base64.b64decode(
                        blob + "=" * (-len(blob) % 4)).decode("utf-8", "ignore")
                except Exception:
                    continue
                if not re.search(r"\d{5,}", dec):
                    continue
                for cand in self._PHONE_CAND_RE.finditer(dec):
                    phone = self._valid_phone(cand.group(0))
                    if phone and phone not in self.phone_blacklist:
                        found.append(phone)
            for m in itertools.islice(self._CHARCODE_RE.finditer(js), 20):
                try:
                    dec = "".join(
                        chr(int(part)) for part in m.group(1).split(",")
                        if part.strip().isdigit())
                except Exception:
                    continue
                for cand in self._PHONE_CAND_RE.finditer(dec):
                    phone = self._valid_phone(cand.group(0))
                    if phone and phone not in self.phone_blacklist:
                        found.append(phone)
        return found

    def extract_detail_phone(self, html: str) -> str:
        """Extract the contact phone from a detail page.

        The number is usually hidden behind a "Показать телефон" button,
        yet the raw value is still in the page source. Sources, in priority
        order: the reveal button's own markup (data-* attributes, onclick,
        plain text), tel: links, WhatsApp links, data-phone/data-tel
        attributes, JSON "phone" state keys (incl. camelCase keys, bare
        numbers, arrays), inline JS (plain/base64/charCode), visible text,
        keyword-anchored raw HTML. Returns "" when nothing found.
        """
        phone, _ = self._extract_detail_phone(html)
        return phone

    def _extract_phone_multi(self, html: str) -> list:
        """Last-resort phone candidate generation. Tries several
        independent algorithms for a number hidden behind a reveal
        button / anti-spam overlay. Returns a list of raw candidate
        strings (NOT validated); the caller validates each with the
        standard phone acceptance checks."""
        if not html:
            return []
        cands = []

        def add(v):
            if v and v not in cands:
                cands.append(v)

        # A) tel: / sms: / whatsapp links (incl. hidden in attrs)
        for rx in (r'tel:([+\d][\d\s\-().]{6,18}\d)',
                   r'sms:([+\d][\d\s\-().]{6,18}\d)',
                   r'(?:wa\.me/|whatsapp://send\?phone=|api\.whatsapp\.com/send\?phone=)(\+?\d{8,15})'):
            for m in re.finditer(rx, html, re.I):
                add(m.group(1))

        # B) data-* contact attributes
        for m in re.finditer(
                r'data-(?:phone|tel|telephone|contact|number|mobile|phone_number)'
                r'\s*=\s*["\']([+\d][\d\s\-().]{6,18}\d)["\']', html, re.I):
            add(m.group(1))

        # C) JSON / JS state keys holding a phone (string or bare number)
        for m in re.finditer(
                r'["\'](?:phone|telephone|phone_number|phone_number_full|'
                r'contact_phone|mobile|tel|phone_digits|phone_value)["\']'
                r'\s*:\s*["\']?([+\d][\d\s\-().]{7,18}\d)["\']?', html, re.I):
            add(m.group(1))

        # D) base64-encoded blobs
        for m in re.finditer(r'["\']([A-Za-z0-9+/]{16,}={0,2})["\']', html):
            tok = m.group(1)
            try:
                dec = base64.b64decode(tok + "=" * (-len(tok) % 4),
                                        validate=False).decode("utf-8", "ignore")
            except Exception:
                continue
            for pm in re.finditer(r'[+\d][\d\s\-().]{8,18}\d', dec):
                add(pm.group(0))

        # E) fromCharCode(...) arrays
        for m in re.finditer(r'fromCharCode\(([\d,\s]{4,300})\)', html):
            try:
                codes = [int(x) for x in m.group(1).split(",") if x.strip().isdigit()]
                dec = "".join(chr(c) for c in codes if 32 <= c < 127)
            except Exception:
                continue
            for pm in re.finditer(r'[+\d][\d\s\-().]{8,18}\d', dec):
                add(pm.group(0))

        # F) HTML-entity-decoded, zero-width-stripped text, keyword-anchored
        try:
            import html as _htmllib
            cleaned = _htmllib.unescape(html)
            cleaned = re.sub('[\u200b\u200c\u200d\ufeff\u00a0]', ' ', cleaned)
            for m in re.finditer(
                    r'\b(?:phone|tel|contact|whatsapp|viber|'
                    r'телефон|звон|контакт|связь|мобил)'
                    r'[^<>{}]{0,50}?([+\d][\d\s\-().]{8,18}\d)', cleaned, re.I):
                add(m.group(1))
        except Exception:
            pass

        return cands

    def _extract_detail_phone(self, html: str) -> tuple[str, str]:
        """Like extract_detail_phone, but also reports the winning stage.

        Stage names: button, tel_link, wa_link, data_attr, json_state,
        js_script, visible_text, raw_keyword — or "" when not found.
        """
        if not html:
            return "", ""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        def ok(phone: str) -> bool:
            return bool(phone) and phone not in self.phone_blacklist

        # 1) "Показать телефон" button — the number often lives right in
        #    the button's markup (data-* attr, onclick, adjacent text).
        for btn in self._reveal_buttons(soup):
            phone = self._first_valid(str(btn)[:4000])
            if ok(phone):
                return phone, "button"
        # 2) tel: links
        for a in soup.find_all("a", href=re.compile(r"^tel:")):
            phone = self._valid_phone((a.get("href") or "")[4:])
            if ok(phone):
                return phone, "tel_link"
        # 3) WhatsApp links: wa.me/7705..., whatsapp://send?phone=...
        for a in soup.find_all("a", href=True):
            m = self._WA_HREF_RE.search(a.get("href") or "")
            if m:
                phone = self._valid_phone(m.group(1))
                if ok(phone):
                    return phone, "wa_link"
        # 4) data-phone / data-tel / data-phonenumber attributes
        #    (bs4 >= 4.13 ignores a compiled regex in attrs=, so match
        #    attribute names with a tag predicate instead)
        for el in soup.find_all(
                lambda t: any(self._PHONE_ATTR_RE.match(a) for a in t.attrs)):
            for attr, val in el.attrs.items():
                if isinstance(val, str) and self._PHONE_ATTR_RE.match(attr):
                    phone = self._valid_phone(val)
                    if ok(phone):
                        return phone, "data_attr"
        # 5) JSON state: "phone": "+7 ...", "phoneNumber": "8 ...",
        #    "phone": 77051234567, "phones": ["+7 ..."]
        for m in self._PHONE_KEY_RE.finditer(html):
            phone = self._valid_phone(
                m.group(1) or m.group(2) or m.group(3) or m.group(4) or "")
            if ok(phone):
                return phone, "json_state"
        # 6) inline JS behind the reveal button (plain/base64/charCode)
        for phone in self._phone_from_scripts(soup):
            if ok(phone):
                return phone, "js_script"
        # 7) visible text (a bare unseparated 10-digit run is an ID, not a
        #    phone — the guard lives in _first_valid)
        phone = self._first_valid(soup.get_text(" ", strip=True))
        if phone:
            return phone, "visible_text"
        # 8) raw HTML, but only next to a phone keyword — bare digit
        #    runs (IDs, hashes) are rejected.
        for m in self._PHONE_KW_RE.finditer(html):
            phone = self._valid_phone(m.group(1))
            if ok(phone):
                return phone, "raw_keyword"
        # 9) multi-algorithm fallback (reveal-button / anti-spam phones)
        for _cand in self._extract_phone_multi(html):
            _p = self._valid_phone(_cand)
            if _p and ok(_p):
                return _p, "multi_algo"
        return "", ""

    def _record_phone_outcome(self, listing: Listing, outcome: str) -> None:
        """Remember how a listing got (or didn't get) its phone this run."""
        self._phone_outcomes[listing.url] = outcome

    def _collect_phone_stats(self, results: list[Listing],
                             stats: "ParserRunStats") -> None:
        """Fill phones_found / phones_missing / phone_reasons for a run.

        Missing reasons: no_phone_source (nothing in the page source and
        no reveal endpoint), endpoint_failed (endpoint(s) found but none
        returned a phone), extraction_error (page parsing blew up),
        detail_fetch_failed (detail page fetch failed, enrichment never
        ran).
        """
        stats.phones_found = sum(1 for l in results if l.phone)
        stats.phones_missing = len(results) - stats.phones_found
        for l in results:
            if not l.phone:
                reason = (self._phone_outcomes.get(l.url)
                          or "detail_fetch_failed")
                stats.phone_reasons[reason] = (
                    stats.phone_reasons.get(reason, 0) + 1)
        if results:
            detail = (", ".join("%s=%d" % kv
                                for kv in sorted(stats.phone_reasons.items()))
                      if stats.phone_reasons else "none")
            log.info("[%s] phones: %d found, %d missing (%s)",
                     self.name, stats.phones_found, stats.phones_missing,
                     detail)

    def _enrich_phone(
        self,
        listing: Listing,
        html: str,
        session: requests.Session | None = None,
    ) -> None:
        """Set listing.phone from the detail page when it is still empty.

        Called by parsers from _fetch_detail_photos() right after the
        detail page is fetched — the same request that collects photos.
        First tries the page source (reveal-button markup, tel:/wa.me
        links, JSON state, inline JS, visible text); if the number is
        served from a click-to-reveal XHR endpoint instead, the endpoint
        URL is discovered from the page and fetched with the page's own
        session (or a fresh one with randomized browser headers).
        Every attempt is recorded in self._phone_outcomes (for run stats)
        and in phone_extraction.log (via the "parsers.phone" logger).
        """
        if listing.phone:
            return
        try:
            phone, stage = self._extract_detail_phone(html)
        except Exception as exc:
            log.debug("[%s] phone extraction failed for %s: %s",
                      self.name, listing.url[:60], exc)
            self._record_phone_outcome(listing, "extraction_error")
            phone_log.info("[%s] %s | missing | reason=extraction_error (%s)",
                           self.name, listing.url[:120], type(exc).__name__)
            return
        if phone:
            listing.phone = phone
            self._record_phone_outcome(listing, "found:" + stage)
            phone_log.info("[%s] %s | found %s | stage=%s",
                           self.name, listing.url[:120], phone, stage)
            return
        # Page source exhausted — try click-to-reveal XHR endpoints.
        reason = "no_phone_source"
        if self.phone_endpoint_enabled:
            try:
                for endpoint in self._discover_phone_endpoints(html):
                    phone = self.fetch_phone_endpoint(
                        session or self._ephemeral_session(),
                        endpoint, listing.url)
                    if phone:
                        listing.phone = phone
                        self._record_phone_outcome(listing, "found:endpoint")
                        phone_log.info(
                            "[%s] %s | found %s | stage=endpoint | url=%s",
                            self.name, listing.url[:120], phone, endpoint)
                        return
                    reason = "endpoint_failed"
            except Exception as exc:
                log.debug("[%s] phone endpoint discovery failed for %s: %s",
                          self.name, listing.url[:60], exc)
        self._record_phone_outcome(listing, reason)
        phone_log.info("[%s] %s | missing | reason=%s",
                       self.name, listing.url[:120], reason)

    def _ephemeral_session(self) -> requests.Session:
        """Throwaway session with randomized browser headers (no cookies).

        Used for click-to-reveal XHR endpoints when the detail page was
        fetched without a session (self.fetch) — the endpoint needs the
        right headers + Referer, not the page's cookie jar.
        """
        sess = requests.Session()
        _pk = self._proxy_kwargs()
        if _pk:
            sess.proxies = _pk
        sess.headers.update(_random_headers())
        sess.headers["X-Requested-With"] = "XMLHttpRequest"
        return sess

    def _discover_phone_endpoints(self, html: str) -> list[str]:
        """Find click-to-reveal phone endpoint URLs embedded in the page.

        The reveal button's JS references the endpoint:
        data-show-phone-url-value (kn.kz), a "phonesUrl" state key
        (krisha.kz), or an /ajax/...phone path. Returns absolute URLs,
        deduplicated, capped at 3.
        """
        found: list[str] = []
        for rx in self._PHONE_ENDPOINT_RES:
            for m in rx.finditer(html or ""):
                url = m.group(1)
                if not url:
                    continue
                if url.startswith("//"):
                    url = "https:" + url
                elif url.startswith("/") and self.base_url:
                    url = self.base_url.rstrip("/") + url
                if not url.startswith(("http://", "https://")):
                    continue
                if url in found:
                    continue
                found.append(url)
                if len(found) >= 3:
                    return found
        return found

    def _phone_proxy_hot(self, key: str) -> bool:
        """True while the proxy's per-IP reveal budget is (probably) spent."""
        last = self._phone_proxy_ok.get(key)
        return (last is not None
                and (time.monotonic() - last) < self.phone_proxy_cooldown)

    def _note_phone_proxy_spent(self, pk: dict) -> None:
        """Mark an exit IP's reveal budget spent (success) or the exit burnt
        (403 / connect failure): skip it for phone_proxy_cooldown seconds."""
        key = pk.get("https") or pk.get("http") or ""
        if key:
            self._phone_proxy_ok[key] = time.monotonic()

    def _phone_proxy_tries(self) -> list[dict]:
        """Rotated proxy picks for phone endpoint calls.

        The reveal API rate-limits per client IP, so every call should use
        a fresh exit IP. Returns up to three random pool proxies that are
        not in their post-success cooldown; direct (``{}``) only when the
        pool is disabled or empty. When every pick is still cooling down,
        one hot pick is returned (a cheap 400 beats skipping the call)."""
        first = self._proxy_kwargs()
        if not first:
            return [{}]
        picks: list[dict] = []
        seen: set[str] = set()
        last = first
        for _ in range(8):
            pk = self._proxy_kwargs() or last
            last = pk
            key = pk.get("https") or pk.get("http") or ""
            if not key:
                return [{}]
            if key not in seen:
                seen.add(key)
                if not self._phone_proxy_hot(key):
                    picks.append(pk)
            if len(picks) >= 3 or len(seen) >= 8:
                break
        # Direct (no proxy) always gets the last slot: its own per-IP
        # budget refills within minutes and must keep being harvested.
        picks.append({})
        return picks

    def fetch_phone_endpoint(
        self,
        session: requests.Session,
        url: str,
        referer: str,
    ) -> str:
        """GET a click-to-show phone endpoint in the page's own session.

        Sites that keep the number out of the detail HTML (kn.kz) serve
        it from an XHR endpoint. Answers seen in the wild: JSON with a
        "phones" array (kn.kz) or an HTML fragment with a tel: link.
        Returns the first valid, non-blacklisted phone or "".
        """
        if not url:
            return ""
        # Soft-blocked (too many consecutive 4xx): skip the HTTP call so
        # we stop feeding the rate-limiter and the block can lift.
        if time.monotonic() < self._phone_block_until:
            return ""
        # Rotate exit IPs across attempts: the reveal budget is per IP, so
        # retrying through the same proxy only re-hits an empty bucket.
        # The sticky page session carries no proxies (they are per-request
        # in _fetch_cffi), so they must be passed explicitly here too.
        # Every chain entry gets its own attempt — a dead proxy or an
        # empty bucket must not prevent the next exit IP from being tried.
        tries = self._phone_proxy_tries()
        resp = None
        for attempt, pk in enumerate(tries):
            if attempt:
                time.sleep(self.phone_endpoint_cooldown)
            try:
                # Serialize across the _enrich_photos thread pool: the reveal
                # API rate-limits rapid concurrent calls (OLX -> 400).
                with self._phone_endpoint_lock:
                    interval = self.phone_endpoint_min_interval
                    if interval and self.phone_endpoint_jitter:
                        interval *= 1.0 + random.uniform(
                            -self.phone_endpoint_jitter,
                            self.phone_endpoint_jitter)
                    elapsed = time.monotonic() - self._phone_last_call
                    if elapsed < interval:
                        time.sleep(interval - elapsed)
                    self._phone_last_call = time.monotonic()
                    tmo = self.timeout if not pk else min(self.timeout, 5)
                    resp = session.get(url, timeout=tmo, proxies=pk, headers={
                        "Referer": referer,
                        "X-Requested-With": "XMLHttpRequest",
                        "Accept": "*/*",
                    })
                self._note_proxy_used(pk)
            except Exception as exc:
                log.debug("[%s] phone endpoint failed: %s", self.name, exc)
                phone_log.info("[%s] endpoint %s | error=%s (%s)",
                               self.name, url[:160], type(exc).__name__,
                               (pk.get("https") or "direct")[:40])
                if pk:
                    self._note_phone_proxy_spent(pk)
                continue
            self._note_proxy_used(pk)
            if resp.status_code == 200:
                self._phone_consec_fail = 0
                self._note_phone_proxy_spent(pk)
                break
            # 400/403/429/503 = soft-block (rate-limit / bot flag). A 400 on
            # a proxied exit just means that IP's bucket is empty — the next
            # chain entry has its own budget, so keep going. A 403 also marks
            # the exit burnt (WAF). The consecutive-failure breaker only
            # matters for single-exit (direct) parsers like kn/krisha; OLX
            # disables it (phone_block_threshold = 0) because OLX 400s are
            # unpunished bucket drains, not escalating blocks.
            if resp.status_code in (400, 403, 429, 503):
                if pk and resp.status_code == 403:
                    self._note_phone_proxy_spent(pk)
                if self.phone_block_threshold:
                    self._phone_consec_fail += 1
                    if (self._phone_consec_fail
                            >= self.phone_block_threshold):
                        self._phone_block_until = (
                            time.monotonic() + self.phone_block_cooldown)
                        phone_log.info(
                            "[%s] endpoint %s | blocked after %d consecutive "
                            "%s responses, pausing %.0fs",
                            self.name, url[:160], self._phone_consec_fail,
                            resp.status_code, self.phone_block_cooldown)
                phone_log.info(
                    "[%s] endpoint %s | http=%s via %s (try %d/%d)",
                    self.name, url[:160], resp.status_code,
                    (pk.get("https") or "direct")[:40],
                    attempt + 1, len(tries))
                continue
            log.debug("[%s] phone endpoint %s -> HTTP %s",
                      self.name, url, resp.status_code)
            phone_log.info("[%s] endpoint %s | http=%s",
                           self.name, url[:160], resp.status_code)
            return ""
        if resp is None or resp.status_code != 200:
            return ""
        body = resp.text or ""
        m = re.search(r'"phones"\s*:\s*\[([^\]]*)\]', body)
        if m:
            for part in re.findall(r'"([^"]+)"', m.group(1)):
                phone = self._valid_phone(part)
                if phone and phone not in self.phone_blacklist:
                    phone_log.info("[%s] endpoint %s | found %s",
                                   self.name, url[:160], phone)
                    return phone
        # JSON answers with a phone key: "phone": "+7 ...", "tel": 7705...
        for m in self._PHONE_KEY_RE.finditer(body):
            phone = self._valid_phone(
                m.group(1) or m.group(2) or m.group(3) or m.group(4) or "")
            if phone and phone not in self.phone_blacklist:
                phone_log.info("[%s] endpoint %s | found %s",
                               self.name, url[:160], phone)
                return phone
        # HTML-fragment answers: reuse the page extractor (tel: links, ...)
        phone = self.extract_detail_phone(body)
        if phone:
            phone_log.info("[%s] endpoint %s | found %s",
                           self.name, url[:160], phone)
        else:
            phone_log.info("[%s] endpoint %s | no_phone", self.name, url[:160])
        return phone
