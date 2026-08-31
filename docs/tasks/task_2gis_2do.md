# 2GIS-парсер Алматы: принятые инструкции к внедрению

Адаптация `task_2gis_chatgpt_scrape.md` (исследование 2GIS от ChatGPT) под
реальную архитектуру проекта `kaz_nedviga`.

Это **инструкции (не код)** для постройки нового парсера 2GIS в проекте. Они
сформулированы так, чтобы исполнитель работал в уже принятых в проекте
паттернах и не отклонялся от них без отдельного решения.

---

## 0. Контекст и допущения

### 0.1. Что предлагает источник

Источник строит 2GIS-парсер вокруг **четырёх потоков**:

```
REALTY_LISTINGS   — сами квартиры (главный объект)
BUILDINGS         — здания/ЖК (недооценённый, очень полезный слой)
AGENCIES          — агентства и риелторы (discovery поставщиков)
INFRA            — транспорт/инфраструктура (scoring)
```

Рекомендованный pipeline источника (§27, §30): **JSON-API/XHR-first**, не HTML-
скрейпинг; structured fields → regex from description → normalize → dedup →
building join → geo-enrich → price history → БД.

### 0.2. Жёсткие ограничения (подтверждено пользователем + источник сам их диктует)

1. **Без OCR.** Источник §9, §28 (Level 5 = «Не использовать»), §44 — «OCR —
   не использовать». Картинки сохраняем как URL, не анализируем.
2. **Без LLM / без внешних AI-API.** Источник §28 (Level 6 = «Не использовать»),
   §44 — «LLM extraction — не использовать». Вся extraction — structured API
   fields + deterministic regex/rules.
3. **Без Selenium/браузерной автоматизации.** Источник §29, §30 — «API > HTML
   > browser automation; не начинать с Selenium». Проект и так не использует
   selenium (см. `requirements.txt`).
4. **HTTP-транспорт — через существующий `BaseParser.fetch`** (`parsers/base.py:423`)
   с anti-bot-стеком `curl_cffi`/`requests`, рандомными заголовками и
   SSL/403-ретраями. Новый парсер не пишет свой HTTP-слой.
5. **ToS/лицензия 2GIS** (источник §30, §44): массовое автоматизированное
   извлечение ограничено лицензионным соглашением 2GIS. Проект — личный
   поисковый инструмент, не коммерческий агрегатор/ML-датасет. Режим:
   on-demand Flask-поиск (`app.py:470`), низкий rate, polite crawl
   (паузы+дитер как в `TelegramParser.run` `parsers/telegram.py:454`).

### 0.3. Что уже есть в проекте и переиспользуется

- Платформа парсеров: `BaseParser` (`parsers/base.py:343`), `run()`
  (`parsers/base.py:693`), `fetch()` (`parsers/base.py:423`), `ParserRunStats`
  (`parsers/base.py:50`), `_LAST_PARSER_STATS`/`get_all_parser_stats()`.
- Реестр парсеров: `PARSER_CLASSES` / `PARSER_REGISTRY` (`parsers/factory.py`),
  регистрация нового парсера — одной строкой в `PARSER_CLASSES`.
- Плоская модель данных: `Listing` (`parsers/models.py:63`) + `SearchParams`
  (`parsers/models.py:6`), общая для всех 6 парсеров (krisha/olx/kn/etagi/
  поля в JSON — это и есть транспорт в UI.
- Хранение результатов: **JSON-файл** через `_save_results()` (`app.py:424`),
  merge с предыдущим прогоном по ключу `f"{source}|{url}"` (`app.py:500`),
  сортировка по цене (`app.py:493`). Новые Optional-поля `Listing`
  автоматически попадают в выдачу через `to_dict()`.
- Хранение избранного + история цен: SQLite `db.py:18` (`SCHEMA`), таблицы
  `favorites` и `price_history` (`db.py:43`), миграции `_MIGRATIONS`
  (`db.py:58`), `listing_key()` (`db.py:74`), `check_prices()` (`db.py:284`).
- География: `data/districts.py:18` (`DISTRICTS` — 8 районов Алматы с
  полигонами), `get_districts_json()` (`data/districts.py:165`),
  `_point_in_polygon()` (`app.py:703`), heatmap `app.py:600`.
- Кеш фото: SHA1-по-URL в `export_utils.py:22` (`cache/photos/`).
- Зависимости (`requirements.txt`): flask, requests, curl_cffi, bs4, lxml,
  fpdf2, Pillow, pytest. **Нет** postgres, selenium, telethon, psycopg2.
- Шаблон тестов: `tests/test_*.py` + фикстуры `tests/fixtures/*.html` /
  `*.json`; `tests/loaders.py::load_fixture`; пример переопределённого `run()`
  и мока `fetch` — `tests/test_telegram.py:130` (`test_run_aggregates_...`).
- Существующая защита от 2GIS-мусора: `parsers/etagi.py:353` уже отсеивает
  2gis map-tiles из photo-CDN — тот же принцип применить в новом парсере.

### 0.4. Принципиальная адаптация (расхождения источник ↔ проект)

| Источник предлагает | Реальность проекта | Решение в адаптации |
|---|---|---|
| PostgreSQL, много таблиц (§32) | SQLite только под избранное + JSON результаты | `listings`→JSON via `Listing.to_dict`; `listing_prices`→существующий `price_history`; `buildings`/`organizations`→новые лёгкие таблицы SQLite или JSON-кеш (см. §11) |
| Places API + Geocoder API (§31) | Нет API-ключа в проекте | Reverse-engineering XHR-эндпоинтов `2gis.kz/almaty` через `fetch()`; rubric_id/category_id — захардкоженные константы, найденные раз вручную через DevTools (§8) |
| HTML-скрейпинг категорий | `BaseParser.run()` умеет перебирать страницы HTML | Новый парсер **переопределяет `run()`** (как `TelegramParser.run` `parsers/telegram.py:435`) — итерирует XHR/JSON-эндпоинты; `_parse_soup` не используется, JSON парсится напрямую |
| Равномерная/адаптивная geo-grid (§16–17) | Уже есть полигоны 8 районов | Использовать `DISTRICTS` (`data/districts.py:18`) как sub-grid; адаптивное дробление — опц., этап 5 |
| Источник: «долгосрочная аренда в слое Almaty может отсутствовать» (§2, §44) | Нельзя это не проверить | Обязательный этап 1: reverse-engineering realty XHR → ответ, есть ли долгосрочная аренда. Если нет — основной ROI: buildings + agencies (§12, §13) |

---

## 1. Четыре потока, адаптированные к проекту

### 1.1. REALTY_LISTINGS — главный поток

- Источник §4, §33: минимальный MVP-набор listing-полей.
- Если долгосрочная аренда доступна в realty-слое Almaty (определяется на
  этапе 1, §0.4) — это основной источник `Listing`-записей 2GIS.
- Если недоступна (только продажа + посуточная — источник §2, §44) — поток
  объявлений **пуст или мал**; 2GIS-парсер переходит в режим «провайдер
  зданий/агентств», а listings берутся из других парсеров (krisha/olx/...).
  Это нормальный fallback и его нужно явно обработать в `run()` (вернуть
  `[]` со статусом `empty`, как делает `BaseParser.run` `parsers/base.py:740`).

### 1.2. BUILDINGS — гео-обогащение (самый ценный слой по источнику §10, §44)

- Источник §5, §10, §11: декомпозиция адреса, building_id, этажность/материал,
  аналитика по ЖК (медианная цена, цена/м²).
- В проекте это **не отдельный поток вывода, а enrichment-слой**: после
  `Listing`-извлечения (из любого парсера, не только 2GIS) достать building
  по координатам/адресу и дополнить `Listing.residential_complex`,
  `Listing.lat/lon`, `Listing.total_floors`,_NORMALIZED_LOCATION.
- Реализация — отдельный модуль-обогатитель (см. §11), не только внутри
  2GIS-парсера. Доступен для других парсеров через общий post-run pass в
  `app.py:api_search` (аналогично тому, как `_enrich_photos` работает в
  `BaseParser.run` `parsers/base.py:601`).

### 1.3. AGENCIES — discovery поставщиков

- Источник §12, §13, §19, §20: query matrix + rubric_id + geo-grid + dedup by
  org_id. Привязка listing → agency (комиссия, phone cluster).
- В проекте: отдельная таблица `organizations` (SQLite, по образцу
  `favorites` в `db.py:18`) ИЛИ JSON-кеш `data/2gis_agencies.json` (по образцу
  results-JSON в `app.py:424`). Рекомендация — SQLite-таблица (маленькая, но
  индексируемая по `org_id`/`phone`), см. §11.
- Это **не `Listing`-записи** (агентство — не квартира). Но в UI/search можно
  показывать их отдельным эндпоинтом (новый `@app.route`), как
  `api_parser_status` (`app.py:535`).

### 1.4. INFRA — scoring (опц., этап 5)

- Источник §23, §26: nearest bus/metro/school/pharmacy + distances.
- В проекте: дополнительные Optional-поля `Listing` (см. §5) + мягкий вклад в
  `quality_score`. **Только если 2GIS отдаёт structured**; не угадывать
  «mountain view по координатам» (источник §26).

---

## 2. Архитектура: куда встраивать (карта файлов)

| Слой | Файл | Что делаем |
|---|---|---|
| Новый парсер | `parsers/twogis.py` (новый) | `class TwoGisParser(BaseParser): name="twogis"`; override `run()`; JSON-парсинг |
| Регистрация | `parsers/factory.py` (`PARSER_CLASSES`) | Добавить `TwoGisParser` в список |
| Модель | `parsers/models.py:63` (`Listing`) | Доп. Optional-поля (§5) |
| Обогащение зданиями | новый модуль `data/buildings.py` + post-run pass в `app.py:api_search` | building_id → ЖК/район/этажность |
| Хранение агентств | `db.py` (новая таблица `organizations` + миграция) | по образцу `favorites` |
| Кеш realty-JSON | `parsers/twogis.py` или `cache/` | по образцу photo-cache `export_utils.py:22` |
| Гео/районы | `data/districts.py:18`, `app.py:703` | переиспользовать для geo-grid (§8) |
| Дедуп | `parsers/base.py:682` + post-run в новом парсере | fingerprint (§9) |
| Экспорт | `export_utils.py` | учесть, что `source="twogis"` (термин «тг»/валюта сохраняется) |
| Тесты | `tests/test_twogis.py` (новый) + фикстуры `tests/fixtures/2gis_*.json/.html` | по образцу `tests/test_telegram.py` |
| Настройки | `app.py:load_settings` (`app.py:123`), `_apply_parser_max_pages` (`app.py:302`) | новый парсер берёт `max_pages` по общей схеме |

---

## 3. Имя и идентификация

- `name = "twogis"` (lowercase, без дефиса — по конвенции остальных парсеров в
- `base_url = "https://2gis.kz"` (или `https://docs.2gis.com` для API — но
  API без ключа в проекте не используется; работаем с `2gis.kz`).
- Признак источника в UI: `source="twogis"`. В `Listing.source` класть
  `"twogis"`. В `Listing.currency` — `"тг"` (см. конвенцию в
  `parsers/models.py:73`).
- В селекторе источников фронтенда (`templates/index.html`) добавить чекбокс
  «2GIS» рядом с telegram/krisha/olx/etc.

---

## 4. Транспорт: переопределение `run()` под JSON

### 4.1. Почему не дефолтный `BaseParser.run()`

`BaseParser.run()` (`parsers/base.py:693`) затачивался под HTML: цикл по
страницам через `build_url(params, page)`, `parse(html, params)` →
`_parse_soup` → `BeautifulSoup`. 2GIS-источникParty JSON-first (§27, §30):
реальные данные приходят из XHR-эндпоинта в виде JSON, а не из HTML-категории.

### 4.2. Решение

Как `TelegramParser.run` (`parsers/telegram.py:435`) — **переопределить
`run()` целиком**, сохранив контракт:

- Возвращает `list[Listing]`.
- Заполняет `ParserRunStats` (`parsers/base.py:50`) и кладёт в
  `_LAST_PARSER_STATS` (как telegram-парсер `parsers/telegram.py:470`).
- Обрабатывает ошибки по сетке `BaseParser.run` (`HTTPError`/`SSLError`/
  `ConnectionError`/`Timeout`/`Exception`) — см. `parsers/base.py:746-807`;
  минимум: `status="error"`, `error[:300]`, сохранение `duration_ms`.
- Использует `self.fetch(url)` для HTTP (антибот/SSL/403-ретраи), НЕ пишет
  свой requests-стек.
- Дедуп через `self._dedupe()` (`parsers/base.py:682`) + собственный
  cross-source fingerprint-pass (§9) для within-2gis дублей.
- Фильтры через `self.apply()` (`parsers/base.py:580`).
- Уважает `params.max_pages` (0 = дефолт парсера) и
  `_apply_parser_max_pages` (`app.py:302`).

### 4.3. Что не делать в `run()`

- Не Proselenium. Не playwright. Не браузер.
- Не писать «свой» HTTP.
- Не Shadow-DOM-парсинг HTML карточек, если есть XHR JSON (источник §30:
  «DevTools → Network → XHR/fetch → inspect JSON»).

---

## 5. Модель `Listing` — расширение

### 5.1. Принцип (тот же, что в `task_telega_2do.md` §3)

`Listing` — общая для всех парсеров; новые поля **только Optional с
`TwoGisParser` заполняет то, что извлёк; остальные оставляют `None`.

### 5.2. Поля для добавления (сгруппированы по ролям источника §4, §5, §8)

Identity / provenance:

```
listing_id_alt   : Optional[str]      # source_listing_id / provider_listing_id
provider         : Optional[str]      # напр. "Этажи", "Суточно.ру", "Отелло"
rental_period    : Optional[str]      # daily | monthly | long_term | unknown
building_id      : Optional[str]      # ключ к таблице buildings (§11)
provider_org_id  : Optional[str]      # привязка к agency (§13)
provider_branch_id: Optional[str]
```

Локация (источник §5):

```
normalized_location: Optional[str]    # «Толе би / Саина» из raw address
microdistrict     : Optional[str]    # «Аксай-3», «Таугуль»
landmark          : Optional[str]    # «возле Сайрана»
```

`lat`/`lon`/`address`/`street`-аналогов в `Listing` уже достаточно
(`address`, `lat`, `lon`). `residential_complex` — новое Optional.

Стоимость (источник §6, §7):

```
deposit           : Optional[int]      # сумма, тенге
deposit_refundable: Optional[bool]
commission_percent: Optional[int]      # % (агент)
commission_fixed  : Optional[int]      # фикс., тенге (если не %)
utilities         : Optional[str]      # "included" | "separate" | "unknown"
utilities_min     : Optional[int]
utilities_max     : Optional[int]
rent_per_m2       : Optional[float]    # вычисляется: rent/area
```

Условия (источник §8 — **только если явно текстом/structured**, без CV):

```
available_from   : Optional[str]       # ISO дата заселения
furnished        : Optional[bool]
pets_allowed     : Optional[bool]
children_allowed : Optional[bool]
smoking_allowed  : Optional[bool]
appliances       : Optional[str]      # pipe-список: "washer|fridge|ac"
infra_features   : Optional[str]      # pipe-список: "parking|security|playground"
```

Scoring/дедуп (источник §21–22, §43 этап5):

```
freshness_score  : Optional[float]
quality_score    : Optional[int]      # 0..100
duplicate_group_id: Optional[str]
sources_count    : Optional[int]      # «3 источника» (§9.3 телега-док)
```

### 5.3. Влияние на БД избранного

`Listing.to_dict()` автоматически прокидывает новые Optional-поля в
выдачу search (через `_to_dict` в `app.py:162` и `_save_results` `app.py:424`).
В БД `favorites` (`db.py:18`) добавить в `_MIGRATIONS` (`db.py:58`) только те
поля, которые хочется персистить в избранном (предлагаемо минимум:
`building_id`, `residential_complex`, `rental_period`, `deposit`,
`provider`). Остальные живут только в JSON-результатах.

---

## 6. Re ведение реального realty-эндпоинта (источник §30, §44) — ОБЯЗАТЕЛЬНЫЙ этап 1

### 6.1. Основной риск (источник §2, §44)

История 2GIS Kazakhstan: слой недвижимости запущен с **продажей + посуточной
арендой**; долгосрочная аренда была обозначена как «дальнейший план». Нельзя
предполагать, что «фильтр Аренда = полноценная база долгосрочной аренды».

### 6.2. Что сделать первым шагом (до написания парсера)

DevTools-разведка `2gis.kz/almaty`:

1. Открыть `2gis.kz/almaty`, вкладка «Недвижимость» / фильтр «Аренда».
2. Network → XHR/fetch → отфильтровать по запросам аренды.
3. Зафиксировать:
   - endpoint URL,
   - method (GET/POST),
   - query/form params (region_id, rubric_id, type, period, page, page_size),
   - response schema (поля listing: price, rooms, area, floor, building_id,
     coordinates, provider, listing_url),
   - **присутствует ли `period=long_term` / помесячная аренда**.
4. Сверить: официальный это API или внутренний endpoint веб-приложения.
   Источник §30: внутренний endpoint ≠ разрешение массово его дёргать.

### 6.3. Решение по результатам разведки

| Результат разведки | Действие |
|---|---|
| Долгосрочная аренда есть, endpoint стабилен | Строим `REALTY_LISTINGS` поток основным (§1.1) |
| Только продажа + посуточная | `REALTY_LISTINGS` — минимальный/пустой; основной ROI переходит на `BUILDINGS` (§1.2) + `AGENCIES` (§1.3) |
| Endpoint под защитой/Bot-fight | Используем `fetch()` (curl_cffi impersonate); если стабильно блокирует — alas, парсер помечаем `status="blocked"` и не включаем в дефолтный набор `get_all_parsers()` (можно опционально через настройки) |

### 6.4. Кеш realty-JSON

Повторные запросы realty-слоя дорогие и rate-limited. Кешировать ответ JSON
по ключу запроса (endpoint+params) в `cache/twogis_realty/<sha1>.json` по
образцу photo-cache `export_utils.py:22`, с TTL (при on-demand поиске —
например, 1 час). Это детерминированный JSON-кеш, не OCR/LLM.

---

## 7. Query matrix + rubric IDs (источник §13, §14)

### 7.1. Query matrix (источник §13)

Использовать при agency-discovery (если API-поиск по тексту) и при fallback
HTML-категорий. Закодировать как константу `QUERY_MATRIX` в `parsers/twogis.py`:

```
Tier A (обязательные):
  аренда квартир, аренда квартиры, агентство недвижимости аренда,
  агентство аренды квартир, агентства по аренде квартир,
  риелтор по аренде квартир, риэлтор по аренде квартир,
  снять квартиру, сдам квартиру, сдача квартир,
  аренда жилья, аренда жилья Алматы, квартиры в аренду, квартиры на аренду

Tier B (вариации):
  аренда квартир на месяц, аренда квартиры на месяц,
  долгосрочная аренда квартир, долгосрочная аренда жилья,
  снять квартиру на длительный срок, снять жилье,
  сдать квартиру, сдать жилье, поиск квартиры, подбор квартиры, подбор жилья

Tier C (посуточка — только если нужен short-term):
  квартиры посуточно, посуточная аренда квартир, квартирное бюро,
  апартаменты, апарт-отель, аренда квартиры посуточно
```

### 7.2. Rubric/category IDs

Источник §14: сначала rubric_id, потом Places API. В проекте нет API-ключа.

- Категории Алматы: «Агентства недвижимости», «Риелторские услуги»,
  «Аренда недвижимости», «Квартиры посуточно», «Квартирное бюро»,
  «Апарт-отели» + дочерние.
- rubric_id найти один раз вручную через `2gis.kz/almaty/search/<category>` →
  DevTools XHR → извлечь `rubric_id` / `category_id`.
- Захардкодить как константы в `parsers/twogis.py`:

  ```
  RUBRIC_AGENCIES   = "..."
  RUBRIC_REALTY_RENT= "..."
  RUBRIC_DAILY       = "..."
  ```
- Поместить комментарий: «найдено DD.MM.YYYY через DevTools `2gis.kz/almaty`;
  пересчитать при смене схемы 2GIS».

### 7.3. Объединение источников discovery (источник §15)

```
discovery =
    rubric_search(RUBRIC_*)          # по rubric_id
  ∪ text_search(QUERY_MATRIX)        # по тексту
  ∪ geo_grid(DISTRICTS)              # по району (§8)
  ∪ building_search(building_id)     # если есть realty layer (§1.2)
→ dedup by org_id                    # для agencies
```

---

## 8. Geo-grid → существующие районы (источник §16–18)

### 8.1. Re-use `DISTRICTS` вместо синтетической сетки

Источник §16 предлагает равномерную сетку; §17 — district sub-grid. В проекте
**уже есть** `data/districts.py:18` (`DISTRICTS`) с полигонами 8 районов и
`get_districts_json()` (`data/districts.py:165`). Использовать их как
готовую sub-grid:

```
for district in DISTRICTS:
    point = (district["lat"], district["lon"])
    radius = derived_from_polygon_extent(district["polygon"])
    → 2gis query with point+radius (или polygon, если API поддерживает)
```

- `_point_in_polygon()` (`app.py:703`) уже используется для heatmap
  (`app.py:600`) — тот же механизм помочь привязать листинг к району.

### 8.2. Адаптивное дробление (источник §17) — опц.

Если `result_count >= threshold` для района (центр плотнее), дробить ячейку.
**Этап 5** (после MVP). MVP использует точки районов из `DISTRICTS`.

### 8.3. Building-first (источник §18)

Если есть realty layer с `building_id`: стратегия `listing → building_id →
all listings in building`. Реализуется через кеш/таблицу buildings (§11) +
запрос по `building_id`.

---

## 9. Дедупликация (источник §21, §22) — учитывая существующий merge

### 9.1. Что есть

- `BaseParser._dedupe` (`parsers/base.py:682`) — по URL внутри одного прогона.
- Merge в `app.py:500` по ключу `source|url` — кросс-прогонный, но
  **разные источники = разные ключи** (krisha|urlA vs twogis|urlA не сольются,
  даже если та же квартира).

### 9.2. Fingerprint (источник §21)

Для 2GIS (и в перспективе кросс-источниковый) ввести `property_fingerprint`:

```
fingerprint = normalize(
    building_id  OR normalized_location
  + rooms
  + area         (bucketed ~±2 м²)
  + floor
  + normalized_price  (bucketed ~±5%)
)
```

Хранить в `duplicate_group_id` (поле `Listing`, §5.2). Одинаковый
fingerprint → одна master-группа (как в `task_telega_2do.md` §9.3:

```
master_listing_url
sources[]            — ["krisha", "twogis", "telegram"]
sources_count
first_seen_at / last_seen_at
```

### 9.3. Уровни совпадения (источник §22)

```
STRONG    — same provider_listing_id  (или same building_id+floor+rooms+area exact)
HIGH      — same building + rooms + floor + area ±2м² + price ±5%
MEDIUM    — same address-normalized + rooms + price ±10%
WEAK      — text similarity high + phone overlap
```

Пороги — настраиваемые константы. MVP: STRONG+HIGH (auto-merge), MEDIUM
(pomark), WEAK (игнор). Мастер — первая по времени.

### 9.4. Где реализовать

- Within-`twogis`-run: post-run pass в `TwoGisParser.run` (после `self._dedupe`).
- Cross-source: post-run слой в `app.py:api_search` (после merge в
  `app.py:500`), потому что там сходятся все источники. Это общее усиление
  проекта, не только 2GIS; совпадает с задачей в `task_telega_2do.md` §9.

### 9.5. «3 источника» = позитивный сигнал

Как и в телега-доке: `sources_count >= 3` повышает `quality_score`, не
штрафуется (источник §16 телега-дока; для 2GIS аналогично — квартира в 3
источниках вероятно реальная).

---

## 10. Цена: контекстный экстрактор (источник §6, §7)

### 10.1. Главная ошибка (источник §7)

Брать первое число как цену. В 2GIS-объявлении могут быть:
`250 000 тг/месяц` (rent), `депозит 250 000`, `комиссия 50%`,
`коммунальные 30 000`.

### 10.2. Контекстная классификация чисел

Для каждой числовой находки определять контекст по ключевым словам в окне:

```
PRICE_RENT       — аренда/в месяц/цена/стоимость (structured API field — #1 приоритет)
PRICE_DEPOSIT    — депозит/залог
PRICE_COMMISSION — комиссия/%
PRICE_UTILITIES  — коммуналки/ком.услуги
PRICE_DAILY      — /сутки (→ rental_period=daily, обычно фильтруем — out of scope)
```

### 10.3. Заполнение `Listing`

```
price          = PRICE_RENT     (привести: тыс/к/млн → целое тенге, валидный диапазон
                                  как в parsers/telegram.py _extract_price:
                                  30000..200_000_000)
deposit        = PRICE_DEPOSIT
commission_percent = PRICE_COMMISSION (%)
commission_fixed   = PRICE_COMMISSION (фикс.)
utilities         = "separate" если PRICE_UTILITIES найден;
                    "included" если «всё включено»;
                    "unknown" иначе
utilities_min/max = «5–6 тыс»
rental_period    = "long_term"|"monthly"|"daily"|"unknown"
rent_per_m2     = price/area (вычисл.)
```

### 10.4. Hierarchy источников данных (источник §28)

В проекте — то же правило приоритетов:

```
Level 1 — structured API field        → price/area/rooms/floor/coords/building_id/provider
Level 2 — attributes[] из API         → (если есть)
Level 3 — description + regex         → deposit/commission/pets/availability/...
Level 4 — HTML DOM                    → только если structured endpoint не даёт
Level 5 — image                       → НЕ ИСПОЛЬЗОВАТЬ (без OCR)
Level 6 — LLM                         → НЕ ИСПОЛЬЗОВАТЬ (без LLM)
```

---

## 11. Хранение: адаптация PostgreSQL-схемы под SQLite+JSON

Источник §32 — 6 таблиц PostgreSQL. Проект — SQLite (favorites+price_history)
+ JSON results. Маппинг:

| Источник (§32) | Проект | Где |
|---|---|---|
| `listings` | JSON-файл результатов через `Listing.to_dict()` + `_save_results` | `app.py:424` |
| `listing_attributes` | плоские поля `Listing` (appliances/infra_features как pipe-строки) + Optional-поля | `parsers/models.py:63` |
| `listing_prices` | существующая таблица `price_history` | `db.py:43` |
| `buildings` | **новая** лёгкая таблица SQLite `buildings` OR JSON-кеш `data/buildings.json` | `db.py` (расширить) |
| `organizations` | **новая** таблица SQLite `organizations` | `db.py` (расширить) |
| `organization_branches` | поля внутри `organizations` (JSON-массив branches) — MVP без отдельной таблицы | `db.py` |
| `listing_organization` | поля в `Listing`: `provider_org_id`, `provider_branch_id` | `parsers/models.py:63` |

### 11.1. Поля `buildings` (источник §10)

```
building_id (PK), address, street, house_number,
lat, lon, floors_total, building_material,
district, microdistrict, residential_complex, year_built
```

### 11.2. Поля `organizations` (источник §19)

```
org_id (PK), name, rubrics (pipe), rating, review_count,
address, lat, lon, website, phones (pipe),
parent_org_id, branch_count, branches (JSON)
```

### 11.3. Принцип миграций

Добавлять новые таблицы через `_apply_migrations(conn)` (`db.py:66`) или
аналогичный `CREATE TABLE IF NOT EXISTS` блок в `SCHEMA` (`db.py:18`). Для
новых колонок в `favorites` — дописать в `_MIGRATIONS` (`db.py:58`) по
образцу существующих текстовых колонок.

### 11.4. Если не хочется трогать БД (минимум-инвазивный вариант)

Здания и агентства — `data/buildings.json` и `data/2gis_agencies.json` (как
`results`-JSON через `_save_results`). Чтение через аналог
`_load_prev_results()` (`app.py:393`). Это быстрее в реализации, но не даёт
индексов по org_id/phone — для дедупа агентств предпочтительнее SQLite
(§13.4). Решение принимается на этапе 3.

---

## 12. Agency discovery (источник §12, §19, §20)

### 12.1. Отдельный crawler

Не смешивать агентства с `Listing`-записями (источник §12). Новый эндпоинт в
`app.py` (по образцу `api_parser_status` `app.py:535`):

- `GET /api/2gis/agencies` — список агентств из `organizations`.
- `GET /api/2gis/agencies?q=<text>` — поиск по name/address/phone.

### 12.2. Discovery-цикл

```
for rubric in RUBRICS_*, for query in QUERY_MATRIX, for district in DISTRICTS:
    fetch(2gis search endpoint, rubric, query, point, radius)
    → parse JSON/HTML → org records
dedup by org_id (and by normalized phone for branches)
store into organizations table / JSON
```

Posture: polite — `time.sleep(random.uniform(...))` между запросами (как
`TelegramParser` `parsers/telegram.py:454`).

### 12.3. Агентство → его объявления (источник §20)

После того как `Listing.provider_org_id` заполнен (для listings из 2GIS или
после матчинга phone-кластера для listings из других парсеров), можно
считать:

```
агентство → кол-во объявлений, медианная цена, районы, типы квартир,
            средняя площадь, частота обновлений
```

Это **аналитика этап 5** (§16), не MVP.

---

## 13. Определение собственник/риелтор через агентскую привязку

### 13.1. Сигналы

- Если у `Listing` есть `provider_org_id` / `commission_percent != None` →
  агент, `owner_probability` низкий.
- Если в тексте «без посредников / собственник» (см. реестр в
  `task_telega_2do.md` §11) → собственник, `owner_probability` высокий.
- Помимо владение 2GIS-источником, **телефон-кластер** — один и тот же номер
  в нескольких объявлениях = вероятный риелтор. Нормализация телефона (см.
  `task_telega_2do.md` §7.1: `+7`+10 цифр) обязательна, иначе кластер не
  соберётся.

### 13.2. Не бинарно

`owner_probability: float 0..1`, `agent_probability = 1 - owner` (как в
телега-доке §11). Без LLM — весовой подсчёт маркеров.

---

## 14. Минимальный MVP listing-полей (источник §33)

Из обязательных полей источника §33 — что уже есть в `Listing` vs что новое:

| Поле источника | В `Listing`? | Действие |
|---|---|---|
| listing_id | через `url` (+ новое `listing_id_alt`) | добавить `listing_id_alt` |
| provider | — (новое) | добавить `provider` |
| listing_url | `url` ✓ | — |
| price | `price` ✓ | — |
| currency | `currency` ✓ (= «тг») | — |
| period | — | добавить `rental_period` |
| rooms | `rooms` ✓ | — |
| area | `area` ✓ | — |
| floor | `floor` ✓ | — |
| floors_total | `total_floors` ✓ | — |
| address | `address` ✓ | — |
| building_id | — | добавить `building_id` |
| lat/lon | `lat`/`lon` ✓ | — |
| provider_org_id | — | добавить `provider_org_id` |
| first_seen/last_seen | `date_published`/`date_updated` (есть в `Listing`) ✓ | использовать |
| is_active | 1 по умолчанию в текущем прогоне | computed в `app.py:api_search` |

---

## 15. Pipeline без OCR/LLM (источник §27, §28)

```
                    2GIS (2gis.kz XHR JSON)
                      │
           ┌──────────┴───────────┐
           │                      │
        Realty                  Places
       (listings)          (organizations + buildings)
           │                      │
           └──────────┬───────────┘
                      │
                  raw JSON
                      │
               schema validation (json keys, типы)
                      │
           ┌──────────┴──────────┐
           │                     │
       structured             description
         fields               + regex
           │                     │
           └──────────┬──────────┘
                      │
                   normalize
                      │
                  deduplicate  (fingerprint §9)
                      │
                  building join  (§11.1)
                      │
                  geo-enrich  (DISTRICTS §8)
                      │
                  price history  (price_history db.py:43)
                      │
             SQLite (organizations/buildings)
             + JSON results (_save_results app.py:424)
```

---

## 16. Аналитика рынка (источник §43, этап 5) — опц.

После MVP (listings + buildings + dedup):

- median rent по ЖК (`residential_complex`),
- rent/m² по комнатности (`rooms`, `area`, `price`),
- медиана по району (`data/districts.py`),
- days-on-market (через `first_seen`/`last_seen` в `price_history`),
- price drops (через историю `check_prices()` `db.py:284`).

Это новое представление/эндпоинт `app.py` (по образцу `api_heatmap`
`app.py:600`), не часть парсера. Парсер остаётся stateless.

---

## 17. Что НЕ делать

- **OCR фото** (источник §9, §28 L5, §44) — исключено заданием и источником.
- **LLM / внешние AI-API** (источник §28 L6, §44) — исключено.
- **Selenium / browser automation** (источник §29, §30) — не начинаем;
  данные ищем в JSON XHR. Проект и так без selenium (`requirements.txt`).
- **Парсинг визуального мусора** — CSS/DOM position/map tiles/отрисованный
  текст на карте/скриншоты/SVG/иконки (источник §29).
- **Свой HTTP-стек** — только `BaseParser.fetch` (`parsers/base.py:423`).
- **Массовое коммерческое извлечение** без сверки с лицензией 2GIS (источник
  §30, §44, [12]): проект — личный on-demand поиск, низкий rate. Любой вывод
  за пределы personal-use — отдельное решение + лицензия.
- **Не ломать остальные 5 парсеров** — новые поля `Listing` только
  Optional/дефолт.
- **Не коммитить API-ключи/секреты** (их тут и нет, и не должно быть).
- **Не предполагать** «2GIS = база долгосрочной аренды Алматы» (источник §44).
  Сначала §6 разведка.
- **Не определять mountain_view/вид по координатам** (источник §26) — только
  если явно в structured/text.
- **Не делать CV-классификацию фото** (кухня/спальня/…) — требует модели,
  противоречит «без ML». Отложено.
- **Не коммитить без явного запроса.**

---

## 18. Тесты (`tests/test_twogis.py` + фикстуры)

По образцу `tests/test_telegram.py` (фикстура `tests/fixtures/telegram.html`,
мок `fetch`, `load_fixture` из `tests/loaders.py`). Покрытия:

1. **Realty XHR JSON-парсинг**: фикстура
   `tests/fixtures/2gis_realty.json` (реальный сэмпл ответа) →
   `parse(json, SearchParams())` извлекает N listings с корректными полями
   (price/rooms/area/floor/building_id/lat/lon/provider).
2. **Цена-контекст**: в description встречаются rent+deposit+commission+
   utilities → каждое число идёт в своё поле, не «первое число = price» (§10).
3. **rental_period**: долгосрочное vs посуточное vs unknown.
4. **building_id извлекается** и сохраняется в `Listing.building_id`.
5. **provider** определяется из structured field источника (Этажи/Суточно/
   Отелло/...).
6. **Geo-привязка**: listing с координатами внутри полигона «Алмалинский»
   (`data/districts.py`) → `district` определяется через `_point_in_polygon`
   (`app.py:703`).
7. **Fingerprint-дедуп**: два листинга same building+floor+rooms+area±2м²+
   price±5% → один `duplicate_group_id` (§9.2). Разные — разные группы.
8. **Agency discovery**: фикстура `tests/fixtures/2gis_agencies.json` →
   парсинг org records (`org_id`, `name`, `phones`, `rubrics`) → сохранение в
   `organizations` (или JSON).
9. **Owner/agent**: listing с `commission_percent=30` → низкий
   `owner_probability`; «без посредников» в description → высокий.
10. **Blocked/пустой realty-слой**: реальная ситуация «long_term отсутствует»
    → `run()` возвращает `[]` со `status="empty"`/`"blocked"`, не падает,
    не валит остальные парсеры (контракт `app.py:api_search` `app.py:487`).
11. **Кеш realty-JSON**: повторный запрос с теми же params → берётся из кеша
    (`cache/twogis_realty/`), второй `fetch()` не делается (через мок-счётчик).
12. **2GIS map-tiles отсеиваются** из photo-URL (как `etagi.py:353`):
    `maps.2gis.com/...` / тайлы не попадают в `Listing.photo`.
13. **exclude map-tiles / avatars** — той же логикой, что в
    `parsers/etagi.py:353`.

Итого: парсер должен пройти `pytest tests/test_twogis.py` + общий `pytest`
без регрессий по остальным парсерам.

---

## 19. Чеклист внедрения (порядок имеет значение)

1. **Reverse-engineering realty XHR** (§6) — ручной DevTools-шаг; фиксация
   endpoint, params, schema, наличия долгосрочной аренды. **Это блокер**: без
   него нельзя решить, основной ли это listing-источник или 2GIS служит
   только buildings/agencies.
2. Сверка лицензии/ToS: убедиться, что личный on-demand режим не нарушает
   условия 2GIS (§0.2.5, источник §30/§44/[12]).
3. Скелет `parsers/twogis.py`: `class TwoGisParser(BaseParser)` с `name`,
  `base_url`, override `run()` по контракту §4.2 + `ParserRunStats`.
4. Регистрация в `parsers/factory.py` (`PARSER_CLASSES`, `PARSER_REGISTRY`).
5. Реальный JSON-парсинг realty-ответа → `Listing` (если слой доступен).
6. Если long_term отсутствует — реализовать только buildings (§11.1) +
   agencies (§12), как пользу-источник для остальных парсеров.
7. Расширение `Listing` Optional-полями (§5.2) + миграции в `db.py:_MIGRATIONS`
   для персистящихся в избранном полей (§5.3).
8. Контекстный экстрактор цены (§10) — отдельная функция
   `_extract_costs(text_or_attrs) → dict`.
9. Fingerprint-дедуп within-run (§9.2) в `TwoGisParser.run` post-run.
10. Building-enrichment модуль `data/buildings.py` + post-run pass в
    `app.py:api_search` (§1.2, §11.1) — не только для 2GIS.
11. Agency discovery (§12): `organizations` SQLite-таблица (`db.py`) ИЛИ
    JSON-кеш `data/2gis_agencies.json`; эндпоинт `app.py`
    `/api/2gis/agencies`.
12. Geo-привязка через `data/districts.py:18` + `_point_in_polygon` (§8) —
    переиспользовать, без новой сетки на MVP.
13. Owner/agent probability (§13) + phone-нормализация (общая с телега-доком
    §7.1: `normalize_phone()`).
14. Кеш realty-JSON в `cache/twogis_realty/<sha1>.json` с TTL (§6.4).
15. Quality/freshness scores (переиспользовать с `task_telega_2do.md` §8/§12,
    общее усиление проекта).
16. Тесты (§18): писать параллельно каждому шагу; `pytest tests/test_twogis.py`.
17. Финальный прогон: `pytest` (без регрессий) + `python app.py` и manual
    `/api/search` с `sources=["twogis"]`, проверка что новые поля видны в UI и
    `parser_stats` для twogis присутствует (`get_all_parser_stats()`).

Контроль качества после каждого блока изменений: `pytest`, ручной запуск
через существующий UI с `sources=["twogis"]`, сверка, что 2GIS-листинги
корректно дедуплицируются с крisha/olx через fingerprint (§9), и что
`organizations`/`buildings` не загрязняют выдачу search (они — отдельные
эндпоинты).
