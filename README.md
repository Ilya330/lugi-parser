# Lugi parser

Полный XML-фид товаров поставщика **Lugi** с оптовой (дроп) ценой, автообновление 4×/сутки.

## Зачем

У поставщика данные разнесены:
- **Полный фид** (описания, фото, категории) — есть, но **без опт-цен**:
  `https://feed.lugi.com.ua/index.php?route=extension/feed/unixml/ukr_ru_new`
- **Дроп-цены** — только в партнёрском прайс-листе (без логина):
  `https://partner.lugi.com.ua/products/get-data/?page=N&filters=...`

Скрипт собирает дроп-цены, сопоставляет с полным фидом **по артикулу** (`vendorCode`),
впрыскивает опт-цену в каждый оффер и публикует готовый фид.

## Конвейер

| Скрипт | Что делает | Результат |
|---|---|---|
| `fetch_drop.py` | обходит все страницы прайс-листа | `drop.json` `{АРТИКУЛ: {цены...}}` |
| `build_feed.py` | качает полный фид, впрыскивает `<vendorprice>` | `docs/feed.xml` |
| `to_sheets.py` | пишет листы «Дроп» и «Товари» в Google Таблицу | — |
| `run.py` | запускает всё по порядку (управление через env) | — |

В фиде: `<price>` = РРЦ (розница), `<vendorprice>` = опт (дроп). Совместимо с Prom.ua YML.

## Локальный запуск

```bash
pip3 install -r requirements.txt
python3 run.py                                  # фид + (если задан ID) таблица
# или по шагам:
python3 fetch_drop.py
python3 build_feed.py
SPREADSHEET_ID=<id> python3 to_sheets.py
```

Проверка фида: `xmllint --noout docs/feed.xml` и `grep -c '<vendorprice>' docs/feed.xml`.

## Переменные окружения

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `MANAGER_ID` | `2` | менеджер в прайс-листе |
| `GROUP_NAME` | `products-drop` | тип цен (`products-drop-pro`, `products-wholesale` …) |
| `FEED_OPT_FIELD` | `price_drop` | какое поле дроп-данных кладём в `<vendorprice>` |
| `INCLUDE_DISCOUNT` | `1` | дополнительно класть `<price_drop_discount>` |
| `SPREADSHEET_ID` | — | id Google Таблицы (без него выгрузка пропускается) |

**Сменить опт-цену в фиде** на акционную: `FEED_OPT_FIELD=discount_price_drop`.

## Google Таблица (разовая настройка)

Сервис-аккаунт не может создавать таблицы сам. Создай пустую Google Таблицу,
расшарь её на `sheets-bot@gallary-434015.iam.gserviceaccount.com` (Редактор),
возьми `SPREADSHEET_ID` из URL. Ключ — `service_account.json` (в репозиторий не коммитится).

## Автообновление (GitHub Actions + Pages)

`.github/workflows/update.yml` гоняет `run.py` по cron `0 */6 * * *` (4×/сутки) и коммитит
`docs/feed.xml`. Настройка:

1. Создать репозиторий и запушить проект.
2. **Settings → Pages**: source = `Deploy from a branch`, branch = `main`, folder = `/docs`.
3. **Settings → Secrets and variables → Actions** добавить:
   - `GOOGLE_SA_JSON` — всё содержимое `service_account.json`;
   - `SPREADSHEET_ID` — id таблицы.
4. **Actions → Update Lugi feed → Run workflow** — ручная проверка.

Стабильная ссылка на фид: `https://<user>.github.io/lugi-parser/feed.xml`.
