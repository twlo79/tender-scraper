#!/usr/bin/env python3
"""
政府標案每日爬蟲
抓取 8 個政府網站的最新標案，整理後寄送 Email 通知

使用方式：
  pip install requests beautifulsoup4 lxml
  python scraper.py

Email 設定：
  設定環境變數 EMAIL_FROM, EMAIL_TO, EMAIL_PASSWORD
  或修改下方 CONFIG 區段
"""

import os
import sys
import smtplib
import logging
from datetime import date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests
from bs4 import BeautifulSoup

# ── 設定區 ────────────────────────────────────────────────────────────────────

CONFIG = {
    "email_from":     os.getenv("EMAIL_FROM", "your_gmail@gmail.com"),
    "email_to":       os.getenv("EMAIL_TO",   "your_gmail@gmail.com"),
    "email_password": os.getenv("EMAIL_PASSWORD", "your_app_password"),  # Gmail App Password
    "smtp_host":      "smtp.gmail.com",
    "smtp_port":      465,
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9",
}

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── 各網站爬蟲函式 ────────────────────────────────────────────────────────────

def fetch(url: str, **kwargs) -> BeautifulSoup | None:
    """共用抓頁函式，回傳 BeautifulSoup 物件"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, **kwargs)
        resp.encoding = resp.apparent_encoding
        return BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        log.warning(f"無法抓取 {url}：{e}")
        return None


def scrape_taipei_water() -> list[dict]:
    """台北自來水處"""
    url = "https://www.water.gov.taipei/News.aspx?n=D2818696FF5048B8&sms=B6EE39DA23E072F5"
    soup = fetch(url)
    items = []
    if not soup:
        return items
    for row in soup.select("table.table tbody tr, ul.news-list li, .listBS li"):
        a = row.find("a")
        if not a:
            continue
        title = a.get_text(strip=True)
        href  = a.get("href", "")
        if href and not href.startswith("http"):
            href = "https://www.water.gov.taipei" + href
        date_tag = row.find(class_=lambda c: c and "date" in c.lower())
        date_str  = date_tag.get_text(strip=True) if date_tag else ""
        if title:
            items.append({"title": title, "url": href, "date": date_str})
    return items[:10]


def scrape_tra() -> list[dict]:
    """國營台鐵"""
    url = "https://www.railway.gov.tw/tra-tip-web/adr/rent-tender-1?&activePage=1"
    soup = fetch(url)
    items = []
    if not soup:
        return items
    for row in soup.select("table tbody tr, .list-group-item, article"):
        a = row.find("a")
        if not a:
            continue
        title = a.get_text(strip=True)
        href  = a.get("href", "")
        if href and not href.startswith("http"):
            href = "https://www.railway.gov.tw" + href
        tds = row.find_all("td")
        date_str = tds[-1].get_text(strip=True) if len(tds) >= 2 else ""
        if title:
            items.append({"title": title, "url": href, "date": date_str})
    return items[:10]


def scrape_ntpc_finance() -> list[dict]:
    """新北市政府財政局"""
    url = "https://www.finance.ntpc.gov.tw/home.jsp?id=8b767bd17dc29316"
    soup = fetch(url)
    items = []
    if not soup:
        return items
    for a in soup.select(".list a, table a, ul.news a"):
        title = a.get_text(strip=True)
        href  = a.get("href", "")
        if href and not href.startswith("http"):
            href = "https://www.finance.ntpc.gov.tw" + href
        if title:
            items.append({"title": title, "url": href, "date": ""})
    return items[:10]


def scrape_ialgo() -> list[dict]:
    """農業部 瑠公管理處"""
    url = "https://www.ialgo.nat.gov.tw/news/NewsPage3?a=10010"
    soup = fetch(url)
    items = []
    if not soup:
        return items
    for row in soup.select("table tbody tr, .listBS li, ul.news-list li"):
        a = row.find("a")
        if not a:
            continue
        title = a.get_text(strip=True)
        href  = a.get("href", "")
        if href and not href.startswith("http"):
            href = "https://www.ialgo.nat.gov.tw" + href
        tds = row.find_all("td")
        date_str = tds[-1].get_text(strip=True) if tds else ""
        if title:
            items.append({"title": title, "url": href, "date": date_str})
    return items[:10]


def scrape_post() -> list[dict]:
    """郵局房地產出租"""
    url = "https://www.post.gov.tw/post/internet/Real_estate/index.jsp?ID=904"
    soup = fetch(url)
    items = []
    if not soup:
        return items
    for row in soup.select("table.table tr, .list tr"):
        a = row.find("a")
        if not a:
            continue
        title = a.get_text(strip=True)
        href  = a.get("href", "")
        if href and not href.startswith("http"):
            href = "https://www.post.gov.tw" + href
        tds = row.find_all("td")
        date_str = tds[-1].get_text(strip=True) if len(tds) >= 2 else ""
        if title:
            items.append({"title": title, "url": href, "date": date_str})
    return items[:10]


def scrape_taipei_dof() -> list[dict]:
    """台北市財政局"""
    url = "https://dof.gov.taipei/News.aspx?n=DBCAF43864F42187&sms=148C417C1585EF00"
    soup = fetch(url)
    items = []
    if not soup:
        return items
    for row in soup.select("table.table tbody tr, ul.news-list li, .listBS li"):
        a = row.find("a")
        if not a:
            continue
        title = a.get_text(strip=True)
        href  = a.get("href", "")
        if href and not href.startswith("http"):
            href = "https://dof.gov.taipei" + href
        date_tag = row.find(class_=lambda c: c and "date" in c.lower())
        date_str  = date_tag.get_text(strip=True) if date_tag else ""
        if title:
            items.append({"title": title, "url": href, "date": date_str})
    return items[:10]


def scrape_hurc() -> list[dict]:
    """國家住宅及都市更新中心"""
    url = "https://www.hurc.org.tw/hurc/procurement"
    soup = fetch(url)
    items = []
    if not soup:
        return items
    for row in soup.select("table tbody tr, .list-item, article.item"):
        a = row.find("a")
        if not a:
            continue
        title = a.get_text(strip=True)
        href  = a.get("href", "")
        if href and not href.startswith("http"):
            href = "https://www.hurc.org.tw" + href
        tds = row.find_all("td")
        date_str = tds[-1].get_text(strip=True) if len(tds) >= 2 else ""
        if title:
            items.append({"title": title, "url": href, "date": date_str})
    return items[:10]


def scrape_fnp() -> list[dict]:
    """國有財產署"""
    url = "https://esvc.fnp.gov.tw/rtMsg?svcId=5eafac8df8c649ba9cf62a591e44223c"
    soup = fetch(url)
    items = []
    if not soup:
        return items
    for row in soup.select("table tbody tr, .list tr, ul li"):
        a = row.find("a")
        if not a:
            continue
        title = a.get_text(strip=True)
        href  = a.get("href", "")
        if href and not href.startswith("http"):
            href = "https://esvc.fnp.gov.tw" + href
        tds = row.find_all("td")
        date_str = tds[-1].get_text(strip=True) if len(tds) >= 2 else ""
        if title:
            items.append({"title": title, "url": href, "date": date_str})
    return items[:10]


# ── 所有來源清單 ───────────────────────────────────────────────────────────────

SOURCES = [
    ("台北自來水處",       "https://www.water.gov.taipei/News.aspx?n=D2818696FF5048B8&sms=B6EE39DA23E072F5",       scrape_taipei_water),
    ("國營台鐵",          "https://www.railway.gov.tw/tra-tip-web/adr/rent-tender-1?&activePage=1",               scrape_tra),
    ("新北市政府財政局",   "https://www.finance.ntpc.gov.tw/home.jsp?id=8b767bd17dc29316",                         scrape_ntpc_finance),
    ("農業部 瑠公管理處",  "https://www.ialgo.nat.gov.tw/news/NewsPage3?a=10010",                                  scrape_ialgo),
    ("郵局房地產出租",     "https://www.post.gov.tw/post/internet/Real_estate/index.jsp?ID=904",                   scrape_post),
    ("台北市財政局",       "https://dof.gov.taipei/News.aspx?n=DBCAF43864F42187&sms=148C417C1585EF00",             scrape_taipei_dof),
    ("國家住宅及都市更新中心", "https://www.hurc.org.tw/hurc/procurement",                                        scrape_hurc),
    ("國有財產署",         "https://esvc.fnp.gov.tw/rtMsg?svcId=5eafac8df8c649ba9cf62a591e44223c",                scrape_fnp),
]


# ── Email 組裝與發送 ──────────────────────────────────────────────────────────

def build_html(results: dict[str, list[dict]]) -> str:
    today = date.today().strftime("%Y/%m/%d")
    sections = ""
    for name, items in results.items():
        source_url = next((s[1] for s in SOURCES if s[0] == name), "#")
        if items:
            rows = "".join(
                f"""<tr>
                      <td style="padding:6px 10px;border-bottom:1px solid #eee;">
                        <a href="{i['url']}" style="color:#1a56db;text-decoration:none;">{i['title']}</a>
                      </td>
                      <td style="padding:6px 10px;border-bottom:1px solid #eee;white-space:nowrap;color:#6b7280;font-size:13px;">{i['date']}</td>
                    </tr>"""
                for i in items
            )
            table = f"""<table width="100%" cellspacing="0" cellpadding="0"
                              style="border-collapse:collapse;margin-bottom:4px;">
                          <tbody>{rows}</tbody>
                        </table>"""
        else:
            table = '<p style="color:#9ca3af;font-size:13px;padding:4px 10px;">（本日無新資料或無法存取）</p>'

        sections += f"""
        <div style="margin-bottom:28px;">
          <h2 style="font-size:15px;font-weight:700;color:#111827;
                     border-left:4px solid #1a56db;padding-left:10px;margin:0 0 10px;">
            <a href="{source_url}" style="color:#111827;text-decoration:none;">{name}</a>
          </h2>
          {table}
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:'Helvetica Neue',Arial,sans-serif;">
  <div style="max-width:680px;margin:32px auto;background:#fff;
              border-radius:8px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08);">

    <!-- Header -->
    <div style="background:#1a56db;padding:20px 28px;">
      <h1 style="margin:0;color:#fff;font-size:18px;font-weight:700;">📋 每日政府標案通知</h1>
      <p style="margin:4px 0 0;color:#bfdbfe;font-size:13px;">{today} 彙整</p>
    </div>

    <!-- Body -->
    <div style="padding:24px 28px;">
      {sections}
    </div>

    <!-- Footer -->
    <div style="padding:14px 28px;background:#f9fafb;border-top:1px solid #e5e7eb;
                font-size:12px;color:#9ca3af;">
      此信件由自動爬蟲產生，資料來源為各政府機關官方網站。
    </div>
  </div>
</body>
</html>"""


def send_email(html: str):
    msg = MIMEMultipart("alternative")
    today = date.today().strftime("%Y/%m/%d")
    msg["Subject"] = f"📋 每日政府標案通知 {today}"
    msg["From"]    = CONFIG["email_from"]
    msg["To"]      = CONFIG["email_to"]
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL(CONFIG["smtp_host"], CONFIG["smtp_port"]) as s:
        s.login(CONFIG["email_from"], CONFIG["email_password"])
        s.send_message(msg)
    log.info(f"Email 已寄出至 {CONFIG['email_to']}")


# ── 主程式 ────────────────────────────────────────────────────────────────────

def main():
    log.info("開始爬取標案資料…")
    results = {}
    for name, _url, scrape_fn in SOURCES:
        log.info(f"  抓取：{name}")
        results[name] = scrape_fn()
        total = len(results[name])
        log.info(f"    → 取得 {total} 筆")

    log.info("組裝 Email…")
    html = build_html(results)

    # 也輸出純文字摘要到 stdout（方便 Claude Code Routine 查看）
    print(f"\n{'='*50}")
    print(f"  每日標案摘要 {date.today()}")
    print(f"{'='*50}")
    for name, items in results.items():
        print(f"\n【{name}】{len(items)} 筆")
        for i in items[:3]:
            print(f"  • {i['title'][:40]}{'…' if len(i['title'])>40 else ''} {i['date']}")

    if CONFIG["email_from"] == "your_gmail@gmail.com":
        log.warning("尚未設定 Email，跳過寄信。請設定環境變數 EMAIL_FROM / EMAIL_TO / EMAIL_PASSWORD")
        # 將 HTML 存成檔案供預覽
        with open("tender_preview.html", "w", encoding="utf-8") as f:
            f.write(html)
        log.info("已將 Email 預覽存為 tender_preview.html")
    else:
        send_email(html)


if __name__ == "__main__":
    main()
