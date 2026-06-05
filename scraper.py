#!/usr/bin/env python3
"""
政府標案每日爬蟲 v6
================================================================
收錄來源（9 個）：
  台北自來水處、國營台鐵、新北市政府財政局、農業部 瑠公管理處
  郵局房地產出租、台北市財政局、國家住宅及都市更新中心
  國有財產署、政府採購網

抓取策略：
  台北自來水處          requests + table tbody tr（CCMS 格式）
  國營台鐵             requests + CSS class / regex fallback
  新北市政府財政局      靜態連結 + Claude fallback
  農業部 瑠公管理處     requests + ul.commonList li parser
  郵局房地產出租        requests + table/list parser
  台北市財政局          requests + table tr（CCMS 格式）
  國家住宅及都市更新中心 requests + table/article + Claude fallback
  國有財產署            requests + ul li span/p parser（批號為 key）
  政府採購網            GitHub Gist HTML（由 Make 每日更新）

篩選架構（統一三層，設定於 SOURCES 每個來源）：
  regions   地區白名單 — 標題或機關名稱須含其中一詞
  whitelist 關鍵字白名單 — 標題須含其中一詞
  blacklist 關鍵字黑名單 — 標題含任一詞則排除
  + is_within_date_window() — 公告日期在當月 ±1 個月內

環境變數（必填）：
  LINE_CHANNEL_TOKEN  LINE Channel Access Token
  LINE_USER_ID        推播目標 LINE User ID（U 開頭）

選填：
  ANTHROPIC_API_KEY   Claude API 金鑰（新北財政局 / 住都中心備援解析）
  GITHUB_TOKEN        GitHub Personal Access Token（用於儲存 state）
  GITHUB_REPO         格式 owner/repo（例如 yourname/tender-scraper）
  STATE_FILE          本地備援路徑（預設 state.json）
"""

import json
import logging
import os
import re
from base64 import b64decode, b64encode
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

import requests

# ── 設定 ──────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
STATE_FILE = Path(os.getenv("STATE_FILE", SCRIPT_DIR / "state.json"))

# ── 全域篩選器（所有來源共用）────────────────────────────────────────────────
# whitelist：標題含任一詞才推播（空串列 = 不限）
# blacklist：標題含任一詞則丟棄（空串列 = 不排除）
# regions  ：標題或機關名稱含任一詞才推播（空串列 = 全台）
# date_window_days：公告日期距今 ±N 天內才推播（無法解析日期時放行）
GLOBAL_FILTER = {
    "whitelist": [
        "出租", "標租", "租賃", "招租", "徵租",
        "房地", "不動產", "標售", "廳舍", "地上物",
        "閒置空間", "公有土地", "招商", "承租",
    ],
    "blacklist": [
        "開標結果",
        "自動販賣機", "場地短期出租",
        "新建工程", "統包工程", "物業管理", "專案管理", "保險",
        "清洗作業", "鑄鐵直管", "延性鑄鐵", "塗裝", "管線",
        "財物採購", "勞務採購",
    ],
    "regions": ["台北", "臺北", "新北", "瑠公管理處"],  # 瑠公管理處轄區皆在台北/新北
}
DATE_WINDOW_DAYS = 10  # 公告日期距今 ±10 天

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

# ── 各網站精準 Parser ─────────────────────────────────────────────────────────

def parse_taipei_water() -> list[dict]:
    """台北自來水處：CCMS 系統 table tbody tr。
    欄位結構（每 td 含 data-title 屬性）：
      編號 | 標案名稱（含 <a> 連結） | 公告日期 | 開標日期 | 標案進度 | 開標結果
    修正說明：
      1. 選 tbody tr 避免 thead（th 無 td，舊版 select("table tr") 仍能跳過，
         但 water.gov.taipei 目前 HTTP 403 host_not_allowed，需等 IP 解封）
      2. 用 td[data-title="標案名稱"] 精準取標案連結，避免誤抓「開標結果」欄的 PDF 連結
      3. 日期取 td[data-title="公告日期"]，而非 tds[-1]（最後欄是「開標結果」非日期）
    """
    BASE = "https://www.water.gov.taipei"
    r = get(f"{BASE}/News.aspx?n=D2818696FF5048B8&sms=B6EE39DA23E072F5")
    if not r: return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, "lxml")
    items = []
    for row in soup.select("table tbody tr"):
        tds = row.find_all("td")
        if not tds:
            continue
        # 用 data-title 屬性建立欄位字典（CCMS 台北市 CMS 固定格式）
        td_map = {td.get("data-title", ""): td for td in tds}
        # 標案名稱欄（含連結）
        title_td = td_map.get("標案名稱") or td_map.get("標題") or td_map.get("主旨")
        if not title_td:
            # fallback：找第一個含 <a href> 的 td
            title_td = next((td for td in tds if td.find("a", href=True)), None)
        if not title_td:
            continue
        a = title_td.find("a", href=True)
        if not a:
            continue
        title = a.get_text(strip=True)
        href  = a["href"]
        if not href.startswith("http"):
            href = urljoin(BASE, href)
        # 公告日期（第 3 欄，data-title="公告日期"）
        date_td = td_map.get("公告日期") or td_map.get("發布日期") or td_map.get("日期")
        dt = date_td.get_text(strip=True) if date_td else ""
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
    # 只保留臺北營業分處的標案
    items = [i for i in items if "臺北營業分處" in i.get("title", "")]
    log.info(f"  [國營台鐵] {len(items)} 筆（僅臺北營業分處）")
    return items


def parse_ntpc_finance() -> list[dict]:
    """新北市政府財政局：呼叫後端 API（AJAX）"""
    api_url = "https://www.finance.ntpc.gov.tw/home.jsp?id=8b767bd17dc29316"
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
    """農業部 瑠公管理處：ul.commonList li.commonList-item > a.newsItem"""
    BASE = "https://www.ialgo.nat.gov.tw"
    r = get(f"{BASE}/news/NewsPage3?a=10010")
    if not r: return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, "lxml")
    items = []
    for li in soup.select("ul.commonList li.commonList-item"):
        a = li.find("a", class_="newsItem")
        if not a:
            continue
        href = a.get("href", "")
        if href and not href.startswith("http"):
            href = urljoin(BASE, href)
        title_div = a.find("div", class_="newsItem__content-title")
        title = title_div.get_text(strip=True) if title_div else a.get("title", "")
        date_span = a.find("span", class_="newsItem__meta-item")
        dt = date_span.get_text(strip=True) if date_span else ""
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
    """國有財產署：SSR 頁面，ul > li > span.title-message + p.form-height 結構。
    詳情 URL：https://esvc.fnp.gov.tw/rtMsg/showInfomation?msgId={msgId}
    fallback：table tr（相容未來改版）
    """
    BASE_URL   = "https://esvc.fnp.gov.tw"
    SVC_ID     = "5eafac8df8c649ba9cf62a591e44223c"
    SOURCE_URL = f"{BASE_URL}/rtMsg?svcId={SVC_ID}"
    DETAIL_URL = f"{BASE_URL}/rtMsg/showInfomation"

    r = get(SOURCE_URL)
    if not r:
        return []

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, "lxml")
    items = []

    # ── 主要策略：ul > li，每個 li 為一筆標案 ──────────────────────────
    for li in soup.select("ul li"):
        labels = [s.get_text(strip=True) for s in li.select("span.title-message")]
        values = [p.get_text(strip=True) for p in li.select("p.form-height")]
        if not labels or not values:
            continue
        fields = dict(zip(labels, values))

        unit    = fields.get("單位", "")
        year    = fields.get("年度", "")
        batch   = fields.get("批號", "")
        pub_dt  = fields.get("公告日期", "")
        open_dt = fields.get("開標日期", "")
        if not unit or unit in ("單位", "機關單位"):
            continue

        # msgId：從 li 屬性或內部連結取得
        msg_id = (li.get("data-msgid") or li.get("data-msg-id")
                  or li.get("data-id") or "")
        a = li.find("a", href=True)
        if a:
            href = a["href"]
            if not href.startswith("http"):
                href = urljoin(BASE_URL, href)
            m = re.search(r"msgId=([a-f0-9A-F]+)", href)
            if m:
                msg_id = m.group(1)
        href = f"{DETAIL_URL}?msgId={msg_id}" if msg_id else SOURCE_URL

        title = f"{unit} {year}年第{batch}批 公告:{pub_dt} 開標:{open_dt}"
        items.append({"title": title, "date": pub_dt, "url": href})

    # ── fallback：table tr ───────────────────────────────────────────────
    if not items:
        for row in soup.select("table tbody tr, table tr"):
            tds = row.find_all("td")
            if len(tds) < 4:
                continue
            unit    = tds[0].get_text(strip=True)
            year    = tds[1].get_text(strip=True)
            batch   = tds[2].get_text(strip=True)
            pub_dt  = tds[3].get_text(strip=True)
            open_dt = tds[4].get_text(strip=True) if len(tds) > 4 else ""
            if not unit or unit in ("單位", "機關單位"):
                continue
            title = f"{unit} {year}年第{batch}批 公告:{pub_dt} 開標:{open_dt}"
            a = row.find("a", href=True)
            href = a["href"] if a else SOURCE_URL
            if href and not href.startswith("http"):
                href = urljoin(BASE_URL, href)
            items.append({"title": title, "date": pub_dt, "url": href or SOURCE_URL})

    log.info(f"  [國有財產署] {len(items)} 筆")
    return items


def parse_pcc() -> list[dict]:
    """政府採購網：從 Make 每日更新的 GitHub Gist 讀取 HTML。
    Gist 由 Make 定期 PATCH 更新，無 IP 封鎖問題。
    欄位順序（依截圖）：
      項次 | 機關名稱 | 標案案號↩標案名稱 | 傳輸次數 | 招標方式 |
      採購性質 | 公告日期 | 截止投標 | 預算金額 | 功能選項(檢視)
    關鍵字 / 地區篩選由 SOURCES.passes_filters() 統一處理。
    """
    from bs4 import BeautifulSoup

    BASE     = "https://web.pcc.gov.tw"
    GIST_ID    = "816c04d08f02cd2e6e7623f5f5450f8a"
    GIST_FILE  = "pcc.html"
    GIST_API   = f"https://api.github.com/gists/{GIST_ID}"
    SOURCE_URL = (
        f"{BASE}/prkms/tender/common/basic/readTenderBasic"
        f"?firstSearch=true&searchType=basic&isBinding=N&isLogIn=N"
        f"&tenderType=TENDER_DECLARATION&tenderWay=TENDER_WAY_ALL_DECLARATION&dateType=isNow"
    )

    gh_headers = {"Accept": "application/vnd.github+json"}
    if CONFIG["gh_token"]:
        gh_headers["Authorization"] = f"Bearer {CONFIG['gh_token']}"

    try:
        resp = requests.get(GIST_API, headers=gh_headers, timeout=15)
        gist_json = resp.json()
        html = gist_json.get("files", {}).get(GIST_FILE, {}).get("content", "")
    except Exception as e:
        log.warning(f"  [政府採購網] Gist API 讀取失敗：{e}")
        return []

    html = html.strip()
    if not html or html == "<!-- placeholder -->":
        log.warning("  [政府採購網] Gist 尚為 placeholder，Make 尚未執行，跳過")
        return []

    # Make 用 base64() 編碼後存入 Gist，先嘗試 decode
    try:
        import base64 as _b64
        html = _b64.b64decode(html).decode("utf-8")
    except Exception:
        pass  # 非 base64（舊格式或直接 HTML），直接使用

    soup  = BeautifulSoup(html, "lxml")
    items = []

    # 結果 table：header 含「項次」且「功能選項」（與表單 table 區分）
    target_table = None
    for tbl in soup.find_all("table"):
        hdr = tbl.find("tr")
        if hdr and "項次" in hdr.get_text() and "功能選項" in hdr.get_text():
            target_table = tbl
            break

    if target_table:
        # 欄位固定：[0]項次 [1]機關 [2]案號+案名 [3]傳輸次數
        # [4]招標方式 [5]採購性質 [6]公告日期 [7]截止投標 [8]預算 [9]功能選項
        for row in target_table.find_all("tr")[1:]:
            tds = row.find_all("td")
            if len(tds) < 9:
                continue

            agency = tds[1].get_text(strip=True)

            # ── 標案名稱：從 JS pageCode2Img("案名") 取出 ─────────────
            name_td  = tds[2]
            td_html  = str(name_td)
            m_title  = re.search(r'pageCode2Img\("([^"]+)"\)', td_html)
            title    = m_title.group(1) if m_title else ""
            # fallback：stripped_strings 最長一行
            if not title:
                lines = list(name_td.stripped_strings)
                title = max(lines, key=len) if lines else ""

            if not title or len(title) <= 3:
                continue

            # ── 檢視連結（td[9] 或 td[2] 的 <a>）──────────────────────
            view_url = SOURCE_URL
            view_a   = tds[9].find("a", href=True) or tds[2].find("a", href=True)
            if view_a:
                href = view_a["href"]
                view_url = href if href.startswith("http") else urljoin(BASE, href)

            # ── 公告日期 ────────────────────────────────────────────────
            date_str = tds[6].get_text(strip=True)

            items.append({"title": title, "date": date_str, "url": view_url, "agency": agency})

    # Claude fallback（HTML 無法解析時）
    if not items and CONFIG["api_key"]:
        items = parse_with_claude_fallback(soup.get_text("\n")[:8000], "政府採購網", BASE)

    log.info(f"  [政府採購網] {len(items)} 筆")
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


# ── 各網站設定 ────────────────────────────────────────────────────────────────
SOURCES = [
    {"name": "台北自來水處",       "url": "https://www.water.gov.taipei/News.aspx?n=D2818696FF5048B8&sms=B6EE39DA23E072F5",           "fn": parse_taipei_water},
    {"name": "國營台鐵",           "url": "https://www.railway.gov.tw/tra-tip-web/adr/rent-tender-1?&activePage=1",                   "fn": parse_tra},
    {"name": "新北市政府財政局",   "url": "https://www.finance.ntpc.gov.tw/home.jsp?id=8b767bd17dc29316",                              "fn": parse_ntpc_finance},
    {"name": "農業部 瑠公管理處",  "url": "https://www.ialgo.nat.gov.tw/news/NewsPage3?a=10010",                                       "fn": parse_ialgo},
    {"name": "郵局房地產出租",     "url": "https://www.post.gov.tw/post/internet/Real_estate/index.jsp?ID=904",                        "fn": parse_post},
    {"name": "台北市財政局",       "url": "https://dof.gov.taipei/News.aspx?n=DBCAF43864F42187&sms=148C417C1585EF00",                 "fn": parse_taipei_dof},
    {"name": "國家住宅及都市更新中心", "url": "https://www.hurc.org.tw/hurc/procurement",                                             "fn": parse_hurc},
    {"name": "國有財產署",         "url": "https://esvc.fnp.gov.tw/rtMsg?svcId=5eafac8df8c649ba9cf62a591e44223c",                    "fn": parse_fnp},
    {"name": "政府採購網",         "url": "https://web.pcc.gov.tw/prkms/tender/common/basic/readTenderBasic",                         "fn": parse_pcc},
]


def passes_filters(item: dict) -> bool:
    """套用 GLOBAL_FILTER 的白名單、黑名單、地區篩選（所有來源共用）。"""
    title  = item.get("title", "")
    agency = item.get("agency", "")
    text   = title + agency

    regions = GLOBAL_FILTER.get("regions", [])
    if regions and not any(k in text for k in regions):
        return False

    whitelist = GLOBAL_FILTER.get("whitelist", [])
    if whitelist and not any(k in title for k in whitelist):
        return False

    blacklist = GLOBAL_FILTER.get("blacklist", [])
    if blacklist and any(k in title for k in blacklist):
        return False

    return True


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


SENT_LOG_FILE = SCRIPT_DIR / "sent_log.json"
SENT_LOG_KEEP_DAYS = 30


def save_sent_log(results: dict, run_time: str):
    """將本次推播的 notify 項目存入 sent_log.json，保留最近 N 天。"""
    key = f"{date.today()} {run_time}"
    entry = {
        name: d["notify"]
        for name, d in results.items()
        if d.get("notify")
    }
    try:
        log_data = json.loads(SENT_LOG_FILE.read_text(encoding="utf-8")) if SENT_LOG_FILE.exists() else {}
    except Exception:
        log_data = {}

    log_data[key] = entry

    # 只保留最近 SENT_LOG_KEEP_DAYS 天
    cutoff = (date.today() - timedelta(days=SENT_LOG_KEEP_DAYS)).strftime("%Y-%m-%d")
    log_data = {k: v for k, v in log_data.items() if k[:10] >= cutoff}

    SENT_LOG_FILE.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"✅ sent_log.json 已更新（key: {key}）")


def item_key(item: dict) -> str:
    return re.sub(r"\s+", "", item.get("title", ""))

def find_new_items(name: str, items: list[dict], state: dict) -> list[dict]:
    seen = set(state.get(name, []))
    new  = [i for i in items if item_key(i) not in seen]
    state[name] = list(seen | {item_key(i) for i in items})[-300:]
    return new


def _extract_dates(text: str) -> list[date]:
    """從字串擷取完整日期（年月日），支援民國／西元、多種分隔符。"""
    found = []
    # 西元：2026-05-12、2026/05/12、2026年05月12日
    for m in re.finditer(r"(20\d{2})[/-年](\d{1,2})[/-月](\d{1,2})", text):
        try:
            found.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    # 民國：115-05-12、115/05/12、115.05.12、115年05月12日
    for m in re.finditer(r"\b(1\d{2})[/\-.年](\d{1,2})[/\-.月](\d{1,2})", text):
        try:
            found.append(date(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    return found


def is_within_date_window(item: dict, window_days: int = DATE_WINDOW_DAYS) -> bool:
    """若標案公告日期在今日 ±window_days 天內則回傳 True；無法解析日期則放行。
    優先用 date 欄位，為空時才掃 title 作為備援。
    """
    today = date.today()
    lo = today - timedelta(days=window_days)
    hi = today + timedelta(days=window_days)

    date_str = item.get("date", "").strip()
    candidates = _extract_dates(date_str) if date_str else _extract_dates(item.get("title", ""))
    if not candidates:
        return True  # 無法解析 → 放行
    return any(lo <= d <= hi for d in candidates)


# ── LINE 推播 ─────────────────────────────────────────────────────────────────

def _push(messages: list[dict]):
    if not CONFIG["line_token"]:
        log.warning("未設定 LINE_CHANNEL_TOKEN")
        return
    r = requests.post(
        "https://api.line.me/v2/bot/message/broadcast",
        headers={"Authorization": f"Bearer {CONFIG['line_token']}", "Content-Type": "application/json"},
        json={"messages": messages},
        timeout=30,
    )
    if r.status_code == 200:
        log.info(f"✅ LINE broadcast 成功（{len(messages)} 則）")
    else:
        log.warning(f"LINE broadcast 失敗：{r.status_code} {r.text[:300]}")


def push_in_batches(messages: list[dict]):
    for i in range(0, len(messages), 5):
        _push(messages[i:i+5])


def build_line_messages(results: dict, run_time: str, original_date: str = "") -> list[dict]:
    today        = original_date or date.today().strftime("%Y/%m/%d")
    total_notify = sum(len(v.get("notify", [])) for v in results.values())
    messages     = []

    # 摘要文字（只統計推播筆數）
    lines = [f"📋 政府標案通知 {today}", f"近期新增 {total_notify} 筆\n"]
    for src in SOURCES:
        name   = src["name"]
        notify = len(results.get(name, {}).get("notify", []))
        err    = results.get(name, {}).get("error")
        icon   = "⚠️" if err else ("🆕" if notify else "✅")
        lines.append(f"{icon} {name}：{'抓取失敗' if err else (f'{notify} 筆' if notify else '無近期新增')}")
    lines.append("\n⚠️ 免責聲明：本通知由自動爬蟲產生，資料僅供參考，請以各機關官方公告為準。")
    messages.append({"type": "text", "text": "\n".join(lines)})

    # 各機關 Flex Message（只顯示通過日期篩選的標案）
    for src in SOURCES:
        name      = src["name"]
        new_items = results.get(name, {}).get("notify", [])
        if not new_items: continue

        body_contents = []
        for item in new_items[:10]:
            title = item.get("title", "（無標題）")[:60]
            dt    = item.get("date", "")
            url   = item.get("url", src["url"])

            title_obj = {"type": "text", "text": f"🔗 {title}", "size": "sm", "color": "#1d4ed8", "wrap": True}
            row = {
                "type": "box", "layout": "vertical", "margin": "md",
                "action": {"type": "uri", "uri": url},
                "contents": [title_obj],
            }
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
                        {"type": "text", "text": f"近期新增 {len(new_items)} 筆", "color": "#bfdbfe", "size": "sm", "margin": "xs"},
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
            items        = src["fn"]()
            new_items    = find_new_items(name, items, state)
            notify_items = [i for i in new_items if passes_filters(i) and is_within_date_window(i)]
            results[name] = {"all": items, "new": new_items, "notify": notify_items, "error": None}
            log.info(f"  → 共 {len(items)} 筆，新增 {len(new_items)} 筆，推播 {len(notify_items)} 筆")
        except Exception as e:
            log.error(f"  → 例外：{e}")
            results[name] = {"all": [], "new": [], "notify": [], "error": str(e)}

    save_state(state)

    # 摘要
    total_new    = sum(len(v["new"])    for v in results.values())
    total_notify = sum(len(v["notify"]) for v in results.values())
    print(f"\n{'='*60}")
    print(f"  每日標案摘要  {date.today()}  {run_time}")
    print(f"{'='*60}")
    for src in SOURCES:
        name   = src["name"]
        d      = results[name]
        new    = len(d["new"])
        notify = len(d["notify"])
        err    = d["error"]
        if err:
            status = f"⚠️ {err}"
        elif new:
            status = f"🆕 新增 {new} 筆（推播 {notify} 筆）"
        else:
            status = "✅ 無新增"
        print(f"  {name:22s}  共{len(d['all']):3d}筆  {status}")
        for item in d["notify"][:3]:
            print(f"       ▸ {item.get('title','')[:55]}")
    print(f"{'='*60}")
    print(f"  合計新增：{total_new} 筆  推播：{total_notify} 筆")
    print(f"{'='*60}\n")

    # 儲存本次推播內容到 sent_log.json（供日後重播）
    save_sent_log(results, run_time)

    # LINE 推播（只發近期標案）
    messages = build_line_messages(results, run_time)
    if messages:
        push_in_batches(messages)

    log.info("=== 完成 ===")


if __name__ == "__main__":
    main()
