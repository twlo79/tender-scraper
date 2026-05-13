#!/usr/bin/env python3
"""
政府標案每日爬蟲 v2  —  Claude AI 智慧解析版
================================================================
流程：
  1. HTTP GET 抓取各網站原始 HTML
  2. 餵給 Claude API → 解析出標案清單（不依賴固定 selector）
  3. 比對上次狀態（state.json），只保留「新增」項目
  4. 寄出 HTML Email 通知

環境變數（必填）：
  EMAIL_FROM        寄件 Gmail 帳號
  EMAIL_TO          收件人（可用逗號分隔多人）
  EMAIL_PASSWORD    Gmail 應用程式密碼（16碼，非登入密碼）
  ANTHROPIC_API_KEY Claude API 金鑰

選填：
  STATE_FILE        狀態檔路徑（預設 state.json，放在腳本同目錄）
  ONLY_NEW          設為 "false" 則每次發送全部標案（預設只發新增）
"""

import json
import logging
import os
import re
import smtplib
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urljoin

import requests

# ── 設定 ──────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
STATE_FILE = Path(os.getenv("STATE_FILE", SCRIPT_DIR / "state.json"))
ONLY_NEW   = os.getenv("ONLY_NEW", "true").lower() != "false"

CONFIG = {
    "email_from":     os.getenv("EMAIL_FROM", ""),
    "email_to":       os.getenv("EMAIL_TO", ""),
    "email_password": os.getenv("EMAIL_PASSWORD", ""),
    "smtp_host":      "smtp.gmail.com",
    "smtp_port":      465,
    "api_key":        os.getenv("ANTHROPIC_API_KEY", ""),
    "claude_model":   "claude-sonnet-4-20250514",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# 8 個目標網站
SOURCES = [
    {
        "name": "台北自來水處",
        "url":  "https://www.water.gov.taipei/News.aspx?n=D2818696FF5048B8&sms=B6EE39DA23E072F5",
        "base": "https://www.water.gov.taipei",
    },
    {
        "name": "國營台鐵",
        "url":  "https://www.railway.gov.tw/tra-tip-web/adr/rent-tender-1?&activePage=1",
        "base": "https://www.railway.gov.tw",
    },
    {
        "name": "新北市政府財政局",
        "url":  "https://www.finance.ntpc.gov.tw/home.jsp?id=8b767bd17dc29316",
        "base": "https://www.finance.ntpc.gov.tw",
    },
    {
        "name": "農業部 瑠公管理處",
        "url":  "https://www.ialgo.nat.gov.tw/news/NewsPage3?a=10010",
        "base": "https://www.ialgo.nat.gov.tw",
    },
    {
        "name": "郵局房地產出租",
        "url":  "https://www.post.gov.tw/post/internet/Real_estate/index.jsp?ID=904",
        "base": "https://www.post.gov.tw",
    },
    {
        "name": "台北市財政局",
        "url":  "https://dof.gov.taipei/News.aspx?n=DBCAF43864F42187&sms=148C417C1585EF00",
        "base": "https://dof.gov.taipei",
    },
    {
        "name": "國家住宅及都市更新中心",
        "url":  "https://www.hurc.org.tw/hurc/procurement",
        "base": "https://www.hurc.org.tw",
    },
    {
        "name": "國有財產署",
        "url":  "https://esvc.fnp.gov.tw/rtMsg?svcId=5eafac8df8c649ba9cf62a591e44223c",
        "base": "https://esvc.fnp.gov.tw",
    },
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Step 1：HTTP GET ───────────────────────────────────────────────────────────

def fetch_html(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    except Exception as e:
        log.warning(f"HTTP 失敗 {url}：{e}")
        return None


def strip_html(html: str) -> str:
    """移除 script/style/nav，保留主要文字 + 連結，控制在 12000 字元內"""
    for tag in ("script", "style", "nav", "header", "footer", "noscript"):
        html = re.sub(
            rf"<{tag}[^>]*>.*?</{tag}>", " ", html,
            flags=re.DOTALL | re.IGNORECASE,
        )
    # <a href="...">文字</a>  →  [文字](href)
    html = re.sub(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        lambda m: f"[{m.group(2).strip()}]({m.group(1)})",
        html, flags=re.DOTALL | re.IGNORECASE,
    )
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"\s{3,}", "\n", html)
    return html.strip()[:12000]


# ── Step 2：Claude API 解析 ───────────────────────────────────────────────────

PARSE_PROMPT = """你是一個政府標案資料擷取助手。
以下是某政府機關「標案/招標/出租」頁面的文字內容。
請找出所有標案或出租公告，每筆回傳：
  - title：標案名稱（必填）
  - date：公告或截止日期（若有，格式不限；沒有填空字串）
  - url：連結（若有，需是完整 URL 或相對路徑；沒有填空字串）

回傳格式為 JSON 陣列，例如：
[
  {{"title": "某工程採購案", "date": "114/05/10", "url": "/path/to/detail"}},
  {{"title": "房地出租公告", "date": "", "url": "https://..."}}
]

若頁面沒有任何標案/招標/出租資訊，回傳空陣列 []。
只回傳 JSON，不要加任何說明文字。

頁面文字：
{page_text}
"""


def parse_with_claude(page_text: str, source_name: str) -> list[dict]:
    if not CONFIG["api_key"]:
        log.error("未設定 ANTHROPIC_API_KEY")
        return []
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         CONFIG["api_key"],
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      CONFIG["claude_model"],
                "max_tokens": 2000,
                "messages":   [{"role": "user", "content": PARSE_PROMPT.format(page_text=page_text)}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json()["content"][0]["text"].strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        items = json.loads(raw)
        log.info(f"  [{source_name}] Claude 解析出 {len(items)} 筆")
        return items if isinstance(items, list) else []
    except Exception as e:
        log.warning(f"  [{source_name}] Claude 解析失敗：{e}")
        return []


def fix_urls(items: list[dict], base_url: str) -> list[dict]:
    for item in items:
        url = item.get("url", "").strip()
        if url and not url.startswith("http"):
            item["url"] = urljoin(base_url, url)
    return items


# ── Step 3：狀態比對 ──────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def item_key(item: dict) -> str:
    return re.sub(r"\s+", "", item.get("title", ""))


def find_new_items(name: str, items: list[dict], state: dict) -> list[dict]:
    seen = set(state.get(name, []))
    new_items = [i for i in items if item_key(i) not in seen]
    all_keys = list(seen | {item_key(i) for i in items})
    state[name] = all_keys[-200:]
    return new_items


# ── Step 4：Email ─────────────────────────────────────────────────────────────

def build_html(results: dict, run_time: str) -> str:
    today     = date.today().strftime("%Y/%m/%d")
    total_new = sum(len(v["new"]) for v in results.values())

    sections = ""
    for src in SOURCES:
        name      = src["name"]
        data      = results.get(name, {"all": [], "new": [], "error": None})
        all_items = data["all"]
        new_items = data["new"]
        error     = data["error"]

        new_badge = (
            f'<span style="background:#dc2626;color:#fff;font-size:11px;'
            f'border-radius:9px;padding:1px 7px;margin-left:8px;">+{len(new_items)} 新增</span>'
            if new_items else ""
        )

        if error:
            body = f'<p style="color:#ef4444;font-size:13px;padding:4px 10px;">⚠️ {error}</p>'
        elif not all_items:
            body = '<p style="color:#9ca3af;font-size:13px;padding:4px 10px;">（本日無公告資料）</p>'
        else:
            if ONLY_NEW and not new_items:
                body = '<p style="color:#9ca3af;font-size:13px;padding:4px 10px;">✅ 今日無新增</p>'
            else:
                display    = new_items if (ONLY_NEW and new_items) else all_items
                new_titles = {item_key(n) for n in new_items}
                rows = ""
                for item in display:
                    is_new    = item_key(item) in new_titles
                    badge     = '🆕 ' if is_new else ''
                    title     = item.get("title", "（無標題）")
                    dt        = item.get("date", "")
                    url       = item.get("url", "")
                    title_td  = (
                        f'<a href="{url}" style="color:#1d4ed8;text-decoration:none;">'
                        f'{badge}{title}</a>'
                        if url else f"{badge}{title}"
                    )
                    rows += (
                        f"<tr>"
                        f'<td style="padding:7px 10px;border-bottom:1px solid #f3f4f6;">{title_td}</td>'
                        f'<td style="padding:7px 10px;border-bottom:1px solid #f3f4f6;'
                        f'white-space:nowrap;color:#6b7280;font-size:12px;">{dt}</td>'
                        f"</tr>"
                    )
                body = (
                    f'<table width="100%" cellspacing="0" cellpadding="0"'
                    f' style="border-collapse:collapse;"><tbody>{rows}</tbody></table>'
                )

        sections += f"""
        <div style="margin-bottom:28px;">
          <h2 style="font-size:14px;font-weight:700;color:#111827;
                     border-left:4px solid #2563eb;padding-left:10px;margin:0 0 8px;">
            <a href="{src['url']}" style="color:#111827;text-decoration:none;">{name}</a>
            {new_badge}
          </h2>
          {body}
        </div>"""

    summary_color = "#15803d" if total_new else "#6b7280"
    summary_text  = f"今日共新增 {total_new} 筆標案" if total_new else "今日無新增標案"

    return f"""<!DOCTYPE html>
<html lang="zh-TW"><head><meta charset="utf-8">
<style>body{{margin:0;padding:0;background:#f3f4f6;
font-family:'Helvetica Neue',Arial,'PingFang TC','Microsoft JhengHei',sans-serif;}}</style>
</head><body>
<div style="max-width:700px;margin:32px auto;background:#fff;
            border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1);">
  <div style="background:#1d4ed8;padding:22px 28px;">
    <h1 style="margin:0;color:#fff;font-size:20px;">📋 每日政府標案通知</h1>
    <p style="margin:6px 0 0;color:#bfdbfe;font-size:13px;">{today}・執行於 {run_time}</p>
    <p style="margin:8px 0 0;font-size:13px;font-weight:600;
              color:#fff;background:rgba(255,255,255,.2);
              display:inline-block;padding:2px 12px;border-radius:12px;">{summary_text}</p>
  </div>
  <div style="padding:24px 28px;">{sections}</div>
  <div style="padding:14px 28px;background:#f9fafb;border-top:1px solid #e5e7eb;
              font-size:11px;color:#9ca3af;">
    由自動爬蟲 + Claude AI 解析產生｜資料來源為各政府機關官方網站
  </div>
</div>
</body></html>"""


def send_email(html: str, total_new: int):
    today   = date.today().strftime("%Y/%m/%d")
    subject = f"📋 政府標案通知 {today}｜新增 {total_new} 筆"
    msg     = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = CONFIG["email_from"]
    msg["To"]      = CONFIG["email_to"]
    msg.attach(MIMEText(html, "html", "utf-8"))

    recipients = [r.strip() for r in CONFIG["email_to"].split(",") if r.strip()]
    with smtplib.SMTP_SSL(CONFIG["smtp_host"], CONFIG["smtp_port"]) as s:
        s.login(CONFIG["email_from"], CONFIG["email_password"])
        s.sendmail(CONFIG["email_from"], recipients, msg.as_string())
    log.info(f"Email 已寄出 → {CONFIG['email_to']}")


# ── 主程式 ────────────────────────────────────────────────────────────────────

def main():
    run_time = datetime.now().strftime("%H:%M")
    log.info(f"=== 開始執行 {date.today()} {run_time} ===")

    state   = load_state()
    results = {}

    for src in SOURCES:
        name = src["name"]
        log.info(f"抓取：{name}")

        html = fetch_html(src["url"])
        if not html:
            results[name] = {"all": [], "new": [], "error": "HTTP 請求失敗"}
            continue

        page_text = strip_html(html)
        items     = parse_with_claude(page_text, name)
        items     = fix_urls(items, src["base"])
        new_items = find_new_items(name, items, state)

        results[name] = {"all": items, "new": new_items, "error": None}
        log.info(f"  → 共 {len(items)} 筆，其中新增 {len(new_items)} 筆")

    save_state(state)
    log.info(f"狀態已儲存至 {STATE_FILE}")

    # ── 印出摘要 ──
    total_new = sum(len(v["new"]) for v in results.values())
    print(f"\n{'='*55}")
    print(f"  每日標案摘要  {date.today()}  {run_time}")
    print(f"{'='*55}")
    for src in SOURCES:
        name = src["name"]
        d    = results[name]
        new  = len(d["new"])
        total_new_local = new
        status = f"新增 {new} 筆" if new else "無新增"
        if d["error"]:
            status = f"⚠️  {d['error']}"
        print(f"  {'🆕' if new else '  '} {name:20s}  共 {len(d['all']):3d} 筆  {status}")
        for item in d["new"][:3]:
            print(f"       ▸ {item.get('title','')[:50]}")
    print(f"{'='*55}")
    print(f"  合計新增：{total_new} 筆")
    print(f"{'='*55}\n")

    # ── 寄信 ──
    html_body = build_html(results, run_time)

    if not CONFIG["email_from"] or not CONFIG["email_password"]:
        preview = SCRIPT_DIR / "tender_preview.html"
        preview.write_text(html_body, encoding="utf-8")
        log.warning(f"未設定 Email 環境變數，已將結果存至 {preview}")
    else:
        send_email(html_body, total_new)

    log.info("=== 完成 ===")


if __name__ == "__main__":
    main()
