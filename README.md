# 政府標案每日爬蟲

自動抓取 14 個政府機關網站的最新標案，每日透過 **LINE** 推播近期新增公告，並產生每日健康報告。

---

## 收錄來源（14 個）

| # | 機關 | 公告頁面 | 抓取方式 | 地區篩選 |
|---|------|----------|----------|----------|
| 1 | 台北自來水處 | [連結](https://www.water.gov.taipei/News.aspx?n=D2818696FF5048B8&sms=B6EE39DA23E072F5) | `table tbody tr`（CCMS） | — |
| 2 | 國營台鐵 | [連結](https://www.railway.gov.tw/tra-tip-web/adr/rent-tender-1) | CSS class + regex fallback | 臺北營業分處 |
| 3 | 新北市政府不動產標租 | [連結](https://www.ntpc.gov.tw/ch/home.jsp?id=b7c44e481de3b2bd) | `table tr` + Claude fallback | — |
| 4 | 農業部 瑠公管理處 | [連結](https://www.ialgo.nat.gov.tw/news/NewsPage3?a=10010) | `ul.commonList li` | — |
| 5 | 郵局房地產出租 | [連結](https://www.post.gov.tw/post/internet/Real_estate/index.jsp?ID=904) | `table tr` / `ul li` | 台北、新北 |
| 6 | 台北市財政局 | [連結](https://dof.gov.taipei/News.aspx?n=DBCAF43864F42187&sms=148C417C1585EF00) | `table tbody tr`（CCMS） | — |
| 7 | 國家住宅及都市更新中心 | [連結](https://www.hurc.org.tw/hurc/procurement) | `table tr` + Claude fallback | — |
| 8 | 國有財產署 | [連結](https://esvc.fnp.gov.tw/rtMsg?svcId=5eafac8df8c649ba9cf62a591e44223c) | `ul li span/p` | — |
| 9 | 政府採購網 | [連結](https://web.pcc.gov.tw/prkms/tender/common/basic/readTenderBasic) | GitHub Gist（由 Make 每日更新） | 台北、新北 |
| 10 | 教育部學產基金 | [連結](https://depart.moe.edu.tw/ed4100/News.aspx?n=D62A8AE8773C5F8A&sms=4FEEAAFFCFBA1F3D) | `table tbody tr` + Claude fallback | 台北、新北 |
| 11 | 台北市都發局 | [連結](https://www.udd.gov.taipei/events/psxwq1j) | `table tr` + Claude fallback | — |
| 12 | 國防部政治作戰局 | [連結](https://gpwd.mnd.gov.tw/Publish.aspx?cnid=609) | `table tr` + Claude fallback | 台北、新北 |
| 13 | 土地銀行出租不動產 | [連結](https://www.landbank.com.tw/Bulletin/RentRealty) | `table tr` / `ul li` + Claude fallback | 台北、新北 |
| 14 | Google Alerts | RSS Feed | `xml.etree.ElementTree`（Atom） | 台北、新北、gov.tw |

---

## 篩選架構

每筆標案依序通過三層篩選才會推播：

```
新標案（不在 state 中）
       │
       ▼
【1. 關鍵字 / 地區篩選】（passes_filters）
  regions   地區白名單：標題或機關名稱含任一詞（空 = 全台）
  whitelist 關鍵字白名單：標題含任一詞（空 = 不限）
  blacklist 關鍵字黑名單：標題含任一詞則丟棄
       │
       ▼
【2. 日期篩選】（is_within_date_window，±10 天）
  支援西元（2026-05-12）與民國（115/05/12）格式
  無法解析日期 → 放行
       │
       ▼
【3. LINE 推播】Flex Message（可點擊直達公告頁）
```

全域黑名單（所有來源共用）：開標結果、自動販賣機、場地短期出租、新建工程、財物採購、勞務採購 等。

---

## 每日排程

| 工作 | 時間（台灣） | 說明 |
|------|-------------|------|
| `daily.yml` — 標案爬蟲 | 每天 11:00 | 抓取 14 個來源，推播 LINE |
| `log-check.yml` — 健康報告 | 每天 11:30 | 解析爬蟲 log，寫入 `daily_report.json` |

`daily_report.json` 保留最近 30 天紀錄，格式：

```json
{
  "2026-06-05": {
    "date": "2026-06-05",
    "sources": {
      "台北自來水處": {"total": 20, "new": 0, "pushed": 0}
    },
    "total_new": 3,
    "total_pushed": 3,
    "errors": [],
    "line": "成功"
  }
}
```

---

## 去重機制（state.json）

```
每次執行
  ├─ 啟動：從 GitHub repo 載入 state.json（失敗改讀本地）
  ├─ 比對：以標題（去除空白）為唯一 key
  │   ├─ 已在 state → 舊標案，略過
  │   └─ 不在 state → 新標案，進入篩選
  ├─ 每個來源最多保留 300 筆 key（自動輪替）
  └─ 結束：state.json 寫入本地 + commit 到 GitHub
```

---

## 安裝與執行

### 相依套件

```bash
pip install requests beautifulsoup4 lxml
```

### 環境變數

| 變數 | 必填 | 說明 |
|------|------|------|
| `LINE_CHANNEL_TOKEN` | ✅ | LINE Channel Access Token |
| `GITHUB_TOKEN` | 建議 | 用於持久化 state.json 到 repo |
| `GITHUB_REPO` | 建議 | 格式 `owner/repo` |
| `ANTHROPIC_API_KEY` | 選填 | Claude API 金鑰（備援解析用） |
| `STATE_FILE` | 選填 | 本地 state.json 路徑（預設 `state.json`） |

### 執行

```bash
# 正常執行（含 LINE 推播）
python scraper.py

# 測試用（不推播 LINE）
DRY_RUN=true python scraper.py
```

---

## GitHub Actions 設定

`.github/workflows/daily.yml`：

```yaml
on:
  schedule:
    - cron: '0 3 * * *'   # 每天 UTC 03:00 = 台灣時間 11:00
  workflow_dispatch:

jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: '3.11'}
      - run: pip install requests beautifulsoup4 lxml
      - run: python scraper.py
        env:
          LINE_CHANNEL_TOKEN: ${{ secrets.LINE_CHANNEL_TOKEN }}
          GITHUB_TOKEN:       ${{ secrets.GITHUB_TOKEN }}
          GITHUB_REPO:        ${{ github.repository }}
          ANTHROPIC_API_KEY:  ${{ secrets.ANTHROPIC_API_KEY }}
```

Secrets 在 GitHub repo → **Settings → Secrets and variables → Actions** 中設定。

---

## 免責聲明

本工具由自動爬蟲產生，資料僅供參考，請以各機關官方公告為準。
