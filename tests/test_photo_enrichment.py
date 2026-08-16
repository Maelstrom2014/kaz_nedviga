"""Tests for detail-page photo enrichment."""
from unittest.mock import patch, MagicMock

import pytest

from parsers.base import BaseParser
from parsers.krisha import KrishaParser
from parsers.kn import KnParser
from parsers.olx import OlxParser
from parsers.models import Listing, SearchParams

from .loaders import load_fixture


class TestKrishaDetailPhotos:

    def test_extracts_gallery_photos(self):
        html = load_fixture("krisha_detail")
        parser = KrishaParser()
        soup = parser.parse_html(html)
        # Simulate: use soup to find photos as the method would
        photos = []
        import re
        for img in soup.select(".gallery__main img, .gallery__small-item img, .gallery__container img"):
            src = (img.get("src") or img.get("data-src") or "")
            if not src or src.startswith("data:") or "/static/" in src:
                continue
            if src.startswith("//"):
                src = "https:" + src
            src = re.sub(r"-\d+x\d+\.(webp|jpe?g|png|avif)$", r"-750x470.\1", src, flags=re.I)
            photo_id = re.sub(r"\.(webp|jpe?g|png|avif)$", "", src, flags=re.I)
            if photo_id not in [re.sub(r"\.(webp|jpe?g|png|avif)$", "", p, flags=re.I) for p in photos]:
                photos.append(src)
        assert len(photos) >= 4
        assert all(p.startswith("https://") for p in photos)
        assert all("/static/" not in p for p in photos)

    def test_fetch_detail_photos_returns_multiple(self):
        parser = KrishaParser()
        listing = Listing(url="https://krisha.kz/a/show/123", source="krisha.kz")
        with patch.object(parser, "fetch", return_value=load_fixture("krisha_detail")):
            photos = parser._fetch_detail_photos(listing)
        assert len(photos) >= 4
        # Thumbnails upgraded to full-size
        assert all("750x470" in p for p in photos if "120x90" not in p)
        assert all("https://" in p for p in photos)

    def test_fetch_detail_photos_empty_on_error(self):
        parser = KrishaParser()
        listing = Listing(url="https://krisha.kz/a/show/999", source="krisha.kz")
        with patch.object(parser, "fetch", side_effect=Exception("network error")):
            photos = parser._fetch_detail_photos(listing)
        assert photos == []


class TestKnDetailPhotos:

    def test_extracts_gallery_big_photos(self):
        html = load_fixture("kn_detail")
        parser = KnParser()
        soup = parser.parse_html(html)
        photos = []
        seen = set()
        for img in soup.select(".swiper img, .gallery img, img"):
            src = (img.get("src") or img.get("data-src") or "")
            if not src or "/gallery_big/" not in src:
                continue
            if src in seen:
                continue
            seen.add(src)
            photos.append(src)
        assert len(photos) == 3
        assert all("gallery_big" in p for p in photos)

    def test_fetch_detail_photos_returns_multiple(self):
        parser = KnParser()
        listing = Listing(url="https://www.kn.kz/card/123", source="kn.kz")
        with patch.object(parser, "fetch", return_value=load_fixture("kn_detail")):
            photos = parser._fetch_detail_photos(listing)
        assert len(photos) == 3
        assert all("gallery_big" in p for p in photos)

    def test_fetch_detail_extracts_map_coordinates(self):
        parser = KnParser()
        listing = Listing(url="https://www.kn.kz/card/123456", source="kn.kz")
        seq = iter([load_fixture("kn_detail"), load_fixture("kn_map")])
        with patch.object(parser, "fetch", side_effect=lambda url: next(seq)):
            parser._fetch_detail_photos(listing)
        assert listing.lat == 43.292631460374
        assert listing.lon == 77.014389068787

    def test_map_coords_none_when_no_frame(self):
        parser = KnParser()
        listing = Listing(url="https://www.kn.kz/card/123", source="kn.kz")
        # No turbo-frame in the detail page
        with patch.object(parser, "fetch", return_value="<html><body>no map</body></html>"):
            parser._fetch_detail_photos(listing)
        assert listing.lat is None
        assert listing.lon is None

    def test_map_coords_rejected_outside_almaty(self):
        parser = KnParser()
        listing = Listing(url="https://www.kn.kz/card/123", source="kn.kz")
        # Coords for Astana (51.1, 71.4) — must be rejected
        map_html = '<turbo-stream><template><div data-controller="kn-simple-map" data-kn-simple-map-latitude-value="51.121869" data-kn-simple-map-longitude-value="71.498625"></div></template></turbo-stream>'
        seq = iter(["<html><body><turbo-frame id='card-map-123' src='/card/map/123'>load</turbo-frame></body></html>", map_html])
        with patch.object(parser, "fetch", side_effect=lambda url: next(seq)):
            parser._fetch_detail_photos(listing)
        assert listing.lat is None
        assert listing.lon is None


class TestOlxDetailPhotos:
    def test_extracts_all_gallery_slides(self):
        parser = OlxParser()
        listing = Listing(url="https://www.olx.kz/d/obyavlenie/123", source="olx.kz")
        with patch.object(parser, "fetch", return_value=load_fixture("olx_detail")):
            photos = parser._fetch_detail_photos(listing)
        assert len(photos) == 3
        # canonical (no ";s=WxH" size suffix) so duplicates collapse
        assert all(";s=" not in p for p in photos)
        assert "photo-a" in photos[0]

    def test_empty_on_fetch_error(self):
        parser = OlxParser()
        listing = Listing(url="https://www.olx.kz/d/obyavlenie/999", source="olx.kz")
        with patch.object(parser, "fetch", side_effect=ConnectionError("blocked")):
            assert parser._fetch_detail_photos(listing) == []

    def test_canonical_photo_strips_size_suffix(self):
        assert OlxParser._canonical_photo(
            "https://x/img;s=510x417;q=50"
        ) == "https://x/img"
        assert OlxParser._canonical_photo("") == ""

    def test_card_photo_canonicalizes_so_merge_dedupes(self):
        # the search card used to keep ";s=510x417" while the gallery
        # canonical URL has no suffix — the same first photo appeared twice.
        assert OlxParser._canonical_photo(
            "https://frankfurt.apollo.olxcdn.com/v1/files/x/image;s=510x417;q=50"
        ) == "https://frankfurt.apollo.olxcdn.com/v1/files/x/image"

    def test_extracts_map_coordinates(self):
        # OLX embeds the map centre as an escaped JSON blob; the fixture
        # carries lat=43.28306, lon=76.88928 (Almaty area).
        parser = OlxParser()
        listing = Listing(url="https://www.olx.kz/d/obyavlenie/123", source="olx.kz")
        with patch.object(parser, "fetch", return_value=load_fixture("olx_detail")):
            parser._fetch_detail_photos(listing)
        assert listing.lat == 43.28306
        assert listing.lon == 76.88928

    def test_coords_none_when_no_map_blob(self):
        parser = OlxParser()
        listing = Listing(url="https://www.olx.kz/d/obyavlenie/123", source="olx.kz")
        with patch.object(parser, "fetch", return_value="<html>no map here</html>"):
            parser._fetch_detail_photos(listing)
        assert listing.lat is None
        assert listing.lon is None

    def test_coords_dropped_non_almaty_city(self):
        # Listing in Абай (not Almaty) — coords must be dropped even
        # though they're present in the page JSON.
        html = '<script>window.__data__ = "\\"map\\":{\\"zoom\\":12,\\"lat\\":43.21331,\\"lon\\":76.75962},\\"cityName\\":\\"Абай\\"";</script>'
        parser = OlxParser()
        listing = Listing(url="https://www.olx.kz/d/obyavlenie/123", source="olx.kz")
        with patch.object(parser, "fetch", return_value=html):
            parser._fetch_detail_photos(listing)
        assert listing.lat is None
        assert listing.lon is None

    def test_coords_dropped_almaty_city_out_of_bbox(self):
        # cityName=Алматы but coords are clearly wrong (e.g. 50,70 — Astana area)
        html = '<script>window.__data__ = "\\"map\\":{\\"zoom\\":12,\\"lat\\":51.11,\\"lon\\":71.48},\\"cityName\\":\\"Алматы\\"";</script>'
        parser = OlxParser()
        listing = Listing(url="https://www.olx.kz/d/obyavlenie/123", source="olx.kz")
        with patch.object(parser, "fetch", return_value=html):
            parser._fetch_detail_photos(listing)
        assert listing.lat is None
        assert listing.lon is None

    def test_coords_accepted_almaty_in_bbox(self):
        # cityName=Алматы and coords within the Almaty bounding box
        html = '<script>window.__data__ = "\\"map\\":{\\"zoom\\":12,\\"lat\\":43.25,\\"lon\\":76.95},\\"cityName\\":\\"Алматы\\"";</script>'
        parser = OlxParser()
        listing = Listing(url="https://www.olx.kz/d/obyavlenie/123", source="olx.kz")
        with patch.object(parser, "fetch", return_value=html):
            parser._fetch_detail_photos(listing)
        assert listing.lat == 43.25
        assert listing.lon == 76.95

    def test_coords_survive_fetch_error(self):
        # fetch error → no photos, coords stay None (no crash)
        parser = OlxParser()
        listing = Listing(url="https://www.olx.kz/d/obyavlenie/999", source="olx.kz")
        with patch.object(parser, "fetch", side_effect=ConnectionError("blocked")):
            parser._fetch_detail_photos(listing)
        assert listing.lat is None
        assert listing.lon is None


class TestEnrichDisabled:

    def test_etagi_enrich_enabled(self):
        from parsers.etagi import EtagiParser
        assert EtagiParser.enrich_photo_count > 0

    def test_kvartirka_no_enrich(self):
        from parsers.kvartirka import KvartirkaParser
        assert KvartirkaParser.enrich_photo_count == 0

    def test_krisha_enrich_enabled(self):
        assert KrishaParser.enrich_photo_count > 0

    def test_kn_enrich_enabled(self):
        assert KnParser.enrich_photo_count > 0

    def test_olx_enrich_enabled(self):
        # OLX now enriches from the detail-page swiper gallery
        # (search card previously exposed only 1 photo).
        assert OlxParser.enrich_photo_count > 0


class TestEtagiDetailPhotos:

    def test_normalize_photo_url_thumb_to_full(self):
        from parsers.etagi import EtagiParser
        url = EtagiParser._normalize_photo_url(
            "//cdn.esoft.digital/320240/cluster/photos/87/2c/HASH.jpeg"
        )
        assert url == "https://cdn.esoft.digital/content/cluster/photos/87/2c/HASH.jpeg"

    def test_normalize_photo_url_medium_to_full(self):
        from parsers.etagi import EtagiParser
        url = EtagiParser._normalize_photo_url(
            "//cdn.esoft.digital/640480/cluster/photos/87/2c/HASH.jpeg"
        )
        assert "320240" not in url
        assert "640480" not in url
        assert "/content/cluster/" in url

    def test_normalize_photo_url_already_full(self):
        from parsers.etagi import EtagiParser
        url = EtagiParser._normalize_photo_url(
            "https://cdn.esoft.digital/content/cluster/photos/87/2c/HASH.jpeg"
        )
        assert url == "https://cdn.esoft.digital/content/cluster/photos/87/2c/HASH.jpeg"

    def test_normalize_photo_url_empty(self):
        from parsers.etagi import EtagiParser
        assert EtagiParser._normalize_photo_url("") == ""
        assert EtagiParser._normalize_photo_url(None) == ""  # type: ignore[arg-type]

    def test_detail_photos_extracts_gallery(self, monkeypatch):
        from parsers.etagi import EtagiParser
        parser = EtagiParser()
        listing = Listing(title="t", price=100000, source="etagi.com",
                          url="https://almaty.etagi.com/realty_rent/123/")
        html = """
        <html><body>
          <div data-testid="object_page_gallery_slider">
            <img src="//cdn.esoft.digital/320240/cluster/photos/87/2c/hash1.jpeg">
            <img src="//cdn.esoft.digital/640480/cluster/photos/f5/80/hash2.jpeg">
            <img src="https://static.maps.2gis.com/1.0?map">
            <img src="//cdn.esoft.digital/320240/cluster/photos/87/2c/hash1.jpeg">
          </div>
        </body></html>
        """
        monkeypatch.setattr(parser, "fetch", lambda url: html)
        photos = parser._fetch_detail_photos(listing)
        assert len(photos) == 2  # hash1 + hash2, dedup, no 2gis map
        assert all("cdn.esoft.digital" in u for u in photos)
        assert all("/content/cluster/" in u for u in photos)

    def test_detail_photos_regex_fallback(self, monkeypatch):
        from parsers.etagi import EtagiParser
        parser = EtagiParser()
        listing = Listing(title="t", price=100000, source="etagi.com",
                          url="https://almaty.etagi.com/realty_rent/123/")
        html = '<img src="//cdn.esoft.digital/320240/cluster/photos/87/2c/h1.jpeg"> and ' \
               '<img src="//cdn.esoft.digital/640480/cluster/photos/87/2c/h1.jpeg">'
        # No gallery div → method 2 (regex fallback)
        monkeypatch.setattr(parser, "fetch", lambda url: html)
        photos = parser._fetch_detail_photos(listing)
        assert len(photos) == 1  # dedup of same hash in different sizes


class TestEnrichInRun:

    def test_enrich_called_in_run(self, monkeypatch):
        """Verify that _enrich_photos is called during run()."""
        parser = KrishaParser()
        parser.max_pages = 1
        html = '<div class="a-card"><a class="a-card__title" href="/a/show/123">test 45 м²</a><div class="a-card__price">100000</div></div>'
        monkeypatch.setattr(parser, "fetch", lambda url: html)
        # Mock _enrich_photos to track it
        enriched = []
        orig = parser._enrich_photos
        def mock_enrich(listings):
            enriched.extend(listings)
            # Don't actually enrich, just track that it was called
        monkeypatch.setattr(parser, "_enrich_photos", mock_enrich)
        results = parser.run(SearchParams(limit=0))
        assert len(enriched) > 0
        assert all(isinstance(r, Listing) for r in enriched)

    def test_enrich_adds_photos(self, monkeypatch):
        """Verify photos are actually enriched during run()."""
        parser = KrishaParser()
        parser.max_pages = 1
        parser.precache_photos = False  # don't hit network in tests
        search_html = '<div class="a-card"><a class="a-card__title" href="/a/show/123">test 45 м²</a><div class="a-card__price">100000</div></div>'
        detail_html = load_fixture("krisha_detail")
        call_count = [0]
        def mock_fetch(url):
            call_count[0] += 1
            if call_count[0] == 1:
                return search_html
            return detail_html
        monkeypatch.setattr(parser, "fetch", mock_fetch)
        results = parser.run(SearchParams(limit=0))
        assert len(results) > 0
        # The first listing should have multiple photos now
        photos = results[0].photo.split("|") if results[0].photo else []
        assert len(photos) >= 4
