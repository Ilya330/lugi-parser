#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сборка итогового XML-фида Lugi: ТОЛЬКО товары в наличии и с опт-ценой.

Источники:
  drop.json     — наличие, количество, РРЦ, дроп-цены (свежее каждый прогон).
  catalog.json  — полная инфа (название, описание, фото, характеристики), мастер-кеш.

Логика прогона (скрейпинг почти не нужен):
  - берём SKU, которые В НАЛИЧИИ и имеют опт-цену;
  - полную инфу берём из catalog.json;
  - НОВЫЙ SKU (нет в catalog и нет в not_on_site) → до-скрейпиваем на лету:
      найден  → дописываем в catalog.json;
      не найден → дописываем в not_on_site.json (больше не пытаемся);
  - для SKU без карточки на сайте — fallback на базовую инфу из drop.json
      (название, одно фото, категория), без описания/характеристик.

Формат оффера — YML как у поставщика: <price>=РРЦ, <vendorprice>=опт.
Опт-цена: первое непустое из FEED_OPT_FIELDS (деф: discount_price_drop, price_drop).
"""
import html as html_mod
import json
import os
import sys
import zlib

import scrape_site

HERE = os.path.dirname(os.path.abspath(__file__))
DROP_JSON = os.environ.get("DROP_JSON", os.path.join(HERE, "drop.json"))
CATALOG = os.environ.get("CATALOG_JSON", os.path.join(HERE, "catalog.json"))
NOT_ON_SITE = os.environ.get("NOT_ON_SITE_JSON", os.path.join(HERE, "not_on_site.json"))
OUT = os.environ.get("FEED_OUT", os.path.join(HERE, "docs", "feed.xml"))

FEED_OPT_FIELDS = [s.strip() for s in os.environ.get(
    "FEED_OPT_FIELDS", "discount_price_drop,price_drop").split(",") if s.strip()]
# Ленивый до-скрейп новых товаров на лету (0 — выключить, только кеш+fallback).
LAZY_SCRAPE = os.environ.get("LAZY_SCRAPE", "1") == "1"


def esc(s):
    return html_mod.escape(str(s or ""), quote=True)


def num(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", ".")
    return s or None


def opt_price(rec):
    for f in FEED_OPT_FIELDS:
        x = num(rec.get(f))
        if x is not None:
            return x
    return None


def instock(rec):
    return str(rec.get("quantity", "0")).strip() not in ("", "0")


def load_json(path, default):
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    return default


def main():
    drop = load_json(DROP_JSON, {})
    catalog = load_json(CATALOG, {})
    not_on_site = set(load_json(NOT_ON_SITE, []))

    # Целевой набор: в наличии И есть опт-цена.
    targets = {k: v for k, v in drop.items() if instock(v) and opt_price(v) is not None}
    print(f"Целевых SKU (в наличии + опт): {len(targets)}", file=sys.stderr)

    # Новые SKU без полной инфы — попробовать до-скрейпить.
    to_scrape = [k for k in targets if k not in catalog and k not in not_on_site]
    if to_scrape and LAZY_SCRAPE:
        print(f"Новых к до-скрейпу: {len(to_scrape)}", file=sys.stderr)
        found = scrape_site.scrape_many(to_scrape)
        for k, d in found.items():
            d.setdefault("params", [])
            d["source"] = "site"
            catalog[k] = d
        missing = [k for k in to_scrape if k not in found]
        not_on_site |= set(missing)
        json.dump(catalog, open(CATALOG, "w", encoding="utf-8"), ensure_ascii=False)
        json.dump(sorted(not_on_site), open(NOT_ON_SITE, "w", encoding="utf-8"),
                  ensure_ascii=False)
        print(f"  до-скрейплено {len(found)}, без карточки {len(missing)}",
              file=sys.stderr)

    # Категории (синтетические id из названий drop).
    cat_names = sorted({(v.get("category") or "").strip()
                        for v in targets.values() if (v.get("category") or "").strip()})
    cat_id = {name: i + 1 for i, name in enumerate(cat_names)}

    # Сборка офферов.
    offers = []
    stats = {"feed": 0, "site": 0, "drop_fallback": 0}
    for sku, d in targets.items():
        info = catalog.get(sku)
        if info:
            name = info.get("name") or d.get("product_name") or ""
            desc = info.get("description") or ""
            pics = info.get("pictures") or []
            params = info.get("params") or []
            vendor = info.get("vendor") or ""
            url = info.get("url") or ""
            stats[info.get("source", "feed")] = stats.get(info.get("source", "feed"), 0) + 1
        else:
            # Fallback на базовую инфу из дроп-листа (дропшип без карточки).
            name = d.get("product_name") or ""
            desc = ""
            pics = [d["photo_url"]] if d.get("photo_url") else []
            params, vendor, url = [], "", ""
            stats["drop_fallback"] += 1

        rrc = num(d.get("price_rrc"))
        opt = opt_price(d)
        qty = str(d.get("quantity", "")).strip()
        cname = (d.get("category") or "").strip()

        parts = [f'<offer id="{zlib.crc32(sku.encode())}" available="true">']
        parts.append(f"<name>{esc(name)}</name>")
        if url:
            parts.append(f"<url>{esc(url)}</url>")
        if rrc is not None:
            parts.append(f"<price>{rrc}</price>")
        parts.append(f"<vendorprice>{opt}</vendorprice>")
        parts.append("<currencyId>UAH</currencyId>")
        if cname in cat_id:
            parts.append(f"<categoryId>{cat_id[cname]}</categoryId>")
        for p in pics:
            parts.append(f"<picture>{esc(p)}</picture>")
        if vendor:
            parts.append(f"<vendor>{esc(vendor)}</vendor>")
        if desc:
            safe = desc.replace("]]>", "]]&gt;")
            parts.append(f"<description><![CDATA[{safe}]]></description>")
        if qty:
            parts.append(f"<quantity_in_stock>{qty}</quantity_in_stock>")
        parts.append(f"<vendorCode>{esc(sku)}</vendorCode>")
        for n, val in params:
            parts.append(f'<param name="{esc(n)}">{esc(val)}</param>')
        parts.append("</offer>")
        offers.append("\n".join(parts))

    # Документ YML.
    from datetime import datetime, timezone
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    cats_xml = "\n".join(f'<category id="{cat_id[n]}">{esc(n)}</category>' for n in cat_names)
    doc = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<yml_catalog date="{date}">\n<shop>\n'
        '<name>Lugi</name>\n<company>Lugi</company>\n'
        '<url>https://lugi.com.ua/</url>\n'
        '<currencies><currency id="UAH" rate="1"/></currencies>\n'
        f"<categories>\n{cats_xml}\n</categories>\n"
        "<offers>\n" + "\n".join(offers) + "\n</offers>\n"
        "</shop>\n</yml_catalog>\n"
    )

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(doc)

    print(f"Офферов в фиде: {len(offers)} (из фида={stats['feed']} "
          f"скрейп-сайт={stats['site']} fallback-drop={stats['drop_fallback']})",
          file=sys.stderr)
    print(f"Категорий: {len(cat_names)} | Записан: {OUT} "
          f"({len(doc.encode('utf-8'))} байт)", file=sys.stderr)


if __name__ == "__main__":
    main()
