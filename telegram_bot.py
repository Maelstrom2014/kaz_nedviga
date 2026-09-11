"""Telegram bot: multi-user real-estate search with a free tier of 10 offers.

Features
- Multi-user access: every /start registers the user; the FIRST user ever
  becomes admin (stored in SQLite via db.register_bot_user).
- Simple filter menu: rooms (студия/1-к/.../4+), then price min/max.
- Cards: photo + full listing info + Telegram's built-in map (sendLocation
  when the parser extracted coordinates).
- Free tier: 10 offers. "More than 10" → paid-subscription STUB that only
  collects interested users (bot_subscription_requests) until billing exists.

Token loading order (never hardcoded — bot_token.txt is gitignored):
  1. BOT_TOKEN environment variable
  2. bot_token.txt next to this file

Run:  python telegram_bot.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from html import escape as html_escape
from pathlib import Path

import db
from logsetup import bot_file_handler
from parsers.factory import get_all_parsers
from parsers.models import Listing, SearchParams

log = logging.getLogger("bot")

# Dedicated rotating file: logs/telegram_bot.log (not shared with the
# "app"/"parsers" loggers; no console spam when embedded in the web server).
_bot_file_handler = bot_file_handler()
log.addHandler(_bot_file_handler)
log.setLevel(logging.DEBUG)
log.propagate = False

TOKEN_FILE = Path(__file__).parent / "bot_token.txt"
FREE_LIMIT = 10          # free tier: first 10 offers per search
DAILY_SEARCH_LIMIT = 10  # searches per day per non-admin user

# (value, label) — value goes into SearchParams.rooms; студия == 0 (parse_rooms)
ROOM_OPTIONS = [(0, "Студия"), (1, "1-к"), (2, "2-к"), (3, "3-к"), (4, "4+")]
# Price menu steps in KZT (0 = no bound)
PRICE_STEPS = [0, 100_000, 200_000, 300_000, 500_000, 800_000, 1_200_000]


def load_token() -> str:
    tok = os.environ.get("BOT_TOKEN", "").strip()
    if tok:
        return tok
    if TOKEN_FILE.is_file():
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    raise SystemExit(
        "Telegram token not found: set BOT_TOKEN env var or put the token "
        f"in {TOKEN_FILE.name} (gitignored).")


# ============================================================
# Search
# ============================================================

# Shared result cache keyed by the normalized filter tuple: repeated searches
# (by ANY user) within the TTL answer instantly instead of re-parsing all
# sites. Distinct filters still parse their own results.
_SEARCH_CACHE: dict[tuple, tuple[float, list[Listing]]] = {}
_SEARCH_CACHE_TTL = 600.0  # seconds
_SEARCH_CACHE_LOCK = threading.Lock()


def _cache_key(rooms: list[int] | None, price_min: int | None,
               price_max: int | None) -> tuple:
    sel = sorted({r for r in (rooms or []) if r in (0, 1, 2, 3, 4)})
    # price bounds are normalized (min<=max) before the key is built
    return (tuple(sel), price_min or None, price_max or None)


def _run_parsers(params: SearchParams, has_4plus: bool,
                 exact: list[int]) -> list[Listing]:
    """Fetch via all parsers concurrently, dedupe, apply "4+" room logic."""
    results: list[Listing] = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(p.run, params): p.name for p in get_all_parsers()}
        for fut in as_completed(futures):
            try:
                results.extend(fut.result())
            except Exception as exc:
                log.warning("[bot] parser %s failed: %s", futures[fut], exc)
    # Dedupe by url+source, keep the first occurrence
    seen: set[tuple] = set()
    deduped: list[Listing] = []
    for r in results:
        key = (r.source, r.url)
        if not r.url or key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    if has_4plus:
        deduped = [r for r in deduped
                   if r.rooms is None or r.rooms in exact or r.rooms >= 4]
    return deduped


def _fair_mix(deduped: list[Listing], cap: int) -> list[Listing]:
    """Round-robin across sources, best-first within each source.

    FAIR MIX: only telegram (and twogis) set quality_score, so a global
    quality/price sort would fill the top-10 with a single source.
    """

    def _best_first_key(r: Listing):
        return (-(r.quality_score or 0),
                r.price if r.price is not None else float("inf"))

    queues: dict[str, list[Listing]] = {}
    for r in deduped:
        queues.setdefault(r.source or "?", []).append(r)
    for q in queues.values():
        q.sort(key=_best_first_key)
    mixed: list[Listing] = []
    while len(mixed) < cap and any(queues.values()):
        for src in sorted(queues):
            if queues[src] and len(mixed) < cap:
                mixed.append(queues[src].pop(0))
    return mixed


def run_search(rooms: list[int] | None, price_min: int | None,
               price_max: int | None, max_results: int = FREE_LIMIT) -> list[Listing]:
    """Run all parsers concurrently, filter, return up to max_results listings.

    ``rooms`` is a MULTI-SELECTION (e.g. [0, 1] or [2, 4]); value 4 means
    "4 rooms OR MORE". Blocking — call via asyncio.to_thread. limit=0 so we
    get everything and can tell whether the free-tier cut anything off.

    Results are cached per filter key for _SEARCH_CACHE_TTL seconds and
    shared between ALL users: a second search with the same filters within
    the TTL is answered from cache without re-parsing the sites.
    """
    # User picked min>max (menu allows any order) — normalize instead of
    # returning zero results.
    if price_min and price_max and price_min > price_max:
        price_min, price_max = price_max, price_min
    key = _cache_key(rooms, price_min, price_max)
    sel = list(key[0])
    has_4plus = 4 in sel
    exact = [r for r in sel if r != 4]
    now = time.monotonic()
    with _SEARCH_CACHE_LOCK:
        hit = _SEARCH_CACHE.get(key)
        if hit and now - hit[0] <= _SEARCH_CACHE_TTL:
            log.info("[bot] search cache hit %s (%d results)", key, len(hit[1]))
            return hit[1][:max_results]
    if has_4plus:
        # Exact-match rooms filter would drop 5+ room listings; filter here
        # instead ("rooms in selection OR rooms >= 4").
        room_filter: list[int] = []
    else:
        room_filter = exact
    params = SearchParams(
        city="almaty",
        rooms=room_filter,
        price_min=price_min or None,
        price_max=price_max or None,
        limit=0,        # no early cut: we need the full count for the paywall
        max_pages=2,    # keep the bot responsive; repeat queries hit the HTML cache
    )
    deduped = _run_parsers(params, has_4plus, exact)
    mixed = _fair_mix(deduped, 10**6)
    with _SEARCH_CACHE_LOCK:
        # prune expired entries while we hold the lock
        for k, (ts, _) in list(_SEARCH_CACHE.items()):
            if now - ts > _SEARCH_CACHE_TTL:
                del _SEARCH_CACHE[k]
        _SEARCH_CACHE[key] = (now, mixed)
    return mixed[:max_results]


# ============================================================
# Formatting
# ============================================================

def rooms_label(rooms: int | None) -> str:
    if rooms is None:
        return "—"
    return "студия" if rooms == 0 else f"{rooms}-к"


def osm_link(lat: float, lon: float) -> str:
    return f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=16/{lat}/{lon}"


def format_card(l: Listing) -> str:
    """Full info card for the photo caption (Telegram limit: 1024 chars).

    All site-sourced text is HTML-escaped: parse_mode="HTML" rejects the
    whole caption on stray '<'/'&' (titles like "2-к <люкс>" come from
    parsers), which would silently lose the card.
    """
    esc = html_escape
    parts = [f"🏠 <b>{esc(l.title or 'Объявление')}</b>"]
    # KZT + RUB + USD (price_all_str uses cached rates; fail-safe)
    price = l.price_all_str() if l.price is not None else "цена не указана"
    parts.append(f"💰 {price}")
    feats = []
    if l.rooms is not None:
        feats.append("студия" if l.rooms == 0 else f"{l.rooms}-комн.")
    if l.area:
        feats.append(f"{l.area:g} м²")
    if l.floor and l.total_floors:
        feats.append(f"этаж {l.floor}/{l.total_floors}")
    elif l.floor:
        feats.append(f"этаж {l.floor}")
    if feats:
        parts.append("🛏 " + " · ".join(feats))
    if l.address:
        parts.append(f"📍 {esc(l.address)}")
    if l.residential_complex:
        parts.append(f"🏢 ЖК {esc(l.residential_complex)}")
    if l.phone:
        parts.append(f"📞 {esc(l.phone)}")
    if l.lat is not None and l.lon is not None:
        parts.append("🗺 карта — следующим сообщением")
    if l.url:
        parts.append(f'🌐 Источник: <a href="{esc(l.url, quote=True)}">{esc(l.source or "источник")}</a>')
    text = "\n".join(parts)
    if len(text) > 1000:  # margin under the 1024 caption limit
        text = text[:997] + "…"
    return text
    if len(text) > 1000:  # margin under the 1024 caption limit
        text = text[:997] + "…"
    return text


def photo_of(l: Listing) -> str | None:
    urls = [u for u in (l.photo or "").split("|") if u.startswith("http")]
    return urls[0] if urls else None


def photo_urls_of(l: Listing) -> list[str]:
    """All photo URLs of a listing (bot tries several before giving up)."""
    return [u for u in (l.photo or "").split("|") if u.startswith("http")]


def sources_line(listings: list[Listing]) -> str:
    """Per-source result counts: "krisha.kz: 40 · olx.kz: 30 · telegram: 34".

    Sorted by count desc (ties alphabetically) — the search summary shows
    which sites were parsed and how much each contributed.
    """
    from collections import Counter
    counts = Counter(l.source or "?" for l in listings)
    return " · ".join(f"{src}: {n}"
                      for src, n in sorted(counts.items(),
                                           key=lambda kv: (-kv[1], kv[0])))


def _download_photo_safe(url: str) -> bytes | None:
    """Photo bytes via the export disk cache + referer-aware download.

    sendPhoto-by-URL makes TELEGRAM's servers fetch the URL — most real-estate
    CDNs refuse that ("Failed to get http url content"), so the bot downloads
    the image itself (export_utils cache: already warm from precache) and
    uploads the bytes instead.
    """
    try:
        from export_utils import _download_photo
        return _download_photo(url)
    except Exception as exc:
        log.info("[bot] photo download failed %s: %s", url[:60], exc)
        return None


def _is_image_bytes(data: bytes) -> bool:
    """Magic-byte check: skip formats Telegram rejects (AVIF/HEIC/HTML)."""
    if data[:3] == b"\xff\xd8\xff":
        return True  # JPEG
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True  # PNG
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True  # WebP
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True  # GIF
    return False


def media_group(caption: str, data_list: list[bytes]):
    """Build a sendMediaGroup payload (2..10 photos, caption on the first).

    Telegram allows exactly one caption per media group — the listing card
    (all listing info) goes onto the FIRST photo; the rest follow silently.
    """
    from telegram import InputMediaPhoto
    return [InputMediaPhoto(media=b,
                            caption=caption if i == 0 else None,
                            parse_mode="HTML" if i == 0 else None)
            for i, b in enumerate(data_list[:10])]


# ============================================================
# Conversation state (per chat; private bot = one user per chat).
# NOTE: the dict is _STATE; the accessor function is _state(). A single
# name for both was a shadowing bug: `def _state` rebound the global dict
# to the function, so _state.setdefault() crashed on the first menu press.
# ============================================================
_STATE: dict[int, dict] = {}

_EMPTY_FILTERS = {"rooms": [], "price_min": None, "price_max": None}


def _state(chat_id: int) -> dict:
    return _STATE.setdefault(chat_id, dict(_EMPTY_FILTERS))


# ============================================================
# Combined filter menu: room checkboxes (multi-select) + min/max price
# + search/reset — all on one panel, edited in place on every toggle.
# ============================================================

def _fmt_price(val: int | None) -> str:
    if not val:
        return "—"
    return f"{val:,} тг".replace(",", " ")


def fmt_price_all(kzt: int | None) -> str:
    """KZT with RUB and USD in parentheses: "200 000 тг (~38 300 ₽ / ~$430)".

    Falls back to KZT-only when rates are unavailable (no network AND no
    cache — rates.py has its own hardcoded fallback, so this is rare).
    """
    if not kzt:
        return "—"
    base = f"{kzt:,} тг".replace(",", " ")
    try:
        from rates import rub_per_kzt, usd_per_kzt
        rub = kzt * rub_per_kzt()
        usd = kzt * usd_per_kzt()
        return f"{base} (~{rub:,.0f} ₽ / ~${usd:,.0f})".replace(",", " ")
    except Exception:
        return base


def _rooms_line(st: dict) -> str:
    sel = st.get("rooms") or []
    if not sel:
        return "Комнаты: любые"
    labels = [lbl for v, lbl in ROOM_OPTIONS if v in sel]
    return "Комнаты: " + ", ".join(labels)


def filters_menu_text(st: dict) -> str:
    pmin, pmax = st.get("price_min"), st.get("price_max")
    price = "Цена: любая" if not pmin and not pmax else \
        f"Цена: от {fmt_price_all(pmin)} до {fmt_price_all(pmax)}"
    return ("🎛 <b>Фильтры поиска</b>\n\n"
            f"{_rooms_line(st)}\n{price}\n\n"
            "Нажмите комнату, чтобы поставить/снять галочку (можно несколько).")


def filters_menu_kb(st: dict):
    """One-panel menu: room checkboxes + price buttons + action buttons."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    sel = set(st.get("rooms") or [])

    def room_btn(v: int, lbl: str) -> InlineKeyboardButton:
        mark = "✅" if v in sel else "▫️"
        return InlineKeyboardButton(f"{mark} {lbl}", callback_data=f"rooms:{v}")

    btns = [room_btn(v, lbl) for v, lbl in ROOM_OPTIONS]
    return InlineKeyboardMarkup([
        btns[:3],
        btns[3:],
        [InlineKeyboardButton(f"От: {fmt_price_all(st.get('price_min'))}",
                              callback_data="pmin:menu"),
         InlineKeyboardButton(f"До: {fmt_price_all(st.get('price_max'))}",
                              callback_data="pmax:menu")],
        [InlineKeyboardButton("🔍 Найти", callback_data="filters:search"),
         InlineKeyboardButton("♻️ Сброс", callback_data="filters:reset")],
    ])


def build_app(token: str):
    """Create the telegram Application with all handlers wired."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                              ContextTypes)

    app = Application.builder().token(token).build()

    # ---- keyboards -----------------------------------------------------

    def main_menu_kb() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("🔍 Подобрать жильё", callback_data="menu:rooms"),
            InlineKeyboardButton("⭐ Подписка", callback_data="menu:sub"),
            InlineKeyboardButton("🛠 Поддержка", callback_data="menu:sup"),
        ]])

    def price_kb(kind: str) -> InlineKeyboardMarkup:
        rows = [[InlineKeyboardButton(
            "без ограничения" if v == 0 else fmt_price_all(v),
            callback_data=f"{kind}:{v}")] for v in PRICE_STEPS]
        return InlineKeyboardMarkup(rows)

    def subscribe_kb() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("💰 Оформить подписку", callback_data="sub:request"),
        ]])

    # ---- helpers -------------------------------------------------------

    def _is_admin(update: Update) -> bool:
        return db.is_bot_admin(update.effective_user.id)

    async def _paywall_footer(update: Update, total: int, shown: int) -> None:
        hidden = max(total - shown, 0)
        if hidden > 0:
            await update.effective_chat.send_message(
                f"⚡ Показано {shown} из {total} найденных.\n"
                "Больше предложений доступно по платной подписке.",
                reply_markup=subscribe_kb())

    async def _subscribe(update: Update) -> None:
        user = update.effective_user
        pos = db.add_subscription_request(user.id, username=user.username or "")
        log.info("[bot] subscription request: user %s (@%s) queue #%d",
                 user.id, user.username, pos)
        await update.effective_message.reply_html(
            "💳 Платная подписка (10+ предложений) в разработке.\n"
            f"Вы <b>#{pos}</b> в списке ожидания — мы свяжемся с вами.")

    async def _run_and_send(update: Update) -> None:
        chat_id = update.effective_chat.id
        user = update.effective_user
        # Daily quota: 10 searches/day for non-admin users.
        u_row = db.get_bot_user(user.id) or {}
        is_admin = u_row.get("role") == "admin"
        if not is_admin:
            used = db.count_bot_searches_today(user.id)
            if used >= DAILY_SEARCH_LIMIT:
                log.info("[bot] quota denied: user %s (%s @%s) used %d/%d today",
                         user.id, user.first_name, user.username, used,
                         DAILY_SEARCH_LIMIT)
                await update.effective_chat.send_message(
                    f"⛔ Достигнут дневной лимит: {DAILY_SEARCH_LIMIT} поисков "
                    "в день.\nОформите подписку, чтобы снять ограничение.",
                    reply_markup=subscribe_kb())
                return
        st = _state(chat_id)
        db.bump_bot_user_searches(user.id)
        log.info("[bot] search start: user %s (@%s) rooms=%s price=%s..%s",
                 user.id, user.username, st["rooms"], st["price_min"],
                 st["price_max"])
        await update.effective_chat.send_action("typing")
        try:
            all_results = await asyncio.to_thread(
                run_search, st["rooms"], st["price_min"], st["price_max"], 10**6)
        except Exception as exc:
            log.exception("[bot] search failed")
            await update.effective_chat.send_message(
                f"Ошибка поиска: {exc}. Попробуйте позже.")
            return
        total = len(all_results)
        results = all_results[:FREE_LIMIT]
        if not results:
            log.info("[bot] search empty: user %s (@%s)", user.id, user.username)
            await update.effective_chat.send_message(
                "Ничего не нашлось. Попробуйте расширить фильтры.")
            return
        log.info("[bot] search done: user %s (@%s) found %d, sending %d",
                 user.id, user.username, total, len(results))
        await update.effective_chat.send_message(
            f"Найдено {total} предложений:\n"
            f"📊 {sources_line(all_results)}\n\n"
            f"Показываю лучшие {len(results)}:")
        shown = 0
        for l in results:
            caption = format_card(l)
            urls = photo_urls_of(l)[:10]  # Telegram album limit
            sent = False
            # Download every photo ourselves (cache-first) — Telegram cannot
            # fetch real-estate CDN links directly.
            data_list: list[bytes] = []
            for url in urls:
                data = await asyncio.to_thread(_download_photo_safe, url)
                if data and _is_image_bytes(data):
                    data_list.append(data)
                if len(data_list) >= 10:
                    break
            try:
                if len(data_list) == 1:
                    await update.effective_chat.send_photo(
                        photo=data_list[0], caption=caption, parse_mode="HTML")
                    sent = True
                elif len(data_list) >= 2:
                    await update.effective_chat.send_media_group(
                        media=media_group(caption, data_list))
                    sent = True
            except Exception as exc:
                log.info("[bot] photo album send failed: %s", exc)
            if not sent and urls:
                # Fallback: URL-based send (Telegram fetches — often refused).
                try:
                    if len(urls) == 1:
                        await update.effective_chat.send_photo(
                            photo=urls[0], caption=caption, parse_mode="HTML")
                    else:
                        from telegram import InputMediaPhoto
                        await update.effective_chat.send_media_group(
                            media=[InputMediaPhoto(
                                media=u, caption=caption if i == 0 else None,
                                parse_mode="HTML" if i == 0 else None)
                                for i, u in enumerate(urls)])
                    sent = True
                except Exception as exc:
                    log.info("[bot] photo url send failed: %s", exc)
            if not sent:
                await update.effective_chat.send_message(
                    caption, parse_mode="HTML")
            if l.lat is not None and l.lon is not None:
                try:
                    await update.effective_chat.send_location(
                        latitude=l.lat, longitude=l.lon)
                except Exception as exc:
                    log.info("[bot] location send failed: %s", exc)
            shown += 1
        await _paywall_footer(update, total, shown)

    # ---- commands ------------------------------------------------------

    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        row = db.register_bot_user(
            user.id, username=user.username or "", first_name=user.first_name or "")
        log.info("[bot] user %s (@%s) started, role=%s",
                 user.id, user.username, row["role"])
        if row["role"] == "admin":
            role_note = "Вы первый пользователь — вам выданы права <b>администратора</b>."
        else:
            role_note = f"Привет, <b>{user.first_name or 'пользователь'}</b>!"
        await update.message.reply_html(
            f"{role_note}\n\nЯ ищу жильё в Алматы по всем сайтам сразу.\n"
            "Нажмите «Подобрать жильё»: отметьте комнаты галочками (можно "
            "несколько), задайте цену — пришлю 10 лучших предложений с фото "
            "и картой (бесплатно).",
            reply_markup=main_menu_kb())

    async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "Команды:\n"
            "/start — меню\n"
            "/search — подбор по текущим фильтрам\n"
            "/support <текст> — написать в техподдержку\n"
            "/subscribe — платная подписка (10+ предложений)\n"
            "Админ: /users, /subs, /msgs, /done <id>, /promote <id>, /stats")

    async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _run_and_send(update)

    async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _subscribe(update)

    async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_admin(update):
            await update.message.reply_text("Только для администратора.")
            return
        users = db.list_bot_users()
        lines = [f"{'👑' if u['role'] == 'admin' else '👤'} "
                 f"{u['first_name']} @{u['username']} (id {u['telegram_id']}), "
                 f"поисков: {u['searches']}" for u in users]
        await update.message.reply_text("\n".join(lines) or "Пользователей нет.")

    async def cmd_subs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_admin(update):
            await update.message.reply_text("Только для администратора.")
            return
        reqs = db.list_subscription_requests()
        lines = [f"• @{r['username']} (id {r['telegram_id']}) — {r['requested_at']}"
                 for r in reqs]
        await update.message.reply_text(
            "Заявки на подписку:\n" + ("\n".join(lines) or "пока нет"))

    async def cmd_promote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_admin(update):
            await update.message.reply_text("Только для администратора.")
            return
        if not context.args or not context.args[0].lstrip("-").isdigit():
            await update.message.reply_text("Использование: /promote <telegram_id>")
            return
        ok = db.set_bot_user_role(int(context.args[0]), "admin")
        await update.message.reply_text("Готово." if ok else "Пользователь не найден.")

    async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_admin(update):
            await update.message.reply_text("Только для администратора.")
            return
        users = db.list_bot_users()
        reqs = db.list_subscription_requests()
        msgs = db.list_support_messages(only_open=True)
        await update.message.reply_text(
            f"Пользователей: {len(users)}\n"
            f"Заявок на подписку: {len(reqs)}\n"
            f"Открытых обращений в поддержку: {len(msgs)}")

    # ---- support -------------------------------------------------------

    async def cmd_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/support <текст> — отправить сообщение техподдержке (админам)."""
        user = update.effective_user
        text = " ".join(context.args or []).strip()
        if not text:
            await update.message.reply_text(
                "Напишите вопрос одной командой:\n"
                "/support Не приходит телефон в карточке")
            return
        msg_id = db.add_support_message(
            user.id, username=user.username or "", text=text[:1000])
        log.info("[bot] support message #%d from user %s (@%s): %s",
                 msg_id, user.id, user.username, text[:200])
        # Deliver to every admin (skip the author if they ARE the admin).
        admins = [u["telegram_id"] for u in db.list_bot_users()
                  if u["role"] == "admin" and u["telegram_id"] != user.id]
        delivered = 0
        for admin_id in admins:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"🛠 Обращение #{msg_id} от @{user.username or user.id}:\n"
                         f"{text}")
                delivered += 1
            except Exception as exc:
                log.warning("[bot] support delivery to %s failed: %s",
                            admin_id, exc)
        log.info("[bot] support #%d delivered to %d admin(s)",
                 msg_id, delivered)
        await update.message.reply_text(
            f"✅ Обращение #{msg_id} отправлено в поддержку.")

    async def cmd_msgs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin: list support requests."""
        if not _is_admin(update):
            await update.message.reply_text("Только для администратора.")
            return
        msgs = db.list_support_messages(limit=20)
        lines = [f"#{m['id']} {'✅' if m['answered'] else '🔴'} "
                 f"@{m['username']} (id {m['telegram_id']}): {m['text'][:120]}"
                 for m in msgs]
        await update.message.reply_text(
            "Обращения в поддержку:\n" + ("\n".join(lines) or "пока нет")
            + "\n\nОтметить отвеченным: /done <id>")

    async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_admin(update):
            await update.message.reply_text("Только для администратора.")
            return
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text("Использование: /done <id>")
            return
        ok = db.mark_support_answered(int(context.args[0]))
        await update.message.reply_text("Отмечено." if ok else "Не найдено.")

    # ---- callbacks -----------------------------------------------------

    async def on_rooms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Toggle one room checkbox; refresh the panel in place."""
        q = update.callback_query
        st = _state(update.effective_chat.id)
        value = int(q.data.split(":")[1])
        sel = set(st["rooms"])
        sel.symmetric_difference_update({value})  # toggle
        st["rooms"] = sorted(sel)
        await q.answer()
        try:
            await q.message.edit_text(
                filters_menu_text(st), parse_mode="HTML",
                reply_markup=filters_menu_kb(st))
        except Exception as exc:
            log.info("[bot] menu edit failed: %s", exc)

    async def on_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        st = _state(update.effective_chat.id)
        kind, value = q.data.split(":")
        await q.answer()
        if value == "menu":
            # Price-step picker on a fresh message.
            await q.message.reply_text(
                "Минимальная цена:" if kind == "pmin" else "Максимальная цена:",
                reply_markup=price_kb(kind))
            return
        # Step chosen: apply and turn the picker message into the panel.
        st["price_min" if kind == "pmin" else "price_max"] = int(value) or None
        try:
            await q.message.edit_text(
                filters_menu_text(st), parse_mode="HTML",
                reply_markup=filters_menu_kb(st))
        except Exception as exc:
            log.info("[bot] menu edit failed: %s", exc)

    async def on_filters_action(update: Update,
                                context: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if q.data == "filters:reset":
            _state(update.effective_chat.id).update(dict(_EMPTY_FILTERS))
            await q.answer("Фильтры сброшены")
            await q.message.edit_text(
                filters_menu_text(_state(update.effective_chat.id)),
                parse_mode="HTML",
                reply_markup=filters_menu_kb(_state(update.effective_chat.id)))
            return
        # "filters:search"
        await q.answer()
        await q.message.reply_text("Ищу предложения… ⏳ (это займёт 1–3 минуты)")
        await _run_and_send(update)

    async def on_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.callback_query.answer()
        await _subscribe(update)

    async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if q.data == "menu:rooms":
            # keep previous selections — the reset button is on the panel
            st = _state(update.effective_chat.id)
            await q.message.reply_html(
                filters_menu_text(st), reply_markup=filters_menu_kb(st))
        elif q.data == "menu:sub":
            await q.message.reply_text(
                "💳 Платная подписка (10+ предложений за поиск) — в разработке. "
                "Оставить заявку: /subscribe")
        elif q.data == "menu:sup":
            await q.message.reply_text(
                "🛠 Техподдержка: напишите вопрос одной командой\n"
                "/support ваш вопрос\n\n"
                "Ответ придёт в этот чат, уведомление получат администраторы.")
        await q.answer()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("subs", cmd_subs))
    app.add_handler(CommandHandler("promote", cmd_promote))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("support", cmd_support))
    app.add_handler(CommandHandler("msgs", cmd_msgs))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CallbackQueryHandler(on_rooms, pattern=r"^rooms:"))
    app.add_handler(CallbackQueryHandler(on_price, pattern=r"^p(min|max):(menu|\d+)$"))
    app.add_handler(CallbackQueryHandler(on_filters_action,
                                         pattern=r"^filters:(search|reset)$"))
    app.add_handler(CallbackQueryHandler(on_subscribe, pattern=r"^sub:request$"))
    app.add_handler(CallbackQueryHandler(on_menu, pattern=r"^menu:"))

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log handler exceptions to the bot file (PTB default only prints
        to the server console, which is invisible when running embedded)."""
        user_msg = getattr(update, "message", None) or getattr(update, "callback_query", None)
        log.exception("[bot] handler error on %r: %s",
                      getattr(user_msg, "data", None)
                      or getattr(user_msg, "text", None) or update,
                      context.error)

    app.add_error_handler(on_error)
    return app


# ============================================================
# Embedding: run the bot as a daemon thread inside the web app.
# Started by app.py when the "bot_enabled" setting is on; toggled live
# from the settings API (same pattern as scheduler.py).
# ============================================================

_bot_app = None            # PTB Application
_bot_thread: threading.Thread | None = None
_bot_stop = threading.Event()
_bot_lock = threading.Lock()
_bot_running_flag = False


def bot_running() -> bool:
    return bool(_bot_thread and _bot_thread.is_alive())


def start_from_settings(settings: dict) -> bool:
    """Start the bot thread when ``bot_enabled`` is on in settings.

    Returns True when the bot is running after the call. Missing token only
    logs a warning (bot_token.txt is optional for web-only installs).
    """
    global _bot_app, _bot_thread
    if not settings.get("bot_enabled"):
        return False
    with _bot_lock:
        if bot_running():
            return True
        token = load_token_quiet()
        if not token:
            log.warning("[bot] autostart skipped: no token (bot_token.txt / BOT_TOKEN)")
            return False
        _bot_stop.clear()
        _bot_app = build_app(token)
        _bot_thread = threading.Thread(
            target=_run_bot_thread, args=(_bot_app, _bot_stop),
            name="telegram-bot", daemon=True)
        _bot_thread.start()
    log.info("[bot] autostarted from settings")
    return True


def configure(enabled: bool) -> None:
    """Live toggle from the settings UI: start or stop the bot thread."""
    if enabled:
        try:
            start_from_settings({"bot_enabled": True})
        except Exception as exc:
            log.warning("[bot] configure(start) failed: %s", exc)
    else:
        stop_bot()


def stop_bot(timeout: float = 10.0) -> None:
    global _bot_thread
    if not bot_running():
        return
    _bot_stop.set()
    t = _bot_thread
    if t is not None:
        t.join(timeout=timeout)
    _bot_thread = None


def _run_bot_thread(app, stop_event: threading.Event) -> None:
    """Run the PTB application on this thread's own event loop.

    ``app.run_polling()`` is main-thread-only (signal handlers), so the
    embed path drives the async lifecycle manually. Every failure point
    logs to the bot file — the thread's stderr is invisible (console-only).
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    except Exception as exc:
        log.error("[bot] thread: event loop setup failed: %s", exc)
        return

    async def _run() -> None:
        log.debug("[bot] thread: initializing…")
        await app.initialize()
        log.debug("[bot] thread: initialized (bot @%s)",
                  (await app.bot.get_me()).username)
        await app.start()
        if app.updater is not None:
            await app.updater.start_polling()
            log.info("[bot] thread: polling started")
        else:
            log.error("[bot] thread: no updater — polling impossible")
        try:
            while not stop_event.is_set():
                await asyncio.sleep(0.5)
        finally:
            if app.updater is not None:
                await app.updater.stop()
            await app.stop()
            await app.shutdown()
            log.info("[bot] thread: stopped cleanly")

    try:
        loop.run_until_complete(_run())
    except Exception as exc:
        log.exception("[bot] thread crashed: %s", exc)
    finally:
        try:
            loop.close()
        except Exception:
            pass


def load_token_quiet() -> str | None:
    """load_token() that returns None instead of exiting (autostart path)."""
    try:
        return load_token()
    except SystemExit:
        log.warning("[bot] token not found — autostart disabled "
                    "(set BOT_TOKEN or create bot_token.txt)")
        return None


def reset() -> None:
    """Test helper: forget thread state (no polling loop was ever started in tests)."""
    global _bot_app, _bot_thread
    _bot_stop.set()
    with _bot_lock:
        _bot_app = None
        _bot_thread = None


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    app = build_app(load_token())
    log.info("[bot] starting @kaz_home_nedviga_bot")
    app.run_polling()


if __name__ == "__main__":
    main()
