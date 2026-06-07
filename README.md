# 政府標案每日爬蟲

自動抓取 14 個政府機關網站的最新標案，每日透過 **LINE** 推播近期新增公告，並產生每日健康報告。

---

## 執行流程

```
每天 10:37（台灣時間）
       │
       ▼
  scraper.py 執行
       │
       ├─ 爬 14 個來源
       │
       ├─ 和 state.json 比對（有沒有見過）
       │      已見過 → 跳過
       │      沒見過 → 「新增」
       │
       ├─ 對「新增」套三層篩選
       │      ① 地區白名單（台北 / 新北）
       │      ② 關鍵字白名單（出租、標租、不動產…）
       │      ③ 日期窗口（±10 天內）
       │
       ├─ 通過篩選的筆數 = total_notify
       │
       ├─ total_notify > 0 → 推播 LINE ✅
       │  total_notify = 0 → 靜默（不發送）✅
       │
       ├─ state.json commit 到 GitHub（持久保存）✅
       └─ sent_log.json commit 到 GitHub（執行紀錄）✅

每天 11:07（台灣時間）
       │
       ▼
  log_checker.py 執行
       │
       └─ 讀 Actions log → 解析 → 存 daily_report.json ✅
```

> **推播規則**：只有在當天出現「通過三層篩選的新案件」時才發 LINE，
> 無新增時完全靜默，不會發送空白或摘要訊息。

---

## 收錄來源（14 個）

| # | 機關 | 公告頁面 | 抓取方式 | 地區篩選 |
|---|------|----------|----------|----------|
| 1 | 台北自來水處 | [連結](https://www.water.gov.taipei/News.aspx?n=D2818696FF5048B8&sms=B6EE39DA23E072F5) | `table tbody tr`（CCMS） | — |
| 2 | 國營台鐵 | [連結](https://www.railway.gov.tw/tra-tip-web/adr/rent-tender-1) | CSS class + regex fallback | 臺北營業分處 |
| 3 | 新北市政府不動產標租 | [連結](https://www.ntpc.gov.tw/ch/home.jsp?id=b7c44e481de3b2bd) | `table tbody tr` + Claude fallback | — |
| 4 | 農業部 瑠公管理處 | [連結](https://www.ialgo.nat.gov.tw/news/NewsPage3?a=10010) | `ul.commonList li` | — |
| 5 | 郵局房地產出租 | [連結](https://www.post.gov.tw/post/internet/Real_estate/index.jsp?ID=904) | `table tr` / `ul li` | 台北、新北 |
| 6 | 台北市財政局 | [連結](https://dof.gov.taipei/News.aspx?n=DBCAF43864F42187&sms=148C417C1585EF00) | `table tbody tr`（CCMS） | — |
| 7 | 國家住宅及都市更新中心 | [連結](https://www.hurc.org.tw/hurc/procurement) | `table tr` + Claude fallback | — |
| 8 | 國有財產署 | [連結](https://esvc.fnp.gov.tw/rtMsg?svcId=5eafac8df8c649ba9cf62a591e44223c) | `ul li span/p` | — |
| 9 | 政府採購網 | [連結](https://web.pcc.gov.tw/prkms/tender/common/basic/readTenderBasic) | 關鍵字查詢（出租／標租，近 7 天） | 台北、新北 |
| 10 | 教育部學產基金 | [連結](https://depart.moe.edu.tw/ed4100/News.aspx?n=D62A8AE8773C5F8A&sms=4FEEAAFFCFBA1F3D) | `table tbody tr`（日期格式驗證）+ Claude fallback | 台北、新北 |
| 11 | 台北市都發局 | [連結](https://udd.gov.taipei/events/psxwq1j) | `table tr` + Claude fallback | — |
| 12 | 國防部政治作戰局 | [連結](https://gpwd.mnd.gov.tw/Publish.aspx?cnid=609) | `table tr` + Claude fallback | 台北、新北 |
| 13 | 土地銀行出租不動產 | [連結](https://www.landbank.com.tw/Bulletin/RentRealty) | `table tbody tr` + Claude fallback | 台北、新北 |
| 14 | Google Alerts | RSS Feed | `xml.etree.ElementTree`（Atom） | 台北、新北、gov.tw |

---

## 三層篩選說明

每筆「新增」標案依序通過以下三層，全部通過才進入推播清單：

| 層次 | 說明 | 未通過則 |
|------|------|----------|
| ① 地區 + 關鍵字 | 標題或機關名含地區詞；標題含白名單關鍵字；標題不含黑名單關鍵字 | 丟棄 |
| ② 日期窗口 | 公告日期在今天 ±10 天內；無法解析日期則放行 | 丟棄 |
| ③ 去重 | 標題（去除空白）不在 state.json 中 | 丟棄 |

**全域黑名單**（所有來源共用）：開標結果、自動販賣機、場地短期出租、新建工程、財物採購、勞務採購 等。

---

## 去重機制（state.json）

```
每次執行
  ├─ 啟動：從 GitHub repo 載入 state.json（失敗改讀本地）
  ├─ 比對：以標題（去除空白）為唯一 key
  │   ├─ 已在 state → 舊標案，略過
  │   └─ 不在 state → 新標案，進入篩選
  ├─ 每個來源最多保留 300 筆 key（自動輪替舊 key）
  └─ 結束：state.json commit 到 GitHub（確保下次執行能讀到）
```

---

## 每日排程

| Workflow | 時間（台灣） | 說明 |
|----------|-------------|------|
| `daily.yml` — 標案爬蟲 | 每天 10:37 | 抓取 14 個來源，有新增才推播 LINE |
| `log-check.yml` — 健康報告 | 每天 11:07 | 解析爬蟲 log，寫入 `daily_report.json` |
| `cleanup-branches.yml` — 清理分支 | 每週日 10:00 | 刪除超過 7 天的 `claude/*` 分支 |

> 使用非整點奇數分鐘（`:37`、`:07`）以避開 GitHub Actions 高峰排隊延遲。

### sent_log.json 格式

每次執行都會寫入並 commit 到 GitHub，保留最近 30 天：

```json
{
  "2026-06-06 10:37": {
    "_summary": {
      "total_fetched": 225,
      "total_new": 2,
      "total_notify": 2,
      "line_pushed": true
    },
    "台北自來水處": {"fetched": 8, "new": 2, "notify": 2, "items": ["案名A", "案名B"]},
    "國營台鐵":     {"fetched": 1, "new": 0, "notify": 0}
  }
}
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
| `LINE_CHANNEL_TOKEN` | ✅ | LINE Channel Access Token（broadcast 推播） |
| `GITHUB_TOKEN` | 建議 | 用於持久化 state.json / sent_log.json 到 repo |
| `GITHUB_REPO` | 建議 | 格式 `owner/repo` |
| `ANTHROPIC_API_KEY` | 選填 | Claude API 金鑰（備援解析用） |

### 執行

```bash
# 正常執行（有新增才推播 LINE）
python scraper.py

# Dry-run：不推播 LINE、不寫入 state.json、不更新 sent_log.json
DRY_RUN=true python scraper.py
```

---

## GitHub Actions 設定

`.github/workflows/daily.yml`：

```yaml
on:
  schedule:
    - cron: '37 2 * * *'  # 每天 UTC 02:37 = 台灣時間 10:37
  workflow_dispatch:

permissions:
  contents: write

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

## 未來優化建議

### 短期（低成本、高效益）

| 項目 | 說明 |
|------|------|
| **新增來源** | 內政部不動產資訊平台、各縣市政府資產活化公告 |
| **日期窗口可設定化** | 目前硬寫 ±10 天，改成環境變數 `DATE_WINDOW_DAYS` 方便調整 |
| **推播內容加入圖片或 Rich Menu** | 目前純文字 + Flex Message，可加入縮圖提升點閱率 |
| **Dry-run 模式加強** | 目前 DRY_RUN 只跳過推播，可加 `--preview` 輸出「今天會推什麼」 |

### 中期（架構調整）

| 項目 | 說明 |
|------|------|
| **失敗來源自動重試** | 單一來源 HTTP 逾時時重試 2 次，減少因暫時性網路問題造成的漏抓 |
| **Claude fallback 結果快取** | 同一 HTML 已用 Claude 解析過，今天若內容沒變則不重複呼叫 API，降低成本 |
| **關鍵字 / 地區設定外部化** | 將白名單、黑名單、地區詞移到 `config.json`，不用改程式碼就能調整篩選條件 |
| **daily_report.json 視覺化** | 在 repo 的 GitHub Pages 上顯示近 30 天的每日統計圖表 |

### 長期（擴展性）

| 項目 | 說明 |
|------|------|
| **多用戶訂閱** | 改為 LINE LIFF 讓用戶自選感興趣的地區 / 關鍵字，後端存 per-user 設定 |
| **資料庫替代 state.json** | 當來源超過 30 個、state 超過 10 萬筆時，改用 SQLite 或雲端 DB |
| **異常即時告警** | 連續 2 天某來源 fetched=0 時，透過 LINE 發送警告（目前需手動查 log） |

---

## 免責聲明

本工具由自動爬蟲產生，資料僅供參考，請以各機關官方公告為準。
