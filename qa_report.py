#!/usr/bin/env python3
"""
QA 監測報告腳本
================
在 Claude Code routine 中執行，分析最新爬蟲執行結果並輸出 QA 報告。

優先從 GitHub API 取最新 sent_log.json（比本地 clone 更即時），
無 token 時 fallback 本地檔案。

執行：
  python qa_report.py              # 最新一次執行報告
  python qa_report.py --days 7     # 加上近 7 天趨勢表
  python qa_report.py --full       # 詳細模式（列出所有推播項目）

環境變數（選填，用於取 GitHub 最新資料）：
  GITHUB_TOKEN   Bearer token
  GITHUB_REPO    格式 owner/repo（預設 twlo79/tender-scraper）
"""

import sys, os, json, re
from datetime import date, datetime, timedelta
from base64 import b64decode
from pathlib import Path

# ── 參數 ──────────────────────────────────────────────────────────────────────
SHOW_DAYS = 7
FULL      = "--full" in sys.argv
for i, arg in enumerate(sys.argv):
    if arg == "--days" and i + 1 < len(sys.argv):
        try: SHOW_DAYS = int(sys.argv[i + 1])
        except ValueError: pass

SCRIPT_DIR = Path(__file__).parent
GH_TOKEN   = os.environ.get("GITHUB_TOKEN", "")
GH_REPO    = os.environ.get("GITHUB_REPO", "twlo79/tender-scraper")

# ── 載入 scraper config（whitelist / blacklist / date window）─────────────────
sys.path.insert(0, str(SCRIPT_DIR))
try:
    import scraper as _scraper
    WHITELIST       = _scraper.GLOBAL_FILTER.get("whitelist", [])
    BLACKLIST       = _scraper.GLOBAL_FILTER.get("blacklist", [])
    DATE_WINDOW     = _scraper.DATE_WINDOW_DAYS
    SOURCE_NAMES    = [s["name"] for s in _scraper.SOURCES]
except Exception as e:
    print(f"[WARN] 無法載入 scraper config：{e}")
    WHITELIST, BLACKLIST, DATE_WINDOW, SOURCE_NAMES = [], [], 10, []


# ── 取得 sent_log.json ─────────────────────────────────────────────────────────
def fetch_sent_log() -> dict:
    """優先從 GitHub API 取，失敗則讀本地檔案。"""
    if GH_TOKEN:
        try:
            import requests
            r = requests.get(
                f"https://api.github.com/repos/{GH_REPO}/contents/sent_log.json",
                headers={"Authorization": f"Bearer {GH_TOKEN}",
                         "Accept": "application/vnd.github+json"},
                timeout=10,
            )
            if r.status_code == 200:
                return json.loads(b64decode(r.json()["content"]).decode())
        except Exception:
            pass

    local = SCRIPT_DIR / "sent_log.json"
    if local.exists():
        return json.loads(local.read_text(encoding="utf-8"))
    return {}


# ── 工具函式 ──────────────────────────────────────────────────────────────────
def parse_date_str(s: str):
    """嘗試解析民國或西元日期字串，回傳 date 或 None。"""
    s = s.strip()
    # 西元：2026/06/01、2026-06-01
    m = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", s)
    if m:
        try: return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError: pass
    # 民國：115/06/01、115-06-01、115年06月01日
    m = re.search(r"\b(1\d{2})[/.\-年](\d{1,2})[/.\-月](\d{1,2})", s)
    if m:
        try: return date(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3)))
        except ValueError: pass
    return None


NOISE_TITLE_PATTERNS = [
    r"^不動產標售租公告$",       # 都發局 bug：分類名稱當標題
    r"^委員名單$",
    r"^加入會員$",
    r"^\s*$",
]

def is_noise_title(title: str) -> str | None:
    """回傳噪音原因字串，或 None 表示正常。"""
    if len(title) < 5:
        return f"標題太短（{len(title)} 字）"
    for pat in NOISE_TITLE_PATTERNS:
        if re.match(pat, title.strip()):
            return f"疑似噪音：{title!r}"
    if BLACKLIST and any(k in title for k in BLACKLIST):
        hit = next(k for k in BLACKLIST if k in title)
        return f"含黑名單關鍵字「{hit}」"
    if WHITELIST and not any(k in title for k in WHITELIST):
        return "不含任何白名單關鍵字"
    return None


def fmt_section(title: str):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


# ── 主程式 ────────────────────────────────────────────────────────────────────
log_data = fetch_sent_log()

if not log_data:
    print("❌ 無法取得 sent_log.json，請確認 GITHUB_TOKEN 或本地檔案存在。")
    sys.exit(1)

# 只保留有資料的 key（排除 {} 空記錄）
valid_entries = {k: v for k, v in log_data.items() if v}
if not valid_entries:
    print("⚠️  sent_log.json 沒有有效執行記錄。")
    sys.exit(0)

sorted_keys = sorted(valid_entries.keys())
latest_key  = sorted_keys[-1]
latest      = valid_entries[latest_key]

today = date.today()

# ══════════════════════════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"  QA 監測報告  {today}  （報告產生：{datetime.now().strftime('%H:%M:%S')}）")
print(f"  分析資料來源：{'GitHub API' if GH_TOKEN else '本地 sent_log.json'}")
print(f"{'='*60}")

# ── 1. 最新執行摘要 ───────────────────────────────────────────────────────────
fmt_section("1. 最新執行摘要")
print(f"  執行時間：{latest_key}")

summary = latest.get("_summary", {})
if summary:
    pushed = "✅ 有推播" if summary.get("line_pushed") else "⭕ 無推播（無新案件）"
    print(f"  LINE 推播：{pushed}")
    print(f"  總計：抓到 {summary.get('total_fetched',0)} 筆"
          f" / 新增 {summary.get('total_new',0)} 筆"
          f" / 推播 {summary.get('total_notify',0)} 筆")
else:
    print("  ⚠️  無 _summary（舊版格式）")

# ── 2. 各來源狀況 ─────────────────────────────────────────────────────────────
fmt_section("2. 各來源狀況")

anomalies = []
for name in SOURCE_NAMES or [k for k in latest if not k.startswith("_")]:
    src = latest.get(name, {})
    if not src:
        continue
    fetched = src.get("fetched", 0)
    new     = src.get("new", 0)
    notify  = src.get("notify", 0)
    items   = src.get("items", [])

    status = ""
    if fetched == 0:
        status = "  ⚠️  fetched=0（連線失敗或無資料）"
        anomalies.append(f"{name}：fetched=0")
    elif notify > 0 and not items:
        status = "  ⚠️  有推播但無 items 記錄"
        anomalies.append(f"{name}：notify={notify} 但無 items")

    print(f"  {name:<20s}  抓{fetched:>3}  新{new:>3}  推{notify:>2}  {status}")

    # 推播項目品質檢查
    for title in items:
        reason = is_noise_title(title)
        tag = f"  ✅ 推播：{title[:60]}"
        if reason:
            tag = f"  ❌ 推播噪音：{title[:60]}  →  {reason}"
            anomalies.append(f"{name} 推播噪音：{title[:40]}（{reason}）")
        if FULL or reason:
            print(f"        {tag}")

# ── 3. 推播內容 QA ────────────────────────────────────────────────────────────
fmt_section("3. 推播內容 QA")

all_pushed_items = []
for name in SOURCE_NAMES or [k for k in latest if not k.startswith("_")]:
    src = latest.get(name, {})
    for title in src.get("items", []):
        all_pushed_items.append((name, title))

if not all_pushed_items:
    print("  本次無推播項目。")
else:
    ok_count    = 0
    noise_count = 0
    for name, title in all_pushed_items:
        reason = is_noise_title(title)
        if reason:
            noise_count += 1
            print(f"  ❌ [{name}] {title[:55]}")
            print(f"     問題：{reason}")
        else:
            ok_count += 1
            if FULL:
                print(f"  ✅ [{name}] {title[:55]}")

    print()
    if noise_count == 0:
        print(f"  ✅ {ok_count} 筆推播，格式全部正常")
    else:
        print(f"  ⚠️  {ok_count} 筆正常 / {noise_count} 筆有問題")

    # sent_log 的 items 只有標題，沒有日期和 URL
    print()
    print("  ℹ️  注意：sent_log 只記錄標題，日期與 URL 需由 dry_run 驗證。")

# ── 4. 近 N 天趨勢 ────────────────────────────────────────────────────────────
if "--days" in sys.argv or SHOW_DAYS:
    fmt_section(f"4. 近 {SHOW_DAYS} 天各來源趨勢")

    cutoff_dt = today - timedelta(days=SHOW_DAYS)
    recent = {k: v for k, v in valid_entries.items()
              if k[:10] >= cutoff_dt.strftime("%Y-%m-%d")}

    if not recent:
        print("  資料不足。")
    else:
        # 找哪些來源有問題
        zero_streak: dict[str, int] = {}
        for key in sorted(recent.keys()):
            entry = recent[key]
            for name in SOURCE_NAMES or []:
                src = entry.get(name, {})
                fetched = src.get("fetched", 0)
                if fetched == 0:
                    zero_streak[name] = zero_streak.get(name, 0) + 1
                else:
                    zero_streak[name] = 0

        print(f"  {'來源':<22s}  " + "  ".join(
            (today - timedelta(days=SHOW_DAYS - 1 - i)).strftime("%m/%d")
            for i in range(SHOW_DAYS)
        ))
        for name in SOURCE_NAMES or []:
            row = []
            for i in range(SHOW_DAYS):
                d_str = (today - timedelta(days=SHOW_DAYS - 1 - i)).strftime("%Y-%m-%d")
                day_entries = [v for k, v in recent.items() if k.startswith(d_str)]
                if not day_entries:
                    row.append("  —  ")
                else:
                    e = day_entries[-1]
                    src = e.get(name, {})
                    f = src.get("fetched", 0)
                    n = src.get("notify", 0)
                    cell = f"f{f:>2}/n{n}" if f > 0 else "  0  "
                    row.append(cell)
            streak = zero_streak.get(name, 0)
            warn = f"  ⚠️  連續{streak}天 fetched=0" if streak >= 3 else ""
            print(f"  {name:<22s}  {'  '.join(row)}{warn}")

# ── 5. 異常摘要與建議 ─────────────────────────────────────────────────────────
fmt_section("5. 異常摘要與優化建議")

if not anomalies:
    print("  ✅ 無明顯異常。")
else:
    print("  發現以下問題：")
    for a in anomalies:
        print(f"    ⚠️  {a}")

# 固定建議（基於 sent_log 無法自動偵測的項目）
print()
print("  定期確認項目（每週）：")
print("    □ 執行 python dry_run_all_regions.py --debug 確認各 parser 標題/日期/URL")
print("    □ 確認 GitHub Actions 最近一週有無 step 失敗")
print("    □ 新北市/教育部/國防部/土地銀行 在 Actions 環境中 fetched 是否正常（目前雲端 IP 被擋）")

print(f"\n{'='*60}\n")
