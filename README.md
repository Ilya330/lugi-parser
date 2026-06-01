# Lugi parser

Полный XML-фид товаров поставщика **Lugi** — **только товары в наличии и с оптовой (дроп)
ценой**, с полной информацией (название, описание, фото, характеристики). Автообновление
4×/сутки. Цены и наличие берутся из прайс-листа, полная инфа — из мастер-кеша каталога.

## Идея

Источники:
- **Наличие/цены** — партнёрский прайс-лист (без логина):
  `https://partner.lugi.com.ua/products/get-data/?page=N&filters=...`
- **Полная инфа** — мастер-кеш `catalog.json`, собранный ОДИН раз из:
  - фида поставщика `https://feed.lugi.com.ua/...` (~3400 товаров, чистые данные с `<param>`);
  - скрейпа сайта `https://lugi.com.ua/` для товаров, которых нет в фиде (поиск
    `/search/?search=<артикул>` → карточка → название/описание/фото).

Ключ сопоставления везде — **артикул** (`vendorCode` / `vendor_code`). Дальше каждый прогон
обновляет только наличие и цены; новый товар до-скрейпивается на лету; описания/фото
не перечитываются (меняются редко).

## Конвейер

| Скрипт | Что делает | Результат |
|---|---|---|
| `fetch_drop.py` | прайс-лист (все страницы) | `drop.json` наличие+цены |
| `build_catalog.py` | **разовый** сид кеша (фид + скрейп пробела) | `catalog.json` |
| `scrape_site.py` | скрейп карточки по артикулу | (модуль) |
| `build_feed.py` | drop + catalog → фид только в наличии+опт | `docs/feed.xml` |
| `to_sheets.py` | листы «Дроп» и «Товари» в Google Таблицу | — |
| `run.py` | рекуррентный прогон: fetch_drop → build_feed → to_sheets | — |

Файлы-кеши (коммитятся в репо): `catalog.json` (полная инфа), `not_on_site.json`
(артикулы, которых нет на сайте — чтобы не искать их повторно).

В фиде: `<price>` = РРЦ, `<vendorprice>` = опт. Опт-цена — первое непустое из
`FEED_OPT_FIELDS` (деф `discount_price_drop,price_drop`).

## Локальный запуск

```bash
pip3 install -r requirements.txt
python3 build_catalog.py                 # ОДИН раз: собрать catalog.json
python3 run.py                           # рекуррентно: drop -> feed -> (sheets)
SPREADSHEET_ID=<id> python3 to_sheets.py # выгрузка в таблицу
```

Проверка: `xmllint --noout docs/feed.xml`, `grep -c '<offer ' docs/feed.xml`.

## Переменные окружения

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `MANAGER_ID` / `GROUP_NAME` | `2` / `products-drop` | прайс-лист |
| `FEED_OPT_FIELDS` | `discount_price_drop,price_drop` | приоритет полей для `<vendorprice>` |
| `LAZY_SCRAPE` | `1` | до-скрейп новых товаров в build_feed |
| `ONLY_INSTOCK` | `1` | build_catalog скрейпит только то, что в наличии |
| `WORKERS` / `DELAY` | `8` / `0.1` | многопоточность скрейпа |
| `SPREADSHEET_ID` | — | id Google Таблицы (без него выгрузка пропускается) |

## Автообновление (GitHub Actions + Pages)

`.github/workflows/update.yml` гоняет `run.py` по cron `0 */6 * * *` (4×/сутки) и коммитит
`docs/feed.xml`, `catalog.json`, `not_on_site.json`. Секреты Actions: `GOOGLE_SA_JSON`,
`SPREADSHEET_ID`. Ссылка на фид: `https://<user>.github.io/lugi-parser/feed.xml`.
Pages: Settings → Pages → branch `main`, папка `/docs`.
