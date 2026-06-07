#!/usr/bin/env python3
"""
政府標案每日爬蟲 v9
================================================================
收錄來源（14 個）：
  台北自來水處、國營台鐵、新北市政府不動產標租、農業部 瑠公管理處
  郵局房地產出租、台北市財政局、國家住宅及都市更新中心
  國有財產署、政府採購網、教育部學產基金、台北市都發局
  國防部政治作戰局、土地銀行出租不動產、Google Alerts

抓取策略：
  台北自來水處            requests + table tbody tr（CCMS 格式）
  國營台鐵               requests + CSS class / regex fallback
  新北市政府不動產標租     requests + table tbody tr parser + Claude fallback
  農業部 瑠公管理處       requests + ul.commonList li parser
  郵局房地產出租           requests + table/list parser
  台北市財政局            requests + table tr（CCMS 格式）
  國家住宅及都市更新中心   requests + table/article + Claude fallback
  國有財產署              4 頁面 table parser（招標/地上權/停車/創業，篩北區分署）
  政府採購網              直接抓取 PCC 查詢頁（出租/標租 關鍵字，近 7 天招標公告）
  教育部學產基金           requests + table tbody tr（含日期格式驗證）+ Claude fallback
  台北市都發局            requests + table tr + Claude fallback
  國防部政治作戰局         requests + table tr + Claude fallback（眷村土地標租）
  土地銀行出租不動產       requests + table tr + Claude fallback

篩選架構（統一三層，設定於 SOURCES 每個來源）：
  regions   地區白名單 — 標題或機關名稱須含其中一詞
  whitelist 關鍵字白名單 — 標題須含其中一詞
  blacklist 關鍵字黑名單 — 標題含任一詞則排除
  + is_within_date_window() — 公告日期在當月 ±1 個月內

環境變數（必填）：
  LINE_CHANNEL_TOKEN  LINE Channel Access Token

選填：
  ANTHROPIC_API_KEY   Claude API 金鑰（備援解析用）
  GITHUB_TOKEN        GitHub Personal Access Token（用於儲存 state）
  GITHUB_REPO         格式 owner/repo（例如 yourname/tender-scraper）
  STATE_FILE          本地備援路徑（預設 state.json）
"""

import json
import logging
import os
import re
from base64 import b64decode, b64encode
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests

# ── 設定 ──────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
STATE_FILE = Path(os.getenv("STATE_FILE", SCRIPT_DIR / "state.json"))
DRY_RUN    = os.getenv("DRY_RUN", "false").lower() == "true"

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
    注意：water.gov.taipei 目前有時 HTTP 403 host_not_allowed，視網路環境而定。
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
        td_map = {td.get("data-title", ""): td for td in tds}
        title_td = td_map.get("標案名稱") or td_map.get("標題") or td_map.get("主旨")
        if not title_td:
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
    """郵局房地產出租：ul.NewsList li a 結構。
    HTML: <a href="index.jsp?...news_no=N...">
            <span class="Date">115/06/04</span>
            <span class="Topic">公告標題<span class="label"></span></span>
            <p class="Thumbs">摘要...</p>
          </a>
    """
    BASE = "https://www.post.gov.tw/post/internet/Real_estate/"
    URL  = f"{BASE}index.jsp?ID=904"
    r = get(URL)
    if not r: return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, "lxml")
    items = []
    for a in soup.select("ul.NewsList li a[href]"):
        date_span  = a.find("span", class_="Date")
        topic_span = a.find("span", class_="Topic")
        if not topic_span:
            continue
        # 移除 span.Topic 內的巢狀 span（例如 span.label 空白標籤）
        for nested in topic_span.find_all("span"):
            nested.decompose()
        title = topic_span.get_text(strip=True)
        if len(title) < 5:
            continue
        dt   = date_span.get_text(strip=True) if date_span else ""
        href = a["href"]
        full_href = href if href.startswith("http") else BASE + href
        items.append({"title": title, "date": dt, "url": full_href})
    log.info(f"  [郵局房地產出租] {len(items)} 筆")
    return items


def parse_taipei_dof() -> list[dict]:
    """台北市財政局：CCMS table tr。
    欄位：編號 | 標案名稱 | 公告日期(data-title) | 開標日期 | 標案進度 | 開標結果
    使用 data-title 定位「公告日期」欄，避免抓到最後欄「開標結果」。
    """
    r = get("https://dof.gov.taipei/News.aspx?n=DBCAF43864F42187&sms=148C417C1585EF00")
    if not r: return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, "lxml")
    items = []
    for row in soup.select("table tbody tr, table tr"):
        a = row.find("a", href=True)
        tds = row.find_all("td")
        if not a or len(tds) < 2: continue
        title = a.get_text(strip=True)
        if len(title) < 4: continue
        href  = a["href"] if a["href"].startswith("http") else urljoin("https://dof.gov.taipei", a["href"])
        td_map = {td.get("data-title", ""): td for td in tds}
        date_td = td_map.get("公告日期") or td_map.get("發布日期") or td_map.get("日期")
        dt = date_td.get_text(strip=True) if date_td else ""
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


# 國有財產署 4 個類別頁面（北區分署）
# 表格欄位：單位 | 年度 | 批號 | 種類 | 公告日期 | 開標日期 | 觀看人數
FNP_PAGES = [
    {"cat": "招標公告",   "url": "https://esvc.fnp.gov.tw/rtMsg?svcId=5eafac8df8c649ba9cf62a591e44223c"},
    {"cat": "地上權設定", "url": "https://esvc.fnp.gov.tw/tenderSetSuperficies?svcId=f6c87d65ebc84e988b4d245777d83a81"},
    {"cat": "停車場",     "url": "https://esvc.fnp.gov.tw/parkingMsg?svcId=a211eb824d6841d598fc784fb44ba962"},
    {"cat": "創業租賃",   "url": "https://esvc.fnp.gov.tw/crtMsg?svcId=ecc2694bdf6144e298053cb2a9f2460f"},
]
FNP_TARGET_UNIT = "北區分署"
FNP_BASE = "https://esvc.fnp.gov.tw"


def parse_fnp() -> list[dict]:
    """國有財產署：抓取 4 個類別頁面，篩選北區分署的招標公告。
    HTML 結構：a.message-flex > span.title-message + p.form-height
    msgId 從 onclick="showInfomation('xxx')" 提取。
    """
    from bs4 import BeautifulSoup

    all_items = []
    for page in FNP_PAGES:
        r = get(page["url"])
        if not r:
            log.warning(f"  [國有財產署/{page['cat']}] 連線失敗")
            continue

        soup  = BeautifulSoup(r.text, "lxml")
        count = 0
        base_path = page["url"].split("?")[0]  # e.g. .../tenderSetSuperficies

        for a in soup.select("a.message-flex"):
            # 提取欄位（label → value）
            labels = [s.get_text(strip=True) for s in a.select("span.title-message")]
            values = [p.get_text(strip=True) for p in a.select("p.form-height")]
            fields = dict(zip(labels, values))

            unit = fields.get("單位", "")
            if not unit or FNP_TARGET_UNIT not in unit:
                continue

            year    = fields.get("年度", "")
            batch   = fields.get("批號", "")
            kind    = fields.get("種類", page["cat"])
            pub_dt  = fields.get("公告日期", "")
            open_dt = fields.get("開標日期", "")

            # 從 onclick 取 msgId，組成詳細頁 URL
            onclick = a.get("onclick", "")
            m = re.search(r"showInfomation\('([^']+)'\)", onclick)
            msg_id  = m.group(1) if m else ""
            url = f"{base_path}/showInfomation?msgId={msg_id}" if msg_id else page["url"]

            title = f"【{page['cat']}】{unit} {year}年第{batch}批 {kind} 公告:{pub_dt} 開標:{open_dt}"
            all_items.append({"title": title, "date": pub_dt, "url": url, "agency": "國有財產署"})
            count += 1

        log.info(f"  [國有財產署/{page['cat']}] {count} 筆")

    log.info(f"  [國有財產署] 合計 {len(all_items)} 筆（4 類別，{FNP_TARGET_UNIT}）")
    return all_items


def _pcc_fetch_keyword(keyword: str, start: str, end: str) -> list[dict]:
    """抓取 PCC 單一關鍵字的查詢結果，回傳 item 清單。"""
    from bs4 import BeautifulSoup
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    BASE = "https://web.pcc.gov.tw"
    URL  = (
        f"{BASE}/prkms/tender/common/basic/readTenderBasic"
        f"?firstSearch=true&searchType=basic&isBinding=N&isLogIn=N"
        f"&orgName=&orgId="
        f"&tenderName={requests.utils.quote(keyword)}"
        f"&tenderId="
        f"&tenderType=TENDER_DECLARATION"
        f"&tenderWay=TENDER_WAY_ALL_DECLARATION"
        f"&dateType=isNow"
        f"&tenderStartDate={start.replace('/', '%2F')}"
        f"&tenderEndDate={end.replace('/', '%2F')}"
        f"&radProctrgCate=&policyAdvocacy="
    )
    try:
        r = requests.get(URL, headers=HTTP_HEADERS, timeout=30, verify=False)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
    except Exception as e:
        log.warning(f"  [政府採購網/{keyword}] 連線失敗：{e}")
        return []

    soup  = BeautifulSoup(r.text, "lxml")
    items = []

    target_table = None
    for tbl in soup.find_all("table"):
        hdr = tbl.find("tr")
        if hdr and "項次" in hdr.get_text() and "功能選項" in hdr.get_text():
            target_table = tbl
            break

    if target_table:
        for row in target_table.find_all("tr")[1:]:
            tds = row.find_all("td")
            if len(tds) < 9:
                continue
            agency  = tds[1].get_text(strip=True)
            name_td = tds[2]
            td_html = str(name_td)
            m_title = re.search(r'pageCode2Img\("([^"]+)"\)', td_html)
            title   = m_title.group(1) if m_title else ""
            if not title:
                lines = list(name_td.stripped_strings)
                title = max(lines, key=len) if lines else ""
            if not title or len(title) <= 3:
                continue
            view_a   = (tds[9].find("a", href=True) if len(tds) > 9 else None) or tds[2].find("a", href=True)
            view_url = URL
            if view_a:
                href = view_a["href"]
                view_url = href if href.startswith("http") else urljoin(BASE, href)
            date_str = tds[6].get_text(strip=True)
            items.append({"title": title, "date": date_str, "url": view_url, "agency": agency})

    if not items and CONFIG["api_key"]:
        items = parse_with_claude_fallback(soup.get_text("\n")[:8000], "政府採購網", BASE)

    log.info(f"  [政府採購網/{keyword}] {len(items)} 筆")
    return items


def parse_pcc() -> list[dict]:
    """政府採購網：以「出租」和「標租」為關鍵字查詢近 7 天招標公告，合併去重。"""
    today = date.today()
    start = (today - timedelta(days=7)).strftime("%Y/%m/%d")
    end   = today.strftime("%Y/%m/%d")

    all_items: list[dict] = []
    for kw in ["出租", "標租"]:
        all_items.extend(_pcc_fetch_keyword(kw, start, end))

    # 按 title 去重（兩個 keyword 查詢可能重疊）
    seen: set[str] = set()
    items: list[dict] = []
    for i in all_items:
        key = re.sub(r"\s+", "", i.get("title", ""))
        if key not in seen:
            seen.add(key)
            items.append(i)

    log.info(f"  [政府採購網] 合計 {len(items)} 筆（去重後）")
    return items


def parse_moe_xuechan() -> list[dict]:
    """教育部學產基金：標租不動產公告（CCMS 系統，結構同台北市財政局）。
    注意：部分雲端環境 IP 被 WAF 封鎖，GitHub Actions runner 可正常存取。
    """
    BASE = "https://depart.moe.edu.tw"
    URL  = f"{BASE}/ed4100/News.aspx?n=D62A8AE8773C5F8A&sms=4FEEAAFFCFBA1F3D"
    r = get(URL)
    if not r:
        return []
    from bs4 import BeautifulSoup
    soup  = BeautifulSoup(r.text, "lxml")
    items = []
    for row in soup.select("table tbody tr, table tr"):
        tds = row.find_all("td")
        if len(tds) < 2:
            continue
        a = row.find("a", href=True)
        if not a:
            continue
        title = a.get_text(strip=True)
        if len(title) < 5:
            continue
        href = a["href"] if a["href"].startswith("http") else urljoin(BASE, a["href"])
        dt   = tds[-1].get_text(strip=True)
        # 跳過最後欄不含日期格式的列（如橫幅廣告、宣傳文字）
        if dt and not re.search(r"\d{2,4}[/.\-年]\d{1,2}", dt):
            continue
        items.append({"title": title, "date": dt, "url": href, "agency": "教育部學產基金"})
    if not items:
        items = parse_with_claude_fallback(soup.get_text("\n")[:8000], "教育部學產基金", BASE)
    log.info(f"  [教育部學產基金] {len(items)} 筆")
    return items


def parse_taipei_udd() -> list[dict]:
    """台北市都市發展局：不動產標售租公告。
    表格結構：tds[0]=公告標題文字, tds[1]=類別連結（不動產標售租公告）, tds[-1]=日期
    不用 a.get_text()（會得到類別名稱），改用 tds[0] 完整文字取得實際標題。
    """
    BASE = "https://udd.gov.taipei"
    URL  = f"{BASE}/events/psxwq1j"
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        r = requests.get(URL, headers=HTTP_HEADERS, timeout=20, verify=False)
        r.encoding = r.apparent_encoding or "utf-8"
    except Exception as e:
        log.warning(f"GET 失敗 {URL}：{e}")
        return []
    from bs4 import BeautifulSoup
    soup  = BeautifulSoup(r.text, "lxml")
    items = []
    for row in soup.select("table tbody tr, table tr"):
        tds = row.find_all("td")
        if len(tds) < 2:
            continue
        # 標題取第一欄完整文字（非 <a> 的連結文字）
        title = tds[0].get_text(strip=True)
        if len(title) < 8:
            continue
        # 最後欄必須含日期，否則跳過（排除表頭列）
        dt = tds[-1].get_text(strip=True)
        if dt and not re.search(r"\d{2,4}[/.\-年]\d{1,2}", dt):
            continue
        # URL：優先取第一欄的連結（文章連結），其次任何列內連結，最後 fallback listing
        a = tds[0].find("a", href=True) or row.find("a", href=True)
        href = URL
        if a:
            raw = a["href"]
            if raw and raw not in ("#", "javascript:void(0)", "/"):
                href = raw if raw.startswith("http") else urljoin(BASE, raw)
        items.append({"title": title, "date": dt, "url": href, "agency": "台北市都發局"})
    if not items:
        items = parse_with_claude_fallback(soup.get_text("\n")[:8000], "台北市都發局", BASE)
    log.info(f"  [台北市都發局] {len(items)} 筆")
    return items


def parse_ntpc_property() -> list[dict]:
    """新北市政府公有不動產標租資訊。
    換源：finance.ntpc.gov.tw 的公告頁為 AJAX 搜尋表單，無法靜態抓取。
    改用 ntpc.gov.tw 的公有不動產標租資訊頁（CCMS 列表格式）。
    注意：部分雲端環境 IP 被 WAF 封鎖，GitHub Actions runner 可正常存取。
    """
    BASE = "https://www.ntpc.gov.tw"
    URL  = f"{BASE}/ch/home.jsp?id=b7c44e481de3b2bd"
    r = get(URL)
    if not r:
        return []
    from bs4 import BeautifulSoup
    soup  = BeautifulSoup(r.text, "lxml")
    items = []
    for row in soup.select("table tbody tr, table tr"):
        a   = row.find("a", href=True)
        tds = row.find_all("td")
        if not a or len(tds) < 2:
            continue
        title = a.get_text(strip=True)
        if len(title) < 8:
            continue
        href = a["href"] if a["href"].startswith("http") else urljoin(BASE, a["href"])
        dt   = tds[-1].get_text(strip=True)
        # 跳過最後欄不含日期格式的列
        if dt and not re.search(r"\d{2,4}[/.\-年]\d{1,2}", dt):
            continue
        items.append({"title": title, "date": dt, "url": href, "agency": "新北市政府"})
    if not items:
        items = parse_with_claude_fallback(soup.get_text("\n")[:8000], "新北市政府不動產標租", BASE)
    log.info(f"  [新北市政府不動產標租] {len(items)} 筆")
    return items


def parse_gpwd() -> list[dict]:
    """國防部政治作戰局：國軍老舊眷村土地標租公告。
    URL: Publish.aspx?cnid=609（眷村土地標租）
    採 CCMS table tr 通用模式，失敗時 Claude fallback。
    """
    BASE = "https://gpwd.mnd.gov.tw"
    URL  = f"{BASE}/Publish.aspx?cnid=609"
    r = get(URL)
    if not r:
        return []
    from bs4 import BeautifulSoup
    soup  = BeautifulSoup(r.text, "lxml")
    items = []
    for row in soup.select("table tbody tr, table tr, .listContent tr"):
        a   = row.find("a", href=True)
        tds = row.find_all("td")
        if not a or len(tds) < 2:
            continue
        title = a.get_text(strip=True)
        if len(title) < 5:
            continue
        href = a["href"] if a["href"].startswith("http") else urljoin(BASE, a["href"])
        dt_td = ({td.get("data-title", ""): td for td in tds}.get("公告日期")
                 or {td.get("data-title", ""): td for td in tds}.get("日期"))
        dt = dt_td.get_text(strip=True) if dt_td else tds[-1].get_text(strip=True)
        items.append({"title": title, "date": dt, "url": href, "agency": "國防部政治作戰局"})
    if not items:
        items = parse_with_claude_fallback(soup.get_text("\n")[:8000], "國防部政治作戰局", BASE)
    log.info(f"  [國防部政治作戰局] {len(items)} 筆")
    return items


def parse_landbank() -> list[dict]:
    """土地銀行：出租不動產公告。
    採 table tbody tr / article 模式，失敗時 Claude fallback。
    """
    BASE = "https://www.landbank.com.tw"
    URL  = f"{BASE}/Bulletin/RentRealty"
    r = get(URL)
    if not r:
        return []
    from bs4 import BeautifulSoup
    soup  = BeautifulSoup(r.text, "lxml")
    items = []
    for row in soup.select("table tbody tr, table tr, .bulletin-item, .list-item, article"):
        a   = row.find("a", href=True)
        tds = row.find_all("td")
        if not a:
            continue
        title = a.get_text(strip=True)
        if len(title) < 5:
            continue
        href = a["href"] if a["href"].startswith("http") else urljoin(BASE, a["href"])
        dt   = tds[-1].get_text(strip=True) if tds else ""
        items.append({"title": title, "date": dt, "url": href, "agency": "土地銀行"})
    if not items:
        items = parse_with_claude_fallback(soup.get_text("\n")[:8000], "土地銀行出租不動產", BASE)
    log.info(f"  [土地銀行出租不動產] {len(items)} 筆")
    return items


GOOGLE_ALERT_FEEDS = [
    "https://www.google.com/alerts/feeds/00230163369583510097/4665050306916176700",   # 公開標租 台北 OR 新北
    "https://www.google.com/alerts/feeds/00230163369583510097/12652254315385751271",  # 標租公告 site:gov.tw
]


def parse_google_alerts() -> list[dict]:
    """Google Alerts RSS：合併兩個 Alert 的 Atom feed。
    使用內建 xml.etree.ElementTree，不需要外部套件。
    """
    import xml.etree.ElementTree as ET
    from urllib.parse import unquote

    NS = "http://www.w3.org/2005/Atom"
    items = []
    for feed_url in GOOGLE_ALERT_FEEDS:
        r = get(feed_url)
        if not r:
            continue
        try:
            root = ET.fromstring(r.content)
        except Exception as e:
            log.warning(f"  [Google Alerts] XML 解析失敗：{e}")
            continue
        for entry in root.findall(f"{{{NS}}}entry"):
            link_el = entry.find(f"{{{NS}}}link")
            link = link_el.get("href", "") if link_el is not None else ""
            m = re.search(r"[?&]url=([^&]+)", link)
            actual_url = unquote(m.group(1)) if m else link
            title_el = entry.find(f"{{{NS}}}title")
            title = re.sub(r"<[^>]+>", "", title_el.text or "").strip() if title_el is not None else ""
            pub_el = entry.find(f"{{{NS}}}published")
            dt = (pub_el.text or "")[:10] if pub_el is not None else ""
            if title and len(title) > 5:
                items.append({"title": title, "date": dt, "url": actual_url, "agency": "Google Alerts"})

    seen, unique = set(), []
    for i in items:
        if i["url"] not in seen:
            seen.add(i["url"])
            unique.append(i)
    log.info(f"  [Google Alerts] {len(unique)} 筆")
    return unique


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
        resp = r.json()
        if "error" in resp:
            log.warning(f"  [{name}] Claude API 錯誤：{resp['error'].get('type')} — {resp['error'].get('message','')[:100]}")
            return []
        raw = resp["content"][0]["text"].strip()
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
    {
        "name": "台北自來水處",
        "url":  "https://www.water.gov.taipei/News.aspx?n=D2818696FF5048B8&sms=B6EE39DA23E072F5",
        "fn":   parse_taipei_water,
        "whitelist": [], "blacklist": [], "regions": [],
    },
    {
        "name": "國營台鐵",
        "url":  "https://www.railway.gov.tw/tra-tip-web/adr/rent-tender-1?&activePage=1",
        "fn":   parse_tra,
        "whitelist": [], "blacklist": [], "regions": [],
    },
    {
        "name": "新北市政府不動產標租",
        "url":  "https://www.ntpc.gov.tw/ch/home.jsp?id=b7c44e481de3b2bd",
        "fn":   parse_ntpc_property,
        "whitelist": ["標租", "出租", "租賃", "招租", "招商", "標售", "不動產", "房地"],
        "blacklist": [], "regions": [],
    },
    {
        "name": "農業部 瑠公管理處",
        "url":  "https://www.ialgo.nat.gov.tw/news/NewsPage3?a=10010",
        "fn":   parse_ialgo,
        "whitelist": [], "blacklist": [], "regions": [],
    },
    {
        "name": "郵局房地產出租",
        "url":  "https://www.post.gov.tw/post/internet/Real_estate/index.jsp?ID=904",
        "fn":   parse_post,
        "whitelist": [], "blacklist": [], "regions": ["台北", "臺北", "新北"],
    },
    {
        "name": "台北市財政局",
        "url":  "https://dof.gov.taipei/News.aspx?n=DBCAF43864F42187&sms=148C417C1585EF00",
        "fn":   parse_taipei_dof,
        "whitelist": [], "blacklist": [], "regions": [],
    },
    {
        "name": "國家住宅及都市更新中心",
        "url":  "https://www.hurc.org.tw/hurc/procurement",
        "fn":   parse_hurc,
        "whitelist": [], "blacklist": [], "regions": [],
    },
    {
        "name": "國有財產署",
        "url":  "https://esvc.fnp.gov.tw/rtMsg?svcId=5eafac8df8c649ba9cf62a591e44223c",
        "fn":   parse_fnp,
        "whitelist": [], "blacklist": [], "regions": [],
        "skip_global_filter": True,  # 標題為組合字串，已在 parser 篩北區分署
    },
    {
        "name": "政府採購網",
        "url":  "https://web.pcc.gov.tw/prkms/tender/common/basic/readTenderBasic",
        "fn":   parse_pcc,
        "whitelist": ["出租", "標租", "租賃", "招租", "房地", "不動產", "標售", "廳舍", "地上物", "閒置空間", "公有土地"],
        "blacklist": [],
        "regions":   ["台北", "臺北", "新北"],
    },
    {
        "name": "教育部學產基金",
        "url":  "https://depart.moe.edu.tw/ed4100/News.aspx?n=D62A8AE8773C5F8A&sms=4FEEAAFFCFBA1F3D",
        "fn":   parse_moe_xuechan,
        "whitelist": ["標租", "出租", "租賃", "招租", "房地", "不動產", "標售"],
        "blacklist": [],
        "regions":   ["台北", "臺北", "新北"],  # 學產基金全國，篩雙北
    },
    {
        "name": "台北市都發局",
        "url":  "https://udd.gov.taipei/events/psxwq1j",
        "fn":   parse_taipei_udd,
        "whitelist": [], "blacklist": [], "regions": [],  # 已是台北市機關
    },
    {
        "name": "國防部政治作戰局",
        "url":  "https://gpwd.mnd.gov.tw/Publish.aspx?cnid=609",
        "fn":   parse_gpwd,
        "whitelist": ["標租", "出租", "租賃", "招租", "土地", "眷村", "不動產", "房地"],
        "blacklist": [],
        "regions":   ["台北", "臺北", "新北"],  # 眷村遍布全台，只留雙北
    },
    {
        "name": "土地銀行出租不動產",
        "url":  "https://www.landbank.com.tw/Bulletin/RentRealty",
        "fn":   parse_landbank,
        "whitelist": ["出租", "標租", "租賃", "招租", "不動產", "房地"],
        "blacklist": [],
        "regions":   ["台北", "臺北", "新北"],  # 公股銀行房產遍布全台，篩雙北
    },
    {
        "name": "Google Alerts",
        "url":  "https://www.google.com/alerts",
        "fn":   parse_google_alerts,
        "whitelist": ["標租", "出租", "租賃", "招租", "標售", "不動產", "房地", "招商"],
        "blacklist": [],
        "regions":   ["台北", "臺北", "新北", "gov.tw"],  # gov.tw 讓全台政府公告通過
    },
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


SENT_LOG_FILE      = SCRIPT_DIR / "sent_log.json"
SENT_LOG_KEEP_DAYS = 30
GH_SENT_LOG_PATH   = "sent_log.json"


def save_sent_log(results: dict, run_time: str, line_pushed: bool):
    """將本次執行完整資訊存入 sent_log.json，保留最近 N 天，並 commit 到 GitHub。"""
    key = f"{date.today()} {run_time}"
    total_fetched = sum(len(d.get("all", []))    for d in results.values())
    total_new     = sum(len(d.get("new", []))    for d in results.values())
    total_notify  = sum(len(d.get("notify", [])) for d in results.values())

    entry = {
        "_summary": {
            "total_fetched": total_fetched,
            "total_new":     total_new,
            "total_notify":  total_notify,
            "line_pushed":   line_pushed,
        }
    }
    for name, d in results.items():
        entry[name] = {
            "fetched": len(d.get("all", [])),
            "new":     len(d.get("new", [])),
            "notify":  len(d.get("notify", [])),
            **({"error": d["error"]} if d.get("error") else {}),
            **({"items": [i.get("title", "") for i in d["notify"]]} if d.get("notify") else {}),
        }

    # 從 GitHub 讀取現有記錄（取得 sha 供更新用）
    log_data: dict = {}
    gh_sha: str | None = None
    if CONFIG["gh_token"] and CONFIG["gh_repo"]:
        try:
            r = requests.get(
                f"https://api.github.com/repos/{CONFIG['gh_repo']}/contents/{GH_SENT_LOG_PATH}",
                headers={"Authorization": f"Bearer {CONFIG['gh_token']}", "Accept": "application/vnd.github+json"},
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                gh_sha   = data["sha"]
                log_data = json.loads(b64decode(data["content"]).decode("utf-8"))
        except Exception as e:
            log.warning(f"GitHub 讀取 sent_log 失敗：{e}")

    # 若 GitHub 讀取失敗，退回本地
    if not log_data and SENT_LOG_FILE.exists():
        try:
            log_data = json.loads(SENT_LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    log_data[key] = entry

    # 只保留最近 SENT_LOG_KEEP_DAYS 天
    cutoff = (date.today() - timedelta(days=SENT_LOG_KEEP_DAYS)).strftime("%Y-%m-%d")
    log_data = {k: v for k, v in log_data.items() if k[:10] >= cutoff}

    # 寫本地備份
    SENT_LOG_FILE.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"✅ sent_log.json 已更新（key: {key}）")

    # commit 到 GitHub
    if CONFIG["gh_token"] and CONFIG["gh_repo"]:
        try:
            today_str = date.today().strftime("%Y-%m-%d")
            payload = {
                "message": f"chore: update sent_log.json {today_str}",
                "content": b64encode(json.dumps(log_data, ensure_ascii=False, indent=2).encode()).decode(),
                "branch":  "main",
            }
            if gh_sha:
                payload["sha"] = gh_sha
            r = requests.put(
                f"https://api.github.com/repos/{CONFIG['gh_repo']}/contents/{GH_SENT_LOG_PATH}",
                headers={"Authorization": f"Bearer {CONFIG['gh_token']}", "Accept": "application/vnd.github+json"},
                json=payload,
                timeout=15,
            )
            if r.status_code in (200, 201):
                log.info("✅ sent_log.json 已 commit 到 GitHub")
            else:
                log.warning(f"GitHub commit sent_log 失敗：{r.status_code} {r.text[:200]}")
        except Exception as e:
            log.warning(f"GitHub commit sent_log 異常：{e}")


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
    for m in re.finditer(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", text):
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
    if DRY_RUN:
        log.info(f"[DRY RUN] 略過 LINE 推播（{len(messages)} 則）")
        return
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

    # 摘要文字（只列有新增或錯誤的來源）
    lines = [f"📋 政府標案通知 {today}", f"共新增 {total_notify} 筆\n"]
    for src in SOURCES:
        name       = src["name"]
        d          = results.get(name, {})
        notify     = d.get("notify", [])
        err        = d.get("error")
        if err:
            lines.append(f"⚠️ {name}：抓取失敗")
        elif notify:
            lines.append(f"\n🆕 {name}：{len(notify)} 筆")
            for item in notify[:5]:
                lines.append(f"  ▸ {item.get('title', '')[:40]}")
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

    # 一次性清除舊版本遺留的垃圾 state key（v10 升版後執行一次）
    _STATE_VER = 10
    if state.get("_version", 0) < _STATE_VER:
        for _src in ["新北市政府不動產標租", "土地銀行出租不動產",
                     "台北市都發局", "郵局房地產出租"]:
            if _src in state:
                del state[_src]
                log.info(f"♻️  清除舊 state key：{_src}")
        state["_version"] = _STATE_VER

    for src in SOURCES:
        name = src["name"]
        log.info(f"抓取：{name}")
        try:
            items        = src["fn"]()
            new_items    = find_new_items(name, items, state)
            skip_filters = src.get("skip_global_filter", False)
            notify_items = [
                i for i in new_items
                if (skip_filters or passes_filters(i)) and is_within_date_window(i)
            ]
            results[name] = {"all": items, "new": new_items, "notify": notify_items, "error": None}
            log.info(f"  → 共 {len(items)} 筆，新增 {len(new_items)} 筆，推播 {len(notify_items)} 筆")
        except Exception as e:
            log.error(f"  → 例外：{e}")
            results[name] = {"all": [], "new": [], "notify": [], "error": str(e)}

    if DRY_RUN:
        log.info("[DRY RUN] 略過 save_state（不寫入 state.json）")
    else:
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

    # LINE 推播（有新案才發）
    line_pushed = False
    if total_notify > 0:
        messages = build_line_messages(results, run_time)
        if messages:
            push_in_batches(messages)
            line_pushed = True
    else:
        log.info("無新增案件，略過 LINE 推播")

    # 儲存本次執行完整記錄到 sent_log.json（dry-run 不寫）
    if DRY_RUN:
        log.info("[DRY RUN] 略過 sent_log.json 更新")
    else:
        save_sent_log(results, run_time, line_pushed)

    log.info("=== 完成 ===")


if __name__ == "__main__":
    main()
