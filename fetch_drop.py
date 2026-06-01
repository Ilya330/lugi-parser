#!/usr/bin/env python3
"""Сбор дроп-цен с партнёрского портала Lugi.

Источник (логин НЕ нужен):
  GET https://partner.lugi.com.ua/products/get-data/?page=N&filters=<JSON>
  filters=[{"property":"manager_id","value":"2"},
           {"property":"group_name","value":"products-drop"}]

Возвращает JSON: total_pages, data[] по 100 строк. Поля строки:
  vendor_code, product_name, photo_url, category, quantity,
  price_rrc, price_drop, discount_price_drop.

Пишет drop.json: {VENDOR_CODE_UPPER: {...поля...}} — ключ нормализован (strip().upper()),
чтобы надёжно сопоставлять с <vendorCode> из XML-фида в build_feed.py.

Только stdlib.
"""
import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request


def make_ssl_context():
    """На macOS системный Python часто не видит корневые сертификаты.
    Пробуем certifi; если нет — отключаем проверку (источник публичный)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


SSL_CTX = make_ssl_context()

BASE = "https://partner.lugi.com.ua/products/get-data/"
MANAGER_ID = os.environ.get("MANAGER_ID", "2")
GROUP_NAME = os.environ.get("GROUP_NAME", "products-drop")
OUT = os.environ.get("DROP_JSON", os.path.join(os.path.dirname(__file__), "drop.json"))
DELAY = float(os.environ.get("DELAY", "0.2"))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# Поля, которые забираем из каждой строки прайс-листа.
FIELDS = ("vendor_code", "product_name", "photo_url", "category",
          "quantity", "price_rrc", "price_drop", "discount_price_drop")


def norm_key(code):
    return (code or "").strip().upper()


def build_filters_qs(page):
    filters = [
        {"property": "manager_id", "value": str(MANAGER_ID)},
        {"property": "group_name", "value": GROUP_NAME},
    ]
    qs = urllib.parse.urlencode({
        "page": page,
        "filters": json.dumps(filters, ensure_ascii=False),
    })
    return BASE + "?" + qs


def fetch_page(page, retries=4):
    url = build_filters_qs(page)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"https://partner.lugi.com.ua/products/{GROUP_NAME}?manager={MANAGER_ID}",
                "Accept": "application/json, text/javascript, */*; q=0.01",
            })
            with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"page {page} failed after {retries} tries: {last}")


def fetch_all():
    first = fetch_page(1)
    total_pages = int(first.get("total_pages", 1))
    manager = (first.get("manager_info") or {}).get("manager_name", "")
    print(f"manager={manager!r} total_pages={total_pages}", file=sys.stderr)

    result = {}
    dup = 0

    def absorb(rows):
        nonlocal dup
        for row in rows:
            key = norm_key(row.get("vendor_code"))
            if not key:
                continue
            if key in result:
                dup += 1
            result[key] = {k: row.get(k) for k in FIELDS}

    absorb(first.get("data", []))
    for page in range(2, total_pages + 1):
        data = fetch_page(page).get("data", [])
        absorb(data)
        if page % 10 == 0 or page == total_pages:
            print(f"  page {page}/{total_pages} … собрано {len(result)} артикулов",
                  file=sys.stderr)
        time.sleep(DELAY)

    print(f"Готово: {len(result)} уникальных артикулов (дублей по артикулу: {dup})",
          file=sys.stderr)
    return result


def main():
    data = fetch_all()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    # Пример строки для глаза.
    sample_key = next(iter(data), None)
    if sample_key:
        print("Пример:", sample_key, "->",
              json.dumps(data[sample_key], ensure_ascii=False), file=sys.stderr)
    print(f"Записано {len(data)} в {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
