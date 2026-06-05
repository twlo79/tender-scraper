#!/usr/bin/env python3
"""
每日爬蟲 log 解析器
從 GitHub Actions log 擷取各來源筆數與錯誤，存成 daily_report.json。
"""

import json
import os
import re
import sys
from base64 import b64encode
from datetime import date

import requests

GITHUB_TOKEN  = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO   = os.getenv("GITHUB_REPO", "")
REPORT_FILE   = "daily_report.json"

GH = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}


def get_latest_log() -> str:
    r = requests.get(
        f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/daily.yml/runs?per_page=2",
        headers=GH, timeout=20,
    )
    # 取第二筆（第一筆是本次 log_checker 所在的 run，或同一個 run 的前一次爬蟲）
    runs = r.json().get("workflow_runs", [])
    # 找最新一筆 daily.yml（排除本身）
    for run in runs:
        if run.get("conclusion") in ("success", "failure"):
            run_id = run["id"]
            break
    else:
        return ""

    r = requests.get(
        f"https://api.github.com/repos/{GITHUB_REPO}/actions/runs/{run_id}/jobs",
        headers=GH, timeout=20,
    )
    jobs = r.json().get("jobs", [])
    if not jobs:
        return ""

    r = requests.get(
        f"https://api.github.com/repos/{GITHUB_REPO}/actions/jobs/{jobs[0]['id']}/logs",
        headers=GH, timeout=30, allow_redirects=True,
    )
    return r.text


def parse_log(log: str) -> dict:
    today = str(date.today())
    report = {"date": today, "sources": {}, "total_new": 0, "total_pushed": 0, "errors": []}

    # 各來源：共 N 筆，新增 M 筆，推播 K 筆
    for m in re.finditer(r"抓取：(.+?)\n.*?共\s*(\d+)\s*筆.*?新增\s*(\d+)\s*筆.*?推播\s*(\d+)\s*筆", log, re.DOTALL):
        name, total, new, pushed = m.group(1).strip(), int(m.group(2)), int(m.group(3)), int(m.group(4))
        report["sources"][name] = {"total": total, "new": new, "pushed": pushed}

    # 合計
    m = re.search(r"合計新增：(\d+)\s*筆.*?推播：(\d+)\s*筆", log)
    if m:
        report["total_new"]    = int(m.group(1))
        report["total_pushed"] = int(m.group(2))

    # 錯誤與警告
    for m in re.finditer(r"(WARNING|ERROR)\s+(.+)", log):
        msg = m.group(2).strip()
        # 過濾不重要的系統訊息
        if any(skip in msg for skip in ["Node.js 20", "deprecated", "POST Setup", "git config"]):
            continue
        report["errors"].append(msg)

    # LINE 推播結果
    if "LINE broadcast 成功" in log:
        report["line"] = "成功"
    elif "LINE broadcast 失敗" in log:
        report["line"] = "失敗"
    elif "未設定 LINE_CHANNEL_TOKEN" in log:
        report["line"] = "未設定"
    else:
        report["line"] = "無推播（0 筆）"

    return report


def save_report(report: dict):
    # 讀現有檔案（保留歷史）
    existing = {}
    r = requests.get(
        f"https://api.github.com/repos/{GITHUB_REPO}/contents/{REPORT_FILE}",
        headers=GH, timeout=20,
    )
    if r.status_code == 200:
        import base64
        data = r.json()
        sha = data["sha"]
        existing = json.loads(base64.b64decode(data["content"]).decode())
    else:
        sha = None

    existing[report["date"]] = report

    # 只保留最近 30 天
    keys = sorted(existing.keys())[-30:]
    existing = {k: existing[k] for k in keys}

    content = b64encode(json.dumps(existing, ensure_ascii=False, indent=2).encode()).decode()
    payload = {"message": f"chore: daily report {report['date']}", "content": content}
    if sha:
        payload["sha"] = sha

    requests.put(
        f"https://api.github.com/repos/{GITHUB_REPO}/contents/{REPORT_FILE}",
        headers=GH, json=payload, timeout=20,
    )
    print(f"✅ 已儲存 {report['date']} 的報告")


if __name__ == "__main__":
    log = get_latest_log()
    if not log:
        print("❌ 無法取得 log")
        sys.exit(1)

    report = parse_log(log)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    save_report(report)
