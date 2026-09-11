# English

# DomAlmaty — Rental housing search for Almaty

A web app for searching apartment rentals across 6 Almaty real-estate sites, with an interactive map, ML price evaluation, favorites, price monitoring, and PDF/TXT export.

## Features

### Architecture: parser and search engine

- **Parser** ("Analyzer" tab): a "Search" button, source and pages-per-site selection. Crawls the chosen sites and fills the listings cache (broad crawl — no filters at collection time, so filters don't drop data before it is cached).
- **Search engine** ("Search and map" tab): filters (rooms, price, district, floor, area, text) are applied instantly on the client side against the cache — a 150 ms debounce on any form change. Re-parsing is never triggered.
- **Separation of concerns**: data collection (parser) → filtering and display (search engine).

### Parsing (6 sites)

| Site | Details |
|---|---|
| **krisha.kz** | 9 pages, real-time coordinates from JSON, photo gallery (all photos) |
| **olx.kz** | 6 pages, coordinates with a bounding-box filter (Almaty only), gallery from the swiper |
| **kn.kz** | 6 pages, photo gallery from the detail page |
| **etagi.com** | 3 pages, SSR JSON-state parsing (~30 listings), gallery from the CDN |
| **telegram** | 4 Almaty channels (`t.me/s/<channel>`), Kazakh-Russian glossary, listing filter |

- **Parallel parsing** of all sites (ThreadPoolExecutor, 5 threads).
- **Anti-bot protection**: `curl_cffi` with browser TLS impersonation (Chrome), randomized User-Agent, delays between pages.
- **WAF bypass**: sticky sessions, TLS-profile rotation, fallback to `requests` when blocked.
- **Real coordinates**: krisha, olx (with an Almaty filter), kn, etagi extract lat/lon from the listing pages.
- **Point-in-polygon** district matching by coordinates (more reliable than text search).

### Search and map

- **Filters** (applied instantly to the cache, no new parsing): room count (0–4+), price, district, floor, area, text query, hide «без мебели» (unfurnished).
- **Result cache**: merging previous and new results (listings don't disappear between searches).
- **Price-change tracking**: `NEW` badges for new listings, price-change badges.
- **Metro distance** (`geo/metro.py`): cards show the nearest Almaty metro station and the walking-distance approximation (haversine by coordinates).
- **Sorting**: by price (default), by date (both directions), and by the ML price evaluation (best deals first / last).
- **Interactive map** (Leaflet + OpenStreetMap):
  - Boundaries of Almaty's 8 districts as polygons (toggleable "Districts" layer)
  - Markers with prices in 3 currencies (KZT / RUB / USD, compact format: `250k / 48k / $532`)
  - Popups with photo, price, address and link
  - **"Favorites only"** button — shows only saved listings on the map
  - Clicking a card focuses the map (flyTo + openPopup); clicking a price marker highlights and scrolls to its card

### On-disk image cache

- **Automatic caching** of all photos during parsing into `cache/photos/` (SHA1 of the URL).
- **WebP storage**: downloaded photos are converted to WebP (quality=85) before being written to disk — 30–50% less space than JPEG. When exporting to PDF, WebP is decoded through Pillow and converted to JPEG for embedding.
- **Background precache** in a daemon thread — does not block the return of search results.
- **Instant export**: repeated PDF/TXT exports take photos from the cache (0 network requests).
- **WebP format**: conversion to JPEG via Pillow (krisha serves WebP, the cache stores WebP).
- **Referer per domain**: correct headers for each CDN (krisha, olx, kn, etagi, telesco).
- **Retry + timeout**: 3 attempts, 15 s timeout, retry on 429/503.
- **LRU eviction**: automatic cleanup of old files when the limit is exceeded.
- **Configurable limit**: in the UI (50–10000 MB, default 500 MB).
- **"Clear cache" button** in the settings.

### Favorites and monitoring

- **Save listings** to favorites (SQLite).
- **Ratings** (1–5 stars) and **comments**.
- **Price history**: charts of price changes over time (Chart.js).
- **Price check**: re-parse a favorite's URL to refresh its price (and detect "archived / not actual" status).
- **Favorites export** to TXT, vertical PDF, horizontal PDF.

### Prices in 3 currencies

- **Exchange rates** KZT → RUB / USD / EUR (auto-refresh every 10 minutes, disk cache).
- **Prices on cards and markers** in all three currencies.
- **Price per m²** on the cards.

### Features, tags and price evaluation (ML)

- **Feature extraction** (`features.py`): ~60 tags parsed from title/description (RU/KZ patterns) — renovation grade, furniture level, appliances, bathroom type, building type, year built, ceiling height, parking/security/playground, view, near metro/school/park, "urgent", "no commission", "owner" and more; plus derived fields (price per m² / per room, floor ratio, first/last floor, district by coordinates, photo count, description stats).
- **Combined CSV** (`features_csv.py`): one flat `data/features.csv` from the results cache (`data/last_results.json`) and/or favorites (SQLite). Flags: `--favorites`, `--only-favorites`, `--with-price-only`, `--min-price`, `--out`.
- **Price-quality model** (`ml/train_price_model.py`): by default **HistGradientBoosting** (winner of the OOF benchmark in `ml/experiments.py` — beats the PyTorch MLP on this data volume; MLP kept via `--model mlp`). Label = price (or price/m²) in the bottom 35% of its peer group (quantile; `--labels median` for the old rule). Price-derived features are excluded to avoid label leakage. Saves `ml/price_model.pkl` + `ml/price_meta.json` with an F1-optimal decision threshold.
- **Inference** (`ml/predict_price.py`): scores a CSV (or a single JSON listing) and prints the top deals with verdicts «хорошая цена / спорно / дорого».
- **Benchmark** (`ml/experiments.py`): stratified 5-fold OOF comparison of label schemes / models / features — rerun after changing the pipeline.
- **Built into the UI**: search cards get a colored badge (green «хорошая цена» / yellow «спорно» / red «дорого» with the probability); **Settings → "Оценка цены (нейросеть)"** has an **«Обучить модель»** button that rebuilds `data/features.csv` from the results cache + favorites, retrains in a background thread and reports F1/AUC (API: `POST /api/ml/train`, `GET /api/ml/status`).
- Requires `scikit-learn` (default) and optionally `torch` (for `--model mlp`): `pip install scikit-learn torch`.

### Interface

- **4 tabs**:
  - **Search and map** — the search engine: filters on the left narrow the cached results in real time (cards + markers + district colors), without re-parsing
  - **Favorites and monitoring** — saved listings, ratings, comments, price charts
  - **Analyzer** — run parsing ("Search" button, source selection, pages per site) + detailed per-parser statistics
  - **Settings** — defaults, exchange rates, image cache, theme, data management
- **6 color themes**: Midnight, Carbon, Forest (dark) + Daylight, Sand, Rose (light).
- **Default search parameters** (stored on the server).
- **Pages per site**: per-parser max_pages setting (1–999).
- **Parser analyzer**: status, time, result count, errors for each parser + how many listings were hidden by filters.
- **Results export** to TXT and PDF (portrait + landscape) with all photos.
- **Photo carousel** on the listing cards.
- **Data management**: clear favorites, reset the database, clear the results cache, clear the image cache.

## Installation

### Requirements

- Python 3.11+
- Windows / macOS / Linux

### Install dependencies

```bash
pip install -r requirements.txt
```

Dependencies:

- `flask` — web server
- `requests` — HTTP requests
- `curl_cffi` — browser TLS impersonation (WAF bypass)
- `beautifulsoup4` + `lxml` — HTML parsing
- `fpdf2` — PDF generation
- `Pillow` — image processing, WebP conversion for the cache
- `pytest` — tests

### Run

```bash
python app.py
```

Or via a batch file (Windows):

```cmd
run.bat
```

Open http://localhost:5000

## Tests

```bash
python -m pytest tests/ -v
```

**800 tests**: parsers (6 sites), models, utilities, factory, export, API, districts, exchange rates, favorites, settings, photo enrichment, coordinates, feature extraction, ML routes.

```bash
# Quick check
python -m pytest -q

# Detailed output
python -m pytest tests/test_photo_enrichment.py -v --tb=short
```

## Project structure

```
kaz_nedviga/
├── app.py                        Flask application composition root, startup side effects
├── features.py                   Feature/tag extraction from listing title+description
├── features_csv.py               Combined data/features.csv builder (cache + favorites)
├── export_utils.py               Export TXT/PDF, on-disk photo cache
├── rates.py                      Exchange rates (KZT -> RUB/USD/EUR)
├── db.py                         SQLite layer (favorites, price history)
├── scheduler.py                  Background parsing scheduler
│
├── webapp/
│   ├── core.py                   Shared state/helpers (single monkeypatch target)
│   └── routes/                   Blueprints: search, settings, ml, favorites, proxy, rates, scheduler
│
├── ml/
│   ├── train_price_model.py      Price-quality model training (HistGB default / MLP)
│   ├── predict_price.py          CLI inference over CSV / single JSON listing
│   ├── scorer.py                 Lazy model loading + batch scoring for the webapp
│   ├── experiments.py            5-fold OOF benchmark of labels/models/features
│   ├── price_model.pkl           Trained HistGB model artifact
│   └── price_meta.json           Feature stats, threshold, metrics
│
├── parsers/
│   ├── __init__.py
│   ├── base.py                   Base parser, anti-bot, photo enrichment + precache
│   ├── models.py                 SearchParams, Listing (with lat/lon)
│   ├── factory.py                Parser registry and factory (6)
│   ├── krisha.py                 krisha.kz (+ coordinates, + gallery)
│   ├── olx.py                    olx.kz (+ coordinates with bbox filter, + gallery)
│   ├── kn.py                     kn.kz (+ photo gallery)
│   ├── etagi.py                  etagi.com (+ gallery from CDN, SVG state)
│   └── telegram.py               Telegram (4 channels, KZ->RU glossary)
│
├── geo/
│   ├── districts.py              8 Almaty districts with polygons
│   └── buildings.py              Building/geo enrichment
│
├── data/                         (runtime, gitignored except __init__)
│   ├── last_results.json         Search results cache
│   ├── features.csv              Combined feature dataset for ML
│   ├── favorites.db              SQLite (favorites + price history)
│   └── settings.json             Theme, parameters, max_pages, photo_cache_mb
│
├── cache/
│   └── photos/                   On-disk image cache (SHA1 of URL)
│
├── templates/
│   └── index.html                 SPA: map, results, favorites, settings
│
├── static/
│   ├── js/app.js                 Frontend logic
│   └── css/app.css               Styles, themes
│
├── tests/
│   ├── fixtures/                  HTML fixtures for tests
│   ├── test_app.py                Flask API
│   ├── test_parsers.py            Parser tests
│   ├── test_parsers_extended.py
│   ├── test_parser_engine.py      Engine: paging, partial, sticky sessions
│   ├── test_parser_analyzer.py    Parser statistics
│   ├── test_photo_enrichment.py   Photo enrichment, OLX/etagi coordinates
│   ├── test_search_engine.py      Search params, district matching, point-in-polygon
│   ├── test_features.py           Feature/tag extraction + CSV builder
│   ├── test_ml_routes.py          /api/ml/train + /api/ml/status + scoring
│   ├── test_settings.py           Settings, photo_cache_mb
│   ├── test_export.py             Export TXT/PDF
│   ├── test_favorites_api.py      Favorites API
│   ├── test_telegram.py           Telegram parser (29 tests)
│   └── ...
│
├── requirements.txt
├── run.bat                        Run on Windows
└── README.md
```

## API

### Search and map

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Main page (SPA) |
| `/api/search` | POST | Search across sites (merged results + parser stats) |
| `/api/districts` | GET | Almaty's 8 districts with coordinates and polygons |
| `/api/ml/train` | POST | Retrain the price-quality model (background) |
| `/api/ml/status` | GET | Training status + model metrics |

### Export

| Endpoint | Method | Description |
|---|---|---|
| `/api/export/txt` | POST | Export results to TXT |
| `/api/export/pdf` | POST | Export to PDF (portrait/landscape, all photos) |

### Image cache

| Endpoint | Method | Description |
|---|---|---|
| `/api/photo-cache` | GET | Size and file count of the cache |
| `/api/photo-cache/clear` | POST | Clear the image cache |

### Favorites

| Endpoint | Method | Description |
|---|---|---|
| `/api/favorites` | GET | List of favorites with price history |
| `/api/favorites` | POST | Add to favorites |
| `/api/favorites/<key>` | DELETE | Remove from favorites |
| `/api/favorites/<key>/rate` | PUT | Rating (1–5 stars) |
| `/api/favorites/<key>/comment` | PUT | Comment |
| `/api/favorites/<key>/history` | GET | Price history + chart |
| `/api/favorites/check-prices` | POST | Price check for all favorites |
| `/api/favorites/clear` | POST | Clear favorites + cache |

### Settings

| Endpoint | Method | Description |
|---|---|---|
| `/api/settings` | GET | Current settings (theme, max_pages, photo_cache_mb, ...) |
| `/api/settings` | POST | Save settings |
| `/api/results/clear` | POST | Clear the results cache |
| `/api/database/reset` | POST | Delete the DB + cache |

### Currencies

| Endpoint | Method | Description |
|---|---|---|
| `/api/rates` | GET | Exchange rates (RUB, USD, EUR) |
| `/api/rates/refresh` | POST | Force-refresh the rates |

## Almaty districts

Almalinskiy — Zhetysuskiy — Auezovskiy — Medeuskiy — Turksibskiy — Nauryzbaiyskiy — Bostandykskiy — Alatau

Each district has:
- Center coordinates (lat, lon)
- Boundary polygon (for point-in-polygon matching)
- Description (streets, boundaries)

## Technologies

| Component | Technology |
|---|---|
| Backend | Flask, BeautifulSoup4, lxml, SQLite |
| Frontend | Leaflet.js, Chart.js, vanilla JS |
| Parsing | requests, curl_cffi (TLS impersonation), ThreadPoolExecutor |
| Anti-bot | randomized headers, sticky sessions, WAF fallback |
| Photo cache | on-disk SHA1, WebP storage, LRU eviction, Pillow (WebP→JPEG) |
| Export | fpdf2 (PDF with embedded photos), TXT |
| Testing | pytest (800 tests) |

## Architecture

```
                    Frontend (index.html)
        [ Search and map ] [ Favorites ] [ Analyzer ] [ Settings ]
        filters + map      + charts      + parser     + cache
                \             |             |            /
                 \            |             |           /
                      Flask API (app.py)
/api/search  /api/ml  /api/favorites  /api/settings
                  \            |             /
    [ parsers/ ]   [ export ]  [ db.py ]     [ rates.py ]
    6 parsers       PDF/TXT     SQLite        KZT -> RUB/
    krisha/olx/     + photo     favorites +   USD/EUR
    kn/etagi/       cache       price history
    telegram
```

<!-- ===== English translation ends; original Russian below ===== -->
# ДомАлматы — Поиск аренды жилья в Алматы

Веб-приложение для поиска аренды квартир по 6 сайтам недвижимости Алматы с интерактивной картой, ML-оценкой цены, избранным, мониторингом цен и экспортом в PDF/TXT.

## Возможности

### Архитектура: парсер и поисковый движок

- **Парсер** (вкладка «Анализатор»): кнопка «Поиск», выбор источников и страниц на сайт. Обходит выбранные сайты и наполняет кэш объявлений (broad crawl — без фильтров на сбор, чтобы фильтры не отсекли данные до кэширования)
- **Поисковый движок** (вкладка «Поиск и карта»): фильтры (комнаты, цена, район, этаж, площадь, текст) применяются к кэшу мгновенно на клиенте — 150 мс debounce на любое изменение формы. Повторный парсинг не запускается
- **Разделение ответственности**: сбор данных (парсер) ≠ фильтрация и отображение (поисковый движок)

### Парсинг (6 сайтов)

| Сайт | Особенности |
|---|---|
| **krisha.kz** | 9 страниц, real-time координаты из JSON, галерея фото (до всех) |
| **olx.kz** | 6 страниц, координаты с bounding-box фильтром (только Алматы), галерея из swiper |
| **kn.kz** | 6 страниц, галерея фото с детальной страницы |
| **etagi.com** | 3 страницы, SSR JSON-state парсинг (~30 объявлений), галерея из CDN |
| **telegram** | 4 канала Алматы (`t.me/s/<channel>`), казахско-русский глоссарий, фильтр объявлений |

- **Параллельный парсинг** всех сайтов (ThreadPoolExecutor, 5 потоков)
- **Антибот-защита**: cffi с browser TLS impersonation (Chrome), рандомизированные User-Agent, задержки между страницами
- **WAF-обход**: sticky sessions, ротация TLS-профилей, fallback на requests при блокировке
- **Реальные координаты**: krisha, olx (с фильтром по Алматы), kn, etagi извлекают lat/lon со страниц объявлений
- **Point-in-polygon** сопоставление районов по координатам (надёжнее текстового поиска)

### Поиск и карта

- **Фильтры** (применяются мгновенно к кэшу, без нового парсинга): количество комнат (0–4+), цена, район, этаж, площадь, текстовый запрос, убрать «без мебели»
- **Кэш результатов**: объединение предыдущих и новых результатов (квартиры не исчезают между поисками)
- **Отслеживание изменений цен**: бейджи `NEW` для новых объявлений, бейджи изменения цены
- **Расстояние до метро** (`geo/metro.py`): на карточках — ближайшая станция метро Алматы и расстояние до неё (гаверсинус по координатам)
- **Сортировка**: по цене (по умолчанию), по дате (в обе стороны) и по ML-оценке цены (сначала выгодные / сначала дорогие)
- **Интерактивная карта** (Leaflet + OpenStreetMap):
  - Границы 8 районов Алматы с полигонами (переключатель «Районы»)
  - Маркеры с ценами в 3 валютах (KZT · RUB · USD, компактный формат: `250к ₸ · 48к ₽ · $532`)
  - Попапы с фото, ценой, адресом и ссылкой
  - Кнопка **«Только избранное»** — показывает на карте только сохранённые объявления
  - Клик по карточке → фокус на карте (flyTo + openPopup)

### Кэш картинок на диске

- **Автоматическое кэширование** всех фото при парсинге в `cache/photos/` (SHA1 от URL)
- **Хранение в WebP**: скачанные фото конвертируются в WebP (quality=85) перед записью на диск — экономия 30-50% места по сравнению с JPEG. При экспорте в PDF WebP декодируется через Pillow и конвертируется в JPEG для встраивания
- **Фоновый precache** в daemon-потоке — не блокирует возврат результатов поиска
- **Мгновенный экспорт**: повторные export PDF/TXT берут фото из кэша (0 сетевых запросов)
- **Формат WebP**: конвертация в JPEG через Pillow (krisha отдаёт webp, кэш хранит webp)
- **Referer по домену**: корректные заголовки для каждого CDN (krisha, olx, kn, etagi, telesco)
- **Retry + timeout**: 3 попытки, 15s timeout, retry на 429/503
- **LRU eviction**: автоочистка старых файлов при превышении лимита
- **Настройка лимита**: вынесен в UI (50–10000 МБ, по умолчанию 500 МБ)
- **Кнопка «Очистить кэш»** в настройках

### Избранное и мониторинг

- **Сохранение объявлений** в избранное (SQLite)
- **Оценки** (1–5 звёзд) и **комментарии**
- **История цен**: графики изменения цены по времени (Chart.js)
- **Проверка цен**: повторный парсинг URL избранного для актуализации цены
- **Экспорт избранного** в TXT, PDF вертикальный, PDF горизонтальный

### Цены в 3 валютах

- **Курсы валют** KZT → RUB / USD / EUR (автообновление каждые 10 минут, кэш на диске)
- **Цены на карточках и маркерах** во всех трёх валютах
- **Цена за м²** на карточках

### Оценка цены (ML)

- **Извлечение признаков** (`features.py`): ~60 тэгов из заголовка/описания (рус/каз паттерны) — ремонт, мебель, техника, санузел, тип дома, год постройки, потолки, парковка/охрана/площадка, вид, «рядом метро/школа/парк», «срочно», «без комиссии», «от собственника» и др.; плюс вычисляемые поля (цена за м²/комнату, floor_ratio, первый/последний этаж, район по координатам, кол-во фото, статистика описания).
- **Общий CSV** (`features_csv.py`): плоский `data/features.csv` из кэша результатов (`data/last_results.json`) и/или избранного (SQLite). Флаги: `--favorites`, `--only-favorites`, `--with-price-only`, `--min-price`, `--out`.
- **Модель** (`ml/train_price_model.py`): по умолчанию **HistGradientBoosting** (победитель OOF-бенчмарка `ml/experiments.py` — стабильнее MLP на этом объёме данных; MLP доступен через `--model mlp`). Метка = цена (или цена/м²) в нижних 35% своей группы (quantile; `--labels median` — старое правило). Ценовые признаки исключены из модели во избежание утечки метки. Артефакты: `ml/price_model.pkl` + `ml/price_meta.json` с F1-оптимальным порогом.
- **Инференс** (`ml/predict_price.py`): оценка CSV или одного JSON-объявления с вердиктами «хорошая цена / спорно / дорого».
- **Бенчмарк** (`ml/experiments.py`): стратифицированная 5-fold OOF сверка меток/моделей/признаков — запускать при изменении пайплайна.
- **Встроено в интерфейс**: на карточках цветной бейдж (зелёный «хорошая цена» / жёлтый «спорно» / красный «дорого» с вероятностью); **Настройки → «Оценка цены (нейросеть)»** — кнопка **«Обучить модель»**: пересобирает `data/features.csv` из кэша + избранного, обучает в фоновом потоке и показывает F1/AUC (API: `POST /api/ml/train`, `GET /api/ml/status`).
- Зависимости: `scikit-learn` (основная модель) и опционально `torch` (для `--model mlp`): `pip install scikit-learn torch`.

### Интерфейс

- **4 вкладки**:
  - **Поиск и карта** — поисковый движок: фильтры слева сужают кэшированные результаты в реальном времени (карточки + маркеры + цвета районов), без повторного парсинга
  - **Избранное и мониторинг** — сохранённые, оценки, комментарии, графики цен
  - **Анализатор** — запуск парсинга (кнопка «Поиск», выбор источников, страниц на сайт) + детальная статистика по каждому парсеру + сколько объявлений скрыто фильтрами
  - **Настройки** — параметры по умолчанию, курс валют, кэш картинок, тема, управление данными
- **6 цветовых тем**: Полночь, Карбон, Лес (тёмные) + Дневной, Песок, Роза (светлые)
- **Параметры поиска по умолчанию** (сохраняются на сервере)
- **Страницы по сайтам**: per-parser настройка max_pages (1–999)
- **Анализатор парсеров**: статус, время, количество результатов, ошибки по каждому парсеру, число объявлений, скрытых фильтрами
- **Выгрузка результатов** в TXT и PDF (портретный + ландшафтный) с всеми фото
- **Карусель фотографий** на карточках объявлений
- **Управление данными**: очистка избранного, сброс базы данных, очистка кэша результатов, очистка кэша картинок

## Установка

### Требования

- Python 3.11+
- Windows / macOS / Linux

### Установка зависимостей

```bash
pip install -r requirements.txt
```

Зависимости:
- `flask` — веб-сервер
- `requests` — HTTP-запросы
- `curl_cffi` — browser TLS impersonation (обход WAF)
- `beautifulsoup4` + `lxml` — парсинг HTML
- `fpdf2` — генерация PDF
- `pytest` — тесты

- `Pillow` — обработка изображений, конвертация в WebP для кэша

### Запуск

```bash
python app.py
```

Или через batch-файл (Windows):

```cmd
run.bat
```

Открыть http://localhost:5000

## Тесты

```bash
python -m pytest tests/ -v
```

**800 тестов**: парсеры (6 сайтов), модели, утилиты, фабрика, экспорт, API, районы, курсы валют, избранное, настройки, фото-обогащение, координаты, извлечение признаков, ML-маршруты.

```bash
# Быстрая проверка
python -m pytest -q

# Подробный вывод
python -m pytest tests/test_photo_enrichment.py -v --tb=short
```

## Структура проекта

```
kaz_nedviga/
├── app.py                        # Flask-приложение (composition root), startup
├── features.py                   # Извлечение признаков/тэгов из объявления
├── features_csv.py               # Сборка общего data/features.csv (кэш + избранное)
├── export_utils.py               # Экспорт TXT/PDF, кэш фото на диске
├── rates.py                      # Курсы валют (KZT → RUB/USD/EUR)
├── db.py                         # SQLite слой (избранное, история цен)
├── scheduler.py                  # Фоновый планировщик парсинга
│
├── webapp/
│   ├── core.py                   # Общее состояние/хелперы (цель для monkeypatch)
│   └── routes/                   # Блюпринты: search, settings, ml, favorites, proxy, rates, scheduler
│
├── ml/
│   ├── train_price_model.py      # Обучение модели оценки цены (HistGB / MLP)
│   ├── predict_price.py          # CLI-инференс по CSV / одному JSON-объявлению
│   ├── scorer.py                 # Ленивая загрузка модели + батч-скоринг для веб-приложения
│   ├── experiments.py            # 5-fold OOF бенчмарк меток/моделей/признаков
│   ├── price_model.pkl           # Обученная HistGB-модель
│   └── price_meta.json           # Статистики признаков, порог, метрики
│
├── parsers/
│   ├── __init__.py
│   ├── base.py                   # Базовый парсер, антибот, photo enrichment + precache
│   ├── models.py                 # SearchParams, Listing (с lat/lon)
│   ├── factory.py                # Реестр и фабрика парсеров (6)
│   ├── krisha.py                 # krisha.kz (+ координаты, +галерея)
│   ├── olx.py                    # olx.kz (+ координаты с bbox фильтром, +галерея)
│   ├── kn.py                     # kn.kz (+галерея фото)
│   ├── etagi.py                  # etagi.com (+галерея из CDN, SVG state)
│   └── telegram.py               # Telegram (4 канала, KZ→RU глоссарий)
│
├── geo/
│   ├── districts.py              # 8 районов Алматы с полигонами
│   └── buildings.py              # Обогащение по зданиям/гео
│
├── data/                         # рантайм-данные (в git не хранятся, кроме __init__)
│   ├── last_results.json         # Кэш результатов поиска
│   ├── features.csv              # Общий датасет признаков для ML
│   ├── favorites.db              # SQLite (избранное + история цен)
│   └── settings.json             # Тема, параметры, max_pages, photo_cache_mb
│
├── cache/
│   └── photos/                   # Кэш картинок на диске (SHA1 от URL)
│
├── templates/
│   └── index.html                 # SPA: карта, результаты, избранное, настройки
│
├── static/
│   ├── js/app.js                 # Логика фронтенда
│   └── css/app.css               # Стили, темы
│
├── tests/
│   ├── fixtures/                  # HTML-фикстуры для тестов
│   ├── test_app.py                # Flask API
│   ├── test_parsers.py            # Тесты парсеров
│   ├── test_parsers_extended.py
│   ├── test_parser_engine.py      # Engine: paging, partial, sticky sessions
│   ├── test_parser_analyzer.py    # Статистика парсеров
│   ├── test_photo_enrichment.py   # Обогащение фото, координаты OLX/etagi
│   ├── test_search_engine.py      # Парметры поиска, районы, point-in-polygon
│   ├── test_features.py           # Извлечение признаков + CSV
│   ├── test_ml_routes.py          # /api/ml/train + /api/ml/status + скоринг
│   ├── test_settings.py           # Настройки, photo_cache_mb
│   ├── test_export.py             # Экспорт TXT/PDF
│   ├── test_favorites_api.py      # API избранного
│   ├── test_telegram.py           # Telegram parser (29 тестов)
│   └── ...
│
├── requirements.txt
├── run.bat                        # Запуск на Windows
└── README.md
```

## API

### Поиск и карта

| Endpoint | Метод | Описание |
|---|---|---|
| `/` | GET | Главная страница (SPA) |
| `/api/search` | POST | Поиск по сайтам (merged-результаты + статистика парсеров) |
| `/api/districts` | GET | 8 районов Алматы с координатами и полигонами |
| `/api/ml/train` | POST | Переобучить модель оценки цены (фоновый поток) |
| `/api/ml/status` | GET | Статус обучения + метрики модели |

### Экспорт

| Endpoint | Метод | Описание |
|---|---|---|
| `/api/export/txt` | POST | Экспорт результатов в TXT |
| `/api/export/pdf` | POST | Экспорт в PDF (portrait/landscape, все фото) |

### Кэш картинок

| Endpoint | Метод | Описание |
|---|---|---|
| `/api/photo-cache` | GET | Размер и количество файлов в кэше |
| `/api/photo-cache/clear` | POST | Очистить кэш картинок |

### Избранное

| Endpoint | Метод | Описание |
|---|---|---|
| `/api/favorites` | GET | Список избранного с историей цен |
| `/api/favorites` | POST | Добавить в избранное |
| `/api/favorites/<key>` | DELETE | Удалить из избранного |
| `/api/favorites/<key>/rate` | PUT | Оценка (1–5 звёзд) |
| `/api/favorites/<key>/comment` | PUT | Комментарий |
| `/api/favorites/<key>/history` | GET | История цен + график |
| `/api/favorites/check-prices` | POST | Проверка цен для всех избранного |
| `/api/favorites/clear` | POST | Очистка избранного + кэша |

### Настройки

| Endpoint | Метод | Описание |
|---|---|---|
| `/api/settings` | GET | Текущие настройки (тема, max_pages, photo_cache_mb, ...) |
| `/api/settings` | POST | Сохранить настройки |
| `/api/results/clear` | POST | Очистка кэша результатов |
| `/api/database/reset` | POST | Удаление БД + кэша |

### Валюты

| Endpoint | Метод | Описание |
|---|---|---|
| `/api/rates` | GET | Курсы валют (RUB, USD, EUR) |
| `/api/rates/refresh` | POST | Принудительное обновление курсов |

## Районы Алматы

Алмалинский · Жетысуский · Ауэзовский · Медеуский · Турксибский · Наурызбайский · Бостандыкский · Алатауский

Каждый район имеет:
- Координаты центра (lat, lon)
- Полигон границ (для point-in-polygon сопоставления)
- Описание (улицы, границы)

## Технологии

| Компонент | Технология |
|---|---|
| Backend | Flask, BeautifulSoup4, lxml, SQLite |
| Frontend | Leaflet.js, Chart.js, ванильный JS |
| Парсинг | requests, curl_cffi (TLS impersonation), ThreadPoolExecutor |
| Антибот | рандомизированные заголовки, sticky sessions, WAF fallback |
| Кэш фото | on-disk SHA1, хранение в WebP, LRU eviction, Pillow (WebP→JPEG) |
| Экспорт | fpdf2 (PDF с встроенными фото), TXT |
| Тестирование | pytest (800 тестов) |

## Архитектура

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (index.html)                 │
│  ┌─────────┐  ┌──────────┐  ┌───────────┐  ┌─────────┐  │
│  │ Поиск    │  │ Избранное│  │ Анализатор │  │Настройки│  │
│  │ фильтры  │  │ + графики│  │ + парсер   │  │  + кэш  │  │
│  │ + карта  │  │          │  │ (кнопка)   │  │         │  │
│  └────┬─────┘  └────┬─────┘  └─────┬─────┘  └────┬────┘  │
│       │             │              │              │       │
└───────┼─────────────┼──────────────┼──────────────┼──────┘
        │             │              │              │
┌───────┴─────────────┴──────────────┴──────────────┴──────┐
│                    Flask API (app.py)                      │
│  /api/search  /api/ml  /api/favorites  /api/settings │
└───────┬─────────────┬──────────────┬──────────────┬──────┘
        │             │              │              │
┌───────┴─────┐ ┌─────┴──────┐ ┌─────┴────┐ ┌──────┴──────┐
│   parsers/   │ │ export_utils│ │  db.py   │ │  rates.py  │
│  6 парсеров  │ │ PDF/TXT +   │ │  SQLite  │ │  KZT→RUB/  │
│  krisha/olx/ │ │ photo cache │ │ favorites│ │  USD/EUR   │
│  kn/etagi/   │ │             │ │ history  │ │            │
│  telegram    │ │             │ │          │ │            │
└──────────────┘ └─────────────┘ └──────────┘ └────────────┘
```
