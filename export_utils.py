from __future__ import annotations

import hashlib
import io
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Sequence

import requests
from fpdf import FPDF

from parsers.models import Listing

log = logging.getLogger("export")

# --- On-disk photo cache ---
# Photos are fetched once and stored permanently under cache/photos/ —
# repeated exports (incl. favorites re-exported after edits) hit the cache
# instead of re-downloading from CDN.  The cache key is a SHA1 of the URL.
_CACHE_DIR = Path(__file__).parent / "cache" / "photos"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_max_bytes() -> int:
    """Read the configured cache size limit from app settings.

    Returns the limit in bytes. Falls back to 500 MB when settings are
    unavailable (e.g. during tests without the app module loaded).
    """
    try:
        import json
        settings_path = Path(__file__).parent / "settings.json"
        if settings_path.exists():
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            mb = int(data.get("photo_cache_mb", 500))
            return mb * 1024 * 1024
    except Exception:
        pass
    return 500 * 1024 * 1024

# TTF fonts with Cyrillic support, per-OS candidates (fpdf needs .ttf, not .ttc).
_FONT_CANDIDATES = [
    # Windows
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    # macOS
    r"/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    r"/Library/Fonts/Arial Unicode.ttf",
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]
_FONT_BOLD_CANDIDATES = [
    r"C:\Windows\Fonts\arialbd.ttf",
    r"/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]


def _first_existing(candidates: list[str]) -> str | None:
    for p in candidates:
        if Path(p).exists():
            return p
    return None


# Generic fallback: search common font dirs by filename when none of the
# well-known distro paths above exist (Alpine, NIXPkgs, $HOME installs, ...).
_FONT_SEARCH_DIRS = [
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    str(Path.home() / ".fonts"),
    str(Path.home() / ".local" / "share" / "fonts"),
]
_FONT_NAME_FALLBACKS = [
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf"),
    ("FreeSans.ttf", "FreeSansBold.ttf"),
    ("NotoSans-Regular.ttf", "NotoSans-Bold.ttf"),
]


def _glob_font(name: str) -> str | None:
    for root in _FONT_SEARCH_DIRS:
        try:
            for hit in Path(root).rglob(name):
                return str(hit)
        except OSError:
            continue
    return None


def _resolve_font(candidates: list[str], bold_name: str | None = None) -> str | None:
    path = _first_existing(candidates)
    if path:
        return path
    if bold_name:
        return _glob_font(bold_name)
    return None


_font_path = _resolve_font(_FONT_CANDIDATES, "DejaVuSans.ttf")
_font_bold = _resolve_font(_FONT_BOLD_CANDIDATES, "DejaVuSans-Bold.ttf") or _font_path

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def _cache_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def _cache_path(url: str) -> Path:
    return _CACHE_DIR / _cache_key(url)


def _cache_get(url: str) -> bytes | None:
    p = _cache_path(url)
    if p.exists():
        try:
            data = p.read_bytes()
        except OSError:
            return None
        return data
    return None


def _cache_put(url: str, data: bytes) -> None:
    p = _cache_path(url)
    try:
        p.write_bytes(data)
    except OSError as exc:
        log.debug("cache write failed: %s", exc)
    # Opportunistic eviction: when the cache grows past the limit, remove
    # the oldest files (by mtime) until under threshold. Runs at most once
    # per put() — cheap relative to the HTTP fetch we just did.
    try:
        _cache_evict_if_needed()
    except Exception:
        pass


def _cache_evict_if_needed() -> None:
    max_bytes = _cache_max_bytes()
    files = sorted(_CACHE_DIR.iterdir(), key=lambda f: f.stat().st_mtime)
    total = sum(f.stat().st_size for f in files)
    if total <= max_bytes:
        return
    evicted = 0
    for f in files:
        if total <= max_bytes * 0.9:  # evict to 90% of limit
            break
        size = f.stat().st_size
        f.unlink()
        total -= size
        evicted += 1
    if evicted:
        log.info("photo cache: evicted %d files to reach %dMB",
                 evicted, total / 1024 / 1024)


def _cache_dir_stats() -> dict:
    """Return total file count and size of the photo cache directory."""
    files = list(_CACHE_DIR.iterdir())
    total = sum(f.stat().st_size for f in files if f.is_file())
    return {"files": len(files), "size_bytes": total}


def _cache_clear() -> int:
    """Delete all files from the photo cache. Returns count deleted."""
    deleted = 0
    for f in _CACHE_DIR.iterdir():
        if f.is_file():
            f.unlink()
            deleted += 1
    return deleted


def _to_webp(data: bytes) -> bytes:
    """Convert image bytes to WebP for compact on-disk caching.

    WebP saves ~30-50% disk vs JPEG at equivalent quality. Returns the
    original bytes when Pillow is unavailable or the input isn't a valid
    image — the cache then stores raw bytes, which _prepare_photo still
    handles (JPEG/PNG fallback or Pillow re-decode at export time).
    """
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        img.load()
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=85, method=6)
        return buf.getvalue()
    except Exception as exc:
        log.debug("webp conversion failed: %s", exc)
        return data


def _download_photo(url: str) -> bytes | None:
    """Download image bytes from URL. Returns None on failure.

    Verifies TLS by default; falls back to verify=False for hosts with
    broken certificates (common on CDN edges). Retries on timeout or
    rate-limit (429/503) since photo CDNs throttle bursty traffic.
    Results are cached on disk permanently — repeat exports/favorites
    re-syncs hit the cache instead of re-downloading."""
    # 1) Cache hit — no network I/O.
    cached = _cache_get(url)
    if cached is not None:
        log.debug("photo cache hit: %s", url[:60])
        return cached

    # Some CDNs reject image hotlinking without a Referer that matches
    # the parent site. Derive one from the URL host.
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    referer = ""
    if "krisha" in host:
        referer = "https://krisha.kz/"
    elif "olxcdn" in host or "olx" in host:
        referer = "https://www.olx.kz/"
    elif "kn.kz" in host:
        referer = "https://www.kn.kz/"
    elif "esoft" in host:
        referer = "https://almaty.etagi.com/"
    elif "telesco" in host:
        referer = "https://t.me/"
    elif host:
        referer = f"https://{host}/"

    headers = {**_HEADERS}
    if referer:
        headers["Referer"] = referer
    headers["Accept"] = "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"

    for attempt in range(3):
        try:
            try:
                resp = requests.get(url, headers=headers, timeout=15, verify=True)
            except requests.exceptions.SSLError:
                resp = requests.get(url, headers=headers, timeout=15, verify=False)
            if resp.status_code in (429, 503) and attempt < 2:
                import time
                time.sleep(1.0 * (attempt + 1))
                continue
            resp.raise_for_status()
            data = _to_webp(resp.content)
            _cache_put(url, data)
            return data
        except requests.exceptions.Timeout:
            if attempt < 2:
                continue
        except Exception as exc:
            log.debug("Photo download failed: %s: %s", url[:60], exc)
            return None
    log.debug("Photo download failed (retries exhausted): %s", url[:60])
    return None


def listing_lines(listings: Sequence[Listing]) -> list[str]:
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("  Результаты поиска аренды жилья в Алматы")
    lines.append(f"  Экспорт: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    lines.append(f"  Найдено объявлений: {len(listings)}")
    lines.append("=" * 70)
    for i, item in enumerate(listings, 1):
        lines.append("")
        lines.append(f"  #{i}  [{item.source}]")
        lines.append(f"  Заголовок: {item.title}")
        lines.append(f"  Цена: {item.price_all_str()}")
        room_str = f"{item.rooms} комн." if item.rooms is not None else "—"
        area_str = f"{item.area} м²" if item.area else "—"
        floor_str = f"{item.floor}/{item.total_floors} этаж" if item.floor else "—"
        lines.append(f"  Комнат: {room_str} | Площадь: {area_str} | Этаж: {floor_str}")
        if item.address:
            lines.append(f"  Адрес: {item.address}")
        if item.date_published or item.date_updated:
            parts = []
            if item.date_published:
                parts.append(f"Опубликовано: {item.date_published}")
            if item.date_updated:
                parts.append(f"Обновлено: {item.date_updated}")
            lines.append(f"  Даты: {' | '.join(parts)}")
        lines.append(f"  Ссылка: {item.url}")
        lines.append("-" * 70)
    return lines


def export_txt(listings: Sequence[Listing]) -> bytes:
    text = "\n".join(listing_lines(listings))
    return text.encode("utf-8")


def _photo_urls(photo: str) -> list[str]:
    """Split the pipe-joined photo field into unique http(s) URLs."""
    urls: list[str] = []
    for u in (photo or "").split("|"):
        u = u.strip()
        if u.startswith("http") and u not in urls:
            urls.append(u)
    return urls


_MAX_PHOTO_PX = 700  # longest side after downscale (keeps the PDF small)


def _prepare_photo(raw: bytes) -> tuple[bytes, int, int] | None:
    """Normalize downloaded photo bytes for PDF embedding.

    Converts any Pillow-readable format (webp, png, ...) to RGB JPEG and
    downscales to _MAX_PHOTO_PX so the report stays small. Returns
    (jpeg_bytes, width_px, height_px). Falls back to the raw bytes when
    Pillow is unavailable/fails and fpdf can embed the format natively
    (JPEG/PNG) — in that case the dimensions are unknown (None).
    """
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(raw))
        img.load()
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.thumbnail((_MAX_PHOTO_PX, _MAX_PHOTO_PX))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return buf.getvalue(), img.size[0], img.size[1]
    except Exception as exc:
        log.debug("Photo normalize failed: %s", exc)
    if raw[:3] == b"\xff\xd8\xff" or raw[:8] == b"\x89PNG\r\n\x1a\n":
        return raw, None, None
    return None


def export_pdf(
    listings: Sequence[Listing],
    orientation: str = "portrait",
    max_photos_per_listing: int = 1000,
    max_total_photos: int = 100000,
) -> bytes:
    """orientation: 'portrait' (для телефона) | 'landscape' (горизонтальный)

    Every photo of every listing is embedded: photos are prefetched in
    parallel, normalized to JPEG, then drawn as a grid under the text block.

    By default ALL photos are embedded (no cap). ``max_photos_per_listing``
    and ``max_total_photos`` can still bound a report if needed. Photos are
    fetched with a 24-worker pool and downscaled, so a 400-photo report
    finishes in a few seconds — well within the browser fetch window.
    """
    o = "P" if orientation == "portrait" else "L"
    pdf = FPDF(orientation=o, unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)

    if _font_path:
        pdf.add_font("AR", "", _font_path)
        if _font_bold and Path(_font_bold).exists():
            pdf.add_font("AR", "B", _font_bold)
        else:
            pdf.add_font("AR", "B", _font_path)
        font_name = "AR"
    else:
        font_name = "Helvetica"

    # Prefetch photos in parallel — the render loop below is sequential
    # and must not hit the network. Cap the counts so a 66-listing export
    # with 400+ photos doesn't hang the HTTP request for 30+ seconds.
    photo_urls: list[str] = []
    per_listing_budget = max_total_photos
    for item in listings:
        urls = _photo_urls(item.photo)[:max_photos_per_listing]
        take = min(len(urls), per_listing_budget)
        if take <= 0:
            break
        for u in urls[:take]:
            if u not in photo_urls:
                photo_urls.append(u)
        per_listing_budget -= take
    photo_cache: dict[str, tuple[bytes, int, int] | None] = {}
    if photo_urls:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=24) as ex:
            futures = {ex.submit(_download_photo, u): u for u in photo_urls}
            for fut in as_completed(futures):
                u = futures[fut]
                raw: bytes | None = None
                try:
                    raw = fut.result()
                except Exception as exc:
                    log.debug("Photo future failed: %s: %s", u[:60], exc)
                photo_cache[u] = _prepare_photo(raw) if raw else None

    missing = sum(1 for v in photo_cache.values() if v is None)
    if missing:
        log.info("export_pdf: %d/%d photos could not be downloaded/prepared",
                 missing, len(photo_urls))

    pdf.add_page()
    w = pdf.w - pdf.l_margin - pdf.r_margin

    pdf.set_font(font_name, style="B", size=16)
    pdf.multi_cell(0, 8, "Результаты поиска аренды жилья в Алматы", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(font_name, style="", size=9)
    pdf.cell(0, 5, f"Экспорт: {datetime.now().strftime('%d.%m.%Y %H:%M')}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, f"Найдено объявлений: {len(listings)}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # Photo grid geometry
    cols = 3 if o == "P" else 4
    gap = 3.0
    cell_w = (w - gap * (cols - 1)) / cols
    max_cell_h = 50.0

    def _fit(pw: int | None, ph: int | None) -> tuple[float, float]:
        """Photo size (mm) fitting the (cell_w x max_cell_h) box."""
        if not pw or not ph:
            return cell_w, cell_w * 0.75
        h = cell_w * ph / pw
        if h > max_cell_h:
            return pw * (max_cell_h / ph), max_cell_h
        return cell_w, h

    for i, item in enumerate(listings, 1):
        if pdf.get_y() > (pdf.h - pdf.b_margin - 55):
            pdf.add_page()

        pdf.set_font(font_name, style="B", size=11)
        pdf.cell(0, 6, f"#{i}  [{item.source}]", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font(font_name, style="", size=9)

        rows = [
            ("Заголовок", item.title),
            ("Цена", item.price_all_str()),
            ("Комнат", f"{item.rooms}" if item.rooms is not None else "—"),
            ("Площадь", f"{item.area} м²" if item.area else "—"),
            ("Этаж", f"{item.floor}/{item.total_floors}" if item.floor else "—"),
            ("Адрес", item.address or "—"),
            ("Опубликовано", item.date_published or "—"),
            ("Обновлено", item.date_updated or "—"),
            ("Ссылка", item.url or "—"),
        ]
        for label, val in rows:
            val = str(val)
            pdf.set_font(font_name, style="B", size=9)
            pdf.cell(28, 5, f"{label}:", border=0)
            pdf.set_font(font_name, style="", size=9)
            remaining = pdf.w - pdf.r_margin - pdf.get_x()
            pdf.multi_cell(remaining, 5, val, border=0, new_x="LMARGIN", new_y="NEXT")

        # All photos of this listing, as a grid under the text block.
        # Only the ones we actually fetched (capped above) are in the cache.
        photos = [photo_cache.get(u) for u in _photo_urls(item.photo)[:max_photos_per_listing]]
        photos = [p for p in photos if p is not None]
        for row_start in range(0, len(photos), cols):
            row = photos[row_start:row_start + cols]
            sizes = [_fit(pw, ph) for _, pw, ph in row]
            row_h = max(h for _, h in sizes)
            if pdf.get_y() + row_h > pdf.h - pdf.b_margin:
                pdf.add_page()
            y0 = pdf.get_y()
            for idx, (data, _, _) in enumerate(row):
                img_w, img_h = sizes[idx]
                x = pdf.l_margin + idx * (cell_w + gap)
                try:
                    pdf.image(io.BytesIO(data), x=x, y=y0, w=img_w, h=img_h)
                except Exception as exc:
                    log.debug("Photo embed failed: %s", exc)
            pdf.set_y(y0 + row_h + gap)

        pdf.ln(2)
        pdf.set_draw_color(220, 220, 220)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(2)

    return bytes(pdf.output())
