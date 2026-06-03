#!/usr/bin/env python3
"""一次性腳本：重新爬取所有來源，過 filter，broadcast 送出，不更新 state。"""

import sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])

from scraper import SOURCES, GLOBAL_FILTER, CONFIG, passes_filters, is_within_date_window, build_line_messages, push_in_batches
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

results = {}
for src in SOURCES:
    name = src["name"]
    log.info(f"抓取：{name}")
    try:
        items = src["fn"]()
        notify = [i for i in items if passes_filters(i) and is_within_date_window(i)]
        results[name] = {"all": items, "new": notify, "notify": notify, "error": None}
        log.info(f"  → 共 {len(items)} 筆，符合推播 {len(notify)} 筆")
    except Exception as e:
        log.error(f"  → 例外：{e}")
        results[name] = {"all": [], "new": [], "notify": [], "error": str(e)}

messages = build_line_messages(results, "補發")
if messages:
    push_in_batches(messages)
    log.info("完成")
else:
    log.info("無訊息可送")
