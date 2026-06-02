#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Выгрузка данных Lugi в Google Таблицу (два листа).

Лист "Дроп"   — из drop.json (партнёрский прайс-лист):
  Артикул | Назва товару | Зображення | Категорія | Кількість | Ціна РРЦ |
  Ціна Дроп | Ціна Дроп зі знижкою

Лист "Товари" — полные товары из итогового docs/feed.xml, с подтянутой опт-ценой:
  Артикул | Назва товару | Опис | Зображення | Категорія | Кількість |
  Ціна РРЦ | Ціна опт | Посилання
(сопоставление по артикулу уже сделано в build_feed.py — берём <vendorprice>).

Доступ: service_account.json (sheets-bot@gallary-434015), таблица расшарена ему.
Запуск:  SPREADSHEET_ID=xxxx python3 to_sheets.py
"""
import json
import os
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
SA_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON",
                         os.path.join(HERE, "service_account.json"))
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "Lugi товари")
DROP_JSON = os.environ.get("DROP_JSON", os.path.join(HERE, "drop.json"))
FEED_OUT = os.environ.get("FEED_OUT", os.path.join(HERE, "docs", "feed.xml"))
NOT_ON_SITE = os.environ.get("NOT_ON_SITE_JSON", os.path.join(HERE, "not_on_site.json"))

CELL_LIMIT = 50000
DROP_HEADERS = ["Артикул", "Назва товару", "Зображення", "Категорія",
                "Кількість", "Ціна РРЦ", "Ціна Дроп", "Ціна Дроп зі знижкою"]
TOVAR_HEADERS = ["Артикул", "Назва товару", "Опис", "Зображення", "Категорія",
                 "Кількість", "Ціна РРЦ", "Ціна опт", "Посилання"]
NOSITE_HEADERS = ["Артикул", "Назва товару", "Категорія", "Кількість", "Ціна РРЦ",
                  "Ціна Дроп", "Ціна Дроп зі знижкою", "Зображення", "Пошук на сайті"]


def build_drop_rows():
    with open(DROP_JSON, encoding="utf-8") as f:
        drop = json.load(f)
    rows = []
    for rec in drop.values():
        rows.append([
            rec.get("vendor_code") or "",
            rec.get("product_name") or "",
            rec.get("photo_url") or "",
            rec.get("category") or "",
            rec.get("quantity") or "",
            rec.get("price_rrc") or "",
            rec.get("price_drop") or "",
            rec.get("discount_price_drop") or "",
        ])
    return rows


def build_nosite_rows():
    """Товары в наличии, которых НЕ нашлось на сайте (для ручной проверки)."""
    if not os.path.exists(NOT_ON_SITE):
        return []
    skus = json.load(open(NOT_ON_SITE, encoding="utf-8"))
    drop = json.load(open(DROP_JSON, encoding="utf-8"))
    rows = []
    for sku in skus:
        rec = drop.get(sku)
        if not rec:
            continue
        rows.append([
            rec.get("vendor_code") or sku,
            rec.get("product_name") or "",
            rec.get("category") or "",
            rec.get("quantity") or "",
            rec.get("price_rrc") or "",
            rec.get("price_drop") or "",
            rec.get("discount_price_drop") or "",
            rec.get("photo_url") or "",
            "https://lugi.com.ua/search/?search=" + (rec.get("vendor_code") or sku),
        ])
    return rows


def _text(el, tag):
    child = el.find(tag)
    return (child.text or "").strip() if child is not None and child.text else ""


def build_tovar_rows():
    """Парсит итоговый feed.xml (с уже впрыснутой <vendorprice>)."""
    tree = ET.parse(FEED_OUT)
    root = tree.getroot()
    shop = root.find("shop")

    cat_names = {}
    cats = shop.find("categories")
    if cats is not None:
        for c in cats.findall("category"):
            cat_names[c.get("id")] = (c.text or "").strip()

    rows = []
    offers = shop.find("offers")
    for off in offers.findall("offer"):
        # Название: предпочитаем украинское.
        name = _text(off, "name_ua") or _text(off, "name")
        desc = _text(off, "description_ua") or _text(off, "description")
        pics = [p.text.strip() for p in off.findall("picture") if p.text]
        cat_id = _text(off, "categoryId")
        rows.append([
            _text(off, "vendorCode"),
            name,
            desc,
            ", ".join(pics),
            cat_names.get(cat_id, cat_id),
            _text(off, "quantity_in_stock"),
            _text(off, "price"),
            _text(off, "vendorprice"),
            _text(off, "url"),
        ])
    return rows


def clip(rows):
    return [[(c[:CELL_LIMIT] if isinstance(c, str) else c) for c in row]
            for row in rows]


def write_sheet(sh, gspread, title, headers, rows):
    table = [headers] + clip(rows)
    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=len(table) + 10, cols=len(headers))
    ws.clear()
    ws.resize(rows=max(len(table), 1), cols=len(headers))
    ws.update(range_name="A1", values=table, value_input_option="RAW")
    print(f"  лист '{title}': {len(rows)} строк (+ заголовок)", file=sys.stderr)


def main():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print("Установи: pip3 install gspread google-auth", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(SA_JSON):
        print(f"Нет ключа: {SA_JSON}", file=sys.stderr)
        sys.exit(1)

    drop_rows = build_drop_rows()
    tovar_rows = build_tovar_rows()
    nosite_rows = build_nosite_rows()

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(SA_JSON, scopes=scopes)
    gc = gspread.authorize(creds)

    if not SPREADSHEET_ID:
        print("Не задан SPREADSHEET_ID. Создай пустую таблицу, расшарь её на\n"
              "  sheets-bot@gallary-434015.iam.gserviceaccount.com (Редактор)\n"
              "и запусти: SPREADSHEET_ID=<id> python3 to_sheets.py", file=sys.stderr)
        sys.exit(1)

    sh = gc.open_by_key(SPREADSHEET_ID)
    # Удаляем старый лист с прежним названием, если остался.
    try:
        sh.del_worksheet(sh.worksheet("Немає на сайті"))
    except gspread.WorksheetNotFound:
        pass

    write_sheet(sh, gspread, "Дроп", DROP_HEADERS, drop_rows)
    write_sheet(sh, gspread, "Товари", TOVAR_HEADERS, tovar_rows)
    write_sheet(sh, gspread, "Немає в наявності", NOSITE_HEADERS, nosite_rows)
    print(f"Готово. Таблица: {sh.url}", file=sys.stderr)


if __name__ == "__main__":
    main()
