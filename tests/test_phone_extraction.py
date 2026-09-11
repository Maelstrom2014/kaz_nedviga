"""Tests for detail-page phone extraction.

Sites hide the contact phone behind a "show number" button, but the raw
value stays in the page source (tel: links, data-* attributes, embedded
JSON state, visible text). extract_detail_phone() must find it without a
browser, normalize it to +7XXXXXXXXXX, and reject masked numbers and
11-digit IDs/hashes.
"""
import logging
import sys
import time
from unittest.mock import MagicMock, patch

from parsers.base import BaseParser, ParserRunStats, phone_log
from parsers.etagi import EtagiParser
from parsers.kn import KnParser
from parsers.krisha import KrishaParser
from parsers.olx import OlxParser
from parsers.models import Listing


def _parser():
    return KrishaParser()


class TestValidPhone:
    """Normalization + rejection rules (BaseParser._valid_phone)."""

    def test_kz_plus7(self):
        assert BaseParser._valid_phone("+7 705 123 45 67") == "+77051234567"

    def test_kz_bare_7(self):
        assert BaseParser._valid_phone("7 7051234567") == "+77051234567"

    def test_ru_plus7_9xx(self):
        assert BaseParser._valid_phone("+7 (900) 123-45-67") == "+79001234567"

    def test_ru_8_4xx_landline(self):
        assert BaseParser._valid_phone("8 495 123-45-67") == "+74951234567"

    def test_zero_zero_prefix(self):
        assert BaseParser._valid_phone("0077051234567") == "+77051234567"

    def test_masked_rejected(self):
        assert BaseParser._valid_phone("+7 705 *** ** **") == ""

    def test_masked_question_rejected(self):
        assert BaseParser._valid_phone("+7 705 ? ?? ?? ??") == ""

    def test_dot_masked_rejected(self):
        assert BaseParser._valid_phone("+7 705 ••• •• ••") == ""

    def test_short_rejected(self):
        assert BaseParser._valid_phone("+7 705 123 45") == ""

    def test_long_rejected(self):
        assert BaseParser._valid_phone("+7 705 123 456 78") == ""

    def test_bad_network_code_rejected(self):
        # Second digit 0/1/2 is not a KZ/RU network code family.
        assert BaseParser._valid_phone("+7 712 123 45 67") == "+77121234567"
        assert BaseParser._valid_phone("+7 212 123 45 67") == ""
        assert BaseParser._valid_phone("+7 112 123 45 67") == ""

    def test_empty(self):
        assert BaseParser._valid_phone("") == ""


class TestExtractDetailPhone:
    """extract_detail_phone() over synthetic detail pages."""

    def test_tel_link(self):
        html = '<body><a href="tel:+77051234567">Показать номер</a></body>'
        assert _parser().extract_detail_phone(html) == "+77051234567"

    def test_data_phone_attr(self):
        html = '<div data-phone="8 705 123-45-67" class="phone-wrap">Скрыт</div>'
        assert _parser().extract_detail_phone(html) == "+77051234567"

    def test_data_tel_attr(self):
        html = '<span data-tel="+7 (771) 234 56 78">телефон</span>'
        assert _parser().extract_detail_phone(html) == "+77712345678"

    def test_json_state_key(self):
        html = r'<script>var data={"ticket":1,"phone":"+7 708 987 65 43","la":43.2};</script>'
        assert _parser().extract_detail_phone(html) == "+77089876543"

    def test_json_state_tel_key(self):
        html = r'<script>window.__data__ = {"contact":{"tel":"77001112233"}};</script>'
        assert _parser().extract_detail_phone(html) == "+77001112233"

    def test_visible_text_kz(self):
        html = "<body><p>Звоните: +7 701 222 33 44</p></body>"
        assert _parser().extract_detail_phone(html) == "+77012223344"

    def test_visible_text_8_format(self):
        html = "<body><p>8 705 123 45 67</p></body>"
        assert _parser().extract_detail_phone(html) == "+77051234567"

    def test_masked_in_text_rejected(self):
        html = "<body><p>+7 705 *** ** **</p><a href='#'>Показать</a></body>"
        assert _parser().extract_detail_phone(html) == ""

    def test_eleven_digit_id_rejected(self):
        # 11-digit run with an invalid network-code family (70...) must not
        # pass, and a bare ID far from any phone keyword must not either.
        html = '<body><div data-id="70012345678"></div><p>Лицей № 90</p></body>'
        assert _parser().extract_detail_phone(html) == ""

    def test_keyword_anchored_raw_html(self):
        # Number only in raw HTML (e.g. inside a script string) next to a
        # phone keyword — accepted via the keyword fallback.
        html = '<script>var t="tel: +7 747 111 22 33";</script>'
        assert _parser().extract_detail_phone(html) == "+77471112233"

    def test_structured_wins_over_text(self):
        html = ('<a href="tel:+77771112233">т</a>'
                '<p>8 705 999 88 77</p>')
        assert _parser().extract_detail_phone(html) == "+77771112233"

    def test_empty_html(self):
        assert _parser().extract_detail_phone("") == ""
        assert _parser().extract_detail_phone(None) == ""


class TestParserIntegration:
    """_fetch_detail_photos must fill listing.phone from the detail page."""

    def test_etagi_fetch_sets_phone(self):
        parser = EtagiParser()
        listing = Listing(url="https://almaty.etagi.com/realty_rent/1/",
                          source="etagi.com")
        html = ('<div data-testid="object_page_gallery_slider"></div>'
                '<div data-phone="+7 705 123 45 67"></div>')
        with patch.object(parser, "fetch", return_value=html):
            parser._fetch_detail_photos(listing)
        assert listing.phone == "+77051234567"

    def test_olx_fetch_sets_phone(self):
        parser = OlxParser()
        listing = Listing(url="https://www.olx.kz/d/obyavlenie/x-1/",
                          source="olx.kz")
        html = ('<div data-testid="ad-price">120 000 ₸</div>'
                '<a href="tel:77051234567">показать номер</a>')
        # olx uses the cffi fetch path (use_cffi=True) -> fetch_detail
        # dispatches to self.fetch, not fetch_session.
        with patch.object(parser, "fetch", return_value=html):
            parser._fetch_detail_photos(listing)
        assert listing.phone == "+77051234567"

    def test_krisha_fetch_sets_phone(self):
        parser = KrishaParser()
        listing = Listing(url="https://krisha.kz/a/show/1", source="krisha.kz")
        html = '<div class="gallery__container"></div>' \
               '<span data-tel="8 771 234 56 78">скрыт</span>'
        with patch.object(parser, "fetch_session", return_value=(MagicMock(), html)):
            parser._fetch_detail_photos(listing)
        assert listing.phone == "+77712345678"

    def test_kn_fetch_sets_phone(self):
        parser = KnParser()
        listing = Listing(url="https://www.kn.kz/card/1", source="kn.kz")
        html = '<div class="swiper"></div>' \
               '<script>{"phone":"+7 700 111 22 33"}</script>'
        with patch.object(parser, "fetch_session",
                          return_value=(MagicMock(), html)):
            parser._fetch_detail_photos(listing)
        assert listing.phone == "+77001112233"

    def test_existing_phone_not_overwritten(self):
        parser = KrishaParser()
        listing = Listing(url="https://krisha.kz/a/show/1", source="krisha.kz",
                          phone="+77000000000")
        html = '<span data-tel="8 771 234 56 78"></span>'
        with patch.object(parser, "fetch_session",
                          return_value=(MagicMock(), html)):
            parser._fetch_detail_photos(listing)
        assert listing.phone == "+77000000000"

    def test_fetch_error_leaves_phone_empty(self):
        parser = KrishaParser()
        listing = Listing(url="https://krisha.kz/a/show/1", source="krisha.kz")
        with patch.object(parser, "fetch_session",
                          side_effect=Exception("blocked")):
            parser._fetch_detail_photos(listing)
        assert listing.phone == ""


class TestPhoneBlacklist:
    """Site hotlines in the footer must never surface as advertiser phones."""

    def test_blacklisted_hotline_rejected(self):
        p = KnParser()
        html = '<a href="tel:77000877877">+7-7000-877-877 с 09:00 до 18:00</a>'
        assert p.extract_detail_phone(html) == ""

    def test_blacklist_falls_through_to_next_source(self):
        p = KnParser()
        html = ('<a href="tel:77000877877">0800</a>'
                '<div data-phone="+7 705 123 45 67"></div>')
        assert p.extract_detail_phone(html) == "+77051234567"

    def test_default_blacklist_empty(self):
        p = KrishaParser()
        html = '<a href="tel:77000877877">x</a>'
        assert p.extract_detail_phone(html) == "+77000877877"


class TestPhoneEndpoint:
    """fetch_phone_endpoint() for click-to-show XHR endpoints."""

    @staticmethod
    def _resp(status=200, text=""):
        r = MagicMock()
        r.status_code = status
        r.text = text
        return r

    def test_json_phones_array(self):
        p = KnParser()
        s = MagicMock()
        s.get.return_value = self._resp(
            200, '{"phones":["+7 701 633 22 22","+7 700 223 0508"],"html":"x"}')
        phone = p.fetch_phone_endpoint(s, "https://www.kn.kz/card/phone/1/1",
                                       "https://www.kn.kz/card/1")
        assert phone == "+77016332222"
        kwargs = s.get.call_args.kwargs
        assert kwargs["headers"]["Referer"] == "https://www.kn.kz/card/1"
        assert kwargs["headers"]["X-Requested-With"] == "XMLHttpRequest"

    def test_blacklisted_skipped_in_endpoint(self):
        p = KnParser()
        s = MagicMock()
        s.get.return_value = self._resp(
            200, '{"phones":["+7 700 087 78 77","+7 705 555 44 33"]}')
        assert p.fetch_phone_endpoint(s, "u", "r") == "+77055554433"

    def test_html_fragment_answer(self):
        p = KnParser()
        s = MagicMock()
        s.get.return_value = self._resp(
            200, '<div><a href="tel:+77081234567">7 081 23 45 67</a></div>')
        assert p.fetch_phone_endpoint(s, "u", "r") == "+77081234567"

    def test_http_error_returns_empty(self):
        p = KnParser()
        s = MagicMock()
        s.get.return_value = self._resp(404, '{"error":"not found"}')
        assert p.fetch_phone_endpoint(s, "u", "r") == ""

    def test_exception_returns_empty(self):
        p = KnParser()
        s = MagicMock()
        s.get.side_effect = ConnectionError("net")
        assert p.fetch_phone_endpoint(s, "u", "r") == ""

    def test_empty_url(self):
        p = KnParser()
        assert p.fetch_phone_endpoint(MagicMock(), "", "r") == ""


class TestKnEndpointIntegration:
    """kn.kz: masked preview in HTML + real number from /card/phone/{ID}/1."""

    def test_kn_endpoint_fills_phone(self):
        parser = KnParser()
        listing = Listing(url="https://www.kn.kz/card/1", source="kn.kz")
        html = ('<div data-controller="show-phone" '
                'data-show-phone-url-value="/card/phone/1/1">'
                '<span class="">+7 701 </span></div>')
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = '{"phones":["+7 701 633 22 22"],"html":""}'
        session.get.return_value = resp
        with patch.object(parser, "fetch_session",
                          return_value=(session, html)):
            parser._fetch_detail_photos(listing)
        assert listing.phone == "+77016332222"
        # endpoint called with the page URL as Referer
        assert session.get.call_args is not None
        _, kwargs = session.get.call_args
        assert kwargs["headers"]["Referer"] == "https://www.kn.kz/card/1"

    def test_kn_hotline_never_reported(self):
        parser = KnParser()
        listing = Listing(url="https://www.kn.kz/card/1", source="kn.kz")
        html = ('<noindex><a class="kn-text-color-white" '
                'href="tel:77000877877">+7-7000-877-877</a></noindex>'
                '<div data-show-phone-url-value="/card/phone/1/1">'
                '<span>+7 701 </span></div>')
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = '{"phones":["+7 705 987 65 43"],"html":""}'
        session.get.return_value = resp
        with patch.object(parser, "fetch_session",
                          return_value=(session, html)):
            parser._fetch_detail_photos(listing)
        assert listing.phone == "+77059876543"

    def test_kn_endpoint_absent_phone_stays_empty(self):
        parser = KnParser()
        listing = Listing(url="https://www.kn.kz/card/1", source="kn.kz")
        html = '<div data-show-phone-url-value="/card/phone/1/1">' \
               '<span>+7 701 </span></div>'
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 404
        resp.text = ""
        session.get.return_value = resp
        with patch.object(parser, "fetch_session",
                          return_value=(session, html)):
            parser._fetch_detail_photos(listing)
        assert listing.phone == ""


class TestShowPhoneButton:
    """Number hidden behind a "Показать телефон" button lives in the
    button's own markup or in the page's inline JS."""

    def test_onclick_handler(self):
        html = ('<button id="show-phone" '
                'onclick="showPhone(\'7 705 123 45 67\')">Показать телефон'
                '</button>')
        assert _parser().extract_detail_phone(html) == "+77051234567"

    def test_data_attr_on_button(self):
        html = ('<button data-phone-number="8 705 123 45 67" '
                'class="js-show-phone">Показать номер</button>')
        assert _parser().extract_detail_phone(html) == "+77051234567"

    def test_plain_text_inside_button(self):
        html = ('<button class="phone-show">Показать свой номер: '
                '+7 701 222 33 44</button>')
        assert _parser().extract_detail_phone(html) == "+77012223344"

    def test_button_beats_footer_hotline(self):
        html = ('<button data-tel="8 771 234 56 78">Показать телефон</button>'
                '<footer><a href="tel:77779998877">+7 777 999 88 77</a>'
                '</footer>')
        assert _parser().extract_detail_phone(html) == "+77712345678"

    def test_base64_in_script(self):
        # b64("+77051234567")
        html = ('<button>Показать телефон</button>'
                '<script>var p = atob("Kzc3MDUxMjM0NTY3");</script>')
        assert _parser().extract_detail_phone(html) == "+77051234567"

    def test_charcode_in_script(self):
        seq = ",".join(str(ord(c)) for c in "+77051234567")
        html = ('<button>Показать телефон</button>'
                '<script>var p = String.fromCharCode(' + seq + ');</script>')
        assert _parser().extract_detail_phone(html) == "+77051234567"


class TestBareNational:
    """Bare 10-digit KZ national numbers, without leading +7/8/007."""

    def test_valid_phone_bare_10(self):
        assert BaseParser._valid_phone("7051234567") == "+77051234567"
        assert BaseParser._valid_phone("7 705 123 45 67") == "+77051234567"
        assert BaseParser._valid_phone("2051234567") == ""
        assert BaseParser._valid_phone("1051234567") == ""

    def test_visible_formatted(self):
        html = "<body><p>Звоните: 705 123 45 67</p></body>"
        assert _parser().extract_detail_phone(html) == "+77051234567"

    def test_unseparated_in_free_text_rejected(self):
        html = "<body><p>Арт. 7051234567 — свободная продажа</p></body>"
        assert _parser().extract_detail_phone(html) == ""

    def test_structured_unseparated_ok(self):
        html = '<div data-phone="7051234567">тел</div>'
        assert _parser().extract_detail_phone(html) == "+77051234567"


class TestWhatsAppLinks:
    """WhatsApp deep links carry the number in the URL itself."""

    def test_wa_me_link(self):
        html = '<body><a href="https://wa.me/77051234567">Написать</a></body>'
        assert _parser().extract_detail_phone(html) == "+77051234567"

    def test_whatsapp_scheme(self):
        html = ('<body><a href="whatsapp://send?phone=77051234567">W'
                '</a></body>')
        assert _parser().extract_detail_phone(html) == "+77051234567"

    def test_api_whatsapp(self):
        html = ('<body><a href="https://api.whatsapp.com/send?phone='
                '77051234567&text=hi">W</a></body>')
        assert _parser().extract_detail_phone(html) == "+77051234567"


class TestJsonStateExtended:
    """JSON state: camelCase keys, unquoted numbers, arrays, single quotes."""

    def test_camelcase_key(self):
        html = r'<script>var s = {"phoneNumber": "8 705 123 45 67"};</script>'
        assert _parser().extract_detail_phone(html) == "+77051234567"

    def test_unquoted_number(self):
        html = r'<script>var s = {"phone": 77051234567};</script>'
        assert _parser().extract_detail_phone(html) == "+77051234567"

    def test_array_value(self):
        html = r'<script>var s = {"phones": ["+7 705 123 45 67"]};</script>'
        assert _parser().extract_detail_phone(html) == "+77051234567"

    def test_single_quoted(self):
        html = r"<script>var s = {'phone': '+7 705 123 45 67'};</script>"
        assert _parser().extract_detail_phone(html) == "+77051234567"


class TestEndpointDiscovery:
    """Number served from a click-to-reveal XHR endpoint: discovered from
    the page source and fetched with browser-like headers + Referer."""

    def test_phones_url_discovered(self):
        parser = KrishaParser()
        listing = Listing(url="https://www.krisha.kz/offer/1",
                          source="krisha.kz")
        html = ('<div>Показать телефон</div>'
                r'<script>var s = {"phonesUrl": "/ajax/phone/1"};</script>')
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = '{"phones": ["+7 701 633 22 22"]}'
        session.get.return_value = resp
        with patch.object(parser, "_ephemeral_session",
                          return_value=session):
            parser._enrich_phone(listing, html)
        assert listing.phone == "+77016332222"
        _, kwargs = session.get.call_args
        assert kwargs["headers"]["Referer"] == "https://www.krisha.kz/offer/1"

    def test_data_show_phone_url_discovered(self):
        parser = KnParser()
        listing = Listing(url="https://www.kn.kz/card/1", source="kn.kz")
        html = ('<div data-show-phone-url-value="/card/phone/1/1">'
                '<span>Показать телефон</span></div>')
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = '{"phones": ["+7 705 987 65 43"]}'
        session.get.return_value = resp
        with patch.object(parser, "_ephemeral_session",
                          return_value=session):
            parser._enrich_phone(listing, html)
        assert listing.phone == "+77059876543"

    def test_no_endpoint_phone_stays_empty(self):
        parser = KrishaParser()
        listing = Listing(url="https://www.krisha.kz/offer/1",
                          source="krisha.kz")
        session = MagicMock()
        with patch.object(parser, "_ephemeral_session",
                          return_value=session):
            parser._enrich_phone(
                listing, "<html><body>no phones</body></html>")
        session.get.assert_not_called()
        assert listing.phone == ""


class TestEndpointBodyScan:
    """fetch_phone_endpoint() answers with a phone key, not just "phones"."""

    def test_json_phone_key_answer(self):
        p = KnParser()
        s = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = '{"phone": "+7 705 123 45 67"}'
        s.get.return_value = resp
        assert p.fetch_phone_endpoint(s, "u", "r") == "+77051234567"

    def test_blacklisted_falls_to_key_answer(self):
        p = KnParser()
        s = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = '{"phones": ["77000877877"], "phone": "+7 705 123 45 67"}'
        s.get.return_value = resp
        assert p.fetch_phone_endpoint(s, "u", "r") == "+77051234567"


class TestPhoneStats:
    """Per-run phone statistics: found/missing counts + missing reasons."""

    def test_collect_phone_stats_counts(self):
        p = KrishaParser()
        a = Listing(url="https://krisha.kz/a", source="krisha.kz",
                    phone="+77051234567")
        b = Listing(url="https://krisha.kz/b", source="krisha.kz")
        c = Listing(url="https://krisha.kz/c", source="krisha.kz")
        p._phone_outcomes = {"https://krisha.kz/b": "no_phone_source"}
        # c has no recorded outcome -> detail_fetch_failed
        stats = p.last_stats
        p._collect_phone_stats([a, b, c], stats)
        assert stats.phones_found == 1
        assert stats.phones_missing == 2
        assert stats.phone_reasons == {
            "no_phone_source": 1, "detail_fetch_failed": 1}

    def test_enrich_phone_records_found_outcome(self):
        p = KrishaParser()
        listing = Listing(url="https://krisha.kz/x", source="krisha.kz")
        p._enrich_phone(listing,
                        '<div data-phone="+7 705 123 45 67"></div>')
        assert listing.phone == "+77051234567"
        assert p._phone_outcomes["https://krisha.kz/x"] == "found:data_attr"

    def test_enrich_phone_records_missing_reason(self):
        p = KrishaParser()
        listing = Listing(url="https://krisha.kz/y", source="krisha.kz")
        session = MagicMock()
        with patch.object(p, "_ephemeral_session", return_value=session):
            p._enrich_phone(listing, "<html><body>nothing</body></html>")
        assert listing.phone == ""
        assert p._phone_outcomes["https://krisha.kz/y"] == "no_phone_source"

    def test_enrich_phone_records_endpoint_failed(self):
        p = KrishaParser()
        listing = Listing(url="https://krisha.kz/e", source="krisha.kz")
        html = ('<html><body><script>var s = {"phonesUrl": '
                '"/ajax/phone/1"};</script></body></html>')
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = '{"html": ""}'
        session.get.return_value = resp
        with patch.object(p, "_ephemeral_session", return_value=session):
            p._enrich_phone(listing, html)
        assert listing.phone == ""
        assert p._phone_outcomes["https://krisha.kz/e"] == "endpoint_failed"

    def test_enrich_phone_records_extraction_error(self):
        p = KrishaParser()
        listing = Listing(url="https://krisha.kz/z", source="krisha.kz")
        with patch.object(p, "_extract_detail_phone",
                          side_effect=ValueError("boom")):
            p._enrich_phone(listing, "<html></html>")
        assert listing.phone == ""
        assert p._phone_outcomes["https://krisha.kz/z"] == "extraction_error"

    def test_to_dict_includes_phone_stats(self):
        stats = ParserRunStats(name="krisha", base_url="https://krisha.kz")
        d = stats.to_dict()
        assert d["phones_found"] == 0
        assert d["phones_missing"] == 0
        assert d["phone_reasons"] == {}


class TestPhoneLog:
    """The 'parsers.phone' logger: separate file, per-listing lines."""

    def _enrich_capturing(self, p, listing, html, **kw):
        lines = []

        class _H(logging.Handler):
            def emit(self, record):
                lines.append(record.getMessage())

        h = _H()
        phone_log.addHandler(h)
        try:
            p._enrich_phone(listing, html, **kw)
        finally:
            phone_log.removeHandler(h)
        return lines

    def test_found_line(self):
        p = KrishaParser()
        listing = Listing(url="https://krisha.kz/x", source="krisha.kz")
        lines = self._enrich_capturing(
            p, listing, '<div data-phone="+7 705 123 45 67"></div>')
        joined = " | ".join(lines)
        assert "found +77051234567" in joined
        assert "stage=data_attr" in joined

    def test_missing_line(self):
        p = KrishaParser()
        listing = Listing(url="https://krisha.kz/y", source="krisha.kz")
        session = MagicMock()
        with patch.object(p, "_ephemeral_session", return_value=session):
            lines = self._enrich_capturing(
                p, listing, "<html><body>nope</body></html>")
        joined = " | ".join(lines)
        assert "missing" in joined
        assert "reason=no_phone_source" in joined

    def test_phone_log_has_own_file_and_no_propagate(self):
        assert phone_log.propagate is False
        assert any(isinstance(h, logging.FileHandler)
                   and "phone_extraction" in h.baseFilename
                   for h in phone_log.handlers)


class TestOlxPhoneEndpoint:
    """OLX hides the number behind a reveal button and keeps no number in
    the page source. The ad ID is present (ad-id= links / JSON-LD sku) and
    the number is fetched from the offers /phones API."""

    def test_uses_sticky_cffi_session(self):
        assert OlxParser.session_sticky is True

    def test_extract_ad_id_from_link(self):
        assert OlxParser._extract_ad_id(
            '<a href="/purchase/promote/variant/?ad-id=399002995">x</a>'
        ) == "399002995"

    def test_extract_ad_id_from_sku(self):
        assert OlxParser._extract_ad_id(
            '<script type="application/ld+json">'
            '{"@type":"Product","sku":"399002995"}</script>'
        ) == "399002995"

    def test_extract_ad_id_none(self):
        assert OlxParser._extract_ad_id("<html>no ad id</html>") is None

    def test_endpoint_discovered(self):
        parser = OlxParser()
        html = '<a href="/purchase/promote/?ad-id=399002995">x</a>'
        assert parser._discover_phone_endpoints(html) == [
            "https://www.olx.kz/api/v1/offers/399002995/phones"]

    def test_enrich_phone_via_endpoint(self):
        parser = OlxParser()
        listing = Listing(url="https://www.olx.kz/d/obyavlenie/x-IDr0aUb.html",
                          source="olx.kz")
        html = ('<html><body>'
                '<a href="/purchase/promote/?ad-id=399002995">x</a>'
                '</body></html>')
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = '{"data": {"phones": ["870 894 20700"]}}'
        session.get.return_value = resp
        parser._enrich_phone(listing, html, session=session)
        assert listing.phone == "+77089420700"
        args, kwargs = session.get.call_args
        assert args[0] == "https://www.olx.kz/api/v1/offers/399002995/phones"
        assert kwargs["headers"]["Referer"] == listing.url


class TestOlxPhoneReveal:
    """OLX phone reveal via OlxParser._fetch_phone_playwright.

    The number is cycled through methods until one yields it:
      1. the page source (tel: links / embedded numbers) via _resolve_phone,
      2. the offers /phones API via a plain curl_cffi GET (fast path),
      3. a headless Playwright browser that clicks the "show" button.
    The winning method is recorded in phone_extraction.log; an offer that
    is not live (non-200 page / no ad id) gives up immediately. These
    tests cover the ad-id resolution, each method, the retries, phone
    number normalisation and the endpoint rate-limit spacing.
    """

    AD_HTML = '<a href="/purchase/promote/?ad-id=123456789">x</a>'
    URL = "https://www.olx.kz/d/obyavlenie/prod-123.html"

    @staticmethod
    def _cffi(page_status, page_text, endpoint_status, endpoint_json):
        """Mocks for curl_cffi.requests.get: [page response, endpoint response]."""
        page = MagicMock(status_code=page_status, text=page_text)
        endpoint = MagicMock(status_code=endpoint_status)
        endpoint.json.return_value = endpoint_json
        return [page, endpoint]

    @staticmethod
    def _pw(phone_text):
        """A sync_playwright() context manager whose page shows phone_text."""
        container = MagicMock()
        container.count.return_value = 1
        container.first.inner_text.return_value = phone_text
        page = MagicMock()
        page.locator.return_value = container
        ctx = MagicMock()
        ctx.new_page.return_value = page
        browser = MagicMock()
        browser.new_context.return_value = ctx
        pw = MagicMock()
        pw.chromium.launch.return_value = browser
        cm = MagicMock()
        cm.__enter__.return_value = pw
        cm.__exit__.return_value = False
        return cm

    def _listing(self):
        listing = Listing(url=self.URL, source="olx.kz")
        listing.id = "OLX-1"
        return listing

    # ---- ad-id resolution (the two sources it reads) ----
    def test_ad_id_from_ad_id_link(self):
        assert OlxParser._extract_ad_id(
            '<a href="/purchase/promote/?ad-id=123456789">x</a>') == "123456789"

    def test_ad_id_from_jsonld_sku(self):
        assert OlxParser._extract_ad_id(
            '<script type="application/ld+json">'
            '{"@type":"Product","sku":"123456789"}</script>'
        ) == "123456789"

    def test_ad_id_link_takes_priority_over_sku(self):
        html = ('<script type="application/ld+json">'
                '{"sku":"111111111"}</script>'
                '<a href="?ad-id=222222222">x</a>')
        assert OlxParser._extract_ad_id(html) == "222222222"

    def test_ad_id_none_when_absent(self):
        assert OlxParser._extract_ad_id(
            "<html><body>no identifier</body></html>") is None

    # ---- phone cleaning ----
    def test_clean_phone_accepts_valid_numbers(self):
        for raw, expected in [
            ("87089420700", "87089420700"),
            ("77051234567", "77051234567"),
            ("7771234567", "7771234567"),
            ("87051234567", "87051234567"),
            ("870 894 20700", "87089420700"),
            ("+7 705 123 45 67", "77051234567"),
            ("8 (705) 123-45-67", "87051234567"),
        ]:
            assert OlxParser._clean_phone(raw) == expected, raw

    def test_clean_phone_rejects_invalid_numbers(self):
        for raw in ["", None, "abc", "12345", "123456789012",
                    "17051234567", "97051234567", "8705123456789"]:
            assert OlxParser._clean_phone(raw) == "", repr(raw)

    # ---- method 1: offers /phones API via plain curl_cffi ----
    def test_endpoint_returns_phone(self):
        parser = OlxParser()
        listing = self._listing()
        with patch("curl_cffi.requests.get",
                   side_effect=self._cffi(200, self.AD_HTML, 200,
                                          {"data": {"phones": ["870 894 20700"]}})), \
             patch("parsers.olx.phone_log") as pl:
            assert parser._fetch_phone_playwright(listing, self.URL) == "87089420700"
        assert listing.phone == "87089420700"
        pl.info.assert_any_call(
            "[%s] %s | found %s | method=olx_endpoint attempt=%d",
            "OLX-1", self.URL, "87089420700", 1)

    def test_endpoint_normalises_phone_formats(self):
        parser = OlxParser()
        for raw, expected in [
            ("870 894 20700", "87089420700"),
            ("+7 705 123 45 67", "77051234567"),
            ("8 777 123 456", "8777123456"),
        ]:
            listing = self._listing()
            with patch("curl_cffi.requests.get",
                       side_effect=self._cffi(200, self.AD_HTML, 200,
                                              {"data": {"phones": [raw]}})), \
                 patch("time.sleep"):
                assert parser._fetch_phone_playwright(listing, self.URL) == expected, raw
            assert listing.phone == expected

    def test_endpoint_spacing_enforced(self):
        parser = OlxParser()
        listing = self._listing()
        parser._pw_phone_ts = time.time()  # pretend the last call just happened
        with patch("curl_cffi.requests.get",
                   side_effect=self._cffi(200, self.AD_HTML, 200,
                                          {"data": {"phones": ["870 894 20700"]}})), \
             patch("time.sleep") as sleep:
            assert parser._fetch_phone_playwright(listing, self.URL) == "87089420700"
        sleep.assert_called_once()

    # ---- not-live offers give up immediately ----
    def test_not_live_no_ad_id(self):
        parser = OlxParser()
        listing = self._listing()
        with patch("curl_cffi.requests.get") as m, \
             patch("parsers.olx.phone_log") as pl:
            m.return_value = MagicMock(status_code=200, text="<html>no id</html>")
            assert parser._fetch_phone_playwright(listing, self.URL) is None
        assert m.call_count == 1  # page only, endpoint never called
        pl.info.assert_any_call(
            "[%s] %s | missing | method=olx_endpoint (offer not live)",
            "OLX-1", self.URL)

    def test_not_live_page_not_found(self):
        parser = OlxParser()
        listing = self._listing()
        with patch("curl_cffi.requests.get") as m, \
             patch("parsers.olx.phone_log") as pl:
            m.return_value = MagicMock(status_code=404, text="not found")
            assert parser._fetch_phone_playwright(listing, self.URL) is None
        assert m.call_count == 1
        pl.info.assert_any_call(
            "[%s] %s | missing | method=olx_endpoint (offer not live)",
            "OLX-1", self.URL)

    # ---- retries: cycled until a method yields a number ----
    def test_endpoint_retryable_then_success(self):
        parser = OlxParser()
        listing = self._listing()
        calls = []
        for status, data in [(400, None), (200, {"data": {"phones": []}}),
                             (200, {"data": {"phones": ["870 894 20700"]}})]:
            calls += self._cffi(200, self.AD_HTML, status, data)
        with patch("curl_cffi.requests.get", side_effect=calls) as m, \
             patch("time.sleep"), \
             patch("parsers.olx.phone_log") as pl, \
             patch("playwright.sync_api.sync_playwright", return_value=self._pw("")):
            assert parser._fetch_phone_playwright(listing, self.URL) == "87089420700"
        assert m.call_count == 6  # 3 attempts x (page + endpoint)
        pl.info.assert_any_call(
            "[%s] %s | found %s | method=olx_endpoint attempt=%d",
            "OLX-1", self.URL, "87089420700", 3)

    def test_endpoint_json_error_is_retryable(self):
        parser = OlxParser()
        listing = self._listing()
        bad = MagicMock(status_code=200)
        bad.json.side_effect = ValueError("no json")
        calls = [MagicMock(status_code=200, text=self.AD_HTML), bad,
                 MagicMock(status_code=200, text=self.AD_HTML),
                 self._cffi(200, self.AD_HTML, 200,
                            {"data": {"phones": ["+7 705 123 45 67"]}})[1]]
        with patch("curl_cffi.requests.get", side_effect=calls) as m, \
             patch("time.sleep"), \
             patch("parsers.olx.phone_log") as pl, \
             patch("playwright.sync_api.sync_playwright", return_value=self._pw("")):
            assert parser._fetch_phone_playwright(listing, self.URL) == "77051234567"
        assert m.call_count == 4
        pl.info.assert_any_call(
            "[%s] %s | found %s | method=olx_endpoint attempt=%d",
            "OLX-1", self.URL, "77051234567", 2)

    # ---- method 2: Playwright browser click fallback ----
    def test_browser_reveals_phone_when_endpoint_empty(self):
        parser = OlxParser()
        listing = self._listing()
        empty = self._cffi(200, self.AD_HTML, 200, {"data": {"phones": []}})
        with patch("curl_cffi.requests.get", side_effect=empty), \
             patch("time.sleep"), \
             patch("parsers.olx.phone_log") as pl, \
             patch("playwright.sync_api.sync_playwright",
                   return_value=self._pw("8 705 123 45 67")):
            assert parser._fetch_phone_playwright(listing, self.URL) == "87051234567"
        assert listing.phone == "87051234567"
        pl.info.assert_any_call(
            "[%s] %s | found %s | method=olx_playwright attempt=%d",
            "OLX-1", self.URL, "87051234567", 1)

    def test_browser_not_installed_returns_none(self):
        parser = OlxParser()
        listing = self._listing()
        calls = []
        for _ in range(OlxParser.phone_max_attempts):
            calls += self._cffi(200, self.AD_HTML, 200, {"data": {"phones": []}})
        with patch("curl_cffi.requests.get", side_effect=calls) as m, \
             patch("time.sleep"), \
             patch.dict(sys.modules, {"playwright.sync_api": None}), \
             patch("parsers.olx.phone_log") as pl:
            assert parser._fetch_phone_playwright(listing, self.URL) is None
        assert m.call_count == OlxParser.phone_max_attempts * 2
        pl.info.assert_any_call(
            "[%s] %s | missing | method=none (endpoint+playwright x%d)",
            "OLX-1", self.URL, OlxParser.phone_max_attempts)

    def test_all_attempts_fail(self):
        parser = OlxParser()
        parser.phone_endpoint_min_interval = 0.0  # no rate-limit waits to count
        listing = self._listing()
        calls = []
        for _ in range(OlxParser.phone_max_attempts):
            calls += self._cffi(200, self.AD_HTML, 429, None)
        with patch("curl_cffi.requests.get", side_effect=calls) as m, \
             patch("time.sleep") as sleep, \
             patch("parsers.olx.phone_log") as pl, \
             patch("playwright.sync_api.sync_playwright",
                   return_value=self._pw("masked")):
            assert parser._fetch_phone_playwright(listing, self.URL) is None
        assert not listing.phone
        assert m.call_count == OlxParser.phone_max_attempts * 2
        assert sleep.call_count == OlxParser.phone_max_attempts - 1
        sleep.assert_called_with(OlxParser.phone_retry_delay)
        pl.info.assert_any_call(
            "[%s] %s | missing | method=none (endpoint+playwright x%d)",
            "OLX-1", self.URL, OlxParser.phone_max_attempts)

    # ---- _resolve_phone: page source first, then the cycle ----
    def test_resolve_phone_from_page_source(self):
        parser = OlxParser()
        listing = self._listing()
        html = '<html><body><a href="tel:77051234567">show number</a></body></html>'
        with patch("parsers.olx.phone_log") as pl:
            parser._resolve_phone(listing, html)
        assert listing.phone == "+77051234567"
        pl.info.assert_any_call(
            "[%s] %s | found %s | method=page_source",
            "OLX-1", self.URL, "+77051234567")

    def test_resolve_phone_raw_digits_from_page_source(self):
        parser = OlxParser()
        listing = self._listing()
        html = "<html><body>id 8705123456 end</body></html>"
        with patch("parsers.olx.phone_log") as pl:
            parser._resolve_phone(listing, html)
        assert listing.phone == "8705123456"
        pl.info.assert_any_call(
            "[%s] %s | found %s | method=page_source",
            "OLX-1", self.URL, "8705123456")

    def test_resolve_phone_falls_through_to_cycle(self):
        parser = OlxParser()
        listing = self._listing()
        with patch.object(parser, "_fetch_phone_playwright",
                          return_value=None) as m:
            parser._resolve_phone(listing, "<html><body>no phone here</body></html>")
        m.assert_called_once_with(listing, self.URL, None)
        assert not listing.phone

    def test_resolve_phone_via_cycle(self):
        parser = OlxParser()
        listing = self._listing()
        with patch("curl_cffi.requests.get",
                   side_effect=self._cffi(200, self.AD_HTML, 200,
                                          {"data": {"phones": ["870 894 20700"]}})), \
             patch("parsers.olx.phone_log") as pl:
            parser._resolve_phone(listing, "<html><body>no phone here</body></html>")
        assert listing.phone == "87089420700"
        pl.info.assert_any_call(
            "[%s] %s | found %s | method=olx_endpoint attempt=%d",
            "OLX-1", self.URL, "87089420700", 1)


class TestOlxPhoneAlgorithms:
    """New OLX phone-extraction algorithms (2026-08): ad-id sources,
    offers-API JSON shapes, and embedded JSON state."""

    # ---- _extract_ad_id: more sources than ad-id= links / sku ----
    def test_ad_id_from_url(self):
        assert OlxParser._extract_ad_id(
            "", "https://www.olx.kz/d/x/399002995") == "399002995"

    def test_ad_id_from_offer_id_state(self):
        assert OlxParser._extract_ad_id(
            '{"offerId":"399002995"}') == "399002995"

    def test_ad_id_from_data_attr(self):
        assert OlxParser._extract_ad_id(
            '<div data-ad-id="399002995"></div>') == "399002995"

    def test_ad_id_link_beats_url(self):
        assert OlxParser._extract_ad_id(
            '<a href="?ad-id=111111">x</a>',
            "https://www.olx.kz/222222222") == "111111"

    def test_ad_id_none_when_short(self):
        assert OlxParser._extract_ad_id(
            "", "https://www.olx.kz/d/12345.html") is None

    # ---- _phones_from_api_json: many offers-API shapes ----
    def test_api_phones_array_strings(self):
        assert OlxParser._phones_from_api_json(
            '{"data": {"phones": ["870 894 20700"]}}') == ["87089420700"]

    def test_api_phones_array_objects(self):
        assert OlxParser._phones_from_api_json(
            '{"data": {"phones": [{"phone": "+7 705 123 45 67"}]}}'
        ) == ["77051234567"]

    def test_api_scalar_contact_key(self):
        assert OlxParser._phones_from_api_json(
            '{"data": {"contactPhone": "8777123456"}}') == ["8777123456"]

    def test_api_invalid_json_regex_fallback(self):
        assert OlxParser._phones_from_api_json(
            'garbage "phone":"87051234567" more') == ["87051234567"]

    def test_api_no_phone(self):
        assert OlxParser._phones_from_api_json(
            '{"data": {"price": 100}}') == []

    def test_api_dedupes_same_number(self):
        assert OlxParser._phones_from_api_json(
            '{"data": {"phones": ["870 894 20700", "87089420700"]}}'
        ) == ["87089420700"]

    # ---- _phone_from_embedded_state ----
    def test_embedded_state_finds_phone(self):
        p = OlxParser()
        html = ('<script type="application/json">'
                '{"data": {"phones": ["870 894 20700"]}}</script>')
        assert p._phone_from_embedded_state(html) == "87089420700"

    def test_embedded_state_ld_json(self):
        p = OlxParser()
        html = ('<script type="application/ld+json">'
                '{"telephone": "+7 705 123 45 67"}</script>')
        assert p._phone_from_embedded_state(html) == "77051234567"

    def test_embedded_state_none(self):
        p = OlxParser()
        assert p._phone_from_embedded_state("<html>no json</html>") == ""

    # ---- _discover_phone_endpoints: /phones only (offer-detail dead) ----
    def test_endpoints_only_phones(self):
        p = OlxParser()
        assert p._discover_phone_endpoints(
            '<a href="?ad-id=399002995">x</a>') == [
            "https://www.olx.kz/api/v1/offers/399002995/phones"]

    def test_endpoints_no_ad_id(self):
        p = OlxParser()
        assert p._discover_phone_endpoints("<html>no id</html>") == []
