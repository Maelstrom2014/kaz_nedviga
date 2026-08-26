"""Tests for per-parser extract_detail_price() used by check_prices.

check_prices re-fetches each favorite's URL and extracts the current price
from the DETAIL page. Detail pages have no search cards, so the base
parse()-based fallback fails on them — every site that overrides
extract_detail_price is covered here. A missing override silently marks
active listings as unavailable ("цена не найдена на странице").
"""
from parsers.olx import OlxParser
from parsers.telegram import TelegramParser

from .loaders import load_fixture


class TestOlxDetailPrice:

    def test_extracts_price_and_coords_from_fixture(self):
        parser = OlxParser()
        html = load_fixture("olx_detail")
        url = "https://www.olx.kz/d/obyavlenie/test-123/"
        price, lat, lon = parser.extract_detail_price(html, url)
        assert price == 120000
        assert lat == 43.28306
        assert lon == 76.88928

    def test_falls_back_to_json_state(self):
        # No visible price element — the escaped JSON state carries it.
        parser = OlxParser()
        html = r'<script>window.__data__ = "{\"price\":250000,\"map\":{\"zoom\":9,\"lat\":43.25,\"lon\":76.9},\"cityName\":\"Алматы\"}";</script>'
        price, lat, lon = parser.extract_detail_price(html, "https://www.olx.kz/d/obyavlenie/x/")
        assert price == 250000
        assert lat == 43.25
        assert lon == 76.9

    def test_coords_dropped_outside_almaty(self):
        parser = OlxParser()
        html = (
            '<p data-testid="ad-price">90 000 ₸</p>'
            + r'<script>window.__data__ = "{\"map\":{\"zoom\":9,\"lat\":43.25,\"lon\":76.9},\"cityName\":\"Тельмана\"}";</script>'
        )
        price, lat, lon = parser.extract_detail_price(html, "https://www.olx.kz/d/obyavlenie/x/")
        assert price == 90000
        assert lat is None
        assert lon is None

    def test_no_price_anywhere(self):
        parser = OlxParser()
        price, lat, lon = parser.extract_detail_price("<html><body>no price</body></html>",
                                                      "https://www.olx.kz/d/obyavlenie/x/")
        assert price is None
        assert lat is None
        assert lon is None

    def test_live_page_jsonld_and_visible_price(self):
        # Real live page (captured 2026-08): schema.org JSON-LD in <head>,
        # rendered price in ad-price-container, escaped map state, and the
        # i18n phrase "больше не доступно" inside a plain <script>.
        parser = OlxParser()
        html = (
            '<html><head>'
            '<script type="application/ld+json">'
            '{"@context":"https://schema.org","@type":"Product",'
            '"name":"Аренда квартиры","sku":"396865530",'
            '"offers":{"@type":"Offer","availability":"https://schema.org/InStock",'
            '"areaServed":{"@type":"City","name":"Алматы"},'
            '"priceCurrency":"KZT","price":200000}}'
            '</script>'
            '<script>{"ad.inactive.message.title":"Объявление больше не доступно"}</script>'
            '</head><body>'
            '<div data-testid="ad-price-container"><h3>200 000 тг.</h3></div>'
            + r'<script>window.__data__ = "{\"map\":{\"zoom\":12,\"lat\":43.29257834,\"lon\":77.01166747},\"cityName\":\"Алматы\"}";</script>'
            '</body></html>'
        )
        price, lat, lon = parser.extract_detail_price(html, "https://www.olx.kz/d/obyavlenie/x/")
        assert price == 200000
        assert lat == 43.29257834
        assert lon == 77.01166747
        # The i18n phrase in the JS bundle must NOT mark a live ad as gone.
        assert parser.is_unavailable(html) is False

    def test_visible_price_container_only(self):
        # No JSON-LD (older page variant) — rendered container is enough.
        parser = OlxParser()
        html = ('<html><body><div data-testid="ad-price-container">'
                '<h3>150 000 тг.</h3></div></body></html>')
        price, _, _ = parser.extract_detail_price(html, "https://www.olx.kz/d/obyavlenie/x/")
        assert price == 150000
        assert parser.is_unavailable(html) is False

    def test_unescaped_json_price_fallback(self):
        # Plain (non-escaped) JSON price — no backslashes before/after "price".
        parser = OlxParser()
        html = '<html><body><script>{"price":123456}</script></body></html>'
        price, _, _ = parser.extract_detail_price(html, "https://www.olx.kz/d/obyavlenie/x/")
        assert price == 123456

    def test_is_unavailable_inactive_page(self):
        # Removed ad: no JSON-LD, no price element, rendered message only.
        parser = OlxParser()
        html = ('<html><body><h1>Объявление больше не доступно</h1>'
                '<p>Автор деактивировал это объявление</p></body></html>')
        assert parser.is_unavailable(html) is True
        price, _, _ = parser.extract_detail_price(html, "https://www.olx.kz/d/obyavlenie/x/")
        assert price is None

class TestTelegramDetailPrice:

    def test_extracts_price_from_target_post(self):
        parser = TelegramParser()
        html = (
            '<div class="tgme_widget_message" data-post="kvartiry_almaty/377">'
            '<div class="tgme_widget_message_text">'
            'Сдаётся 1-комнатная квартира, 180 000 тг в месяц</div></div>'
        )
        price, lat, lon = parser.extract_detail_price(html, "https://t.me/kvartiry_almaty/377")
        assert price == 180000
        assert lat is None
        assert lon is None

    def test_s_url_variant(self):
        parser = TelegramParser()
        html = (
            '<div class="tgme_widget_message" data-post="kvartiry_almaty/377">'
            '<div class="tgme_widget_message_text">'
            'Аренда, 150 000 тенге</div></div>'
        )
        price, _, _ = parser.extract_detail_price(html, "https://t.me/s/kvartiry_almaty/377")
        assert price == 150000

    def test_target_post_missing(self):
        parser = TelegramParser()
        html = (
            '<div class="tgme_widget_message" data-post="kvartiry_almaty/999">'
            '<div class="tgme_widget_message_text">'
            'Другое объявление, 100 000 тг</div></div>'
        )
        price, _, _ = parser.extract_detail_price(html, "https://t.me/kvartiry_almaty/377")
        assert price is None

    def test_post_without_price(self):
        parser = TelegramParser()
        html = (
            '<div class="tgme_widget_message" data-post="kvartiry_almaty/377">'
            '<div class="tgme_widget_message_text">'
            'Сдаётся квартира, '
            'цена договорная</div></div>'
        )
        price, _, _ = parser.extract_detail_price(html, "https://t.me/kvartiry_almaty/377")
        assert price is None
