Да. Я бы строил парсер **не вокруг одного “самого большого” канала**, а вокруг портфеля каналов с разным профилем: крупный поток, качественные объявления, подселение/комнаты и дополнительные источники. По текущему исследованию на август 2026 года это дает заметно лучший recall и меньше дублей.

![Image](https://images.openai.com/static-rsc-4/99aAG55UWEtg-S4lzd-5_BQR3ba_0IRIM1KoNYi4iZueSjc-KlMchRZF9fplFWzG8d1sKECxVwgV_yg024Q1EDFjYnUgHjfvAMAXrhy99xvF55HBUkl1tvNCQFv7tVQDH5_GJcF87LixAjwc0dN9MLzAYTkEAc4JmJuPE4aASNn5HgUUivJ_csh9PmB65-Rs?purpose=fullsize)

![Image](https://images.openai.com/static-rsc-4/kJnQ0o8tLMajHuJti1X08sH15woCrF2RUBA5wmw2pW_AFmXbWUPO9bOXIxc_n56GRPmFUEc9w7eHdm_hPV77zu4yZsFBTSbXz-Ge-t28-n1OC3C0l60FiEgLCyxbc9YBWkHGkS_kK1mX6_KpdqjpKXklblqP6jt48iGI1QMzl5rEYH3zKWpvq0b7QLH4RFLg?purpose=fullsize)

![Image](https://images.openai.com/static-rsc-4/5SFdRtjE6MaOtr8pLW51iLMR2Bx2BtLlaZeTqxUsxR-VsdGfYp82LO39f5lhiucg1Y2FlBvlNn3chCAEZlTg62q0TsHeYzuYBUqoHIonGm8COZcnM5yUvjCPSTktgmMS9aSDMM716jPY1TMu4YUTbju1zIzci7dtipfy4aUcEBmbzH3YNlYzIuVSn2H4T-VE?purpose=fullsize)

![Image](https://images.openai.com/static-rsc-4/jKnSs8Rb_3vC-2YE_iMF-k0gZVUhyhy9OrCS0LktFjLAZh0xZ82Tx5S4v78Msu_DfiVuPeeik57VZtmoiBtvR-88T9_j7psJx-i3DhHGjYsIyVrJ83CHvCvzUV6Ahz-cvlkQhHvrkmAcb2xTNjYSXN0x2Uwg2l82llDbB9YKaV9V7qDawqI9GXCHUazQFqhm?purpose=fullsize)

![Image](https://images.openai.com/static-rsc-4/JoxCxN8gazuGPOMInwwWCN7oA8ZkBaR_JIkEj-3f2Vyjhng3v5iOyWgQw2y2cpTUEgRVSO95qNlzwT4CMFjIp6EJbGMhdTMz0bbA_bKkcNS2QCmYVkRNCG8kmEuicOqRlRskhTcXU7kBqnzpZR6Rpk4E2klm4zWWSMgHbwHFDqb97syiUyMZF6CO6mL7ZvI3?purpose=fullsize)

# 1. Что я нашел

### Главные каналы, которые стоит парсить

| Приоритет | Канал                                                           | Username                                                   | Размер / активность                        | Что там есть                                   | Моя оценка |
| --------- | --------------------------------------------------------------- | ---------------------------------------------------------- | ------------------------------------------ | ---------------------------------------------- | ---------- |
| 🥇        | **Аренда квартира Алматы Подселение**                           | `@kvartiry2`                                               | ~85–92K подписчиков в разных свежих срезах | квартиры, комнаты, подселение                  | **10/10**  |
| 🥈        | **Квартиры Алматы | Аренда квартир Алматы | Поиск соседей**     | `@kvartira_v_almaty`                                       | ~17.5K, ~1.3K просмотров/пост в среднем    | аренда + подселение + поиск соседей            | **9.5/10** |
| 🥉        | **Аренда квартиры Алматы🏠**                                    | `@arenda_kvartiry_almaty_kz`                               | ~19.7K                                     | очень много объявлений, включая казахский язык | **8.5/10** |
| 4         | **Аренда квартиры Алматы БЕЗ РИЭЛТОРОВ**                        | `@kvartiry222`                                             | ~350–430                                   | небольшой, но сильно специализированный        | **8/10**   |
| 5         | **Твоя Недвижимость в Алматы**                                  | `@tn_almaty`                                               | ~2.1K                                      | аренда + продажа + подселение + коммерция      | **6.5/10** |
| 6         | **Аренда Квартира Алматы | Подселение | Посуточно | Помесячно** | `@Arenda_Kvartira_Ala02`                                   | ~690                                       | аренда/подселение                              | **6/10**   |
| 7         | **АЛМАТЫ КВАРТИРА ПОЙСК ПОДСЕЛЕНИЕ**                            | `@kvartiram7` / идентификатор канала в Telemetr отличается | небольшой актуальный поток                 | подселение/поиск                               | **5.5/10** |
| 8         | **АРЕНДА КВАРТИР/АЛМАТЫ**                                       | `@arendakvartiralma`                                       | ~325                                       | очень маленький канал                          | **3/10**   |

Самый очевидный источник — `@kvartiry2`: Telegram сейчас показывает порядка **92 тыс. подписчиков**, 143 тыс. фото и 2.48 тыс. видео в доступном срезе; сам канал позиционируется как канал аренды квартир, комнат и подселения. ([Telegram][1])

Второй источник — `@kvartira_v_almaty`. У него около **17.5 тыс. подписчиков**, а Telemetr показывает около **1,282–1,291 просмотров на пост и ER ~5.57%**. При этом в контенте действительно много объявлений, а не просто рекламного контента. ([Telemetr][2])

`@arenda_kvartiry_almaty_kz` интересен тем, что имеет около **19.7 тыс. подписчиков и более 15 тыс. постов**, причем поток заметно мультиязычный — русский + казахский. Это важно, если задача — не потерять предложения от казахоязычной аудитории. ([Telemetr][3])

---

# 2. Я бы начал вот с этих трех

### Tier A — обязательный парсинг

**1. `@kvartiry2`**

[Открыть канал @kvartiry2](https://t.me/kvartiry2?utm_source=chatgpt.com)

Почему:

* самый большой из найденных специализированных источников;
* огромный объем фото;
* много подселения;
* квартиры + комнаты;
* очень высокая вероятность получать объявления раньше, чем они попадут на агрегаторы;
* есть как структурированные, так и совершенно неструктурированные посты.

Но есть минус: **много мусора и дублей**. Размер аудитории ≠ качество объявления.

Например, в постах встречается что-то вроде:

> район → цена → количество людей → требования → WhatsApp

но часто отсутствуют площадь, этаж, точный адрес и фотографии.

Поэтому здесь нужен сильный NLP/regex extraction layer.

---

**2. `@kvartira_v_almaty`**

[Открыть канал @kvartira_v_almaty](https://t.me/kvartira_v_almaty?utm_source=chatgpt.com)

Это, пожалуй, **самый полезный источник для качественного MVP**.

У него намного меньше аудитория, чем у `@kvartiry2`, но посты часто содержат очень полезные поля.

Например, реальные посты содержат:

* ЖК;
* район;
* улицу;
* цену;
* коммунальные;
* депозит;
* количество проживающих;
* пол/возраст жильца;
* срок заселения;
* мебель;
* технику;
* контакт;
* иногда координаты/ссылку на 2GIS;
* фото.

Например, встречаются объявления с форматом:

**ЖК → площадь → количество комнат → цена → коммунальные → депозит → дата заселения → контакт.** ([Telemetr][4])

Это очень хороший источник для построения **нормализованной базы объявлений**.

---

**3. `@arenda_kvartiry_almaty_kz`**

[Открыть канал @arenda_kvartiry_almaty_kz](https://t.me/arenda_kvartiry_almaty_kz?utm_source=chatgpt.com)

Я бы обязательно добавил его именно из-за **языкового разнообразия**.

В потоке есть русские и казахские объявления:

* `пәтер`;
* `квартира`;
* `подселение`;
* `қыз`;
* `ұл`;
* `бөлме`;
* `комната`;
* `жалға`;
* `аренда`.

Например, в одном небольшом срезе присутствуют объявления о подселении за 50–72.5 тыс. ₸, поиск комнаты, а также полноценная долгосрочная аренда квартиры за 220 тыс. ₸. ([Telemetr][3])

**Это важный источник для казахского NLP.**

---

# 3. Дополнительные каналы

### `@kvartiry222`

[Открыть канал @kvartiry222](https://t.me/kvartiry222?utm_source=chatgpt.com)

Название: **Аренда квартиры Алматы БЕЗ РИЭЛТОРОВ**.

Он гораздо меньше — порядка нескольких сотен подписчиков, но интересен другим:

> меньше охват → более узкая специализация → потенциально меньше шума.

Telemetr показывает около 356–426 подписчиков в разных свежих срезах и примерно 62–96 просмотров на пост. В объявлениях есть комнаты, подселение, депозит, район и WhatsApp. ([Telemetr][5])

Я бы не ставил его выше крупных каналов, но **добавил как источник recall**.

---

### `@tn_almaty`

[Открыть канал «Твоя Недвижимость в Алматы»](https://t.me/tn_almaty?utm_source=chatgpt.com)

Плюс — более широкий рынок:

* квартиры;
* дома;
* участки;
* коммерческая недвижимость;
* аренда;
* продажа;
* подселение.

Размер около 2.1 тыс. подписчиков. Но проблема — много **продаж**, поэтому для rental-пайплайна придется фильтровать значительную часть потока. ([Telemetr][6])

Я бы использовал его как **Tier C**, а не как основной источник.

---

### `@Arenda_Kvartira_Ala02`

[Открыть канал @Arenda_Kvartira_Ala02](https://t.me/Arenda_Kvartira_Ala02?utm_source=chatgpt.com)

Около 690 подписчиков; заявлена аренда помесячно/посуточно и поиск соседей. Канал явно специализирован под Алматы. ([Telegram][7])

Хороший дополнительный источник, особенно если вам интересна **посуточная + помесячная аренда**.

---

# 4. Очень важное наблюдение: нельзя просто искать слово «аренда»

Если ваша цель — получить **все реальные варианты жилья**, поиск должен быть гораздо шире.

Например, объявление может вообще не содержать слова `аренда`.

### Ключевые классы сообщений

**Сдача:**

```text
сдается
сдам
сдаю
сдаётся
сдается квартира
сдается комната
сдам квартиру
сниму
снять
```

**Подселение:**

```text
подселение
на подселение
ищем девушку
ищем парня
возьму девушку
возьму парня
нужен сосед
нужна соседка
соседку
соседа
```

**Поиск жилья:**

```text
ищу квартиру
сниму квартиру
нужна квартира
нужна комната
ищу комнату
пәтер іздеймін
пәтер керек
бөлме керек
```

**Казахский:**

```text
жалға
жалға беремін
жалға беріледі
пәтер
бөлме
үй
қыз керек
ұл керек
подселениеге
```

И обязательно ловить варианты с ошибками:

```text
подселениеге
подселение
подселение
подселения
подселит
подселиться
```

---

# 5. Что конкретно вычленять из каждого объявления

Я бы не складывал просто `text + image`.

Нужна **нормализованная rental schema**.

Например:

```text
listing_id
source_channel
telegram_message_id
published_at

listing_type
  ├── apartment
  ├── room
  ├── house
  ├── roommate
  ├── sublet
  └── wanted

deal_type
  ├── long_term
  ├── short_term
  └── unknown

price
currency
price_period

deposit
deposit_refundable

utilities
utilities_included

commission
commission_amount

rooms
area_m2

floor
floors_total

district
microdistrict
street
house_number
residential_complex

metro
landmark

furnished
appliances

condition
renovation

available_from
minimum_term

tenants_allowed
gender
age_requirement
pets_allowed
children_allowed
smoking_allowed

owner_or_agent

contact_telegram
contact_phone
contact_whatsapp

description

photos[]
videos[]

source_url

duplicate_group_id
confidence_score
```

---

# 6. Самое важное — разделить квартиру и подселение

Это **не одна сущность**.

Например:

### Listing A

> 2-комнатная квартира
> ЖК X
> 300 000 ₸/мес
> депозит 100 000

Это **whole apartment**.

А:

### Listing B

> Ищу девушку на подселение
> 70 000 + коммунальные
> жить будем вдвоем

Это **roommate/sublet**.

Если смешать эти сущности, поиск станет практически бесполезным.

Я бы сделал:

```text
WHOLE_APARTMENT
PRIVATE_ROOM
SHARED_ROOM
ROOMMATE
SUBLET
HOUSE
SHORT_TERM
WANTED
UNKNOWN
```

---

# 7. Особо важный extraction: цена

Цена в Telegram будет написана совершенно по-разному:

```text
300к
300 000
300.000
300000
300 тыс
300т
300 т
300 k
300K
300 мың
300мын
```

Парсер должен привести всё к:

```json
{
  "price": 300000,
  "currency": "KZT",
  "period": "month"
}
```

А для подселения:

```json
{
  "price": 70000,
  "price_per_person": true,
  "utilities": "separate"
}
```

Особенно важно различать:

**70 000 за человека**

и

**70 000 за всю квартиру.**

---

# 8. Коммунальные — отдельное поле

Это одна из самых важных вещей для реального пользователя.

Из:

> 70 000 + ком

делаем:

```text
rent = 70 000
utilities = separate
utilities_amount = unknown
```

А из:

> 70 000 + ком услуги 5–6 тыс.

получаем:

```text
rent = 70 000
utilities = separate
utilities_amount_min = 5 000
utilities_amount_max = 6 000
effective_monthly_cost = 75 000–76 000
```

И это позволяет ранжировать квартиры **по реальной стоимости**, а не по рекламной цене.

---

# 9. Депозит тоже надо нормализовать

Например:

> депозит 50к возвратный

→

```text
deposit = 50000
deposit_refundable = true
```

> без депозита

→

```text
deposit = 0
```

> депозит 50%

→

```text
deposit = percentage
deposit_value = 0.5 * monthly_rent
```

> первый платеж 120к, дальше 80к

Это уже более сложный кейс:

```text
first_payment = 120000
monthly_rent = 80000
```

И **не следует ошибочно записывать 120K как аренду**.

---

# 10. Адрес надо геокодировать

Telegram дает очень плохие адреса:

```text
Саина Шаляпина
```

```text
возле Сайрана
```

```text
Аксай 3
```

```text
рядом с Мегой
```

```text
Толе би - Саина
```

```text
ЖК Алма Сити 4
```

Поэтому я бы хранил **три уровня**:

```text
raw_location
normalized_location
coordinates
```

Например:

```text
raw:
"Толе би-Саина, напротив Asia Park"

normalized:
"Толе би / Саина"

lat:
...

lon:
...
```

И отдельно:

```text
district = Ауэзовский
microdistrict = Аксай-1
landmark = Asia Park
```

---

# 11. Очень полезно парсить 2GIS-ссылки

В объявлениях `@kvartira_v_almaty` уже встречаются ссылки на 2GIS. ([Telegram][8])

Это практически подарок для парсера.

Если встречается:

```text
https://2gis.kz/almaty/geo/...
```

не нужно пытаться угадывать адрес из текста.

Можно сохранить:

```text
2gis_url
lat
lon
2gis_object_id
```

и затем построить нормальную карту.

---

# 12. Фото нужно не просто сохранять

Я бы делал отдельный **image pipeline**.

![Image](https://images.openai.com/static-rsc-4/CTlRHquUvf60evRGy-ZwQL0FKL9MABumlkbUhTYP3WVw75nLUB4jWB2VatmIQYY6NlkDSZP_JOX6m3lfK4Nw9XUNff9ceNPGM1PEHUl43PfUpphaTZM2DDSUNPGdeuAnl9j2-O9lr2ebcR9AeYalQLP_KomzBB4Pxgx1gIVTnVYQzxumfnk21swn7DGQhd7x?purpose=fullsize)

![Image](https://images.openai.com/static-rsc-4/dQcAkaqIsxGHulIWEYmvAcTbWWYjm9cpR57q4ylNomFSGbBOjQgHGOFrtYtA0LsEd4oon6hiL_Cg3l74n6PAVLz2j_MdsT_tpDaEol7mG56_idUu0o5FHrnyMPz3KFWn-ovEWPyCZf3A_FtlQF_knjFjHYTxDdvhs9NtHYqXc9ueXCc9bR_HsTE_ya9C5y7m?purpose=fullsize)

![Image](https://images.openai.com/static-rsc-4/5SFdRtjE6MaOtr8pLW51iLMR2Bx2BtLlaZeTqxUsxR-VsdGfYp82LO39f5lhiucg1Y2FlBvlNn3chCAEZlTg62q0TsHeYzuYBUqoHIonGm8COZcnM5yUvjCPSTktgmMS9aSDMM716jPY1TMu4YUTbju1zIzci7dtipfy4aUcEBmbzH3YNlYzIuVSn2H4T-VE?purpose=fullsize)

![Image](https://images.openai.com/static-rsc-4/hyjGbzg27wBJl-99s_ep7gdW2QnyqFIZz0vgLPKrHSatSgrI99e19iqpzZJdNAHfeYwTtzMgq5gz_RO4BK3lp1JczvVWUspQsDNGw6nnU51nkIirA7SCwKM57p5D0Ra4jSoF69UXow1-ffKgUKCobTjToGOyvVMn-4DmQW6p7zzRTL4pfyt7zlUt8ULHPHzp?purpose=fullsize)

![Image](https://images.openai.com/static-rsc-4/JNr_JGftENOBXLQEkwjWAOZFH6D11AZ1Vd3EbuKuw7KzBk737fOpYcib5Bx78hKTgKaklZVXtXF9IdTKRDjgvdOummJu5EabuMmz0jMyXXO3eU3BL_O1sBJtGmkVr5YYESK6TcK6RZDtfS1zAYyMhDJtK5QpsmE0bKLDccJg7PBWSaTYbuYGy_eGLR4_uUMt?purpose=fullsize)

![Image](https://images.openai.com/static-rsc-4/YMgUBSwPnwrBnIqfc2HInpnRomuwdbSEbyP41pbkHltSwhbXgn3Pb4fbmmh-CkLdDsLbw42-PJ4MonurT7g71OyI2FDZkG_qYCL09Dnpnlrz-_zC0AhJ4ChitYiW3ulP7BsZPmtgjUl15maQf61hMOs3hmCrn9Bqe1aXHNQ56UnBzy78uGXKvvZ53ISUqkLe?purpose=fullsize)

![Image](https://images.openai.com/static-rsc-4/kJnQ0o8tLMajHuJti1X08sH15woCrF2RUBA5wmw2pW_AFmXbWUPO9bOXIxc_n56GRPmFUEc9w7eHdm_hPV77zu4yZsFBTSbXz-Ge-t28-n1OC3C0l60FiEgLCyxbc9YBWkHGkS_kK1mX6_KpdqjpKXklblqP6jt48iGI1QMzl5rEYH3zKWpvq0b7QLH4RFLg?purpose=fullsize)

Для каждого объявления:

```text
photos_count
photo_urls
photo_hashes
image_width
image_height
```

Потом:

### Шаг 1 — удалить технические дубли

Perceptual hash:

```text
pHash
dHash
aHash
```

Потому что один и тот же риелтор может кинуть одинаковую квартиру:

* в `@kvartiry2`;
* в `@kvartira_v_almaty`;
* в `@arenda_kvartiry_almaty_kz`.

---

### Шаг 2 — определять тип изображения

Например:

```text
apartment_interior
kitchen
bedroom
bathroom
living_room
exterior
floor_plan
map
document
contact_card
collage
irrelevant
```

---

### Шаг 3 — считать качество фотографии

Например:

```text
blur_score
brightness
resolution
```

И потом пользователю можно показывать:

> **8 фото · реальные фото · 2 спальни · кухня · санузел**

---

# 13. Еще круче — OCR с фотографий

Очень много Telegram-объявлений устроено так:

> фото квартиры + на картинке написано
> `2 ком / 250 000 / ЖК ...`

А текст сообщения практически пустой.

Поэтому pipeline должен быть:

```text
Telegram message
       ↓
caption extraction
       ↓
photo download
       ↓
OCR
       ↓
caption + OCR объединяются
       ↓
entity extraction
       ↓
normalized listing
```

То есть:

```text
TEXT:
"Сдается квартира"

IMAGE:
"ЖК Алма Сити
2 ком
250 000
+ ком
WhatsApp ..."
```

→ полноценная карточка.

---

# 14. Еще один очень сильный сигнал — контакт

Контакты стоит выделять отдельно:

```text
Telegram username
phone
WhatsApp
external URL
```

И нормализовать:

```text
87071234567
+7 707 123 45 67
8 (707) 123-45-67
wa.me/77071234567
```

→

```text
+77071234567
```

Это позволит находить **одного и того же арендодателя**, даже если он разместил объявление в 4 каналах.

---

# 15. Дедупликация будет критически важной

Я ожидаю огромное количество дублей.

Например:

```text
Channel A
"Сдается 2-комнатная ЖК ..."
```

через час:

```text
Channel B
"Сдам 2-комнатную квартиру..."
```

а потом:

```text
Channel C
фото те же
телефон тот же
цена та же
```

Нужно строить:

```text
duplicate_score =
    text_similarity
  + phone_similarity
  + image_similarity
  + address_similarity
  + price_similarity
```

Например:

```text
same phone       +0.30
same images      +0.30
same ЖК          +0.15
same price       +0.10
same rooms       +0.05
similar text     +0.10
```

Если:

```text
score > 0.75
```

→ вероятный дубль.

---

# 16. Но дубли нельзя просто удалять

Лучше сделать **master listing**.

Например:

```text
MASTER #193821

Квартира:
2 комнаты
ЖК X
250 000
60 м²

Источники:
@kvartiry2
@kvartira_v_almaty
@arenda_kvartiry_almaty_kz

Первое появление:
16.08 09:12

Последнее появление:
16.08 13:47

Количество источников:
3
```

Это даже полезный сигнал.

Если квартиру публикуют сразу в трех каналах — вероятность, что она реально существует и активно сдается, выше.

---

# 17. Очень важный feature: «свежесть»

Для аренды объявление пятимесячной давности практически бесполезно.

Поэтому:

```text
freshness_score
```

Например:

```text
0–2 часа       1.00
2–6 часов      0.95
6–12 часов     0.90
12–24 часа     0.80
1–2 дня        0.60
3–7 дней       0.30
7+ дней        0.10
```

И отдельно искать:

```text
срочно
сегодня
заселение сегодня
заехать можно
освободилась
с 1 августа
с завтрашнего дня
```

У таких объявлений нужно повышать priority.

---

# 18. Каналы я бы ранжировал так

### Tier A — парсить постоянно

```text
@kvartiry2
@kvartira_v_almaty
@arenda_kvartiry_almaty_kz
```

### Tier B — постоянно или каждые 5–10 минут

```text
@kvartiry222
@Arenda_Kvartira_Ala02
```

### Tier C — каждые 15–30 минут

```text
@tn_almaty
@kvartiram7
@arendakvartiralma
```

Причина — не только размер, но и **соотношение полезных rental posts / всего контента**.

---

# 19. Что я бы НЕ делал

Не стал бы делать:

```text
Telegram → regex → database
```

Слишком много потеряется.

Лучше:

```text
                    ┌─ text parser
Telegram ───────────┼─ OCR
                    ├─ image parser
                    ├─ contact parser
                    ├─ location parser
                    └─ language detector
                              ↓
                       normalization
                              ↓
                       deduplication
                              ↓
                       quality scoring
                              ↓
                         database
                              ↓
                     search / alerts
```

---

# 20. Идеальная карточка объявления

В итоге я бы показывал пользователю примерно:

> 🏠 **2-комнатная квартира — ЖК Алма Сити 4**
>
> 📍 Наурызбайский район
> 💰 **250 000 ₸/мес**
> 🧾 Депозит: 50 000 ₸, возвратный
> 💡 Коммунальные: отдельно
> 🛏 2 комнаты
> 📐 53 м²
> 🏢 7/12 этаж
> 🛋 Мебель + техника
> 🚇 ~15 мин до метро
> 📅 Заселение: с 1 сентября
> 👤 Собственник / не определено
>
> 📸 12 фото
> 🔗 3 источника
> 🕐 Опубликовано 18 минут назад
>
> **Реальная стоимость:** ~255–260K/мес
>
> [Открыть оригинал]

---

# 21. Я бы добавил quality score

Например:

```text
listing_quality = 0–100
```

### +20

Есть цена

### +15

Есть точная локация

### +15

Есть фото

### +10

Есть площадь

### +10

Есть количество комнат

### +10

Есть депозит

### +10

Есть условия коммунальных

### +5

Есть дата заселения

### +5

Есть контакт

И штрафы:

```text
-20 подозрение на риелтора
-20 duplicate
-15 слишком мало информации
-10 подозрительный текст
-10 только картинка без OCR
```

В результате пользователь видит не 500 сырых Telegram-постов, а:

> **17 релевантных вариантов**

---

# 22. Особенно рекомендую определять «собственник / риелтор»

В Telegram это критично.

Сигналы риелтора:

```text
комиссия
риелтор
агентство
АН
агент
услуги риелтора
комиссия 20%
комиссия 30%
```

Сигналы собственника:

```text
я собственник
собственник
сама хозяйка
сам хозяин
без посредников
без риелторов
```

Причем это не должно быть бинарным:

```text
owner_probability = 0.87
agent_probability = 0.13
```

---

# 23. Важный нюанс с API Telegram

Для технической реализации я бы использовал **официальный Telegram API + Telethon**, а не HTML-скрейпинг `t.me`.

Telegram API официально предоставляет методы получения истории сообщений; `messages.getHistory` возвращает сообщения канала, а Telethon предоставляет `iter_messages()` и `download_media()` для работы с сообщениями и медиа. ([Telegram][9])

[Telegram API — messages.getHistory](https://core.telegram.org/method/messages.getHistory?utm_source=chatgpt.com)

[Telethon documentation](https://docs.telethon.dev/en/stable/?utm_source=chatgpt.com)

При этом здесь есть важное ограничение: **Telegram сейчас прямо запрещает scraping/aggregation данных платформы для создания AI/ML datasets и обучения моделей**, а использование API должно соответствовать API Terms и Content Licensing Terms. Поэтому если задача — именно сервис поиска аренды/агрегатор с нормальным использованием данных, нужно проектировать сбор с учетом этих условий, прав на контент и минимизации персональных данных; я бы не строил на этих данных обучающий датасет. ([Telegram][10])

---

# 24. Мой рекомендуемый MVP

Если задача — **запустить это быстро**, я бы вообще не начинал с 8 каналов.

### Фаза 1

Только:

```text
@kvartiry2
@kvartira_v_almaty
@arenda_kvartiry_almaty_kz
```

Собирать:

```text
message_id
date
text
photos
channel
views
```

### Фаза 2

Добавить:

```text
price
rooms
area
district
address
 ЖК
deposit
utilities
available_from
contact
listing_type
owner/agent
```

### Фаза 3

Добавить:

```text
OCR
image dedup
text dedup
2GIS parsing
phone normalization
Kazakh NLP
```

### Фаза 4

Сделать:

```text
master listings
freshness score
quality score
price normalization
map
alerts
```

---

# 25. В итоге мой рейтинг

**Если цель — максимально много предложений:**

**1. `@kvartiry2` — 10/10**
Максимальный охват, огромное количество фото и объявлений. ([Telegram][1])

**2. `@kvartira_v_almaty` — 9.5/10**
Лучший баланс качества, структуры и объема; особенно хорош для подселения и комнат. ([Telemetr][2])

**3. `@arenda_kvartiry_almaty_kz` — 8.5/10**
Очень большой архив + хороший казахоязычный поток. ([Telemetr][3])

**4. `@kvartiry222` — 8/10**
Маленький, но очень целевой канал «без риэлторов». ([Telemetr][5])

**5. `@Arenda_Kvartira_Ala02` — 6/10**
Хороший дополнительный источник. ([Telegram][7])

**6. `@tn_almaty` — 6.5/10**
Полезен, но много продажи/коммерции. ([Telemetr][6])

---

### Главное

Если делать это **как нормальный rental intelligence system**, ценность будет не в самом Telegram-парсере. Она будет в четырех вещах:

**Telegram → extraction → deduplication → нормализованная карточка объекта.**

Особенно я бы вложился в **OCR фотографий + 2GIS + дедуп по телефону/фото + определение собственника + расчет реальной месячной стоимости**. Именно это превратит поток из десятков тысяч грязных сообщений в реально полезную базу квартир.

[1]: https://t.me/s/kvartiry2?before=170159&utm_source=chatgpt.com "Аренда квартира Алматы Подселение – Telegram"
[2]: https://telemetr.me/content/AAAAAEG-qMrZWxu5PPYWbA?utm_source=chatgpt.com "Telegram-канал \"Квартиры Алматы | Аренда квартир Алматы | Поиск соседей Алматы\" — kvartira_v_almaty — Telemetr - kvartira_v_almaty"
[3]: https://telemetr.me/content/arenda_kvartiry_almaty_kz?utm_source=chatgpt.com "Telegram-канал \"Аренда квартиры Алматы🏠\" — arenda_kvartiry_almaty_kz — Telemetr - arenda_kvartiry_almaty_kz"
[4]: https://telemetr.me/content/kvartira_v_almaty?utm_source=chatgpt.com "Telegram-канал \"Квартиры Алматы | Аренда квартир Алматы | Поиск соседей Алматы\" — kvartira_v_almaty — Telemetr - kvartira_v_almaty"
[5]: https://telemetr.me/content/almaty_kvartirabarma?utm_source=chatgpt.com "Telegram-канал \"Аренда квартиры Алматы БЕЗ РИЭЛТОРОВ\" — kvartiry222 — Telemetr - kvartiry222"
[6]: https://telemetr.me/content/tn_almaty?utm_source=chatgpt.com "Telegram-канал \"Твоя Недвижимость в Алматы\" — tn_almaty — Telemetr - tn_almaty"
[7]: https://t.me/Arenda_Kvartira_Ala02?utm_source=chatgpt.com "Telegram: View @Arenda_Kvartira_Ala02"
[8]: https://t.me/s/kvartira_v_almaty?before=34911&utm_source=chatgpt.com "Квартиры Алматы | Аренда квартир Алматы | Поиск соседей Алматы – Telegram"
[9]: https://core.telegram.org/method/messages.getHistory?utm_source=chatgpt.com "messages.getHistory"
[10]: https://core.telegram.org/api/terms?utm_source=chatgpt.com "Telegram API Terms of Service"
