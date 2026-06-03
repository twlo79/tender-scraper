#!/usr/bin/env python3
"""
重播指定日期的 LINE 推播內容。

用法：
  python resend.py               # 重播今天最新一筆記錄
  python resend.py 2026-06-03    # 重播指定日期最新一筆記錄
  python resend.py list          # 列出所有可重播的記錄
"""

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from scraper import SENT_LOG_FILE, build_line_messages, push_in_batches
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)


def load_log() -> dict:
    if not SENT_LOG_FILE.exists():
        log.error("找不到 sent_log.json，請先執行 scraper.py 至少一次。")
        sys.exit(1)
    return json.loads(SENT_LOG_FILE.read_text(encoding="utf-8"))


def main():
    log_data = load_log()

    arg = sys.argv[1] if len(sys.argv) > 1 else ""

    if arg == "list":
        print("\n可重播的記錄：")
        for key, entry in sorted(log_data.items(), reverse=True):
            total = sum(len(v) for v in entry.values())
            print(f"  {key}  （{total} 筆）")
        return

    # 找符合日期的最新記錄
    target_date = arg if arg else ""
    candidates = sorted(
        [k for k in log_data if k.startswith(target_date)],
        reverse=True
    )
    if not candidates:
        log.error(f"找不到符合 '{target_date or '今天'}' 的記錄。")
        log.info("可用 'python resend.py list' 查看所有記錄。")
        sys.exit(1)

    key = candidates[0]
    log.info(f"重播記錄：{key}")

    # 將 log entry 轉成 results 格式
    entry = log_data[key]
    results = {
        name: {"all": items, "new": items, "notify": items, "error": None}
        for name, items in entry.items()
    }

    run_time = key[11:] if len(key) > 10 else "補發"
    messages = build_line_messages(results, f"{run_time}（補發）")
    if messages:
        push_in_batches(messages)
        log.info("完成")
    else:
        log.info("該記錄無推播內容")


if __name__ == "__main__":
    main()
