"""Tests for telegram bot search flow and the bug fixes:
- HTML escaping of site-sourced card text (parse_mode=HTML safety)
- "4+" rooms filter includes 5+ room listings
- price min>max normalization
- listings without price sort last
- FIFO subscription queue keeps position on re-request
"""
import pytest

import db
import telegram_bot as tb
from parsers.models import Listing


class FakeParser:
    """Stands in for a real parser: applies the same filters as BaseParser.run."""

    def __init__(self, name: str, listings: list[Listing]):
        self.name = name
        self._listings = listings

    def run(self, params):
        return [l for l in self._listings
                if params.matches_price(l.price) and params.matches_rooms(l.rooms)]


def L(url: str, **kw) -> Listing:
    return Listing(url=url, source="test.kz", **kw)


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


class TestRunSearchFilters:
    def test_rooms_multi_select(self, monkeypatch):
        """Multi-select: [0, 1] matches studio AND 1-room listings."""
        listings = [
            L("u0", rooms=0, price=90000),
            L("u1", rooms=1, price=100000),
            L("u2", rooms=2, price=120000),
        ]
        monkeypatch.setattr(tb, "get_all_parsers",
                            lambda: [FakeParser("t", listings)])
        res = tb.run_search(rooms=[0, 1], price_min=None, price_max=None)
        assert [r.url for r in res] == ["u0", "u1"]

    def test_rooms_plus4_includes_5room(self, monkeypatch):
        """'4+' must not be an exact match: 5- and 6-room listings count."""
        listings = [
            L("u1", rooms=4, price=100000),
            L("u2", rooms=5, price=110000),
            L("u3", rooms=1, price=120000),
            L("u4", rooms=None, price=130000),
        ]
        monkeypatch.setattr(tb, "get_all_parsers",
                            lambda: [FakeParser("t", listings)])
        res = tb.run_search(rooms=[4], price_min=None, price_max=None)
        # u4 has no room count — kept (matches_rooms(None) is True)
        assert [r.url for r in res] == ["u1", "u2", "u4"]

    def test_rooms_mixed_exact_and_4plus(self, monkeypatch):
        """[2, 4] = exactly 2 rooms OR 4+ rooms."""
        listings = [
            L("a", rooms=2, price=100000),
            L("b", rooms=3, price=110000),
            L("c", rooms=4, price=120000),
            L("d", rooms=5, price=130000),
        ]
        monkeypatch.setattr(tb, "get_all_parsers",
                            lambda: [FakeParser("t", listings)])
        res = tb.run_search(rooms=[2, 4], price_min=None, price_max=None)
        assert [r.url for r in res] == ["a", "c", "d"]

    def test_studio_filter_matches_zero(self, monkeypatch):
        listings = [L("s", rooms=0, price=90000), L("o", rooms=1, price=95000)]
        monkeypatch.setattr(tb, "get_all_parsers",
                            lambda: [FakeParser("t", listings)])
        res = tb.run_search(rooms=[0], price_min=None, price_max=None)
        assert [r.url for r in res] == ["s"]

    def test_price_min_max_swapped(self, monkeypatch):
        listings = [
            L("cheap", rooms=1, price=50_000),
            L("mid", rooms=1, price=200_000),
            L("rich", rooms=1, price=900_000),
        ]
        monkeypatch.setattr(tb, "get_all_parsers",
                            lambda: [FakeParser("t", listings)])
        # user pressed min=500k before max=100k — must behave as 100k..500k
        res = tb.run_search(rooms=None, price_min=500_000, price_max=100_000)
        assert [r.url for r in res] == ["mid"]

    def test_dedup_by_url_and_source(self, monkeypatch):
        dup = L("same", rooms=2, price=100000)
        # two parsers return the same listing (same instance is enough —
        # dedup keys on (source, url))
        monkeypatch.setattr(
            tb, "get_all_parsers",
            lambda: [FakeParser("a", [dup]), FakeParser("b", [dup])])
        res = tb.run_search(rooms=None, price_min=None, price_max=None)
        assert len(res) == 1

    def test_free_limit_10(self, monkeypatch):
        listings = [L(f"u{i}", rooms=1, price=100000 + i) for i in range(25)]
        monkeypatch.setattr(tb, "get_all_parsers",
                            lambda: [FakeParser("t", listings)])
        assert len(tb.run_search(None, None, None)) == 10

    def test_none_price_sorts_last(self, monkeypatch):
        listings = [
            L("noprice", rooms=2, price=None),
            L("priced", rooms=2, price=300000),
            L("cheaper", rooms=2, price=150000),
        ]
        monkeypatch.setattr(tb, "get_all_parsers",
                            lambda: [FakeParser("t", listings)])
        res = tb.run_search(None, None, None)
        assert [r.url for r in res] == ["cheaper", "priced", "noprice"]

    def test_quality_score_ranks_first(self, monkeypatch):
        listings = [
            L("low", rooms=2, price=100000, quality_score=40),
            L("high", rooms=2, price=500000, quality_score=90),
        ]
        monkeypatch.setattr(tb, "get_all_parsers",
                            lambda: [FakeParser("t", listings)])
        res = tb.run_search(None, None, None)
        assert [r.url for r in res] == ["high", "low"]

    def test_fair_mix_across_sources(self, monkeypatch):
        """A single source must not fill the whole top-10.

        Regression: telegram/twogis set quality_score while other parsers
        don't — a global sort put only telegram listings into the free tier.
        Round-robin must mix sources instead.
        """
        cheap = [
            L(f"tg{i}", rooms=1, price=100_000 + i, quality_score=90)
            for i in range(10)
        ]
        for l in cheap:
            l.source = "telegram"
        dear = [
            L(f"kr{i}", rooms=1, price=300_000 + i, quality_score=None)
            for i in range(10)
        ]
        for l in dear:
            l.source = "krisha.kz"
        monkeypatch.setattr(
            tb, "get_all_parsers",
            lambda: [FakeParser("telegram", cheap), FakeParser("krisha", dear)])
        res = tb.run_search(None, None, None)
        sources = [r.source for r in res]
        # interleaved: krisha, telegram, krisha, ... (alphabetical order)
        assert sources[0] == "krisha.kz" and sources[1] == "telegram"
        assert set(sources) == {"telegram", "krisha.kz"}
        assert sources.count("telegram") == sources.count("krisha.kz")

    def test_parser_failure_does_not_kill_search(self, monkeypatch):
        class BrokenParser:
            name = "broken"

            def run(self, params):
                raise RuntimeError("WAF block")

        good = FakeParser("good", [L("ok", rooms=1, price=100000)])
        monkeypatch.setattr(tb, "get_all_parsers",
                            lambda: [BrokenParser(), good])
        res = tb.run_search(None, None, None)
        assert [r.url for r in res] == ["ok"]


class TestConversationState:
    def test_accessor_returns_dict(self):
        """Regression: `def _state` used to shadow the _STATE dict, so the
        first menu press crashed with
        'function' object has no attribute 'setdefault'."""
        st = tb._state(424242)
        assert st == {"rooms": [], "price_min": None, "price_max": None}
        st2 = tb._state(424242)
        assert st2 is st
        st2["rooms"] = [0, 2]
        assert tb._state(424242)["rooms"] == [0, 2]
        tb._STATE.pop(424242, None)


class TestSearchCache:
    """Shared TTL cache: repeated searches with the same filters do not
    re-parse the sites (any user, same process)."""

    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        tb._SEARCH_CACHE.clear()
        yield
        tb._SEARCH_CACHE.clear()

    def _install_counter(self, monkeypatch, listings):
        calls = {"n": 0}

        class CounterParser(FakeParser):
            def run(self, params):
                calls["n"] += 1
                return super().run(params)

        calls = {"n": 0}
        monkeypatch.setattr(tb, "get_all_parsers",
                            lambda: [CounterParser("t", listings)])
        return calls

    def test_repeated_search_hits_cache(self, monkeypatch):
        listings = [L("a", rooms=1, price=150000)]
        calls = self._install_counter(monkeypatch, listings)
        r1 = tb.run_search([1], 100000, 200000)
        r2 = tb.run_search([1], 100000, 200000)
        assert calls["n"] == 1  # second search served from cache
        assert [x.url for x in r1] == ["a"]
        assert [x.url for x in r2] == ["a"]

    def test_cache_shared_between_users(self, monkeypatch):
        """Same filter key from a different caller = same cached list."""
        calls = self._install_counter(
            monkeypatch, [L("a", rooms=1, price=150000)])
        tb.run_search([1], 100000, 200000)
        tb.run_search([1], 100000, 200000)  # another "user", same filters
        assert calls["n"] == 1

    def test_different_filters_parse_separately(self, monkeypatch):
        calls = self._install_counter(
            monkeypatch, [L("a", rooms=1, price=150000)])
        tb.run_search([1], 100000, 200000)
        tb.run_search([2], 100000, 200000)
        assert calls["n"] == 2

    def test_ttl_expiry_reparses(self, monkeypatch):
        calls = self._install_counter(
            monkeypatch, [L("a", rooms=1, price=150000)])
        tb.run_search([1], None, None)
        key = tb._cache_key([1], None, None)
        ts, data = tb._SEARCH_CACHE[key]
        tb._SEARCH_CACHE[key] = (ts - tb._SEARCH_CACHE_TTL - 1, data)
        tb.run_search([1], None, None)
        assert calls["n"] == 2

    def test_cache_stores_full_list(self, monkeypatch):
        """First call capped at 10 must not shrink the cached pool."""
        listings = [L(f"u{i}", rooms=1, price=100000 + i) for i in range(25)]
        calls = self._install_counter(monkeypatch, listings)
        assert len(tb.run_search(None, None, None)) == 10
        assert len(tb.run_search(None, None, None, max_results=25)) == 25
        assert calls["n"] == 1

    def test_price_bounds_normalized_for_key(self, monkeypatch):
        """min>max and max<min are the same search (same cache entry)."""
        calls = self._install_counter(
            monkeypatch, [L("a", rooms=1, price=150000)])
        r1 = tb.run_search([1], 200000, 100000)
        r2 = tb.run_search([1], 100000, 200000)
        assert calls["n"] == 1
        assert [x.url for x in r1] == [x.url for x in r2]


class TestFilterMenu:
    def test_menu_text_lists_selections(self):
        st = {"rooms": [0, 2], "price_min": 100000, "price_max": None}
        text = tb.filters_menu_text(st)
        assert "Студия, 2-к" in text
        assert "от 100 000 тг" in text
        assert "до —" in text
        assert "Цена:" in text

    def test_menu_text_empty(self):
        text = tb.filters_menu_text(dict(tb._EMPTY_FILTERS))
        assert "Комнаты: любые" in text
        assert "Цена: любая" in text

    def test_menu_kb_checkmarks_and_callbacks(self):
        kb = tb.filters_menu_kb({"rooms": [1], "price_min": None,
                                 "price_max": 250000})
        data = [(b.text, b.callback_data)
                for row in kb.inline_keyboard for b in row]
        texts = {t for t, _ in data}
        assert "✅ 1-к" in texts
        assert "▫️ Студия" in texts
        assert "▫️ 4+" in texts
        assert "От: —" in texts
        assert any(t.startswith("До: 250 000 тг") and "₽" in t and "$" in t
                   for t in texts)
        cbs = {c for _, c in data}
        assert {"rooms:1", "pmin:menu", "pmax:menu",
                "filters:search", "filters:reset"} <= cbs

    def test_error_handler_registered(self):
        app = tb.build_app("123456:TEST-TOKEN")
        # PTB stores error handlers in app.error_handlers (not handlers[-1])
        assert len(app.error_handlers) == 1


class TestCardEscaping:
    def _listing(self, **kw):
        defaults = dict(
            title="2-к, <люкс>", price=250000, currency="тг", rooms=2,
            url="https://krisha.kz/a/show/1?utm=x&y=1", source="krisha.kz",
            address="ул. Толе би & Абая",
        )
        defaults.update(kw)
        return Listing(**defaults)

    def test_title_escaped(self):
        card = tb.format_card(self._listing())
        assert "&lt;люкс&gt;" in card
        assert "<люкс>" not in card
        # the caption's own bold markup is intact
        assert card.startswith("🏠 <b>")

    def test_address_escaped(self):
        card = tb.format_card(self._listing())
        assert "ул. Толе би &amp; Абая" in card

    def test_url_attr_escaped(self):
        card = tb.format_card(self._listing())
        assert 'href="https://krisha.kz/a/show/1?utm=x&amp;y=1"' in card

    def test_source_escaped(self):
        card = tb.format_card(self._listing(source="<script>x</script>"))
        assert "&lt;script&gt;" in card


class TestSubscriptionQueueFIFO:
    def test_position_kept_on_re_request(self, fresh_db, monkeypatch):
        times = iter(["2026-01-01 10:00:00", "2026-01-01 10:00:05",
                      "2026-01-01 10:00:10"])
        monkeypatch.setattr(db, "_now", lambda: next(times))
        assert db.add_subscription_request(222, username="b") == 1
        assert db.add_subscription_request(111, username="a") == 2
        # 222 re-requests: keeps #1 (was moved to the back before the fix)
        assert db.add_subscription_request(222, username="b2") == 1
        reqs = db.list_subscription_requests()
        assert len(reqs) == 2
        assert reqs[0]["username"] == "b2"  # username refreshed, place kept

    def test_same_second_tiebreak_by_id(self, fresh_db, monkeypatch):
        """Same-second requests: deterministic order by telegram id."""
        monkeypatch.setattr(db, "_now", lambda: "2026-01-01 10:00:00")
        db.add_subscription_request(222, username="b")
        db.add_subscription_request(111, username="a")
        assert db.add_subscription_request(222) == 2
        assert db.add_subscription_request(111) == 1


class TestDailyQuota:
    """10 searches/day per non-admin user (quota itself lives in db)."""

    def test_counts_rise_with_bump(self, fresh_db):
        db.register_bot_user(111)
        assert db.count_bot_searches_today(111) == 0
        for _ in range(3):
            db.bump_bot_user_searches(111)
        assert db.count_bot_searches_today(111) == 3

    def test_day_rollover_resets(self, fresh_db, monkeypatch):
        db.register_bot_user(111)
        for _ in range(3):
            db.bump_bot_user_searches(111)
        assert db.count_bot_searches_today(111) == 3
        # simulate the next day: _today() is the tested seam
        monkeypatch.setattr(db, "_today", lambda: "2027-01-01")
        assert db.count_bot_searches_today(111) == 0  # yesterday's counter
        db.bump_bot_user_searches(111)  # new day, counter restarts
        assert db.count_bot_searches_today(111) == 1

    def test_lifetime_total_kept(self, fresh_db):
        db.register_bot_user(111)
        for _ in range(12):
            db.bump_bot_user_searches(111)
        assert db.get_bot_user(111)["searches"] == 12
        assert db.count_bot_searches_today(111) == 12

    def test_unknown_user_counts_zero(self, fresh_db):
        assert db.count_bot_searches_today(999999) == 0
        db.bump_bot_user_searches(999999)  # no row -> silent no-op
        assert db.count_bot_searches_today(999999) == 0

    def test_migration_adds_quota_columns(self, fresh_db):
        """Old DBs (pre-quota columns) are upgraded in place."""
        cols = {r[1] for r in db.get_conn().execute(
            "PRAGMA table_info(bot_users)")}
        assert {"searches_today", "last_search_day"} <= cols


class TestSupportInbox:
    def test_add_and_list(self, fresh_db):
        mid = db.add_support_message(222, username="b", text="не приходит телефон")
        msgs = db.list_support_messages()
        assert len(msgs) == 1
        assert msgs[0]["id"] == mid
        assert msgs[0]["text"] == "не приходит телефон"
        assert msgs[0]["answered"] == 0

    def test_mark_answered(self, fresh_db):
        mid = db.add_support_message(222, username="b", text="вопрос")
        assert db.mark_support_answered(mid)
        assert db.list_support_messages(only_open=True) == []
        assert len(db.list_support_messages()) == 1

    def test_mark_answered_unknown_id(self, fresh_db):
        assert db.mark_support_answered(424242) is False

    def test_list_limit(self, fresh_db):
        for i in range(7):
            db.add_support_message(111 + i, username=f"u{i}", text=f"m{i}")
        assert len(db.list_support_messages(limit=5)) == 5

    def test_position_fifo_by_time(self, fresh_db, monkeypatch):
        counter = {"n": 0}

        def fake_now():
            counter["n"] += 1
            return f"2026-01-01 10:00:{counter['n']:02d}"

        monkeypatch.setattr(db, "_now", fake_now)
        db.add_subscription_request(111, username="a")
        db.add_subscription_request(222, username="b")
        positions = sorted(
            (r["telegram_id"], db.add_subscription_request(r["telegram_id"]))
            for r in db.list_subscription_requests())
        assert positions == [(111, 1), (222, 2)]
