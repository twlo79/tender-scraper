# 政府標案每日爬蟲

自動抓取 8 個政府機關網站的最新標案，每日透過 **LINE** 推播近期新增公告。

---

## 收錄網站

| 機關 | 公告頁面 | 抓取方式 | 備註 |
|------|----------|----------|------|
| 台北自來水處 | [連結](https://www.water.gov.taipei/News.aspx?n=D2818696FF5048B8&sms=B6EE39DA23E072F5) | `table tbody tr`（CCMS 格式） | 目前此環境 IP 被 WAF 封鎖，本機執行正常 |
| 國營台鐵 | [連結](https://www.railway.gov.tw/tra-tip-web/adr/rent-tender-1) | CSS class + regex fallback | **只抓臺北營業分處**，其餘分處略過 |
| 新北市政府財政局 | [連結](https://www.finance.ntpc.gov.tw/home.jsp?id=8b767bd17dc29316) | 靜態連結 + Claude fallback | AJAX 動態頁面，靜態無法取得時用 Claude API 解析 |
| 農業部 瑠公管理處 | [連結](https://www.ialgo.nat.gov.tw/news/NewsPage3?a=10010) | `ul.commonList li.commonList-item` | 無 table，為 ul/li 結構 |
| 郵局房地產出租 | [連結](https://www.post.gov.tw/post/internet/Real_estate/index.jsp?ID=904) | `table tr` / `ul li` | 無各別標案頁，連結統一指向來源頁 |
| 台北市財政局 | [連結](https://dof.gov.taipei/News.aspx?n=DBCAF43864F42187&sms=148C417C1585EF00) | `table tbody tr`（CCMS 格式） | 以 `data-title` 屬性精準取欄位 |
| 國家住宅及都市更新中心 | [連結](https://www.hurc.org.tw/hurc/procurement) | `table tr` + Claude fallback | |
| 國有財產署 | [連結](https://esvc.fnp.gov.tw/rtMsg?svcId=5eafac8df8c649ba9cf62a591e44223c) | `ul li > span.title-message + p.form-height` | SSR 頁面，詳情 URL 含 msgId |

---

## 完整流程

### 一、爬取流程

```
執行 scraper.py
       │
       ├─ 對每個來源網站發出 HTTP GET
       │   ├─ 帶有 Windows Chrome User-Agent（模擬瀏覽器）
       │   └─ timeout = 20 秒
       │
       ├─ 依各網站結構以 BeautifulSoup 解析 HTML
       │   └─ 若精準 parser 抓到 0 筆，部分網站會 fallback 呼叫 Claude API 解析純文字
       │
       └─ 每筆標案輸出格式：{ title, date（公告日期）, url（詳情連結）}
```

### 二、紀錄流程（state.json）

```
每次執行
       │
       ├─ 啟動時：從 GitHub repo 載入 state.json
       │           └─ 失敗時改讀本地 state.json
       │
       ├─ 比對：用標題（去除空白後）作為唯一 key
       │   ├─ 已在 state 中 → 舊標案，略過
       │   └─ 不在 state 中 → 新標案，進入篩選
       │
       ├─ 無論是否推播，所有新標案都存入 state
       │   └─ 每個來源最多保留 300 筆 key（自動輪替）
       │
       └─ 結束時：state.json 同時寫入本地 + commit 到 GitHub
                  （解決 VM 每次重置後資料遺失的問題）
```

### 三、篩選流程

```
新標案（不在 state 中）
       │
       ▼
【地區篩選】（僅台鐵）
  標題包含「臺北營業分處」→ ✅ 保留
  其他分處（花蓮、高雄…） → ❌ 直接丟棄（爬取時就過濾）
       │
       ▼
【日期篩選】（所有來源）
  讀取 date 欄位（公告日期 / 釋出日）
  解析民國曆（115年05月）或西元曆（2026-05）
  在當月 ±1 個月內 → ✅ 加入推播清單（notify）
  超出範圍         → ❌ 不推播（但仍存入 state 避免重複）
  date 欄位為空    → 用 title 文字備援解析；仍無法解析 → 放行
       │
       ▼
【推播】LINE Flex Message（每筆可點擊直達公告頁）
```

**日期篩選設計原因：**
正常每日執行時，爬到的新標案公告日期就是近期的，兩層篩選都會通過。
萬一 state.json 重置，舊標案會突然被視為「新」，但因公告日期是數月前的，日期篩選會自動擋掉，不會推播無商業價值的舊公告。

---

## 如何確認每天的更新與比對

### 1. 執行後的終端機摘要

每次執行結束後會印出：

```
============================================================
  每日標案摘要  2026-05-14  08:00
============================================================
  台北自來水處          共  0筆  ✅ 無新增
  國營台鐵              共  2筆  ✅ 無新增
  農業部 瑠公管理處     共 14筆  🆕 新增 3 筆（推播 3 筆）
       ▸ 瑠公管理處 115 年度第 5 批...
  郵局房地產出租        共 83筆  ✅ 無新增
  ...
============================================================
  合計新增：3 筆  推播：3 筆
============================================================
```

- **新增 N 筆**：本次爬到且不在 state 中的標案數
- **推播 N 筆**：通過日期篩選、實際送出 LINE 通知的標案數
- 兩者相等表示所有新標案都是近期公告

### 2. state.json 歷史記錄

`state.json` 記錄每個來源已通知過的標案 key（標題），可直接查看：

```bash
cat state.json | python -m json.tool
```

或查看 GitHub repo 的 commit 歷史，每次執行後都會有一筆 `chore: update state.json YYYY-MM-DD` 的 commit，可追溯每天新增了哪些標案。

### 3. LINE 推播內容

收到的 LINE 訊息包含：
- 摘要文字：各來源的近期新增筆數
- 各機關的 Flex Message 卡片：列出每筆標案標題與公告日期，**點擊可直達原始公告頁**

---

## 安裝與執行

### 相依套件

```bash
pip install requests beautifulsoup4 lxml
```

### 環境變數

| 變數名稱 | 必填 | 說明 |
|----------|------|------|
| `LINE_CHANNEL_TOKEN` | ✅ | LINE Channel Access Token |
| `LINE_USER_ID` | ✅ | 推播目標 User ID（U 開頭） |
| `GITHUB_TOKEN` | 建議 | Personal Access Token，用於持久化 state.json |
| `GITHUB_REPO` | 建議 | 格式 `owner/repo`，例如 `yourname/tender-scraper` |
| `ANTHROPIC_API_KEY` | 選填 | Claude API 金鑰，供新北財政局 / 住都中心備援解析 |
| `STATE_FILE` | 選填 | 本地 state.json 路徑（預設 `state.json`） |

### 執行

```bash
python scraper.py
```

---

## 自動化排程（GitHub Actions）

在 `.github/workflows/daily.yml` 設定每日定時執行：

```yaml
on:
  schedule:
    - cron: '0 0 * * *'   # 每天 UTC 00:00（台灣時間 08:00）
  workflow_dispatch:        # 支援手動觸發

jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install requests beautifulsoup4 lxml
      - run: python scraper.py
        env:
          LINE_CHANNEL_TOKEN: ${{ secrets.LINE_CHANNEL_TOKEN }}
          LINE_USER_ID: ${{ secrets.LINE_USER_ID }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_REPO: ${{ github.repository }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

> Secrets 在 GitHub repo → Settings → Secrets and variables → Actions 中設定。

---

## 免責聲明

本工具由自動爬蟲產生，資料僅供參考，請以各機關官方公告為準。
