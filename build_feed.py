#!/usr/bin/env python3
"""Сборка итогового XML-фида Lugi с оптовой (дроп) ценой.

1. Берёт исходный полный фид поставщика (YML / OpenCart unixml), ~22 МБ, 3407 офферов.
2. Загружает карту дроп-цен из drop.json (см. fetch_drop.py), ключ = vendorCode.upper().
3. Точечно вставляет <vendorprice> (= price_drop) сразу после </vendorCode> в каждом оффере.
   Опционально добавляет <price_drop_discount> (= discount_price_drop).
   Остальное (CDATA-описания, категории, картинки, param) остаётся без изменений.
4. Пишет docs/feed.xml. Логирует matched / unmatched.

Только stdlib. Инъекция строковая — исходные байты офферов не пересобираются,
поэтому CDATA и спецсимволы сохраняются как есть.
"""
import html
import json
import os
import re
import ssl
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FEED_URL = os.environ.get(
    "FEED_URL",
    "https://feed.lugi.com.ua/index.php?route=extension/feed/unixml/ukr_ru_new",
)
DROP_JSON = os.environ.get("DROP_JSON", os.path.join(HERE, "drop.json"))
OUT = os.environ.get("FEED_OUT", os.path.join(HERE, "docs", "feed.xml"))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# Какое поле дроп-данных кладём в <vendorprice> (опт). Переключение цены — здесь.
FEED_OPT_FIELD = os.environ.get("FEED_OPT_FIELD", "price_drop")
# Дополнительно класть акционную дроп-цену отдельным тегом.
INCLUDE_DISCOUNT = os.environ.get("INCLUDE_DISCOUNT", "1") == "1"

OFFER_RE = re.compile(r"<offer\b.*?</offer>", re.DOTALL)
VENDORCODE_RE = re.compile(r"<vendorCode>(.*?)</vendorCode>", re.DOTALL)


def make_ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180, context=make_ssl_context()) as r:
        return r.read().decode("utf-8")


def num(val):
    """Нормализуем цену в строку без мусора; пусто -> None."""
    if val is None:
        return None
    s = str(val).strip().replace(",", ".")
    return s or None


def main():
    with open(DROP_JSON, encoding="utf-8") as f:
        drop = json.load(f)
    print(f"drop.json: {len(drop)} артикулов", file=sys.stderr)

    print(f"Скачиваю фид: {FEED_URL}", file=sys.stderr)
    xml = download(FEED_URL)
    print(f"  получено {len(xml.encode('utf-8'))} байт", file=sys.stderr)

    stats = {"offers": 0, "matched": 0, "no_code": 0, "no_price": 0, "unmatched": 0}

    def process_offer(m):
        block = m.group(0)
        stats["offers"] += 1
        cm = VENDORCODE_RE.search(block)
        if not cm:
            stats["no_code"] += 1
            return block
        code_raw = html.unescape(cm.group(1)).strip()
        rec = drop.get(code_raw.upper())
        if not rec:
            stats["unmatched"] += 1
            return block
        opt = num(rec.get(FEED_OPT_FIELD))
        if opt is None:
            stats["no_price"] += 1
            return block
        inject = f"\n<vendorprice>{opt}</vendorprice>"
        if INCLUDE_DISCOUNT:
            disc = num(rec.get("discount_price_drop"))
            if disc is not None:
                inject += f"\n<price_drop_discount>{disc}</price_drop_discount>"
        stats["matched"] += 1
        # Вставляем сразу после закрывающего тега </vendorCode>.
        return block[:cm.end()] + inject + block[cm.end():]

    out = OFFER_RE.sub(process_offer, xml)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(out)

    print(
        "Итого: offers={offers} matched={matched} unmatched={unmatched} "
        "no_code={no_code} no_price={no_price}".format(**stats),
        file=sys.stderr,
    )
    print(f"Записан фид: {OUT} ({len(out.encode('utf-8'))} байт)", file=sys.stderr)


if __name__ == "__main__":
    main()
