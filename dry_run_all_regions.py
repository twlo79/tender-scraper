#!/usr/bin/env python3
"""
地區限制解除測試腳本
=====================
功能：執行所有 parser，移除地區（台北/新北）限制，顯示所有通過關鍵字+日期篩選的標案。
用途：
  - 驗證各 parser 的標題 / 日期 / URL 是否正確
  - 確認雙北標案若符合條件，是否正確出現
  - 模擬 bug 修正後的推播內容（無地區限制版）

注意：此腳本只讀取頁面，不寫入 state.json / sent_log.json，可安全重複執行。

執行：
  python dry_run_all_regions.py
  python dry_run_all_regions.py --only-taipei    # 僅顯示含台北/新北的項目
  python dry_run_all_regions.py --source 郵局    # 僅測試特定來源
  python dry_run_all_regions.py --debug          # 顯示每筆 raw item 資料

環境變數（選填）：
  ANTHROPIC_API_KEY   Claude fallback 用（都發局等 JS 渲染頁面）
"""

import sys
import os
import re
import logging
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))
import scraper

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

# ── 參數 ──────────────────────────────────────────────────────────────────────
ONLY_TAIPEI   = "--only-taipei" in sys.argv
DEBUG         = "--debug"       in sys.argv
SOURCE_FILTER = None
for i, arg in enumerate(sys.argv):
    if arg == "--source" and i + 1 < len(sys.argv):
        SOURCE_FILTER = sys.argv[i + 1]

# ── 移除地區限制（保留白名單、黑名單、日期窗口）──────────────────────────────
scraper.GLOBAL_FILTER["regions"] = []
for src in scraper.SOURCES:
    src["regions"] = []

# ── 執行 ──────────────────────────────────────────────────────────────────────
print(f"\n{'='*70}")
print(f"  地區限制解除測試  {date.today()}")
print(f"  模式: {'僅顯示台北/新北' if ONLY_TAIPEI else '全台所有'}"
      + (" | DEBUG" if DEBUG else ""))
if SOURCE_FILTER:
    print(f"  來源篩選: {SOURCE_FILTER}")
print(f"{'='*70}\n")

total_found = 0

for src in scraper.SOURCES:
    name = src["name"]
    if SOURCE_FILTER and SOURCE_FILTER not in name:
        continue

    print(f"{'─'*70}")
    print(f"  來源：{name}")
    print(f"  URL：{src['url']}")
    print()

    try:
        items = src["fn"]()
    except Exception as e:
        print(f"  ❌ 抓取失敗：{e}\n")
        continue

    if DEBUG:
        print(f"  [DEBUG] 抓到 {len(items)} 筆原始資料：")
        for raw in items[:5]:
            print(f"    title={raw.get('title','')[:60]!r}  date={raw.get('date','')!r}  url={raw.get('url','')[:60]!r}")
        if len(items) > 5:
            print(f"    ... 還有 {len(items)-5} 筆")
        print()

    print(f"  抓到 {len(items)} 筆，套用關鍵字篩選 + 日期窗口 (±{scraper.DATE_WINDOW_DAYS}天)：")

    passed = []
    for item in items:
        title = item.get("title", "")
        wl = scraper.GLOBAL_FILTER.get("whitelist", [])
        bl = scraper.GLOBAL_FILTER.get("blacklist", [])
        skip_filter = src.get("skip_global_filter", False)

        if not skip_filter:
            if wl and not any(k in title for k in wl):
                continue
            if bl and any(k in title for k in bl):
                continue

        if not scraper.is_within_date_window(item):
            continue

        if ONLY_TAIPEI:
            text = title + item.get("agency", "")
            if not any(k in text for k in ["台北", "臺北", "新北"]):
                continue

        passed.append(item)

    if not passed:
        print(f"  → 無符合條件的標案\n")
        continue

    total_found += len(passed)
    for item in passed:
        t = item.get("title", "（無標題）")
        d = item.get("date", "")
        u = item.get("url", "")
        taipei_tag = ""
        if any(k in t + item.get("agency", "") for k in ["台北", "臺北", "新北"]):
            taipei_tag = " 🔔[雙北]"

        date_display = d if d else "⚠️ 無日期"
        url_display  = u[:90] if u else "⚠️ 無URL"

        print(f"  ✅ 標題：{t[:70]}{taipei_tag}")
        print(f"     日期：{date_display}")
        print(f"     URL ：{url_display}")
        print()

print(f"{'='*70}")
print(f"  合計符合條件：{total_found} 筆")
print(f"{'='*70}\n")
