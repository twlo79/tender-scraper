#!/usr/bin/env python3
"""
政府標案每日爬蟲 v3  —  LINE OA 推播版
================================================================
流程：
  1. HTTP GET 抓取各網站原始 HTML
  2. Claude API 智慧解析標案清單（不依賴固定 selector）
  3. 比對 state.json，只保留「新增」項目
  4. 透過 LINE Messaging API 推播通知

環境變數（必填）：
  ANTHROPIC_API_KEY       Claude API 金鑰
  LINE_CHANNEL_TOKEN      LINE Channel Access Token
  LINE_USER_ID            推播目標的 LINE User ID（U 開頭）

選填：
  STATE_FILE   狀態檔路徑（預設 state.json）
  ONLY_NEW     "false" 則每次發送全部標案（預設只發新增）
"""

import json
import logging
import os
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

import requests

# ── 設定 ──────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
STATE_FILE  = Path(os.getenv("STATE_FILE", SCRIPT_DIR / "state.json"))
ONLY_NEW    = os.getenv("ONLY_NEW", "true").lower() != "false"

CONFIG = {
    "api_key":      os.getenv("ANTHROPIC_API_KEY", ""),
    "line_token":   os.getenv("LINE_CHANNEL_TOKEN", ""),
    "line_user_id": os.getenv("LINE_USER_ID", ""),
    "claude_model": "claude-sonnet-4-6",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

SOURCES = [
    {"name": "台北自來水處",        "url": "https://www.water.gov.taipei/News.aspx?n=D2818696FF5048B8&sms=B6EE39DA23E072F5", "base": "https://www.water.gov.taipei"},
    {"name": "國營台鐵",            "url": "https://www.railway.gov.tw/tra-tip-web/adr/rent-tender-1?&activePage=1",        "base": "https://www.railway.gov.tw"},
    {"name": "新北市政府財政局",    "url": "https://www.finance.ntpc.gov.tw/home.jsp?id=8b767bd17dc29316",                   "base": "https://www.finance.ntpc.gov.tw"},
    {"name": "農業部 瑠公管理處",   "url": "https://www.ialgo.nat.gov.tw/news/NewsPage3?a=10010",                           "base": "https://www.ialgo.nat.gov.tw"},
    {"name": "郵局房地產出租",      "url": "https://www.post.gov.tw/post/internet/Real_estate/index.jsp?ID=904",            "base": "https://www.post.gov.tw"},
    {"name": "台北市財政局",        "url": "https://dof.gov.taipei/News.aspx?n=DBCAF43864F42187&sms=148C417C1585EF00",      "base": "https://dof.gov.taipei"},
    {"name": "國家住宅及都市更新中心", "url": "https://www.hurc.org.tw/hurc/procurement",                                  "base": "https://www.hurc.org.tw"},
    {"name": "國有財產署",          "url": "https://esvc.fnp.gov.tw/rtMsg?svcId=5eafac8df8c649ba9cf62a591e44223c",         "base": "https://esvc.fnp.gov.tw"},
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

# ── Step 1：抓取 HTML ─────────────────────────────────────────────────────────

def fetch_html(url: str) -> str | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.encoding = r.apparent_encoding or "utf-8"
        return r.text
    except Exception as e:
        log.warning(f"HTTP 失敗 {url}：{e}")
        return None


def strip_html(html: str) -> str:
    for tag in ("script", "style", "nav", "header", "footer", "noscript"):
        html = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        lambda m: f"[{m.group(2).strip()}]({m.group(1)})",
        html, flags=re.DOTALL | re.IGNORECASE,
    )
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"\s{3,}", "\n", html)
    return html.strip()[:12000]


# ── Step 2：Claude 解析 ───────────────────────────────────────────────────────

PARSE_PROMPT = """你是政府標案資料擷取助手。
以下是某政府機關「標案/招標/出租」頁面的文字內容。
找出所有標案或出租公告，每筆回傳：
  - title：標案名稱（必填）
  - date：公告或截止日期（若無填空字串）
  - url：連結（完整 URL 或相對路徑；若無填空字串）

只回傳 JSON 陣列，不要任何說明文字。若無標案回傳 []。

頁面文字：
{page_text}
"""

def parse_with_claude(page_text: str, name: str) -> list[dict]:
    if not CONFIG["api_key"]:
        log.error("未設定 ANTHROPIC_API_KEY")
        return []
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CONFIG["api_key"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": CONFIG["claude_model"], "max_tokens": 2000,
                  "messages": [{"role": "user", "content": PARSE_PROMPT.format(page_text=page_text)}]},
            timeout=60,
        )
        r.raise_for_status()
        raw = r.json()["content"][0]["text"].strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        items = json.loads(raw)
        log.info(f"  [{name}] 解析出 {len(items)} 筆")
        return items if isinstance(items, list) else []
    except Exception as e:
        log.warning(f"  [{name}] 解析失敗：{e}")
        return []


def fix_urls(items: list[dict], base: str) -> list[dict]:
    for item in items:
        u = item.get("url", "").strip()
        if u and not u.startswith("http"):
            item["url"] = urljoin(base, u)
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
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def item_key(item: dict) -> str:
    return re.sub(r"\s+", "", item.get("title", ""))

def find_new_items(name: str, items: list[dict], state: dict) -> list[dict]:
    seen = set(state.get(name, []))
    new  = [i for i in items if item_key(i) not in seen]
    state[name] = list(seen | {item_key(i) for i in items})[-200:]
    return new


# ── Step 4：LINE 推播 ─────────────────────────────────────────────────────────

def send_line_message(messages: list[dict]):
    """發送 Flex Message 或文字訊息到指定 User ID"""
    if not CONFIG["line_token"] or not CONFIG["line_user_id"]:
        log.warning("未設定 LINE_CHANNEL_TOKEN 或 LINE_USER_ID，跳過推播")
        return

    r = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Authorization": f"Bearer {CONFIG['line_token']}",
            "Content-Type": "application/json",
        },
        json={"to": CONFIG["line_user_id"], "messages": messages},
        timeout=30,
    )
    if r.status_code == 200:
        log.info("LINE 推播成功")
    else:
        log.warning(f"LINE 推播失敗：{r.status_code} {r.text}")


def build_line_messages(results: dict, run_time: str) -> list[dict]:
    """組裝 LINE Flex Message（每個有新標案的機關一則）"""
    today     = date.today().strftime("%Y/%m/%d")
    total_new = sum(len(v["new"]) for v in results.values())
    messages  = []

    # ── 摘要訊息（純文字）──
    summary_lines = [f"📋 每日政府標案通知 {today}", f"共新增 {total_new} 筆\n"]
    for src in SOURCES:
        name = src["name"]
        new  = len(results.get(name, {}).get("new", []))
        err  = results.get(name, {}).get("error")
        if err:
            summary_lines.append(f"⚠️ {name}：抓取失敗")
        elif new:
            summary_lines.append(f"🆕 {name}：{new} 筆新增")
        else:
            summary_lines.append(f"✅ {name}：無新增")

    messages.append({"type": "text", "text": "\n".join(summary_lines)})

    # ── 各機關新增標案明細（Flex Message）──
    for src in SOURCES:
        name      = src["name"]
        new_items = results.get(name, {}).get("new", [])
        if not new_items:
            continue

        # 最多顯示 10 筆
        body_contents = []
        for item in new_items[:10]:
            title = item.get("title", "（無標題）")[:60]
            dt    = item.get("date", "")
            url   = item.get("url", "")

            row = {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": title,
                        "size": "sm",
                        "color": "#1d4ed8",
                        "wrap": True,
                        **({"action": {"type": "uri", "uri": url}} if url else {}),
                    }
                ],
            }
            if dt:
                row["contents"].append({
                    "type": "text",
                    "text": f"📅 {dt}",
                    "size": "xs",
                    "color": "#9ca3af",
                    "margin": "xs",
                })
            body_contents.append(row)
            # 分隔線
            body_contents.append({"type": "separator", "margin": "md", "color": "#f3f4f6"})

        if body_contents and body_contents[-1].get("type") == "separator":
            body_contents.pop()  # 移除最後一條分隔線

        flex = {
            "type": "flex",
            "altText": f"🆕 {name}：{len(new_items)} 筆新標案",
            "contents": {
                "type": "bubble",
                "header": {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#1d4ed8",
                    "paddingAll": "16px",
                    "contents": [
                        {"type": "text", "text": name, "color": "#ffffff", "weight": "bold", "size": "md"},
                        {"type": "text", "text": f"新增 {len(new_items)} 筆標案", "color": "#bfdbfe", "size": "sm", "margin": "xs"},
                    ],
                },
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "paddingAll": "16px",
                    "contents": body_contents,
                },
                "footer": {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#f9fafb",
                    "paddingAll": "10px",
                    "contents": [
                        {
                            "type": "button",
                            "action": {"type": "uri", "label": "前往原始網頁", "uri": src["url"]},
                            "style": "link",
                            "color": "#1d4ed8",
                            "height": "sm",
                        }
                    ],
                },
            },
        }
        messages.append(flex)

        # LINE 單次最多 5 則，超過要分批
        if len(messages) >= 5:
            send_line_message(messages)
            messages = []

    return messages


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
        items     = fix_urls(parse_with_claude(strip_html(html), name), src["base"])
        new_items = find_new_items(name, items, state)
        results[name] = {"all": items, "new": new_items, "error": None}
        log.info(f"  → 共 {len(items)} 筆，新增 {len(new_items)} 筆")

    save_state(state)

    # ── 印出摘要 ──
    total_new = sum(len(v["new"]) for v in results.values())
    print(f"\n{'='*55}")
    print(f"  每日標案摘要  {date.today()}  {run_time}")
    print(f"{'='*55}")
    for src in SOURCES:
        name = src["name"]
        d    = results[name]
        new  = len(d["new"])
        status = f"新增 {new} 筆" if new else "無新增"
        if d["error"]:
            status = f"⚠️  {d['error']}"
        print(f"  {'🆕' if new else '  '} {name:22s} 共{len(d['all']):3d}筆  {status}")
        for item in d["new"][:3]:
            print(f"       ▸ {item.get('title','')[:50]}")
    print(f"{'='*55}")
    print(f"  合計新增：{total_new} 筆")
    print(f"{'='*55}\n")

    # ── LINE 推播 ──
    remaining = build_line_messages(results, run_time)
    if remaining:
        send_line_message(remaining)

    log.info("=== 完成 ===")


if __name__ == "__main__":
    main()
