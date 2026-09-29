#!/usr/bin/env python3
"""
測試 ERP 匯入連線與 KEY（不跑爬蟲、不推 LINE、不動 state.json）。

用法：
  python erp_test.py            # 只驗證：送空的 records，看 KEY 通不通（不新增資料）
  python erp_test.py --send     # 送 1 筆標記為「測試」的假資料，驗證欄位格式（會寫入 ERP，事後請手動刪除）

需要環境變數 ANXING_URL、ANXING_KEY。
"""
import os
import sys

import requests


def main():
    url = os.getenv("ANXING_URL", "").strip()
    key = os.getenv("ANXING_KEY", "").strip()
    if not url or not key:
        print("❌ 沒設 ANXING_URL / ANXING_KEY")
        sys.exit(1)
    print(f"URL：{url}（KEY 長度 {len(key)}，含換行：{'是' if chr(10) in os.getenv('ANXING_KEY', '').strip() else '否'}）")

    records = []
    if "--send" in sys.argv:
        records = [{
            "source": "測試",
            "title": "【測試資料，請刪除】ERP 匯入連線測試",
            "url": "https://example.com/test",
            "agency": "測試",
            "date": "2026-01-01",
        }]
    r = requests.post(url, json={"records": records}, headers={"x-import-key": key}, timeout=30)
    print(f"HTTP {r.status_code}\n{r.text[:500]}")
    hints = {
        200: "✅ 通了" + ("，資料已寫入，請到 ERP 確認並刪除測試那筆" if records else "（僅驗證，未寫入資料）"),
        400: "KEY 通過但資料格式被拒 → 把上面回應貼給我，對欄位",
        401: "❌ KEY 不對或 ERP 端沒設對應 KEY",
        403: "❌ KEY 不對或被拒絕",
        404: "❌ ANXING_URL 網址錯誤",
    }
    print(hints.get(r.status_code, "❓ 其他狀況，把回應貼給我"))
    sys.exit(0 if r.status_code == 200 else 2)


if __name__ == "__main__":
    main()
