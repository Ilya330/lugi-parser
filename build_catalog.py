#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Разовый сид мастер-каталога catalog.json (ключ = SKU.upper()).

Источники полной инфы (минимум скрейпинга):
  1) Фид поставщика (FEED_URL) — ~3403 товара, данные чистые (есть <param>).
  2) Скрейп сайта (scrape_site) — только SKU из дроп-листа, которых нет в фиде.
     По умолчанию скрейпим лишь те, что СЕЙЧАС в наличии (ONLY_INSTOCK=1); остальные
     будут до-скрейплены build_feed на лету, когда появятся в наличии.

Возобновляемо: уже присутствующие в catalog.json SKU повторно не трогаются.
Запуск: python3 build_catalog.py   (env WORKERS, DELAY, ONLY_INSTOCK, FEED_URL)
"""
import html
import json
import os
import re
import sys
import urllib.request

import scrape_site

HERE = os.path.dirname(os.path.abspath(__file__))
FEED_URL = os.environ.get(
    "FEED_URL",
    "https://feed.lugi.com.ua/index.php?route=extension/feed/unixml/ukr_ru_new")
DROP_JSON = os.environ.get("DROP_JSON", os.path.join(HERE, "drop.json"))
CATALOG = os.environ.get("CATALOG_JSON", os.path.join(HERE, "catalog.json"))
ONLY_INSTOCK = os.environ.get("ONLY_INSTOCK", "1") == "1"

OFFER_RE = re.compile(r"<offer\b.*?</offer>", re.DOTALL)


def _tag(block, tag):
    m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.DOTALL)
    if not m:
        return ""
    val = m.group(1)
    val = re.sub(r"^\s*<!\[CDATA\[", "", val)
    val = re.sub(r"\]\]>\s*$", "", val)
    return val.strip()


def catalog_from_feed():
    """Скачивает фид поставщика и строит {SKU: full data, source='feed'}."""
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": scrape_site.UA})
    with urllib.request.urlopen(req, timeout=180, context=scrape_site.SSL_CTX) as r:
        xml = r.read().decode("utf-8")
    out = {}
    for m in OFFER_RE.finditer(xml):
        b = m.group(0)
        code = html.unescape(_tag(b, "vendorCode")).strip()
        if not code:
            continue
        pics = re.findall(r"<picture>(.*?)</picture>", b, re.DOTALL)
        params = [[html.unescape(n).strip(), html.unescape(v).strip()]
                  for n, v in re.findall(r'<param name="(.*?)">(.*?)</param>', b, re.DOTALL)]
        out[code.upper()] = {
            "name": _tag(b, "name_ua") or _tag(b, "name"),
            "description": _tag(b, "description_ua") or _tag(b, "description"),
            "pictures": [p.strip() for p in pics if p.strip()],
            "params": params,
            "vendor": _tag(b, "vendor"),
            "source": "feed",
        }
    return out


def main():
    drop = json.load(open(DROP_JSON, encoding="utf-8"))

    catalog = {}
    if os.path.exists(CATALOG):
        catalog = json.load(open(CATALOG, encoding="utf-8"))
        print(f"Загружен существующий catalog.json: {len(catalog)}", file=sys.stderr)

    # 1) Сид из фида (не перезаписываем уже скрейпленные site-записи без нужды).
    feed_cat = catalog_from_feed()
    added_feed = 0
    for k, v in feed_cat.items():
        if k not in catalog:
            catalog[k] = v
            added_feed += 1
    print(f"Из фида: {len(feed_cat)} (новых добавлено {added_feed})", file=sys.stderr)

    # 2) Какие SKU дроп-листа ещё без полной инфы.
    def instock(v):
        return str(v.get("quantity", "0")).strip() not in ("", "0")

    gap = [k for k, v in drop.items()
           if k not in catalog and (instock(v) if ONLY_INSTOCK else True)]
    print(f"К скрейпу с сайта: {len(gap)} SKU "
          f"(ONLY_INSTOCK={ONLY_INSTOCK})", file=sys.stderr)

    if gap:
        scraped = scrape_site.scrape_many(gap)
        for k, d in scraped.items():
            d.setdefault("params", [])
            d["source"] = "site"
            catalog[k] = d
        print(f"Скрейп успешно: {len(scraped)} из {len(gap)} "
              f"(не найдено {len(gap) - len(scraped)})", file=sys.stderr)

    json.dump(catalog, open(CATALOG, "w", encoding="utf-8"), ensure_ascii=False)
    srcs = {}
    for v in catalog.values():
        srcs[v.get("source")] = srcs.get(v.get("source"), 0) + 1
    print(f"Итог catalog.json: {len(catalog)} (по источникам {srcs}) -> {CATALOG}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
