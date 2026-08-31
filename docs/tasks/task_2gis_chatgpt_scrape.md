Да. Я бы здесь **не строил парсер вокруг HTML 2GIS**. Для Алматы у 2ГИС есть две принципиально разные сущности: **объявления недвижимости** и **карточки агентств/квартирных бюро**. Для долгосрочной аренды это особенно важно.

## Короткий вывод

**Оптимальная схема:**

1. **Слой “Недвижимость” 2GIS** → парсить реальные объявления, если нужный тип аренды реально отдается в данных.
2. **Карточки зданий** → использовать как дополнительный источник привязки объявления к конкретному дому/ЖК.
3. **Карточки агентств недвижимости** → обязательно собирать отдельно: это полезный источник телефонов, сайтов, WhatsApp, филиалов и потенциальных поставщиков объявлений.
4. **Не пытаться вытаскивать признаки квартиры из картинок.** Всё, что можно получить из структурированных данных, брать оттуда; текст — только regex/правила.
5. Для **долгосрочной аренды в Алматы** я бы не полагался только на 2GIS: официальные материалы 2GIS указывают, что при запуске слоя в Казахстане были объявления о продаже и **посуточной** аренде, а в последнем найденном официальном описании СНГ долгосрочная аренда всё ещё обозначена как направление, которое планировалось добавить. ([Доставили][1])
6. Поэтому если задача — **получить максимально полную базу долгосрочной аренды Алматы**, 2GIS лучше использовать как **геосправочник + слой недвижимости + discovery источников**, а не как единственный classified.

---

# 1. Что реально есть в 2GIS

![Image](https://static.2gis.com/files/5970b2cac9f71de9.png)

![Image](https://images.openai.com/static-rsc-4/hAFLA9gqLkvcdPZqRZ2tObqmz5zOmF0vP1NxN_tuv3k1JWLNqdlgBq_8FbVboVqPBhnSyQ3hSt-B6Hz65BHo1lo4nUlUufI6r9PbrY01MhfgT755ffUylETa7YS1DojIMgVnl1162QagPxbPsPRAKUOfpVd4-roOtr4hmMcylK8gFArQLzaaqcOTnx526vOu?purpose=fullsize)

Исторически 2GIS строил недвижимость именно как агрегатор объявлений: пользователь открывает дом → вкладку «Недвижимость» → видит объявления → переходит к оригинальному поставщику. Сам 2GIS описывает это как отдельный сценарий поиска жилья. ([Доставили][2])

При этом в 2GIS есть ещё более интересный слой: **данные самого здания и окружающей инфраструктуры**.

Для квартиры можно связать:

```text
объявление
   │
   ├── provider
   ├── listing URL
   ├── price
   ├── rooms
   ├── area
   ├── floor
   ├── coordinates
   │
   └── building_id
          │
          ├── адрес
          ├── ЖК / ориентир
          ├── этажность
          ├── организации
          ├── остановки
          ├── парковки
          ├── фото дома
          └── отзывы / инфраструктура
```

Именно **building_id + география** я считаю одной из самых ценных частей 2GIS для аналитики аренды.

---

# 2. Важный нюанс именно для Алматы

Здесь есть существенная разница с российскими городами.

В официальном материале 2GIS от июня 2024 года указано, что слой недвижимости в Казахстане запустили сразу в 11 городах, включая Алматы. На старте там были объявления о продаже от «Этажей» и объявления **посуточной аренды** от Суточно.ру. ([Доставили][1])

Затем 2GIS добавил Отелло как ещё один источник посуточной аренды. В официальном материале 2GIS перечисляет источники слоя: Отелло, ЦИАН, Домклик, Самолёт Плюс, ПИК, Суточно.ру, Этажи, Жилфонд и Владис. ([Доставили][3])

Но ещё более важен следующий официальный материал:

> для Казахстана и других стран СНГ слой недвижимости был запущен с **посуточной арендой**, а подключение объявлений о продаже и **долгосрочной аренде** было обозначено как дальнейший план. ([Доставили][4])

Поэтому я бы **не делал предположение**, что наличие в интерфейсе 2GIS фильтра «Аренда» автоматически означает наличие полноценной базы долгосрочной аренды Алматы.

Это надо проверить непосредственно по текущему сетевому API.

---

# 3. Что именно парсить

Я бы разделил парсер на **4 потока**.

| Поток             | Что собираем             | Приоритет |
| ----------------- | ------------------------ | --------: |
| `REALTY_LISTINGS` | сами квартиры            |     ⭐⭐⭐⭐⭐ |
| `BUILDINGS`       | дома/ЖК                  |     ⭐⭐⭐⭐⭐ |
| `AGENCIES`        | агентства и риелторы     |      ⭐⭐⭐⭐ |
| `INFRA`           | транспорт/инфраструктура |       ⭐⭐⭐ |

Главная ошибка — смешивать эти сущности в одну таблицу.

---

# 4. Поток №1 — объявления

Если текущий 2GIS API действительно отдает объявления аренды Алматы, это **главный объект парсинга**.

Я бы сохранял минимум такой набор.

### Identity

```text
listing_id
listing_url
provider
provider_listing_id
category_id
category_name
scraped_at
```

### Основные параметры

```text
title
description

property_type
rooms
area_m2

floor
floors_total

price
currency
price_per_m2

rental_period
```

Где:

```text
rental_period =
    daily
    monthly
    long_term
    unknown
```

**Не определять `monthly` по одной строке текста.** Если 2GIS уже отдаёт тип аренды структурированно — использовать его.

---

# 5. Адрес — очень важен

Не хранить только:

```text
"Алматы, улица Абая..."
```

Нужно раскладывать:

```text
country
region
city
district
street
house_number

building_id
building_group_id

residential_complex
landmark

lat
lon
```

У Places API 2GIS есть структурированные данные объекта, координаты, адрес, административная привязка и связанные объекты. ([2ГИС][5])

Это позволит потом делать нормальный spatial join.

Например:

```text
listing
   ↓
building_id = X
   ↓
district = Бостандыкский
   ↓
ЖК = Mega Tower
   ↓
lat/lon
```

вместо попытки вычленять район из описания.

---

# 6. Что обязательно вынимать из `description`

Здесь **LLM вообще не нужен**.

Если описание приходит обычным текстом — делаем deterministic parser.

Например:

```text
"Сдается 2-комнатная квартира.
55 м2.
7/12 этаж.
Депозит 100 000.
Комиссия 30%.
Без животных."
```

Регулярками можно получить:

```text
rooms = 2
area = 55
floor = 7
floors_total = 12
deposit = 100000
commission = 30%
pets = false
```

### Я бы сделал словарь regex-экстракторов:

#### Комнаты

```regex
(\d+)\s*[- ]?\s*(комн|комнат)
```

Плюс:

```text
студия
евродвушка
евротрешка
1+1
2+1
```

Но `евродвушка` лучше хранить как:

```text
rooms_raw = "евродвушка"
rooms = 2
rooms_confidence = rule
```

---

## Площадь

```regex
(\d+(?:[.,]\d+)?)\s*(м2|м²|кв\.?\s*м)
```

---

## Этаж

```regex
(\d+)\s*/\s*(\d+)\s*(этаж|эт)
```

или:

```text
7 этаж
7/12
7 из 12
```

---

## Цена

Нужен отдельный money parser:

```text
250 000
250000
250 тыс
250к
250 000 ₸
250 000 тг
```

приводить к:

```json
{
  "value": 250000,
  "currency": "KZT",
  "period": "month"
}
```

---

# 7. Очень важный момент — не путать цену

В Алматы это особенно критично.

В тексте могут одновременно встретиться:

```text
250 000 тг/месяц
депозит 250 000
комиссия 50%
коммунальные 30 000
```

Нельзя делать:

```text
first_number = price
```

Нужна классификация контекста.

Например:

```text
PRICE_RENT
PRICE_DEPOSIT
PRICE_COMMISSION
PRICE_UTILITIES
PRICE_DAILY
```

И итоговая структура:

```json
{
  "rent": 250000,
  "deposit": 250000,
  "commission_percent": 50,
  "utilities": 30000
}
```

---

# 8. Из описания я бы обязательно извлекал

Вот реально полезные признаки:

### Заселение

```text
available_from
available_now
```

### Собственник / агент

```text
owner
agent
agency
unknown
```

### Комиссия

```text
commission_exists
commission_value
commission_percent
commission_fixed
```

### Депозит

```text
deposit_exists
deposit_value
```

### Животные

```text
pets_allowed
pets_small
pets_no
unknown
```

### Дети

```text
children_allowed
children_no
unknown
```

### Курение

```text
smoking_allowed
smoking_no
unknown
```

### Мебель

```text
furnished
partially_furnished
unfurnished
unknown
```

### Бытовая техника

```text
washing_machine
fridge
dishwasher
air_conditioner
oven
microwave
tv
```

### Инфраструктура

```text
parking
security
concierge
closed_yard
playground
```

### Вид

```text
mountain_view
city_view
yard_view
```

Но **только если это явно написано текстом/полем**, а не предположено по фотографии.

---

# 9. Картинки — не анализировать

Это как раз соответствует твоему условию.

Если 2GIS отдаёт:

```json
"images": [
    "https://..."
]
```

то сохранять:

```text
image_urls[]
```

и всё.

Не делать:

```text
image → OCR
image → computer vision
image → LLM
```

Потому что для аналитической базы это ещё и плохо воспроизводимо.

Лучше:

```text
structured field → 100% доверие
regex from description → deterministic
image → ссылка
```

---

# 10. Поток №2 — здания

Это, на мой взгляд, **самая недооценённая часть 2GIS**.

У объявления нужно получить:

```text
building_id
```

А дальше отдельным запросом получить здание.

2GIS Places API официально поддерживает поиск зданий и получение объектов по ID; отдельные поля могут включать адрес, геометрию, этажность и связанные объекты. ([2ГИС][5])

Таблица:

```text
building_id
address
street
house_number

lat
lon

floors_total
building_material

district
microdistrict

residential_complex

year_built
```

Если поле есть.

---

# 11. Что даст привязка к зданию

Например, 100 объявлений:

```text
250k
270k
280k
300k
...
```

Можно получить:

```text
ЖК
↓
building_id
↓
median rent
↓
rent/m²
↓
rooms
↓
floor
↓
district
```

И уже строить:

### Цена аренды по ЖК

```text
ЖК A      280 000
ЖК B      310 000
ЖК C      245 000
```

### Цена за м²

```text
1-комн  → 7 100 ₸/м²
2-комн  → 6 300 ₸/м²
3-комн  → 5 900 ₸/м²
```

Это намного ценнее, чем просто список объявлений.

---

# 12. Поток №3 — агентства

А вот здесь 2GIS **очень полезен даже если долгосрочных объявлений в слое мало**.

Поисковые страницы Алматы показывают сотни результатов по запросам вроде «агентства по аренде квартиры», «риелтор по аренде квартир», «сдать квартиры». Например, по некоторым формулировкам 2GIS показывает около 100–125 агентств/мест. ([2ГИС][6])

То есть я бы обязательно делал отдельный crawler:

```text
AGENCY DISCOVERY
```

---

# 13. Какие поисковые запросы использовать

Не один:

```text
аренда квартир
```

а query matrix.

### Tier A — обязательные

```text
аренда квартир
аренда квартиры
агентство недвижимости аренда
агентства недвижимости аренда квартир
агентство аренды квартир
агентства по аренде квартир
риелтор по аренде квартир
риэлтор по аренде квартир
снять квартиру
сдам квартиру
сдача квартир
аренда жилья
аренда жилья Алматы
квартиры в аренду
квартиры на аренду
```

---

### Tier B — вариации

```text
аренда квартир на месяц
аренда квартиры на месяц
долгосрочная аренда квартир
долгосрочная аренда жилья
снять квартиру на длительный срок
снять жилье
сдать квартиру
сдать жилье
поиск квартиры
подбор квартиры
подбор жилья
```

---

### Tier C — посуточка

Если нужен short-term:

```text
квартиры посуточно
посуточная аренда квартир
квартирное бюро
апартаменты
апарт-отель
аренда квартиры посуточно
```

В 2GIS это отдельный очень заметный сегмент. В Алматы поисковая выдача прямо показывает «Квартиры посуточно», «Почасовая аренда квартир» и цены за сутки. ([2ГИС][7])

---

# 14. Какие категории брать

Я бы сначала находил **rubric/category ID**, а не полагался только на текстовый поиск.

У 2GIS есть Categories API: сначала определяется категория в регионе, затем её ID используется в Places API. ([2ГИС][8])

Для Алматы искать категории вокруг:

```text
Агентства недвижимости
Риелторские услуги
Аренда недвижимости
Квартиры посуточно
Квартирное бюро
Апарт-отели
```

Плюс все дочерние рубрики.

Почему это важно:

```text
query search
```

может вернуть релевантные по тексту организации.

А:

```text
rubric_id
```

даёт более контролируемую выборку.

---

# 15. И отдельно — поиск по тексту

Categories + text search надо **объединять**, а не выбирать одно.

Схема:

```text
                   ┌─ rubric search
                   │
2GIS discovery ────┼─ text queries
                   │
                   ├─ geo grid
                   │
                   └─ building-based search
```

После чего:

```text
deduplicate by org_id
```

---

# 16. Почему нужен geo-grid

Не делал бы:

```text
Алматы → один огромный запрос
```

Лучше разбить город на сетку.

Например:

```text
+----+----+----+----+
|    |    |    |    |
+----+----+----+----+
|    |    |    |    |
+----+----+----+----+
|    |    |    |    |
+----+----+----+----+
|    |    |    |    |
+----+----+----+----+
```

Каждая ячейка:

```text
center_lat
center_lon
radius
```

и запрос:

```text
rentals + point + radius
```

Places API официально поддерживает географические ограничения через точку/радиус, прямоугольник и произвольную область. ([2ГИС][9])

---

# 17. Для Алматы я бы сделал ещё лучше — не равномерную сетку

Сначала собрать географию Алматы:

```text
districts
microdistricts
streets
residential complexes
```

Потом:

```text
district → sub-grid
```

Почему?

Центр города намного плотнее, чем периферия.

Например:

```text
центр:
500–1000 м cells

средняя плотность:
1–2 км

периферия:
2–4 км
```

И затем адаптивно дробить ячейку, если:

```text
result_count >= threshold
```

Это существенно уменьшает число запросов.

---

# 18. Ещё лучше — building-first strategy

Для **объявлений** я бы тестировал такую стратегию:

```text
Almaty
  ↓
realty listings
  ↓
unique building_id
  ↓
building
  ↓
all listings attached to building
```

Почему это интересно:

2GIS сам построил UX вокруг недвижимости в здании — в карточке здания показываются варианты по этому адресу. ([Доставили][2])

И у 2GIS даже есть вкладка недвижимости в карточках **улиц и районов**, причём сейчас в ней можно посмотреть количество предложений и перейти ко всем объявлениям. ([Доставили][10])

---

# 19. Что хранить по агентству

Минимум:

```text
org_id
branch_id

name
rubrics[]

address
lat
lon

phones[]
emails[]
websites[]

social_links[]

rating
review_count

working_hours

description

photos[]

is_advertisement

branch_count
```

И очень важно:

```text
parent_org_id
```

если 2GIS позволяет его получить.

Потому что условно:

```text
Grand Estate
   ├── филиал 1
   ├── филиал 2
   └── филиал 3
```

не должны превращаться в три разных агентства.

---

# 20. Агентство → его объявления

Это уже очень интересная модель.

Если объявление имеет:

```text
provider_org_id
provider_branch_id
```

можно связать:

```text
LISTING
   ↓
provider
   ↓
2GIS organization
   ↓
phone
website
rating
reviews
branches
```

И получить:

```text
агентство
↓
количество объявлений
↓
медианная цена
↓
районы
↓
тип квартир
↓
средняя площадь
↓
частота обновлений
```

Это уже нормальная аналитика рынка.

---

# 21. Дедупликация — обязательно

У 2GIS исторически была известная проблема: одна квартира могла присутствовать одновременно у нескольких поставщиков. Сам 2GIS прямо писал об отсутствии дедубликации в первоначальной версии сервиса. ([Доставили][2])

Поэтому:

```text
listing_id
```

недостаточно.

Нужен `property_fingerprint`.

Я бы сделал:

```text
fingerprint =
    normalize(
        address/building_id
        + rooms
        + area
        + floor
        + normalized_price
    )
```

Например:

```text
building_id = 123
rooms = 2
area = 54.8
floor = 7
price = 280000
```

→ fingerprint.

---

# 22. Но fingerprint должен быть fuzzy

Потому что:

```text
54.8 м²
55 м²
55.1 м²
```

может быть одна квартира.

Я бы делал несколько уровней.

### Strong match

```text
same provider_listing_id
```

### High-confidence

```text
same building
same rooms
same floor
area diff <= 1 m²
price diff <= 2%
```

### Medium

```text
same building
same rooms
area diff <= 2 m²
```

### Weak

```text
same building
same rooms
similar price
```

---

# 23. Историю цен сохранять обязательно

Не перезаписывать:

```text
price = 300000
```

а делать:

```text
listing_id
observed_at
price
```

Например:

| Дата  | Цена |
| ----- | ---: |
| 01.08 | 320k |
| 05.08 | 310k |
| 12.08 | 295k |
| 18.08 | 280k |

Тогда появляется очень интересная аналитика:

```text
price_drop
days_on_market
```

---

# 24. Что я бы вычислял

Из сырого слоя:

### Цена за м²

```text
rent_per_m2 = monthly_rent / area
```

### Discount

```text
previous_price - current_price
```

### Days on market

Если объявление исчезло:

```text
last_seen - first_seen
```

### Availability

```text
last_seen_recently = true
```

### Агентская комиссия

```text
effective_first_month_cost =
    rent
    + deposit
    + commission
```

Это намного более полезно арендатору, чем просто `250 000 ₸`.

---

# 25. Инфраструктура — отдельный enrichment

У 2GIS это как раз сильная сторона.

Для каждого `building_id` можно получить/рассчитать:

```text
nearest_bus_stop
nearest_metro
nearest_school
nearest_kindergarten
nearest_supermarket
nearest_pharmacy
nearest_hospital
nearest_parking
```

Places API официально поддерживает связанные объекты, в том числе ближайшие остановки и парковки. ([2ГИС][9])

И получается:

```text
Квартира
↓
building
↓
transport
↓
schools
↓
shops
↓
pharmacies
```

---

# 26. Что особенно полезно для Алматы

Я бы отдельно сохранял:

```text
distance_to_metro
distance_to_bus_stop
distance_to_school
distance_to_kindergarten
distance_to_supermarket
distance_to_pharmacy
```

И:

```text
mountain_direction / view
```

только если это явно присутствует в structured data/description.

**Не определять mountain view по координатам.**

---

# 27. Парсинг без OCR и LLM: рекомендованный pipeline

Я бы строил так:

```text
                   2GIS
                     │
          ┌──────────┴───────────┐
          │                      │
       Realty                  Places
          │                      │
     listings              organizations
          │                      │
          └──────────┬───────────┘
                     │
                  raw JSON
                     │
              schema validation
                     │
          ┌──────────┴──────────┐
          │                     │
      structured             text
        fields               parser
          │                     │
          └──────────┬──────────┘
                     │
                 normalize
                     │
                deduplicate
                     │
                building join
                     │
                geo-enrich
                     │
                price history
                     │
                 PostgreSQL
```

---

# 28. Приоритет источников данных

Я бы ввёл такой hierarchy:

### Level 1 — structured API field

```text
price
area
rooms
floor
coordinates
building_id
provider
```

**Максимальный приоритет.**

### Level 2 — structured attribute

```text
attributes[]
```

### Level 3 — description + regex

```text
"есть депозит"
"без животных"
"комиссия 50%"
```

### Level 4 — HTML DOM

Только если structured endpoint не даёт нужное.

### Level 5 — изображение

**Не использовать**, учитывая твоё требование.

### Level 6 — LLM

**Не использовать.**

---

# 29. Что не надо парсить

Я бы сознательно НЕ собирал:

```text
цвет иконки
CSS
DOM position
map tile images
отрисованный текст на карте
скриншоты
визуальный рейтинг
SVG icons
```

Если это не нужно бизнес-задаче.

И особенно:

```text
не кликать каждую карточку через Selenium,
если те же данные приходят JSON.
```

---

# 30. API против браузера

Официальный Places API 2GIS отдаёт JSON и предназначен именно для поиска и сбора информации об объектах. Он поддерживает пагинацию, географические ограничения, категории, поиск по ID и т. д. ([2ГИС][11])

Поэтому:

### Для организаций

**API > HTML > browser automation**

Я бы вообще не начинал с Selenium.

---

### Для недвижимости

Сначала:

```text
DevTools
→ Network
→ XHR/fetch
→ filter "Аренда"
→ inspect JSON
```

и установить:

```text
какой endpoint
какие parameters
какой response schema
```

Если этот endpoint является официально предоставленным API — использовать API.

Если это внутренний endpoint веб-приложения, сначала проверить лицензионные условия/разрешение на автоматизированный сбор. Текущая лицензия 2GIS прямо ограничивает автоматизированные запросы и использование данных, если это отдельно не разрешено. ([2ГИС Правовая Информация][12])

То есть **технически найти endpoint ≠ автоматически получить право массово его дергать**.

---

# 31. Официальный API — очень хороший вариант для геосправочника

Places API умеет:

```text
q
rubric_id
city_id
building_id
point
radius
polygon
page
page_size
```

и другие параметры. ([2ГИС][9])

Есть также:

```text
byid
bysite
byphone
```

что полезно для дедупликации агентств. ([2ГИС][9])

А Geocoder API позволяет:

```text
address → coordinates
coordinates → address
```

и возвращает JSON. ([2ГИС][11])

---

# 32. Как я бы организовал БД

Не одна таблица.

### `listings`

```text
id
source
source_listing_id

title
description

property_type
rooms
area_m2

floor
floors_total

price
currency
rent_period

address
building_id
lat
lon

provider_org_id
provider_branch_id

listing_url

first_seen
last_seen
is_active
```

### `listing_attributes`

```text
listing_id
attribute
value
source
```

### `listing_prices`

```text
listing_id
observed_at
price
currency
```

### `buildings`

```text
building_id
address
district
microdistrict
residential_complex
lat
lon
floors_total
```

### `organizations`

```text
org_id
name
rubrics
rating
review_count
address
lat
lon
website
phones
```

### `organization_branches`

```text
org_id
branch_id
address
phone
lat
lon
```

### `listing_organization`

```text
listing_id
org_id
branch_id
relationship
```

---

# 33. Какие данные я бы считал обязательными

Если задача — **аренда квартир**, минимальный MVP:

```text
listing_id
provider
listing_url

price
currency
period

rooms
area
floor
floors_total

address
building_id

lat
lon

property_type

first_seen
last_seen
```

Но production-версия:

```text
+ description
+ images
+ provider_org_id
+ residential_complex
+ district
+ deposit
+ commission
+ utilities
+ furnished
+ pets
+ children
+ appliances
+ parking
+ availability
+ price history
```

---

# 34. А что делать с агентствами

Я бы строил второй dataset:

```text
agency_id
name
rubrics
rating
review_count

phone[]
website[]
social[]

address
lat
lon

description

branches_count

first_seen
last_seen
```

И потом считать:

```text
agency → number of listings
agency → median price
agency → districts
agency → average rooms
agency → average price/m²
```

Это позволит быстро увидеть, кто реально работает с арендой, а кто просто имеет рубрику «Недвижимость».

---

# 35. Очень полезный scoring агентств

Можно сделать deterministic score:

```text
+3  есть "аренда" в rubric
+3  есть "квартиры"
+2  есть сайт
+2  есть телефон
+2  есть WhatsApp
+2  есть объявления
+1  > 100 отзывов
+1  rating >= 4.5
```

Получается:

```text
agency_relevance_score
```

И парсер может сначала собрать условные 100–200 агентств, а затем **глубоко обрабатывать только top-N**.

---

# 36. Как отличать долгосрочную аренду от посуточной без LLM

Правила:

### Daily

```text
сутки
посуточно
за сутки
/сут
/день
ночь
почасово
час
```

### Long-term

```text
месяц
/мес
ежемесячно
долгосрочно
на длительный срок
на год
от 6 месяцев
```

Но при конфликте:

```text
"от 15 000 в сутки, при аренде от месяца..."
```

сохранять **оба значения**, а не выбирать первое.

---

# 37. Как не потерять казахский язык

Для Алматы обязательно иметь словари:

```text
ru
kk
mixed
```

Например:

```text
пәтер
жалға алу
жалға беріледі
айына
тәулік
```

и смешанные:

```text
квартира жалға
пәтер аренда
```

Даже если основной интерфейс русский.

---

# 38. Что я бы тестировал первым

Не строить сразу production.

Сначала сделать **audit crawler** на 500–1000 результатов.

Для каждой сущности сохранять:

```text
raw_response
endpoint
request_params
timestamp
parser_version
```

После чего ответить на 10 вопросов:

1. Есть ли сейчас в Алматы долгосрочная аренда?
2. Сколько объявлений?
3. Какие providers?
4. Какие поля реально присутствуют?
5. Есть ли `building_id`?
6. Есть ли координаты?
7. Есть ли цена за месяц?
8. Есть ли описание?
9. Есть ли изображения?
10. Есть ли уникальный listing ID?

Только после этого писать полноценный crawler.

---

# 39. Контроль качества

Для каждой выгрузки:

```text
total listings
unique listings
duplicate rate

price_null_rate
area_null_rate
rooms_null_rate
floor_null_rate
building_id_null_rate
coordinates_null_rate

provider distribution
```

Например:

```text
10 000 listings

price missing       0.2%
area missing        1.1%
rooms missing       4.3%
building_id missing 0.7%
coordinates missing 0%
```

Если внезапно:

```text
building_id missing = 87%
```

— скорее всего, изменился endpoint/response schema.

---

# 40. Особенно важный мониторинг схемы

Сохранять JSON Schema snapshot.

Например:

```text
schema_hash
parser_version
endpoint_version
```

Если 2GIS поменял:

```json
price
```

на:

```json
priceValue.value
```

парсер не должен молча записывать `NULL`.

Должен появиться:

```text
SCHEMA_DRIFT_ALERT
```

---

# 41. Что я бы НЕ делал

❌ Selenium на весь город.

❌ Парсинг DOM карточек вместо JSON.

❌ OCR цен с карты.

❌ LLM для определения комнат.

❌ LLM для определения района.

❌ LLM для классификации «посуточная/долгосрочная».

❌ Один запрос «аренда квартир Алматы».

❌ Только text search.

❌ Только категории.

❌ Только агентства.

❌ Хранить только текущую цену.

❌ Не хранить `provider`.

❌ Не хранить `building_id`.

❌ Смешивать посуточную и долгосрочную аренду.

---

# 42. Моя рекомендуемая архитектура

```text
                 ┌──────────────────┐
                 │ 2GIS REALTY      │
                 │ network/API      │
                 └────────┬─────────┘
                          │
                     raw listings
                          │
                          ▼
                 ┌──────────────────┐
                 │ LISTING PARSER   │
                 │ JSON only        │
                 └────────┬─────────┘
                          │
          ┌───────────────┼────────────────┐
          ▼               ▼                ▼
       pricing        attributes       location
          │               │                │
          └───────────────┼────────────────┘
                          ▼
                    NORMALIZATION
                          │
                          ▼
                    DEDUPLICATION
                          │
             ┌────────────┴───────────┐
             ▼                        ▼
         BUILDING                 PROVIDER
             │                        │
             ▼                        ▼
       GEO ENRICHMENT             AGENCY DB
             │                        │
             └────────────┬───────────┘
                          ▼
                     PostgreSQL
                          │
             ┌────────────┼────────────┐
             ▼            ▼            ▼
          current       history      analytics
```

---

# 43. Приоритет реализации

### Этап 1 — обязательно

**Realty network discovery**

Понять текущий endpoint слоя недвижимости Алматы.

↓

**Structured extraction**

```text
listing_id
price
area
rooms
floor
address
coordinates
provider
URL
building_id
```

↓

**1000 listings test**

---

### Этап 2

**Building enrichment**

```text
building_id
ЖК
район
адрес
этажность
координаты
```

↓

**Price history**

---

### Этап 3

**Agency crawler**

```text
rubric + query matrix + geo grid
```

↓

dedup by:

```text
org_id / branch_id
```

---

### Этап 4

**Text extraction**

Только regex/rules:

```text
deposit
commission
pets
children
furniture
appliances
availability
```

---

### Этап 5

**Market analytics**

```text
rent/m²
median rent
district
ЖК
rooms
floor
agency
provider
days on market
price drops
```

---

# 44. Итоговая рекомендация именно для Алматы

Я бы оценил источники так:

| Источник 2GIS                 | Для чего               | Ценность |
| ----------------------------- | ---------------------- | -------: |
| **Недвижимость → объявления** | actual inventory       |    ⭐⭐⭐⭐⭐ |
| **Карточки зданий**           | нормализация адреса/ЖК |    ⭐⭐⭐⭐⭐ |
| **Агентства недвижимости**    | discovery поставщиков  |    ⭐⭐⭐⭐⭐ |
| **Квартирные бюро**           | short-term inventory   |     ⭐⭐⭐⭐ |
| **Геокодер**                  | координаты/адреса      |     ⭐⭐⭐⭐ |
| **Инфраструктура**            | scoring квартиры       |     ⭐⭐⭐⭐ |
| Отзывы агентств               | quality/reputation     |      ⭐⭐⭐ |
| Фото                          | хранить URL            |       ⭐⭐ |
| OCR                           | не использовать        |        — |
| LLM extraction                | не использовать        |        — |

И главное: **не исходить из того, что “2GIS = база долгосрочной аренды Алматы”**. По официальной истории продукта Казахстан сначала получил слой недвижимости с продажей и посуточной арендой, а долгосрочная аренда была обозначена как последующее расширение. ([Доставили][1]) При этом поисковая выдача 2GIS Алматы уже сейчас очень хорошо покрывает агентства и квартирные бюро. ([2ГИС][13])

Поэтому **лучший вариант для production**:

> **2GIS Realty → объявления + 2GIS Places → здания/агентства + deterministic extraction → собственная дедупликация и история.**

И я бы сделал первым делом **не сам парсер, а reverse-engineering текущего realty network API на `2gis.kz/almaty`**. Это даст точный ответ, какие поля и какие виды аренды реально доступны в Алматы сейчас, а не по документации 2024–2025 годов.

Отдельно: если планируется коммерческое массовое извлечение данных, нужно сверить конкретный способ доступа с лицензией/API 2GIS. Текущая лицензия прямо ограничивает автоматизированные запросы и использование данных без соответствующего разрешения. ([2ГИС Правовая Информация][12])

[Официальная документация Places API 2GIS](https://docs.2gis.com/api/search/places/overview?utm_source=chatgpt.com)
[Официальная документация Geocoder API 2GIS](https://docs.2gis.com/api/search/geocoder/overview?utm_source=chatgpt.com)
[Официальные правила API/лицензия 2GIS](https://law.2gis.ru/licensing-agreement?utm_source=chatgpt.com)

[1]: https://dostavili.2gis.ru/public/0624/commercial/?utm_source=chatgpt.com "Новости коммерческих продуктов"
[2]: https://dostavili.2gis.ru/public/0221/realty/?utm_source=chatgpt.com "Недвижимость на карте"
[3]: https://dostavili.2gis.ru/public/0824/realty/?utm_source=chatgpt.com "Отелло в слое «Недвижимость»"
[4]: https://dostavili.2gis.ru/public/0625/realestate/?utm_source=chatgpt.com "Рыночная цена недвижимости"
[5]: https://docs.2gis.com/en/api/search/places/overview?utm_source=chatgpt.com "Overview | 2GIS Documentation"
[6]: https://2gis.kz/almaty/search/%D0%90%D0%B3%D0%B5%D0%BD%D1%82%D1%81%D1%82%D0%B2%D0%B0%20%D0%BF%D0%BE%20%D0%B0%D1%80%D0%B5%D0%BD%D0%B4%D1%83%20%D0%BA%D0%B2%D0%B0%D1%80%D1%82%D0%B8%D1%80%D1%8B?utm_source=chatgpt.com "Агентства по аренду квартиры в Алматы на карте: ☎ телефоны, ★ отзывы — 2ГИС"
[7]: https://2gis.kz/almaty/search/%D0%90%D1%80%D0%B5%D0%BD%D0%B4%D0%B0%20%D0%BA%D0%B2%D0%B0%D1%80%D1%82%D0%B8%D1%80%D1%88%D1%83?utm_source=chatgpt.com "Аренда квартиршу в Алматы на карте: ☎ телефоны, ★ отзывы — 2ГИС"
[8]: https://docs.2gis.com/en/api/search/categories/overview?utm_source=chatgpt.com "Overview | 2GIS Documentation"
[9]: https://docs.2gis.com/en/api/search/places/examples?utm_source=chatgpt.com "Examples | 2GIS Documentation"
[10]: https://dostavili.2gis.ru/public/1024/realty/?utm_source=chatgpt.com "Новое в Недвижимости"
[11]: https://docs.2gis.com/api/search/geocoder/overview?utm_source=chatgpt.com "Обзор | 2ГИС Документация"
[12]: https://law.2gis.ru/licensing-agreement?utm_source=chatgpt.com "Лицензионное соглашение"
[13]: https://2gis.kz/almaty/search/%D0%A1%D0%B4%D0%B0%D0%BC%20%D0%BA%D0%B2%D0%B0%D1%80%D1%82%D0%B8%D1%80%D1%8B?utm_source=chatgpt.com "Сдам квартиры в Алматы на карте: ☎ телефоны, ★ отзывы — 2ГИС"
