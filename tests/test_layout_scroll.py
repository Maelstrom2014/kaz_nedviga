"""Layout/scroll regression tests («при скроллинге уезжает карта и меню»).

История: раскладка дважды строилась на position:sticky (карта, верхнее
меню) — и оба раза в реальном браузере липкость отваливалась (sticky
ломается, если предок становится scroll-контейнером). Сейчас используется
«app-shell» раскладка: страница целиком не скроллится вообще
(body { overflow: hidden }), скроллятся только внутренние панели
(фильтры, список карточек, тела вкладок) — карта и меню прибиты
конструкцией, а не sticky.

Часть 1 — статические проверки CSS/DOM через Flask test client.
Часть 2 — живой браузер (Playwright Chromium): реально скроллим список
карточек и панель фильтров, проверяем, что карта и меню не сдвинулись,
а window вообще не скроллится. Пропускается, если Playwright или
браузер не установлены.
"""
import re
import tempfile
from pathlib import Path

import pytest

from app import app


# --------------------------------------------------------------- helpers

@pytest.fixture
def client():
    """Изоляция сети: /api/settings дергает proxy-pool (сетевой fetch) и
    пишет настройки — в тестах ни то, ни другое не нужно."""
    import tempfile

    import webapp.core as core

    class _FakeProxyPool:
        def configure(self, cfg=None):
            pass

    app.config["TESTING"] = True
    old_pool, old_path = core.get_proxy_pool, core.SETTINGS_PATH
    core.get_proxy_pool = lambda: _FakeProxyPool()
    core.SETTINGS_PATH = Path(tempfile.mkdtemp()) / "settings.json"
    with app.test_client() as c:
        yield c
    core.get_proxy_pool = old_pool
    core.SETTINGS_PATH = old_path


def _get_index(client) -> str:
    resp = client.get("/")
    assert resp.status_code == 200
    return resp.data.decode("utf-8")


def _get_css(client) -> str:
    resp = client.get("/static/css/app.css")
    assert resp.status_code == 200
    return resp.data.decode("utf-8")


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _css_block(css: str, selector: str) -> str:
    """Тело первого CSS-правила, чей селектор занимает начало строки."""
    m = re.search(
        r"(?m)^[ \t]*" + re.escape(selector) + r"[ \t]*\{", css
    )
    assert m, f"selector {selector!r} not found in css"
    open_idx = css.find("{", m.start())
    depth = 0
    for i in range(open_idx, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[open_idx + 1:i]
    raise AssertionError(f"unbalanced braces after {selector!r}")


# ------------------------------------------------- статические проверки

def test_no_sticky_anywhere(client):
    """Sticky больше не используется нигде: он и был причиной бага."""
    css = _strip_comments(_get_css(client))
    assert "position: sticky" not in css
    assert "position:sticky" not in css


def test_app_shell_locks_page_scroll(client):
    """Страница целиком не скроллится: html/body фиксированной высоты,
    у body overflow: hidden. Скролл живут только внутри панелей."""
    css = _strip_comments(_get_css(client))
    assert "height: 100%" in _css_block(css, "html, body")
    assert "overflow: hidden" in _css_block(css, "body")
    assert "height: 100%" in _css_block(css, ".container")


def test_tab_bar_is_structurally_pinned(client):
    """Верхнее меню прибито тем, что .main-content не скроллится."""
    css = _strip_comments(_get_css(client))
    bar = _css_block(css, ".tab-switcher")
    assert "position: sticky" not in bar
    assert "flex: none" in bar
    main = _css_block(css, ".main-content")
    assert "overflow: hidden" in main


def test_map_column_is_fixed_full_height(client):
    css = _strip_comments(_get_css(client))
    sec = _css_block(css, "#searchTab.map-right .map-section")
    assert "position: sticky" not in sec
    assert "height: 100%" in sec
    col = _css_block(css, "#searchTab.map-right .map-col")
    assert "height: 100%" in col


def test_results_grid_is_the_only_search_scroller(client):
    """В поиске скроллится только список карточек, шапка с сортировкой
    остаётся на месте."""
    css = _strip_comments(_get_css(client))
    grid = _css_block(css, "#searchTab.map-right .results-grid")
    assert "overflow-y: auto" in grid
    assert "flex: 1" in grid
    header = _css_block(css, "#searchTab.map-right .results-header")
    assert "flex: none" in header


def test_results_cards_columns_configurable(client):
    """Колонки/ширина карточек задаётся JS из настроек; в CSS — дефолт."""
    html = _get_index(client)
    assert "resultsColumnsInput" in html
    assert "cardWidthScaleInput" in html
    assert "applyCardLayout" in client.get("/static/js/app.js").data.decode()
    css = _strip_comments(_get_css(client))
    grid = _css_block(css, "#searchTab.map-right .results-grid")
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in grid


def test_results_rows_sized_by_content(client):
    """Регрессия «карточки в одну строку»: auto-rows в definite-height
    сетке Chromium делит на равные доли и сжимает карточки — строки
    должны явно размеряться по контенту карточки."""
    css = _strip_comments(_get_css(client))
    grid = _css_block(css, "#searchTab.map-right .results-grid")
    assert "grid-auto-rows: max-content" in grid


def test_non_search_tabs_scroll_internally(client):
    """Избранное/настройки/анализатор скроллятся внутри вкладки,
    меню не уезжает."""
    css = _strip_comments(_get_css(client))
    tab = _css_block(css, ".tab-content.active")
    assert "overflow-y: auto" in tab
    assert "flex: 1" in tab


def test_css_cache_buster_bumped(client):
    """CSS менялся — ссылка должна содержать свежий ?v=, иначе браузер
    покажет старую (сломанную) раскладку из кэша."""
    html = _get_index(client)
    m = re.search(r"app\.css\?v=(\d+)", html)
    assert m, "app.css must be linked with a ?v= cache-buster"
    assert int(m.group(1)) >= 4


def test_search_tab_dom_structure(client):
    html = _get_index(client)
    assert 'id="searchTab"' in html
    assert "map-right" in html
    assert '<div class="map-col">' in html
    assert 'id="map"' in html
    assert 'class="tab-switcher"' in html


# --------------------------------- живой браузер (Playwright, subprocess)

def test_ui_scroll_guard():
    """Живой браузер: скроллим список карточек и фильтры, проверяем что
    карта и меню не сдвинулись, а window не скроллится вообще.

    Playwright sync API на Windows/Python 3.13 иногда падает с heap
    corruption (в т.ч. на teardown) и убивает pytest-процесс, поэтому
    браузерная сессия выполняется в отдельном процессе
    (tests/ui_scroll_check.py). Результат читается из stdout (метка
    'ALL UI CHECKS PASSED' печатается до закрытия браузера), exit code
    из-за краша драйвера не учитывается; при редком краше дочернего
    процесса — повторные попытки.
    """
    import subprocess
    import sys as _sys
    from pathlib import Path

    pytest.importorskip("playwright")
    script = Path(__file__).parent / "ui_scroll_check.py"

    out = err = ""
    for attempt in range(6):  # повторы только на краш, не на FAIL
        try:
            proc = subprocess.run(
                [_sys.executable, str(script)],
                capture_output=True, text=True, timeout=180,
                cwd=str(script.parents[1]),
            )
        except subprocess.TimeoutExpired:
            pytest.fail("UI scroll check timed out after 180s")
        out, err = proc.stdout, proc.stderr
        if "ALL UI CHECKS PASSED" in out:
            return
        if "FAIL " in out:
            pytest.fail(  # реальная ошибка проверки, повтор не поможет
                "UI scroll check failed:\n" + out[-3000:]
                + "\n--- stderr ---\n" + err[-1500:]
            )
        if "BROWSER UNAVAILABLE" in out:
            pytest.skip("playwright chromium browser not installed")

    pytest.fail(
        f"UI scroll check crashed (6 attempts):\n" + out[-3000:]
        + "\n--- stderr ---\n" + err[-1500:]
    )
