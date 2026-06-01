#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Оркестратор конвейера Lugi: дроп-цены -> фид с опт-ценой -> Google Таблицы.

Шаги управляются переменными окружения (по умолчанию все включены):
  STEP_FETCH=1   собрать дроп-цены  (fetch_drop)   -> drop.json
  STEP_FEED=1    собрать XML-фид    (build_feed)    -> docs/feed.xml
  STEP_SHEETS=1  залить в таблицу   (to_sheets)     -> листы Дроп/Товари
                 (пропускается без SPREADSHEET_ID — это не ошибка)

Фид — главная цель проекта, поэтому сбой выгрузки в таблицу не валит весь прогон.
"""
import os
import sys
import traceback


def on(name, default="1"):
    return os.environ.get(name, default) == "1"


def main():
    if on("STEP_FETCH"):
        print(">>> Шаг 1/3: сбор дроп-цен", file=sys.stderr)
        import fetch_drop
        fetch_drop.main()

    if on("STEP_FEED"):
        print(">>> Шаг 2/3: сборка XML-фида", file=sys.stderr)
        import build_feed
        build_feed.main()

    if on("STEP_SHEETS"):
        if not os.environ.get("SPREADSHEET_ID"):
            print(">>> Шаг 3/3: выгрузка в таблицу ПРОПУЩЕНА (нет SPREADSHEET_ID)",
                  file=sys.stderr)
        else:
            print(">>> Шаг 3/3: выгрузка в Google Таблицу", file=sys.stderr)
            try:
                import to_sheets
                to_sheets.main()
            except Exception:  # noqa: BLE001
                print("ОШИБКА выгрузки в таблицу (фид собран, продолжаем):",
                      file=sys.stderr)
                traceback.print_exc()

    print(">>> Готово.", file=sys.stderr)


if __name__ == "__main__":
    main()
