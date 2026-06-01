#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Скрейп полной карточки товара с lugi.com.ua по артикулу (SKU).

Логин НЕ нужен. Путь: /search/?search=SKU -> ссылка на карточку -> парс карточки.
Со страницы берём: название (<h1>), описание (блок div.opis, включает характеристики),
фото (галерея thumbnail главного товара), признак наличия. Артикул/категория/цены —
из дроп-листа (тут не нужны), они передаются отдельно при сборке фида.

Используется build_catalog.py (разовый сид) и build_feed.py (ленивый до-скрейп новых).
Только stdlib. SSL-контекст как в fetch_drop.py.
"""
import concurrent.futures as cf
import html
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request

SITE = "https://lugi.com.ua"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"
WORKERS = int(os.environ.get("WORKERS", "8"))
DELAY = float(os.environ.get("DELAY", "0.1"))
# Язык сайта: ru -> поиск/карточки на русском (/ru/...). uk -> украинский.
LANG = os.environ.get("SITE_LANG", "ru")
SEARCH_URL = (f"{SITE}/ru/search/?search=" if LANG == "ru"
              else f"{SITE}/search/?search=")

# Маркер начала блока «Схожі товари» — всё после него (связанные товары) игнорируем.
RELATED_MARKER = "Схожі товари"
# Якоря главной галереи помечены data-fancybox="gallery"; берём из них href с картинкой.
GALLERY_RE = re.compile(
    r'<a\b[^>]*data-fancybox="gallery"[^>]*href="(https://lugi\.com\.ua/image/cache/[^"]+?\.(?:jpg|jpeg|png|webp))"',
    re.IGNORECASE)
# Фолбэк: любые якоря-thumbnail с картинкой из /image/cache/.
THUMB_RE = re.compile(
    r'<a\b[^>]*class="[^"]*thumbnail[^"]*"[^>]*href="(https://lugi\.com\.ua/image/cache/[^"]+?\.(?:jpg|jpeg|png|webp))"',
    re.IGNORECASE)


def make_ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


SSL_CTX = make_ssl_context()


def get(url, retries=3):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=45, context=SSL_CTX) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            import time
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"GET failed {url}: {last}")


def _product_href(block):
    """Ссылка на карточку внутри блока результата. uk: /<slug>/, ru: /ru/ru-<slug>/."""
    for href in re.findall(r'href="(https://lugi\.com\.ua/[^"#]+)"', block):
        if re.search(r'(route=|/index\.php|/image/|/search|/specials)', href):
            continue
        path = href[len(SITE):].strip("/")
        # отбрасываем префикс языка, ожидаем одиночный slug карточки
        if path.startswith("ru/"):
            path = path[3:]
        if path and "/" not in path:
            return href
    return None


def find_product_url(sku):
    """Ищем товар по артикулу. Возвращаем URL карточки строго при совпадении
    product-model с артикулом; иначе None (товара нет на сайте)."""
    url = SEARCH_URL + urllib.parse.quote(sku)
    h = get(url)
    # Разбиваем на блоки результатов. Если их нет — товар не найден.
    blocks = re.split(r'(?=product-layout product-grid)', h)
    blocks = [b for b in blocks if b.startswith("product-layout product-grid")]
    if not blocks:
        return None
    want = sku.strip().upper()
    first_href = None
    for b in blocks:
        mm = re.search(r'product-model"[^>]*>\s*([^<]+)', b)
        model = html.unescape(mm.group(1)).strip().upper() if mm else ""
        href = _product_href(b)
        if href and first_href is None:
            first_href = href
        if href and model == want:
            return href
    # Точного совпадения модели нет, но единственный результат — принимаем его.
    return first_href if len(blocks) == 1 else None


def parse_card(h):
    # Название
    nm = re.search(r'<h1[^>]*>(.*?)</h1>', h, re.DOTALL)
    name = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', nm.group(1)))).strip() if nm else ""

    # Регион главного товара (до «Схожі товари»)
    cut = h.find(RELATED_MARKER)
    main = h[:cut] if cut > 0 else h

    # Описание: содержимое div.opis (включает списки характеристик).
    desc = ""
    dm = re.search(r'<div class="opis">(.*)', main, re.DOTALL)
    if dm:
        tail = dm.group(1)
        # обрезаем на служебных блоках, которые идут после описания
        for stop in ('<div class="harakteristiki-plus"', '<div class="mobile-container"',
                     '<div id="iframe-alive"', '<div class="vidguk'):
            i = tail.find(stop)
            if i != -1:
                tail = tail[:i]
        # Берём внутренность обёртки text-opis, убираем хвостовые пустые </div>.
        inner = re.search(r'<div class="text-opis">(.*)', tail, re.DOTALL)
        desc = (inner.group(1) if inner else tail)
        desc = re.sub(r'(\s*</div>\s*)+$', '', desc).strip()

    # Фото: главная галерея (data-fancybox="gallery"), фолбэк — thumbnail-якоря.
    hits = GALLERY_RE.findall(main) or THUMB_RE.findall(main)
    pics, seen = [], set()
    for img in hits:
        if img not in seen:
            seen.add(img)
            pics.append(img)

    # Характеристики: таблица div#tab-specification (блоки short-attribute).
    params = []
    spec = re.search(r'id="tab-specification"(.*)', main, re.DOTALL)
    region = spec.group(1) if spec else main
    for blk in re.findall(r'<div class="short-attribute">(.*?)</div>', region, re.DOTALL):
        nm = re.search(r'attr-name"[^>]*>\s*<span>(.*?)</span>', blk, re.DOTALL)
        vt = re.search(r'attr-text"[^>]*>\s*<span>(.*?)</span>', blk, re.DOTALL)
        if not (nm and vt):
            continue
        n = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', nm.group(1)))).strip()
        v = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', vt.group(1)))).strip()
        if n and v:
            params.append([n, v])

    return {"name": name, "description": desc, "pictures": pics, "params": params}


def scrape_sku(sku):
    """Полный скрейп по артикулу. Возвращает dict или None (не найден)."""
    url = find_product_url(sku)
    if not url:
        return None
    data = parse_card(get(url))
    data["url"] = url
    return data


def scrape_many(skus, log_every=100):
    """Многопоточно. Возвращает {SKU_UPPER: data} только для найденных."""
    out = {}
    done = [0]

    def work(sku):
        import time
        try:
            d = scrape_sku(sku)
        except Exception as e:  # noqa: BLE001
            d = None
            print(f"  ! {sku}: {e}", file=sys.stderr)
        time.sleep(DELAY)
        return sku, d

    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for sku, d in ex.map(work, skus):
            done[0] += 1
            if d and d.get("name"):
                out[sku.strip().upper()] = d
            if done[0] % log_every == 0:
                print(f"  скрейп {done[0]}/{len(skus)} … найдено {len(out)}",
                      file=sys.stderr)
    return out


if __name__ == "__main__":
    import json
    for sku in (sys.argv[1:] or ["HP-N50B"]):
        d = scrape_sku(sku)
        if not d:
            print(f"{sku}: НЕ НАЙДЕН", file=sys.stderr)
            continue
        print(f"=== {sku} ===")
        print("url:", d["url"])
        print("name:", d["name"])
        print("pictures:", len(d["pictures"]), d["pictures"][:4])
        print("description (300):", d["description"][:300].replace("\n", " "))
