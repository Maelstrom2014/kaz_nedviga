"""Telegram bot storage + formatting tests (no network, no token needed)."""
from pathlib import Path

import pytest

import db
import telegram_bot as tb


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db.close_db()
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "bot.db")
    db.get_conn()
    yield
    db.close_db()


@pytest.fixture(autouse=True)
def _fixed_rates(monkeypatch):
    """Deterministic exchange rates — no network in formatting tests."""
    import rates
    monkeypatch.setattr(rates, "rub_per_kzt", lambda: 0.2)
    monkeypatch.setattr(rates, "usd_per_kzt", lambda: 0.0025)


class TestBotUsers:
    def test_first_user_becomes_admin(self, fresh_db):
        u = db.register_bot_user(111, username="alpha", first_name="Alpha")
        assert u["role"] == "admin"

    def test_second_user_is_regular(self, fresh_db):
        db.register_bot_user(111, username="alpha")
        u = db.register_bot_user(222, username="beta", first_name="Beta")
        assert u["role"] == "user"
        assert db.is_bot_admin(111)
        assert not db.is_bot_admin(222)

    def test_reregister_keeps_role(self, fresh_db):
        db.register_bot_user(111, username="alpha")
        u = db.register_bot_user(111, username="alpha2", first_name="A2")
        assert u["role"] == "admin"
        assert u["username"] == "alpha2"

    def test_promote_and_demote(self, fresh_db):
        db.register_bot_user(111)
        db.register_bot_user(222)
        assert db.set_bot_user_role(222, "admin")
        assert db.is_bot_admin(222)
        db.set_bot_user_role(222, "user")
        assert not db.is_bot_admin(222)

    def test_set_role_invalid(self, fresh_db):
        db.register_bot_user(111)
        with pytest.raises(ValueError):
            db.set_bot_user_role(111, "superuser")

    def test_list_users_ordered_by_join(self, fresh_db):
        db.register_bot_user(111)
        db.register_bot_user(222)
        ids = [u["telegram_id"] for u in db.list_bot_users()]
        assert ids == [111, 222]

    def test_bump_searches(self, fresh_db):
        db.register_bot_user(111)
        db.bump_bot_user_searches(111)
        db.bump_bot_user_searches(111)
        assert db.get_bot_user(111)["searches"] == 2


class TestSubscriptionRequests:
    def test_request_position_grows(self, fresh_db):
        assert db.add_subscription_request(111, username="a") == 1
        assert db.add_subscription_request(222, username="b") == 2

    def test_duplicate_request_keeps_one_row(self, fresh_db):
        db.add_subscription_request(111, username="a")
        db.add_subscription_request(111, username="a2")
        reqs = db.list_subscription_requests()
        assert len(reqs) == 1
        assert reqs[0]["username"] == "a2"


class TestFormatting:
    def _listing(self, **kw):
        defaults = dict(
            title="2-к квартира, 54 м²", price=250000, currency="тг",
            rooms=2, area=54.0, floor=3, total_floors=9,
            address="ул. Панфилова 100", url="https://krisha.kz/a/show/1",
            source="krisha.kz", phone="+7 705 123 45 67",
            photo="https://img/1.jpg|https://img/2.jpg",
            lat=43.25, lon=76.95,
        )
        defaults.update(kw)
        return tb.Listing(**defaults)

    def test_card_contains_all_info(self):
        card = tb.format_card(self._listing())
        assert "250 000 тг" in card
        # KZT + RUB + USD (fixed rates: 0.2 ₽, 0.0025 $ per тг)
        assert "50 000 руб" in card
        assert "$625" in card
        assert "2-комн." in card
        assert "54 м²" in card
        assert "этаж 3/9" in card
        assert "ул. Панфилова" in card
        assert "705 123 45 67" in card
        assert "https://krisha.kz/a/show/1" in card

    def test_studio_label(self):
        assert "студия" in tb.format_card(self._listing(rooms=0, title="Студия"))

    def test_card_truncated_under_telegram_limit(self):
        l = self._listing(description="x" * 5000, title="y" * 3000)
        card = tb.format_card(l)
        assert len(card) <= 1001
        assert card.endswith("…")

    def test_photo_of_picks_first(self):
        l = self._listing(photo="https://a/1.jpg|https://a/2.jpg")
        assert tb.photo_of(l) == "https://a/1.jpg"

    def test_photo_urls_of_all(self):
        l = self._listing(photo="https://a/1.jpg|https://a/2.jpg|bad|https://a/3.jpg")
        assert tb.photo_urls_of(l) == ["https://a/1.jpg", "https://a/2.jpg",
                                       "https://a/3.jpg"]

    def test_download_photo_safe_uses_export_cache(self, monkeypatch):
        """Bytes must come from the export photo cache (Telegram cannot
        fetch real-estate CDN URLs itself — that's the missing-photos bug)."""
        from parsers.models import Listing

        calls = {"n": 0}
        import export_utils

        def fake_download(url):
            calls["n"] += 1
            return b"jpeg-bytes"

        monkeypatch.setattr(export_utils, "_download_photo", fake_download)
        l = Listing(url="u", source="s", photo="https://a/1.jpg")
        assert tb._download_photo_safe("https://a/1.jpg") == b"jpeg-bytes"
        assert calls["n"] == 1

    def test_download_photo_safe_failure(self, monkeypatch):
        import export_utils

        def boom(url):
            raise RuntimeError("down")

        monkeypatch.setattr(export_utils, "_download_photo", boom)
        assert tb._download_photo_safe("https://a/1.jpg") is None

    def test_photo_of_none(self):
        assert tb.photo_of(self._listing(photo="")) is None


class TestAllPhotos:
    def test_is_image_bytes(self):
        assert tb._is_image_bytes(b"\xff\xd8\xffjunk") is True       # JPEG
        assert tb._is_image_bytes(b"\x89PNG\r\n\x1a\njunk") is True  # PNG
        assert tb._is_image_bytes(b"RIFF\x18\x00\x00\x00WEBPjunk") is True  # WebP
        assert tb._is_image_bytes(b"\x00\x00\x00\x18ftypavif") is False  # AVIF
        assert tb._is_image_bytes(b"<html>") is False

    def test_media_group_caption_on_first_only(self):
        items = tb.media_group("карточка", [b"a", b"b", b"c"])
        assert len(items) == 3
        assert items[0].caption == "карточка"
        assert items[0].parse_mode == "HTML"
        assert items[1].caption is None
        assert items[2].caption is None

    def test_media_group_capped_at_10(self):
        items = tb.media_group("c", [b"x"] * 15)
        assert len(items) == 10


class TestOsmlink:
    def test_osm_link(self):
        assert "mlat=43.25" in tb.osm_link(43.25, 76.95)
        assert "#map=16/43.25/76.95" in tb.osm_link(43.25, 76.95)


class TestSourcesLine:
    def test_counts_desc(self):
        from parsers.models import Listing

        ls = [Listing(url=f"u{i}", source="telegram") for i in range(3)]
        ls += [Listing(url=f"k{i}", source="krisha.kz") for i in range(5)]
        ls += [Listing(url="o", source="olx.kz")]
        line = tb.sources_line(ls)
        assert line == "krisha.kz: 5 · telegram: 3 · olx.kz: 1"

    def test_tie_alphabetical_and_unknown(self):
        from parsers.models import Listing

        ls = [Listing(url="a", source="b.kz"), Listing(url="b", source="a.kz"),
              Listing(url="c", source="")]
        # "?" (unknown source) sorts before lettered names alphabetically
        assert tb.sources_line(ls) == "?: 1 · a.kz: 1 · b.kz: 1"

    def test_card_labels_source(self):
        l = tb.Listing(url="https://krisha.kz/a/show/1", source="krisha.kz",
                       title="t", price=100000)
        assert "🌐 Источник: <a href=" in tb.format_card(l)
        assert ">krisha.kz</a>" in tb.format_card(l)


class TestFmtPriceAll:
    def test_unset(self):
        assert tb.fmt_price_all(None) == "—"
        assert tb.fmt_price_all(0) == "—"

    def test_all_three_currencies(self):
        # fixed rates: 1 тг = 0.2 ₽ = 0.0025 $
        out = tb.fmt_price_all(200_000)
        assert out.startswith("200 000 тг")
        assert "(~40 000 ₽ / ~$500)" in out

    def test_rub_usd_in_menu_and_picker(self):
        kb = tb.filters_menu_kb({"rooms": [], "price_min": 200000,
                                 "price_max": 500000})
        texts = [b.text for row in kb.inline_keyboard for b in row]
        assert any("От: 200 000 тг" in t and "₽" in t and "$" in t
                   for t in texts)
        assert any("До: 500 000 тг" in t and "₽" in t and "$" in t
                   for t in texts)

    def test_menu_text_has_all_currencies(self):
        st = {"rooms": [1], "price_min": 100000, "price_max": 250000}
        text = tb.filters_menu_text(st)
        assert "от 100 000 тг (~20 000 ₽ / ~$250)" in text
        assert "до 250 000 тг (~50 000 ₽ / ~$625)" in text

    def test_rates_failure_falls_back_to_kzt(self, monkeypatch):
        import rates

        def boom():
            raise RuntimeError("no rates")

        monkeypatch.setattr(rates, "rub_per_kzt", boom)
        assert tb.fmt_price_all(200000) == "200 000 тг"


class TestTokenLoading:
    def test_token_from_file(self, tmp_path, monkeypatch):
        f = tmp_path / "bot_token.txt"
        f.write_text("123:ABC\n", encoding="utf-8")
        monkeypatch.setattr(tb, "TOKEN_FILE", f)
        monkeypatch.delenv("BOT_TOKEN", raising=False)
        assert tb.load_token() == "123:ABC"

    def test_token_env_wins(self, tmp_path, monkeypatch):
        f = tmp_path / "bot_token.txt"
        f.write_text("123:FILE", encoding="utf-8")
        monkeypatch.setattr(tb, "TOKEN_FILE", f)
        monkeypatch.setenv("BOT_TOKEN", "123:ENV")
        assert tb.load_token() == "123:ENV"

    def test_token_missing_exits(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tb, "TOKEN_FILE", tmp_path / "absent.txt")
        monkeypatch.delenv("BOT_TOKEN", raising=False)
        with pytest.raises(SystemExit):
            tb.load_token()

    def test_load_token_quiet_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tb, "TOKEN_FILE", tmp_path / "absent.txt")
        monkeypatch.delenv("BOT_TOKEN", raising=False)
        assert tb.load_token_quiet() is None


class TestEmbedLifecycle:
    """Bot-as-a-thread inside the web server (start_from_settings)."""

    def test_disabled_setting_is_noop(self, fresh_db, monkeypatch):
        monkeypatch.setattr(tb, "load_token_quiet", lambda: None)
        assert tb.start_from_settings({"bot_enabled": False}) is False
        assert not tb.bot_running()

    def test_missing_token_does_not_start(self, fresh_db, monkeypatch):
        monkeypatch.setattr(tb, "load_token_quiet", lambda: None)
        assert tb.start_from_settings({"bot_enabled": True}) is False
        assert not tb.bot_running()

    def test_reset_clears_state(self, fresh_db, monkeypatch):
        monkeypatch.setattr(tb, "load_token_quiet", lambda: "123:TEST")
        fake_app = object()
        monkeypatch.setattr(tb, "build_app", lambda token: fake_app)

        class FakeThread:
            def __init__(self, target=None, args=None, name=None, daemon=None):
                self._target, self._args = target, args

            def start(self):
                pass

            def is_alive(self):
                return False

        monkeypatch.setattr(tb.threading, "Thread", FakeThread)
        assert tb.start_from_settings({"bot_enabled": True}) is True
        tb.reset()
        assert not tb.bot_running()

    def test_configure_stop_is_safe_when_not_running(self, fresh_db, monkeypatch):
        monkeypatch.setattr(tb, "load_token_quiet", lambda: None)
        tb.configure(False)  # must not raise
        assert not tb.bot_running()
