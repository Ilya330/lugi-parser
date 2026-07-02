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
NOT_ON_SITE = os.environ.get("NOT_ON_SITE_JSON", os.path.join(HERE, "not_on_site.json"))
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
            "name": _tag(b, "name") or _tag(b, "name_ua"),
            "description": _tag(b, "description") or _tag(b, "description_ua"),
            "pictures": [p.strip() for p in pics if p.strip()],
            "params": params,
            "vendor": _tag(b, "vendor"),
            "source": "feed",
        }
    return out


def instock(v):
    return str(v.get("quantity", "0")).strip() not in ("", "0")


def main():
    drop = json.load(open(DROP_JSON, encoding="utf-8"))

    # REFRESH=1 — чистый пересбор: актуальный фид + пере-скрейп ВСЕХ нужных карточек
    # (обновляет фото/описания/характеристики, перепроверяет «нет на сайте»),
    # без накопления устаревших записей. При сетевой ошибке страхуемся старыми данными.
    # Иначе — инкрементально: добавляем только новое, существующее не трогаем.
    refresh = os.environ.get("REFRESH", "0") == "1"

    old = json.load(open(CATALOG, encoding="utf-8")) if os.path.exists(CATALOG) else {}
    not_on_site = set(json.load(open(NOT_ON_SITE, encoding="utf-8"))) \
        if os.path.exists(NOT_ON_SITE) else set()

    feed_cat = catalog_from_feed()
    print(f"Из фида: {len(feed_cat)}", file=sys.stderr)

    if refresh:
        print("РЕЖИМ REFRESH: чистый пересбор каталога", file=sys.stderr)
        # Каталог = свежий фид + заново скрейпленные site-товары.
        catalog = dict(feed_cat)
        # Скрейпим все in-stock SKU, которых нет в фиде (site + перепроверка not_on_site).
        to_scrape = sorted(k for k, v in drop.items()
                           if k not in feed_cat
                           and (instock(v) if ONLY_INSTOCK else True))
        not_on_site = set()  # пересоберём заново
    else:
        # Инкрементально: сохраняем всё старое, обновляем фид, добавляем только новое.
        catalog = dict(old)
        catalog.update(feed_cat)
        to_scrape = [k for k, v in drop.items()
                     if k not in catalog and k not in not_on_site
                     and (instock(v) if ONLY_INSTOCK else True)]

    print(f"К скрейпу с сайта: {len(to_scrape)} SKU (REFRESH={refresh})", file=sys.stderr)

    if to_scrape:
        found, clean_not_found = scrape_site.scrape_many(to_scrape)
        for k, d in found.items():
            d.setdefault("params", [])
            d["source"] = "site"
            catalog[k] = d
            not_on_site.discard(k)
        # Точно отсутствующие на сайте -> skip-лист.
        not_on_site |= clean_not_found
        # Упавшие по сетевой ошибке (ни найдены, ни точно-нет): страхуемся старыми
        # данными из прежнего каталога, чтобы не потерять товар из-за сбоя.
        errored = set(to_scrape) - set(found) - clean_not_found
        rescued = 0
        for k in errored:
            if k in old and old[k].get("source") == "site":
                catalog[k] = old[k]
                rescued += 1
        print(f"Скрейп: найдено {len(found)}, нет на сайте {len(clean_not_found)}, "
              f"сетевых сбоев {len(errored)} (из них спасено старыми данными {rescued})",
              file=sys.stderr)

    json.dump(catalog, open(CATALOG, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(sorted(not_on_site), open(NOT_ON_SITE, "w", encoding="utf-8"),
              ensure_ascii=False)
    srcs = {}
    for v in catalog.values():
        srcs[v.get("source")] = srcs.get(v.get("source"), 0) + 1
    print(f"Итог catalog.json: {len(catalog)} (по источникам {srcs}); "
          f"not_on_site: {len(not_on_site)} -> {CATALOG}", file=sys.stderr)


if __name__ == "__main__":
    main()
