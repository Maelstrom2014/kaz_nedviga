"""Tests for export utilities (TXT + PDF)."""
import io
from unittest.mock import patch

from parsers.models import Listing
from export_utils import (export_txt, export_pdf, listing_lines,
                          _prepare_photo, _photo_urls, _to_webp)


SAMPLE_LISTINGS = [
    Listing(
        title="1-комнатная квартира, 45 м²",
        price=150000, currency="тг", rooms=1, area=45.0,
        floor=3, total_floors=5, address="Алмалинский, ул. Абая",
        url="https://krisha.kz/123", source="krisha.kz",
    ),
    Listing(
        title="2-комнатная квартира, 65 м²",
        price=250000, currency="тг", rooms=2, area=65.0,
        floor=5, total_floors=9, address="Бостандыкский, мкр. 5",
        url="https://krisha.kz/456", source="krisha.kz",
    ),
]


def test_listing_lines():
    lines = listing_lines(SAMPLE_LISTINGS)
    assert any("Алматы" in l for l in lines)
    assert any("2" in l for l in lines)  # count


def test_export_txt():
    data = export_txt(SAMPLE_LISTINGS)
    assert isinstance(data, bytes)
    text = data.decode("utf-8")
    assert "1-комнатная" in text
    assert "150 000" in text
    assert "krisha.kz" in text


def test_export_pdf_portrait():
    data = export_pdf(SAMPLE_LISTINGS, orientation="portrait")
    assert isinstance(data, bytes)
    assert data[:4] == b"%PDF"  # PDF magic bytes


def test_export_pdf_landscape():
    data = export_pdf(SAMPLE_LISTINGS, orientation="landscape")
    assert isinstance(data, bytes)
    assert data[:4] == b"%PDF"


def test_export_empty_listings():
    assert export_txt([]) is not None
    assert export_pdf([], "portrait")[:4] == b"%PDF"


def _make_jpeg(w=200, h=150, color=(180, 60, 60)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="JPEG")
    return buf.getvalue()


def test_photo_urls_split_and_dedupe():
    assert _photo_urls("") == []
    assert _photo_urls("https://a/1.jpg|https://a/2.jpg|https://a/1.jpg") == [
        "https://a/1.jpg", "https://a/2.jpg"]
    # non-http and whitespace entries are dropped
    assert _photo_urls("  |ftp://x|https://a/1.jpg ") == ["https://a/1.jpg"]


def test_prepare_photo_converts_webp_to_jpeg():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (640, 420), (10, 200, 30)).save(buf, format="WEBP")
    data, w, h = _prepare_photo(buf.getvalue())
    assert data[:3] == b"\xff\xd8\xff"  # JPEG SOI
    assert (w, h) == (640, 420)


def test_to_webp_converts_jpeg_to_webp():
    from PIL import Image
    jpeg_bytes = _make_jpeg(640, 420)
    webp_bytes = _to_webp(jpeg_bytes)
    img = Image.open(io.BytesIO(webp_bytes))
    assert img.format == "WEBP"
    assert (640, 420) == img.size


def test_to_webp_preserves_rgba():
    from PIL import Image
    png_buf = io.BytesIO()
    Image.new("RGBA", (100, 100), (10, 20, 30, 128)).save(png_buf, format="PNG")
    webp_bytes = _to_webp(png_buf.getvalue())
    img = Image.open(io.BytesIO(webp_bytes))
    assert img.format == "WEBP"
    assert img.mode in ("RGB", "RGBA")


def test_to_webp_falls_back_on_garbage():
    assert _to_webp(b"not an image") == b"not an image"


def test_download_photo_caches_as_webp(tmp_path, monkeypatch):
    from export_utils import _download_photo, _cache_get
    monkeypatch.setattr("export_utils._CACHE_DIR", tmp_path)
    monkeypatch.setattr("export_utils.requests.get", lambda *a, **kw: type("R", (), {
        "status_code": 200, "content": _make_jpeg(400, 300),
        "raise_for_status": lambda self: None,
    })())
    result = _download_photo("https://example.com/photo.jpg")
    assert result is not None
    cached = _cache_get("https://example.com/photo.jpg")
    from PIL import Image
    img = Image.open(io.BytesIO(cached))
    assert img.format == "WEBP"


def test_download_photo_cache_hit_returns_webp(tmp_path, monkeypatch):
    from export_utils import _download_photo, _cache_put, _cache_get
    monkeypatch.setattr("export_utils._CACHE_DIR", tmp_path)
    webp_bytes = _to_webp(_make_jpeg(200, 150))
    _cache_put("https://example.com/cached.jpg", webp_bytes)
    hit = _cache_get("https://example.com/cached.jpg")
    assert hit == webp_bytes



def test_prepare_photo_downscales():
    data, w, h = _prepare_photo(_make_jpeg(4000, 3000))
    assert max(w, h) <= 700


def test_prepare_photo_garbage_returns_none():
    assert _prepare_photo(b"not an image at all") is None


def _jpeg_for(u: str) -> bytes:
    # distinct bytes per URL — fpdf2 dedupes identical images by hash, and
    # solid-color JPEGs can quantize to identical bytes for close colors
    from PIL import Image, ImageDraw
    seed = sum(ord(c) for c in u)
    img = Image.new("RGB", (200, 150),
                    ((seed + 10) % 200 + 10, seed % 256, (seed * 13) % 256))
    d = ImageDraw.Draw(img)
    d.rectangle([seed % 50, (seed * 3) % 100,
                 (seed % 50) + 40, ((seed * 3) % 100) + 30],
                fill=((seed * 11) % 256, (seed * 17) % 256,
                      (seed * 23) % 256))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_pdf_embeds_all_photos():
    a = Listing(title="A", url="https://x/a", source="t", price=1,
                photo="https://x/one.jpg|https://x/two.jpg|https://x/three.jpg")
    b = Listing(title="B", url="https://x/b", source="t", price=2,
                photo="https://x/four.jpg")
    with patch("export_utils._download_photo", side_effect=_jpeg_for):
        data = export_pdf([a, b], "portrait")
    assert data[:4] == b"%PDF"
    # each distinct photo is a DCTDecode stream starting with the JPEG SOI
    assert data.count(b"\xff\xd8\xff") == 4


def test_pdf_dedupes_identical_photos():
    # the same photo bytes in two listings must be embedded only once
    a = Listing(title="A", url="https://x/a", source="t", price=1,
                photo="https://x/one.jpg|https://x/two.jpg")
    b = Listing(title="B", url="https://x/b", source="t", price=2,
                photo="https://x/one.jpg")
    with patch("export_utils._download_photo", side_effect=_jpeg_for):
        data = export_pdf([a, b], "portrait")
    assert data.count(b"\xff\xd8\xff") == 2


def test_pdf_embeds_all_photos_landscape():
    a = Listing(title="A", url="https://x/a", source="t", price=1,
                photo="|".join(f"https://x/a{i}.jpg" for i in range(5)))
    with patch("export_utils._download_photo", side_effect=_jpeg_for):
        data = export_pdf([a], "landscape")
    assert data[:4] == b"%PDF"
    # default now embeds ALL photos (no cap)
    assert data.count(b"\xff\xd8\xff") == 5


def test_pdf_caps_photos_per_listing():
    # explicit per-listing cap drops the 4th/5th photo
    a = Listing(title="A", url="https://x/a", source="t", price=1,
                photo="|".join(f"https://x/a{i}.jpg" for i in range(5)))
    with patch("export_utils._download_photo", side_effect=_jpeg_for):
        data = export_pdf([a], "portrait", max_photos_per_listing=3)
    assert data.count(b"\xff\xd8\xff") == 3


def test_pdf_default_embeds_all_photos():
    # default (no cap) keeps all photos of every listing
    a = Listing(title="A", url="https://x/a", source="t", price=1,
                photo="|".join(f"https://x/a{i}.jpg" for i in range(8)))
    with patch("export_utils._download_photo", side_effect=_jpeg_for):
        data = export_pdf([a], "portrait")
    assert data.count(b"\xff\xd8\xff") == 8


def test_pdf_caps_total_photos():
    # 10 listings × 4 photos = 40 urls, total cap 6 → only 6 embedded.
    # URLs use i*10+j so _jpeg_for seeds (char-sum) stay distinct (no fpdf2 dedup).
    listings = [
        Listing(title=f"A{i}", url=f"https://x/a{i}", source="t", price=1,
                photo="|".join(f"https://x/a{i}_{i*10+j}.jpg" for j in range(4)))
        for i in range(10)
    ]
    with patch("export_utils._download_photo", side_effect=_jpeg_for):
        data = export_pdf(listings, "portrait", max_total_photos=6)
    assert data.count(b"\xff\xd8\xff") == 6


def test_pdf_survives_photo_download_failure():
    a = Listing(title="A", url="https://x/a", source="t", price=1,
                photo="https://x/a1.jpg|https://x/a2.jpg")
    with patch("export_utils._download_photo", return_value=None):
        data = export_pdf([a], "portrait")
    assert data[:4] == b"%PDF"
    assert data.count(b"\xff\xd8\xff") == 0


def test_pdf_mixed_ok_and_failed_photos():
    def fake_download(u):
        return _make_jpeg() if u.endswith("1.jpg") else None
    a = Listing(title="A", url="https://x/a", source="t", price=1,
                photo="https://x/a1.jpg|https://x/a2.jpg")
    with patch("export_utils._download_photo", side_effect=fake_download):
        data = export_pdf([a], "portrait")
    assert data.count(b"\xff\xd8\xff") == 1
