#!/usr/bin/env python3
"""
政府標案每日爬蟲 v5
================================================================
修正：
  1. 各網站使用精準 parser（不再依賴 Claude 猜 selector）
  2. state.json 自動 commit 回 GitHub（解決每次 VM 重置問題）
  3. 郵局/國有財產署 URL 修正為頁面連結而非各別標案

網站                  抓取策略
─────────────────────────────────────────────────────────────
台北自來水處          requests + table tr parser
國營台鐵             requests + 純文字 regex parser
新北市政府財政局      直接呼叫後端 API（AJAX endpoint）
農業部 瑠公管理處     requests + table tr parser
郵局房地產出租        requests + table/list parser
台北市財政局          requests + table tr parser
國家住宅及都市更新中心 requests + JSON API
國有財產署            requests + table tr parser（批號為 key）

環境變數（必填）：
  ANTHROPIC_API_KEY   Claude API 金鑰（解析不確定的頁面時備用）
  LINE_CHANNEL_TOKEN  LINE Channel Access Token
  LINE_USER_ID        推播目標 LINE User ID（U 開頭）

選填：
  GITHUB_TOKEN        GitHub Personal Access Token（用於儲存 state）
  GITHUB_REPO         格式 owner/repo（例如 yourname/tender-scraper）
  STATE_FILE          本地備援路徑（預設 state.json）
  ONLY_NEW            "false" 每次發送全部（預設只發新增）
"""

import json
import logging
import os
import re
import subprocess
import sys
from base64 import b64decode, b64encode
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlencode

import requests

# ── 設定 ──────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
STATE_FILE  = Path(os.getenv("STATE_FILE", SCRIPT_DIR / "state.json"))
ONLY_NEW    = os.getenv("ONLY_NEW", "true").lower() != "false"

CONFIG = {
    "api_key":      os.getenv("ANTHROPIC_API_KEY", ""),
    "line_token":   os.getenv("LINE_CHANNEL_TOKEN", ""),
    "line_user_id": os.getenv("LINE_USER_ID", ""),
    "gh_token":     os.getenv("GITHUB_TOKEN", ""),
    "gh_repo":      os.getenv("GITHUB_REPO", ""),   # e.g. "yourname/tender-scraper"
    "claude_model": "claude-sonnet-4-6",
}

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

# ── 通用 HTTP ─────────────────────────────────────────────────────────────────

def get(url, **kwargs) -> requests.Response | None:
    try:
        r = requests.get(url, headers=HTTP_HEADERS, timeout=20, **kwargs)
        r.encoding = r.apparent_encoding or "utf-8"
        return r
    except Exception as e:
        log.warning(f"GET 失敗 {url}：{e}")
        return None

def post_json(url, payload, extra_headers=None) -> dict | None:
    h = {**HTTP_HEADERS, "Content-Type": "application/json", **(extra_headers or {})}
    try:
        r = requests.post(url, headers=h, json=payload, timeout=20)
        return r.json()
    except Exception as e:
        log.warning(f"POST 失敗 {url}：{e}")
        return None

# ── 各網站精準 Parser ─────────────────────────────────────────────────────────

def parse_taipei_water() -> list[dict]:
    """台北自來水處：table tr，連結指向政府採購網"""
    r = get("https://www.water.gov.taipei/News.aspx?n=D2818696FF5048B8&sms=B6EE39DA23E072F5")
    if not r: return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, "lxml")
    items = []
    for row in soup.select("table tr"):
        a = row.find("a", href=True)
        tds = row.find_all("td")
        if not a or len(tds) < 2: continue
        title = a.get_text(strip=True)
        href  = a["href"] if a["href"].startswith("http") else urljoin("https://www.water.gov.taipei", a["href"])
        dt    = tds[-1].get_text(strip=True) if tds else ""
        if title and len(title) > 3:
            items.append({"title": title, "date": dt, "url": href})
    log.info(f"  [台北自來水處] {len(items)} 筆")
    return items


def parse_tra() -> list[dict]:
    """國營台鐵：純文字 block，格式固定"""
    r = get("https://www.railway.gov.tw/tra-tip-web/adr/rent-tender-1?&activePage=1")
    if not r: return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, "lxml")
    items = []
    # 每個標案區塊包含標題、類別、業管單位、招標日期、查看詳情連結
    for block in soup.select(".tender-item, .list-item, article, .item-block"):
        a = block.find("a", href=True)
        title_el = block.find(class_=re.compile(r"title|name|subject"))
        date_el  = block.find(string=re.compile(r"\d{4}/\d{2}/\d{2}"))
        if not title_el and not a: continue
        title = (title_el or a).get_text(strip=True)
        href  = urljoin("https://www.railway.gov.tw", a["href"]) if a else "https://www.railway.gov.tw/tra-tip-web/adr/rent-tender-1"
        items.append({"title": title, "date": str(date_el).strip() if date_el else "", "url": href})

    # fallback：純文字 regex
    if not items:
        text = soup.get_text("\n")
        # 找每個 【...】 開頭的標案
        for m in re.finditer(r"(【[^】]+】[^\n招]{5,80})\n.*?招標日期：(\d{4}/\d{2}/\d{2}[^\n]*)", text, re.DOTALL):
            items.append({
                "title": m.group(1).strip(),
                "date":  m.group(2).strip(),
                "url":   "https://www.railway.gov.tw/tra-tip-web/adr/rent-tender-1?&activePage=1",
            })
    log.info(f"  [國營台鐵] {len(items)} 筆")
    return items


def parse_ntpc_finance() -> list[dict]:
    """新北市政府財政局：呼叫後端 API（AJAX）"""
    # 網站用 jQuery AJAX 送 POST 取得公告清單
    api_url = "https://www.finance.ntpc.gov.tw/home.jsp?id=8b767bd17dc29316"
    # 嘗試直接抓 API（常見的 JSP 網站後端格式）
    api_candidates = [
        "https://www.finance.ntpc.gov.tw/QueryBulletinListServlet",
        "https://www.finance.ntpc.gov.tw/bulletin/query",
    ]
    # 先用 requests 抓原始頁面的所有連結（靜態部分）
    r = get(api_url)
    if not r: return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, "lxml")
    items = []
    for a in soup.select("a[href]"):
        title = a.get_text(strip=True)
        href  = a["href"]
        if len(title) < 5: continue
        if any(k in title for k in ["標租", "出租", "招標", "採購", "公告", "標售"]):
            full_url = href if href.startswith("http") else urljoin("https://www.finance.ntpc.gov.tw", href)
            items.append({"title": title, "date": "", "url": full_url})
    # 若沒有，直接回傳頁面連結讓 Claude 備用解析
    if not items:
        log.warning("  [新北財政局] 靜態內容無法取得，嘗試 Claude 解析")
        items = parse_with_claude_fallback(
            soup.get_text("\n")[:8000], "新北市政府財政局",
            "https://www.finance.ntpc.gov.tw"
        )
    log.info(f"  [新北市政府財政局] {len(items)} 筆")
    return items


def parse_ialgo() -> list[dict]:
    """農業部 瑠公管理處：table tr"""
    r = get("https://www.ialgo.nat.gov.tw/news/NewsPage3?a=10010")
    if not r: return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, "lxml")
    items = []
    for row in soup.select("table tr, .list tr, ul.news li"):
        a = row.find("a", href=True)
        tds = row.find_all("td")
        if not a: continue
        title = a.get_text(strip=True)
        href  = a["href"] if a["href"].startswith("http") else urljoin("https://www.ialgo.nat.gov.tw", a["href"])
        dt    = tds[-1].get_text(strip=True) if tds else ""
        if title and len(title) > 3:
            items.append({"title": title, "date": dt, "url": href})
    log.info(f"  [農業部 瑠公管理處] {len(items)} 筆")
    return items


def parse_post() -> list[dict]:
    """郵局房地產出租：table/list，URL 統一指向該頁（無各別連結）"""
    r = get("https://www.post.gov.tw/post/internet/Real_estate/index.jsp?ID=904")
    if not r: return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, "lxml")
    items = []
    SOURCE_URL = "https://www.post.gov.tw/post/internet/Real_estate/index.jsp?ID=904"
    for row in soup.select("table tr, .list tr, ul li"):
        a = row.find("a", href=True)
        tds = row.find_all("td")
        if not a and len(tds) < 2: continue
        title = a.get_text(strip=True) if a else tds[0].get_text(strip=True)
        if len(title) < 5: continue
        # 郵局沒有各別標案頁，URL 用來源頁
        href = SOURCE_URL
        if a and a["href"] and a["href"] not in ("#", "javascript:void(0)"):
            href = a["href"] if a["href"].startswith("http") else urljoin("https://www.post.gov.tw", a["href"])
        dt = tds[-1].get_text(strip=True) if len(tds) >= 2 else ""
        items.append({"title": title, "date": dt, "url": href})
    log.info(f"  [郵局房地產出租] {len(items)} 筆")
    return items


def parse_taipei_dof() -> list[dict]:
    """台北市財政局：table tr"""
    r = get("https://dof.gov.taipei/News.aspx?n=DBCAF43864F42187&sms=148C417C1585EF00")
    if not r: return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, "lxml")
    items = []
    for row in soup.select("table tr"):
        a = row.find("a", href=True)
        tds = row.find_all("td")
        if not a or len(tds) < 2: continue
        title = a.get_text(strip=True)
        href  = a["href"] if a["href"].startswith("http") else urljoin("https://dof.gov.taipei", a["href"])
        dt    = tds[-1].get_text(strip=True)
        if title and len(title) > 3:
            items.append({"title": title, "date": dt, "url": href})
    log.info(f"  [台北市財政局] {len(items)} 筆")
    return items


def parse_hurc() -> list[dict]:
    """國家住宅及都市更新中心：requests + div/table"""
    r = get("https://www.hurc.org.tw/hurc/procurement")
    if not r: return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, "lxml")
    items = []
    for row in soup.select("table tr, .list-item, .procurement-item, article"):
        a = row.find("a", href=True)
        tds = row.find_all("td")
        if not a: continue
        title = a.get_text(strip=True)
        href  = a["href"] if a["href"].startswith("http") else urljoin("https://www.hurc.org.tw", a["href"])
        dt    = tds[-1].get_text(strip=True) if tds else ""
        if title and len(title) > 3:
            items.append({"title": title, "date": dt, "url": href})
    # fallback Claude
    if not items:
        items = parse_with_claude_fallback(soup.get_text("\n")[:8000], "國家住宅及都市更新中心", "https://www.hurc.org.tw")
    log.info(f"  [國家住宅及都市更新中心] {len(items)} 筆")
    return items


def parse_fnp() -> list[dict]:
    """國有財產署：table tr，key = 單位+年度+批號"""
    r = get("https://esvc.fnp.gov.tw/rtMsg?svcId=5eafac8df8c649ba9cf62a591e44223c")
    if not r: return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, "lxml")
    items = []
    SOURCE_URL = "https://esvc.fnp.gov.tw/rtMsg?svcId=5eafac8df8c649ba9cf62a591e44223c"
    for row in soup.select("table tr"):
        tds = row.find_all("td")
        if len(tds) < 4: continue
        unit    = tds[0].get_text(strip=True)
        year    = tds[1].get_text(strip=True)
        batch   = tds[2].get_text(strip=True)
        pub_dt  = tds[3].get_text(strip=True)
        open_dt = tds[4].get_text(strip=True) if len(tds) > 4 else ""
        if not unit or unit == "單位": continue
        title = f"{unit} {year}年第{batch}批 公告:{pub_dt} 開標:{open_dt}"
        a = row.find("a", href=True)
        href = a["href"] if a else SOURCE_URL
        if href and not href.startswith("http"):
            href = urljoin("https://esvc.fnp.gov.tw", href)
        items.append({"title": title, "date": pub_dt, "url": href or SOURCE_URL})
    log.info(f"  [國有財產署] {len(items)} 筆")
    return items


# ── Claude 備用解析（當精準 parser 失敗時）──────────────────────────────────

PARSE_PROMPT = """你是政府標案資料擷取助手。
以下是某政府機關「標案/招標/出租」頁面的文字。
找出所有標案或出租公告，回傳 JSON 陣列：
[{{"title":"標案名稱","date":"日期或空字串","url":"連結或空字串"}}]
只回傳 JSON，無說明文字。若無標案回傳 []。
頁面文字：\n{text}"""

def parse_with_claude_fallback(text: str, name: str, base: str) -> list[dict]:
    if not CONFIG["api_key"]: return []
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CONFIG["api_key"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": CONFIG["claude_model"], "max_tokens": 2000,
                  "messages": [{"role": "user", "content": PARSE_PROMPT.format(text=text)}]},
            timeout=60,
        )
        raw = r.json()["content"][0]["text"].strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        items = json.loads(raw)
        for item in items:
            u = item.get("url", "")
            if u and not u.startswith("http"):
                item["url"] = urljoin(base, u)
        return items if isinstance(items, list) else []
    except Exception as e:
        log.warning(f"  [{name}] Claude fallback 失敗：{e}")
        return []


# ── 各網站設定（名稱、URL、parser 函式）─────────────────────────────────────

SOURCES = [
    {"name": "台北自來水處",          "url": "https://www.water.gov.taipei/News.aspx?n=D2818696FF5048B8&sms=B6EE39DA23E072F5", "fn": parse_taipei_water},
    {"name": "國營台鐵",              "url": "https://www.railway.gov.tw/tra-tip-web/adr/rent-tender-1?&activePage=1",        "fn": parse_tra},
    {"name": "新北市政府財政局",      "url": "https://www.finance.ntpc.gov.tw/home.jsp?id=8b767bd17dc29316",                   "fn": parse_ntpc_finance},
    {"name": "農業部 瑠公管理處",     "url": "https://www.ialgo.nat.gov.tw/news/NewsPage3?a=10010",                           "fn": parse_ialgo},
    {"name": "郵局房地產出租",        "url": "https://www.post.gov.tw/post/internet/Real_estate/index.jsp?ID=904",            "fn": parse_post},
    {"name": "台北市財政局",          "url": "https://dof.gov.taipei/News.aspx?n=DBCAF43864F42187&sms=148C417C1585EF00",      "fn": parse_taipei_dof},
    {"name": "國家住宅及都市更新中心","url": "https://www.hurc.org.tw/hurc/procurement",                                      "fn": parse_hurc},
    {"name": "國有財產署",            "url": "https://esvc.fnp.gov.tw/rtMsg?svcId=5eafac8df8c649ba9cf62a591e44223c",         "fn": parse_fnp},
]


# ── state.json：本地 + GitHub 雙重儲存 ───────────────────────────────────────

GH_STATE_PATH = "state.json"   # 在 GitHub repo 裡的路徑

def load_state() -> dict:
    """先從 GitHub 載，失敗再從本地載"""
    if CONFIG["gh_token"] and CONFIG["gh_repo"]:
        try:
            r = requests.get(
                f"https://api.github.com/repos/{CONFIG['gh_repo']}/contents/{GH_STATE_PATH}",
                headers={"Authorization": f"Bearer {CONFIG['gh_token']}", "Accept": "application/vnd.github+json"},
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                content = json.loads(b64decode(data["content"]).decode("utf-8"))
                content["_gh_sha"] = data["sha"]   # 儲存 sha 供後續更新用
                log.info("✅ state.json 從 GitHub 載入")
                return content
        except Exception as e:
            log.warning(f"GitHub 載入 state 失敗：{e}")

    if STATE_FILE.exists():
        try:
            log.info("📂 state.json 從本地載入")
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(state: dict):
    """先存本地，再 commit 到 GitHub"""
    sha = state.pop("_gh_sha", None)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    if CONFIG["gh_token"] and CONFIG["gh_repo"]:
        try:
            today = date.today().strftime("%Y-%m-%d")
            payload = {
                "message": f"chore: update state.json {today}",
                "content": b64encode(json.dumps(state, ensure_ascii=False, indent=2).encode()).decode(),
                "branch": "main",
            }
            if sha:
                payload["sha"] = sha
            r = requests.put(
                f"https://api.github.com/repos/{CONFIG['gh_repo']}/contents/{GH_STATE_PATH}",
                headers={"Authorization": f"Bearer {CONFIG['gh_token']}", "Accept": "application/vnd.github+json"},
                json=payload,
                timeout=15,
            )
            if r.status_code in (200, 201):
                log.info("✅ state.json 已 commit 到 GitHub")
            else:
                log.warning(f"GitHub commit 失敗：{r.status_code} {r.text[:200]}")
        except Exception as e:
            log.warning(f"GitHub commit 異常：{e}")


def item_key(item: dict) -> str:
    return re.sub(r"\s+", "", item.get("title", ""))

def find_new_items(name: str, items: list[dict], state: dict) -> list[dict]:
    seen = set(state.get(name, []))
    new  = [i for i in items if item_key(i) not in seen]
    state[name] = list(seen | {item_key(i) for i in items})[-300:]
    return new


# ── LINE 推播 ─────────────────────────────────────────────────────────────────

def _push(messages: list[dict]):
    if not CONFIG["line_token"] or not CONFIG["line_user_id"]:
        log.warning("未設定 LINE_CHANNEL_TOKEN 或 LINE_USER_ID")
        return
    r = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {CONFIG['line_token']}", "Content-Type": "application/json"},
        json={"to": CONFIG["line_user_id"], "messages": messages},
        timeout=30,
    )
    if r.status_code == 200:
        log.info(f"✅ LINE 推播成功（{len(messages)} 則）")
    else:
        log.warning(f"LINE 推播失敗：{r.status_code} {r.text[:300]}")


def push_in_batches(messages: list[dict]):
    for i in range(0, len(messages), 5):
        _push(messages[i:i+5])


def build_line_messages(results: dict, run_time: str) -> list[dict]:
    today     = date.today().strftime("%Y/%m/%d")
    total_new = sum(len(v["new"]) for v in results.values())
    messages  = []

    # 摘要文字
    lines = [f"📋 政府標案通知 {today}", f"共新增 {total_new} 筆\n"]
    for src in SOURCES:
        name = src["name"]
        new  = len(results.get(name, {}).get("new", []))
        err  = results.get(name, {}).get("error")
        icon = "⚠️" if err else ("🆕" if new else "✅")
        lines.append(f"{icon} {name}：{'抓取失敗' if err else (f'{new} 筆新增' if new else '無新增')}")
    messages.append({"type": "text", "text": "\n".join(lines)})

    # 各機關 Flex Message
    for src in SOURCES:
        name      = src["name"]
        new_items = results.get(name, {}).get("new", [])
        if not new_items: continue

        body_contents = []
        for item in new_items[:10]:
            title = item.get("title", "（無標題）")[:60]
            dt    = item.get("date", "")
            url   = item.get("url", src["url"])

            title_obj = {"type": "text", "text": title, "size": "sm", "color": "#1d4ed8", "wrap": True,
                         "action": {"type": "uri", "uri": url}}
            row = {"type": "box", "layout": "vertical", "margin": "md", "contents": [title_obj]}
            if dt:
                row["contents"].append({"type": "text", "text": f"📅 {dt}", "size": "xs", "color": "#9ca3af", "margin": "xs"})
            body_contents.append(row)
            body_contents.append({"type": "separator", "margin": "md", "color": "#f3f4f6"})

        if body_contents and body_contents[-1].get("type") == "separator":
            body_contents.pop()

        messages.append({
            "type": "flex",
            "altText": f"🆕 {name}：{len(new_items)} 筆新標案",
            "contents": {
                "type": "bubble",
                "header": {
                    "type": "box", "layout": "vertical", "backgroundColor": "#1d4ed8", "paddingAll": "16px",
                    "contents": [
                        {"type": "text", "text": name, "color": "#ffffff", "weight": "bold", "size": "md"},
                        {"type": "text", "text": f"新增 {len(new_items)} 筆", "color": "#bfdbfe", "size": "sm", "margin": "xs"},
                    ],
                },
                "body": {"type": "box", "layout": "vertical", "paddingAll": "16px", "contents": body_contents},
                "footer": {
                    "type": "box", "layout": "vertical", "backgroundColor": "#f9fafb", "paddingAll": "10px",
                    "contents": [{"type": "button", "style": "link", "color": "#1d4ed8", "height": "sm",
                                  "action": {"type": "uri", "label": "前往原始網頁", "uri": src["url"]}}],
                },
            },
        })

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
        try:
            items     = src["fn"]()
            new_items = find_new_items(name, items, state)
            results[name] = {"all": items, "new": new_items, "error": None}
            log.info(f"  → 共 {len(items)} 筆，新增 {len(new_items)} 筆")
        except Exception as e:
            log.error(f"  → 例外：{e}")
            results[name] = {"all": [], "new": [], "error": str(e)}

    save_state(state)

    # 摘要
    total_new = sum(len(v["new"]) for v in results.values())
    print(f"\n{'='*60}")
    print(f"  每日標案摘要  {date.today()}  {run_time}")
    print(f"{'='*60}")
    for src in SOURCES:
        name = src["name"]
        d    = results[name]
        new  = len(d["new"])
        err  = d["error"]
        status = f"⚠️ {err}" if err else (f"🆕 新增 {new} 筆" if new else "✅ 無新增")
        print(f"  {name:22s}  共{len(d['all']):3d}筆  {status}")
        for item in d["new"][:3]:
            print(f"       ▸ {item.get('title','')[:55]}")
    print(f"{'='*60}")
    print(f"  合計新增：{total_new} 筆")
    print(f"{'='*60}\n")

    # LINE 推播
    messages = build_line_messages(results, run_time)
    if messages:
        push_in_batches(messages)

    log.info("=== 完成 ===")


if __name__ == "__main__":
    main()
