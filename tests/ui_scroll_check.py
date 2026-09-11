"""Standalone UI scroll guard.

Запускается из tests/test_layout_scroll.py В ОТДЕЛЬНОМ процессе:
Playwright sync API на Windows + Python 3.13 иногда падает с heap
corruption (в т.ч. на teardown) и убивает весь pytest-процесс.
Изоляция в subprocess делает падение безвредным для прогона тестов,
а async API (без greenlet) заметно стабильнее sync.

Важно: 'ALL UI CHECKS PASSED' печатается ДО закрытия браузера, а выход
через os._exit — штатное завершение драйвера не может испортить результат.

Сама проверка: страница-«app-shell» не скроллится целиком; при скролле
списка карточек и панели фильтров карта и верхнее меню остаются на месте.
"""
import asyncio
import faulthandler
import os
import socket
import sys
import threading
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# app.py пропускает автостарт шедулера/бота при "pytest" in sys.modules —
# повторяем этот контракт для дочернего процесса без pytest.
sys.modules.setdefault("pytest", types.ModuleType("pytest"))

# Никаких реальных сетевых парсингов: фронт сам запускает обход при
# пустом кэше — это лишний сетевой шум внутри браузерного теста.
import webapp.core as _core  # noqa: E402

_core._run_all_parsers = lambda *a, **k: []

# POST /api/settings дергает scheduler.configure и telegram_bot.configure —
# заглушаем: реальный бот и шедулер в тесте недопустимы. Настройки пишем
# во временную папку, чтобы не трогать настоящие data/settings.json.
import tempfile  # noqa: E402

import scheduler as _scheduler  # noqa: E402
import telegram_bot as _telegram_bot  # noqa: E402

_scheduler.configure = lambda **kwargs: None
_telegram_bot.configure = lambda enabled: None
_core.SETTINGS_PATH = Path(tempfile.mkdtemp()) / "settings.json"
# /api/settings вызывает proxy-pool configure — тот по сети тянет списки
# прокси и вешает тестовый процесс. Заглушка без сети.
class _FakeProxyPool:
    def configure(self, cfg=None):
        pass

_core.get_proxy_pool = lambda: _FakeProxyPool()

from playwright.async_api import async_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from app import app  # noqa: E402


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _rect_top_js(selector: str) -> str:
    return (
        "(() => { const el = document.querySelector(" + repr(selector) + ");"
        " return el ? el.getBoundingClientRect().top : null; })()"
    )


_FILL_RESULTS = """(n) => {
  const grid = document.getElementById('resultsGrid');
  grid.innerHTML = '';
  for (let i = 0; i < n; i++) {
    const c = document.createElement('div');
    c.className = 'listing-card';
    c.style.height = '260px';
    c.textContent = 'card ' + i;
    grid.appendChild(c);
  }
}"""


async def _top(page, sel: str) -> float:
    return await page.evaluate(_rect_top_js(sel))


# --- проверки ------------------------------------------------------------

async def _check_window(page):
    await page.evaluate("window.scrollTo(0, 500)")
    await page.wait_for_timeout(100)
    assert await page.evaluate("window.scrollY") == 0, "window.scrollY != 0"
    assert await page.evaluate("window.scrollX") == 0, "window.scrollX != 0"


async def _check_results(page):
    """Скролл списка карточек: карта и меню не сдвигаются."""
    await page.evaluate(_FILL_RESULTS, 60)
    bar_top = await _top(page, ".tab-switcher")
    map_top = await _top(page, "#searchTab .map-section")
    assert 0 <= bar_top <= 40, f"menu not in top zone: {bar_top}"
    assert 0 < map_top < 150, f"map not visible: {map_top}"
    await page.evaluate(
        "() => { const g = document.getElementById('resultsGrid');"
        " g.scrollTop = g.scrollHeight; }"
    )
    await page.wait_for_timeout(150)
    st = await page.evaluate(
        "(() => document.getElementById('resultsGrid').scrollTop)()"
    )
    assert st > 0, f"results list did not scroll (scrollTop={st})"
    assert abs((await _top(page, ".tab-switcher")) - bar_top) <= 1, \
        "tab bar moved while scrolling results"
    assert abs((await _top(page, "#searchTab .map-section")) - map_top) <= 1, \
        "map moved while scrolling results"


async def _check_filters(page):
    """Скролл панели фильтров: карта и меню не сдвигаются."""
    await page.evaluate(
        """() => {
          const d = document.createElement('div');
          d.style.height = '2000px';
          document.querySelector('.search-panel').appendChild(d);
        }"""
    )
    bar_top = await _top(page, ".tab-switcher")
    map_top = await _top(page, "#searchTab .map-section")
    await page.evaluate(
        "() => { const p = document.querySelector('.search-panel');"
        " p.scrollTop = p.scrollHeight; }"
    )
    await page.wait_for_timeout(150)
    st = await page.evaluate(
        "(() => document.querySelector('.search-panel').scrollTop)()"
    )
    assert st > 0, f"filters panel did not scroll (scrollTop={st})"
    assert abs((await _top(page, ".tab-switcher")) - bar_top) <= 1, \
        "tab bar moved while scrolling filters"
    assert abs((await _top(page, "#searchTab .map-section")) - map_top) <= 1, \
        "map moved while scrolling filters"


async def _check_map_tall(page):
    # Проверка на вкладке поиска: на других вкладках карта скрыта.
    if not await page.evaluate(
            "document.getElementById('searchTab').classList.contains('active')"):
        await page.click("#tabSearch")
        await page.wait_for_timeout(300)
    h = await page.evaluate(
        "(() => document.getElementById('map').getBoundingClientRect().height)()"
    )
    assert h > 300, f"map too short: {h}px"


async def _check_tab_switch(page):
    """Переключение вкладок туда-обратно не ломает раскладку."""
    await page.click("#tabFav")
    await page.wait_for_selector("#favTab.active")
    await page.click("#tabSearch")
    await page.wait_for_selector("#searchTab.active")
    await page.wait_for_timeout(250)  # map.invalidateSize()
    top_bar = await _top(page, ".tab-switcher")
    top_map = await _top(page, "#searchTab .map-section")
    assert top_bar <= 40, f"menu moved after tab switch: {top_bar}"
    assert top_map < 150, f"map misplaced after tab switch: {top_map}"


_CHECK_CARDS_AUTO_HEIGHT = """() => {
  // Реалистичная карточка БЕЗ фиксированной высоты: фото-карусель (200px)
  // + тело с текстом. Регрессия: без grid-auto-rows:max-content строки
  // сетки делили высоту контейнера поровну и карточки сжимались
  // до «одной строки» (клипались overflow:hidden).
  const grid = document.getElementById('resultsGrid');
  grid.innerHTML = '';
  const card = (i) => `
    <div class="listing-card" data-idx="${i}">
      <div class="photo-carousel">
        <div class="photo-carousel-track">
          <img src="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='400' height='200'><rect width='400' height='200' fill='%23334155'/></svg>">
        </div>
      </div>
      <div class="listing-body">
        <div class="listing-title">2-комнатная квартира, ${50 + i} м², ${i + 3} эт.</div>
        <div class="listing-price">180 000 ₸</div>
        <div class="listing-meta"><span>2 комн.</span><span>52 м²</span><span>эт. 4/9</span></div>
        <span class="listing-source">krisha</span>
      </div>
    </div>`;
  for (let i = 0; i < 6; i++) grid.insertAdjacentHTML('beforeend', card(i));
}"""


async def _check_cards_full_height(page):
    """Карточки держат полную высоту (фото + тело), не сжимаются в строку."""
    await page.evaluate(_CHECK_CARDS_AUTO_HEIGHT)
    await page.wait_for_timeout(200)
    card_h = await page.evaluate(
        "(() => document.querySelector('.listing-card')"
        ".getBoundingClientRect().height)()"
    )
    # фото 200 + тело (заголовок/цена/мета) — карточка не может быть
    # ниже ~320px; сломанная раскладка давала 41px.
    assert card_h >= 320, f"card collapsed to {card_h}px (one-line regression)"
    scroll_h = await page.evaluate(
        "(() => document.getElementById('resultsGrid').scrollHeight)()"
    )
    client_h = await page.evaluate(
        "(() => document.getElementById('resultsGrid').clientHeight)()"
    )
    assert scroll_h > client_h, \
        f"grid must scroll internally (scrollH={scroll_h}, clientH={client_h})"


async def _check_card_layout_settings(page):
    """applyCardLayout меняет сетку: колонки 1/2/3 и ширина >100% (scrollX).

    Серверную часть (API/персист) покрывают API-тесты; здесь только
    CSS-механика, потому что /api/settings дергает proxy-pool и
    telegram-бота (реальный поллинг в тестовом процессе недопустим).
    """
    async def apply(cols, scale):
        # getComputedStyle форсирует пересчёт стилей — ожидание не нужно;
        # меньше round-trips к браузеру = меньше шансов на краш драйвера.
        return await page.evaluate(
            """([c, s]) => {
              window._cardLayout = {results_columns: c, card_width_scale: s};
              applyCardLayout();
              return getComputedStyle(document.getElementById('resultsGrid'))
                  .gridTemplateColumns;
            }""",
            [cols, scale],
        )

    cols2 = await apply(2, 100)
    assert len(cols2.split()) == 2, f"2 cols expected, got: {cols2}"
    cols1 = await apply(1, 100)
    assert len(cols1.split()) == 1, f"1 col expected, got: {cols1}"
    cols3 = await apply(3, 70)
    assert len(cols3.split()) == 3, f"3 cols expected, got: {cols3}"

    async def geometry():
        return await page.evaluate(
            """() => {
              const r = (el) => el.getBoundingClientRect();
              return {
                gridW: r(document.getElementById('resultsGrid')).width,
                mapW: r(document.querySelector('#searchTab .map-col')).width,
                panelW: r(document.querySelector('.search-panel')).width,
                gridScrollX: (() => { const g =
                    document.getElementById('resultsGrid');
                    return g.scrollWidth - g.clientWidth; })(),
              };
            }"""
        )

    # Ширина >100%: колонка результатов расширяется, карта и фильтры
    # уступают место. Горизонтального скролла быть не должно.
    base = await geometry()
    await apply(2, 130)
    g130 = await geometry()
    assert g130["gridW"] > base["gridW"] + 20, \
        f"results col must grow at 130% (was {base['gridW']}, now {g130['gridW']})"
    assert g130["mapW"] < base["mapW"] - 20, \
        f"map must shrink at 130% (was {base['mapW']}, now {g130['mapW']})"
    assert g130["panelW"] <= base["panelW"], \
        f"filters must not grow at 130% (was {base['panelW']}, now {g130['panelW']})"
    assert g130["gridScrollX"] <= 1, \
        f"no horizontal scroll allowed (overflow {g130['gridScrollX']}px)"
    # Ширина <100%: карта расширяется обратно.
    await apply(2, 70)
    g70 = await geometry()
    assert g70["mapW"] > g130["mapW"], \
        f"map must widen at 70% (was {g130['mapW']}, now {g70['mapW']})"
    await apply(2, 100)  # вернуть дефолт


async def _check_crawl_sources_autosave(page):
    """Выбор источников «Запуска парсинга» сохраняется автоматически и
    восстанавливается через applyCrawlSources (её же вызывает init).

    Работаем с DOM напрямую (элементы в DOM даже на скрытой вкладке):
    переключение вкладок тянет loadMlStatus/photo-cache и провоцирует
    краш драйвера. Видимость блока в настройках покрывают HTML-тесты.
    """
    async def checked_values():
        return await page.evaluate(
            """() => [...document.querySelectorAll(
                  '#sourcesGroup input[name="sources"]:checked')].map(c => c.value)"""
        )

    before = await checked_values()
    assert before, "all sources checked by default"

    # Снять один источник → автосохранение
    await page.evaluate(
        """() => {
          const cb = [...document.querySelectorAll(
              '#sourcesGroup input[name="sources"]')]
              .find(c => c.value === 'olx.kz');
          cb.checked = false;
          cb.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )
    await page.wait_for_timeout(400)
    saved = await page.evaluate(
        "fetch('/api/settings').then(r => r.json()).then(s => s.crawl_sources)"
    )
    # Сняли olx.kz → сохранён список «все кроме него».
    assert "olx.kz" not in saved, f"autosave failed: {saved}"
    assert "krisha.kz" in saved and len(saved) >= 5, \
        f"autosave lost other sources: {saved}"

    # Восстановление при загрузке страницы — та же applyCrawlSources,
    # что вызывается из fetch('/api/settings') в init.
    restored = await page.evaluate(
        """(saved) => {
          document.querySelectorAll('#sourcesGroup input[name="sources"]')
              .forEach(c => { c.checked = false; });
          applyCrawlSources(saved);
          return [...document.querySelectorAll(
              '#sourcesGroup input[name="sources"]:checked')].map(c => c.value);
        }""",
        saved,
    )
    assert "olx.kz" not in restored, f"restore failed: {restored}"
    assert "krisha.kz" in restored
    # вернуть все источники
    await page.evaluate(
        """() => {
          const cb = [...document.querySelectorAll(
              '#sourcesGroup input[name="sources"]')]
              .find(c => c.value === 'olx.kz');
          cb.checked = true;
          cb.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )
    await page.wait_for_timeout(300)


CHECKS = [
    ("crawl sources autosave", _check_crawl_sources_autosave),
    ("window never scrolls", _check_window),
    ("results scroll keeps map+menu", _check_results),
    ("filters scroll keeps map+menu", _check_filters),
    ("cards keep full height (no one-line)", _check_cards_full_height),
    ("card layout settings apply", _check_card_layout_settings),
    ("map tall enough", _check_map_tall),
    ("tab switch keeps shell", _check_tab_switch),
]


async def _run(base_url: str) -> None:
    failures = []
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception as exc:
            print(f"BROWSER UNAVAILABLE: {exc}", flush=True)
            os._exit(2)
        page = await browser.new_page(viewport={"width": 1700, "height": 900})
        # Тяжёлые внешние ресурсы (тайлы OSM, фото, шрифты) блокируем:
        # тесту нужна геометрия раскладки, а не сеть — медленная сеть
        # провоцирует краши драйвера. Скрипты CDN (leaflet/chart.js) не
        # трогаем — на них завязана инициализация app.js.
        await page.route(
            "**/*",
            lambda route: route.abort()
            if any(h in route.request.url for h in (
                "tile.openstreetmap.org", "unpkg.com/leaflet@1.9.4/images",
                "basemaps", "arcgis"))
            else route.continue_()
        )
        await page.goto(base_url + "/", wait_until="domcontentloaded")
        await page.wait_for_selector("#searchTab.active")
        await page.wait_for_timeout(600)

        from playwright.async_api import Error as PWError

        for name, fn in CHECKS:
            try:
                await fn(page)
                print(f"  ok  {name}", flush=True)
            except (AssertionError, PWError) as exc:
                failures.append(f"{name}: {exc!r}")
                print(f" FAIL {name}: {exc!r}", flush=True)

        # PASS печатается ДО teardown, а выход — через os._exit: штатное
        # завершение playwright-драйвера не должно влиять на результат.
        if not failures:
            print("ALL UI CHECKS PASSED", flush=True)
            os._exit(0)

    print("UI CHECKS FAILED:")
    for f in failures:
        print(" -", f)
    os._exit(1)


def main() -> None:
    # Краш-дампы драйвера в stderr упрощают разбор редких падений.
    faulthandler.enable()
    server = make_server("127.0.0.1", _free_port(), app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.port}"
    asyncio.run(_run(base_url))


if __name__ == "__main__":
    main()
