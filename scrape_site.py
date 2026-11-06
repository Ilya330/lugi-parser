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
import urllib.error
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


# Заглушка антибот-защиты хостинга: с сентября 2026 сайт отдаёт её раннерам GitHub
# (IP Azure, США) с кодом 429 на ПЕРВЫЙ же запрос. Повторы её не снимают, а страница
# без блоков товаров иначе читалась бы как «товара нет на сайте».
BLOCK_TITLE_RE = re.compile(r"<title>\s*(Захищена сторінка|Защищенная страница)", re.IGNORECASE)


class SiteBlocked(RuntimeError):
    """Сайт отдал заглушку защиты вместо страницы — повторять бессмысленно."""


def _blocked_reason(status, body):
    if BLOCK_TITLE_RE.search(body[:4000]):
        return f"HTTP {status}: заглушка защиты хостинга «Захищена сторінка»"
    return None


def get(url, retries=3):
    import time
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=45, context=SSL_CTX) as r:
                body = r.read().decode("utf-8", "replace")
                reason = _blocked_reason(r.status, body)
                if reason:
                    raise SiteBlocked(reason)
                return body
        except SiteBlocked:
            raise
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace") if e.fp else ""
            reason = _blocked_reason(e.code, body)
            if reason:
                raise SiteBlocked(reason) from None
            last = f"HTTP {e.code} {e.reason}"
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"GET {url}: {last}")


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


def warn(title, msg):
    """В лог и, на раннере GitHub, аннотацией в сводку прогона (видно без чтения лога)."""
    print(f"  ВНИМАНИЕ: {title}: {msg}", file=sys.stderr)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning title={title}::{msg}", flush=True)


def scrape_many(skus, log_every=100):
    """Многопоточно. Возвращает (found, not_found, blocked):
    found     — {SKU_UPPER: data} успешно спарсенные;
    not_found — set(SKU_UPPER) ТОЧНО отсутствующих на сайте (поиск без результата);
    blocked   — None или причина, по которой сайт отдаёт заглушку защиты. Тогда скрейп
                прерывается сразу (не долбим сайт 20 минут впустую), остальные SKU
                считаются несделанными.
    Упавшие по сетевой ошибке НЕ попадают ни в found, ни в not_found (повтор в след. раз)."""
    import collections
    import threading
    import time

    found = {}
    not_found = set()
    reasons = collections.Counter()
    stop = threading.Event()
    blocked = []
    done = errors = 0

    def work(sku):
        if stop.is_set():
            return sku, None, None
        try:
            d = scrape_sku(sku)
        except SiteBlocked as e:
            blocked.append(str(e))
            stop.set()
            return sku, None, str(e)
        except Exception as e:  # noqa: BLE001
            # «GET <url>: <причина>» -> «<причина>», чтобы одинаковые сбои сложились.
            return sku, None, str(e).split(": ", 1)[-1] or type(e).__name__
        time.sleep(DELAY)
        return sku, d, None

    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for sku, d, err in ex.map(work, skus):
            done += 1
            key = sku.strip().upper()
            if d and d.get("name"):
                found[key] = d
            elif err:
                errors += 1
                reasons[err] += 1
            elif not stop.is_set():
                not_found.add(key)
            if done % log_every == 0 and not stop.is_set():
                print(f"  скрейп {done}/{len(skus)} … найдено {len(found)}, "
                      f"нет на сайте {len(not_found)}, ошибок {errors}",
                      file=sys.stderr)

    if blocked:
        skipped = len(skus) - len(found) - len(not_found) - errors
        warn("lugi.com.ua закрыт защитой",
             f"{blocked[0]}. Скрейп прерван: найдено {len(found)}, "
             f"не сделано {errors + skipped} из {len(skus)} (повтор в след. прогон)")
    elif errors:
        top = "; ".join(f"{r} ×{n}" for r, n in reasons.most_common(3))
        print(f"  сетевых ошибок (будут повторены позже): {errors} — {top}", file=sys.stderr)
        if errors == len(skus):
            warn("Скрейп lugi.com.ua: все запросы с ошибкой", top)
    return found, not_found, (blocked[0] if blocked else None)


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
