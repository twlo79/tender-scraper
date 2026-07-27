# 政府標案每日爬蟲

自動抓取 13 個政府機關網站，篩選**台北／新北地區**公有不動產標租公告，每日透過 **LINE** 推播標案名稱、公告日期與連結。

---

## 目的與核心規則

**目標**：找到雙北地區近期公告的不動產標租案，並在第一時間推播 LINE 通知。

**推播內容**：每筆標案包含
- 標案名稱
- 公告日期
- 原始公告 URL

**篩選條件（三層，全部通過才推播）**：

| 層次 | 條件 | 說明 |
|------|------|------|
| ① 地區 | 標題或機關名含「台北」「臺北」「新北」 | 部分來源本身就是雙北專屬，不做地區過濾 |
| ② 關鍵字 | 標題含白名單詞（出租、標租、不動產…）且不含黑名單詞 | 排除開標結果、短期場地、財物採購等雜訊 |
| ③ 日期窗口 | 公告日期在今天 ±10 天內；無法解析日期則放行 | 避免推播過期或未來案件 |

去重機制另外透過 `state.json` 確保同一案件不重複推播。

---

## 執行流程

```
每天 ~10:37（台灣時間）
       │  Make.com 定時呼叫 daily.yml（workflow_dispatch）
       │  ※ daily.yml 本身沒有 cron，觸發完全依賴 Make
       ▼
  GitHub Actions 執行 scraper.py
       │
       ├─ 爬 13 個來源
       │
       ├─ 和 state.json 比對（去重）
       │      已見過 → 跳過
       │      沒見過 → 進入篩選
       │
       ├─ 三層篩選
       │      ① 地區（雙北）
       │      ② 關鍵字白名單 / 黑名單
       │      ③ 日期窗口（±10 天）
       │
       ├─ total_notify > 0 → 推播 LINE（標案名 + 公告日 + URL）
       │  total_notify = 0 → 靜默，不發送
       │  ※ 使用 broadcast 端點：推送給該 LINE OA 的「所有好友」，
       │    非指定單一 User ID。每 5 則訊息一批送出。
       │
       ├─ state.json commit 到 GitHub
       └─ sent_log.json commit 到 GitHub

每天 ~11:07（台灣時間）
       │
       ▼
  GitHub Actions 執行 log_checker.py
       └─ 解析 Actions log → 寫入 daily_report.json
```

> 排程使用非整點奇數分鐘（`:37`、`:07`）以避開 GitHub Actions 高峰排隊。
> GitHub Actions 在尖峰時段可能延遲 30 分鐘至數小時，為正常現象。
>
> ⚠️ **單點故障**：`daily.yml` 沒有 cron，只有 `workflow_dispatch`，
> 由 Make.com 每日呼叫觸發。Make 若額度用盡或流程停用，爬蟲會靜默不執行，
> 而「沒收到 LINE」與「今天沒有新標案」表徵完全相同，不易察覺。
> 補一組 cron 作為備援可消除此風險。

---

## 檔案說明

### 主要腳本

| 檔案 | 用途 |
|------|------|
| `scraper.py` | 主爬蟲。抓取 13 個來源、篩選、去重、推播 LINE、更新 state/sent_log |
| `log_checker.py` | 解析 GitHub Actions 執行 log，寫入 `daily_report.json` |
| `dry_run_all_regions.py` | 測試用：移除地區限制執行所有 parser，驗證標題/日期/URL 格式 |
| `qa_report.py` | QA 監測：讀取 sent_log.json，輸出爬蟲健康報告 |
| `resend.py` | 重播指定日期的 LINE 推播內容（重用 scraper 的 build_line_messages） |

### 資料檔案

| 檔案 | 用途 | 存放位置 |
|------|------|---------|
| `state.json` | 去重記錄。每個來源最多保留 300 筆標題 key | GitHub repo（每次執行後 commit） |
| `sent_log.json` | 執行記錄。保留最近 30 天，每筆含各來源統計 | GitHub repo（每次執行後 commit） |
| `daily_report.json` | 健康報告。由 log_checker.py 產生 | GitHub repo |

### GitHub Actions Workflows

| 檔案 | 觸發方式 | 說明 |
|------|---------|------|
| `.github/workflows/daily.yml` | `workflow_dispatch`（Make.com 每天約 10:37 呼叫，或手動） | 主爬蟲。支援 `dry_run` 輸入參數 |
| `.github/workflows/log-check.yml` | cron `7 3 * * *`（UTC 03:07 = 台灣 11:07） | 健康報告 |
| `.github/workflows/cleanup-branches.yml` | cron `0 2 * * 0`（每週日 UTC 02:00 = 台灣 10:00） | 刪除超過 7 天的 `claude/*` 分支 |

---

## 腳本參數說明

### `scraper.py`

```bash
python scraper.py                    # 正常執行
DRY_RUN=true python scraper.py       # 不推播 LINE、不寫 state/sent_log
```

| 環境變數 | 必填 | 說明 |
|---------|------|------|
| `LINE_CHANNEL_TOKEN` | ✅ | LINE Channel Access Token |
| `GITHUB_TOKEN` | 建議 | 用於 state/sent_log 持久化到 repo |
| `GITHUB_REPO` | 建議 | 格式 `owner/repo` |
| `ANTHROPIC_API_KEY` | 選填 | Claude API（備援解析 JS 渲染頁面用） |

### `dry_run_all_regions.py`

```bash
python dry_run_all_regions.py                  # 全台所有（含雙北，標記區分）
python dry_run_all_regions.py --non-taipei     # 只顯示非台北/新北（debug 用）
python dry_run_all_regions.py --source 郵局    # 只測試特定來源
python dry_run_all_regions.py --debug          # 顯示每筆 raw item 資料
```

> 安全：不寫入 state.json 或 sent_log.json，可重複執行。

### `qa_report.py`

```bash
python qa_report.py              # 最新一次執行報告 + 7 天趨勢
python qa_report.py --days 14    # 改成 14 天趨勢
python qa_report.py --full       # 顯示所有推播項目（含正常的）
```

> 優先從 GitHub API 取最新 sent_log.json，需設定 `GITHUB_TOKEN`；無 token 則讀本地檔案。

---

## sent_log.json 格式

```json
{
  "2026-06-07 06:49": {
    "_summary": {
      "total_fetched": 180,
      "total_new": 40,
      "total_notify": 2,
      "line_pushed": true
    },
    "台北自來水處": { "fetched": 8, "new": 0, "notify": 0 },
    "台北市都發局": {
      "fetched": 36, "new": 2, "notify": 2,
      "items": ["標案名稱A", "標案名稱B"]
    }
  }
}
```

> 若當次所有來源 fetched=0，`_summary` 會加上 `"note": "⚠️ 所有來源 fetched=0，疑似網路失敗或全部被 IP 封鎖"`。

---

## 收錄來源（13 個）

| # | 機關 | 抓取方式 | 地區篩選 |
|---|------|----------|----------|
| 1 | 台北自來水處 | `table tbody tr`（CCMS） | 來源本身限雙北 |
| 2 | 國營台鐵 | `ul.tender-list li.rent-item` | 臺北營業分處 |
| 3 | 農業部 瑠公管理處 | `ul.commonList li.commonList-item` | 全台（關鍵字篩） |
| 4 | 郵局房地產出租 | `ul.NewsList li a` | 台北、新北 |
| 5 | 台北市財政局 | `table tbody tr`（CCMS，data-title 定位公告日期） | 來源本身限雙北 |
| 6 | 國家住宅及都市更新中心 | `table tr`（tds[2]=案名，tds[4]=公告日期） | 全台（關鍵字篩） |
| 7 | 國有財產署 | `a.message-flex`（4 個類別頁，限北區分署） | 北區分署 |
| 8 | 政府採購網 | 關鍵字 API 查詢（出租／標租，近 7 天） | 台北、新北 |
| 9 | 教育部學產基金 | `table tbody tr` + Claude fallback | 台北、新北 |
| 10 | 台北市都發局 | `table tr` + Claude fallback | 來源本身限雙北 |
| 11 | 國防部政治作戰局 | `table tr` + Claude fallback | 台北、新北 |
| 12 | 土地銀行出租不動產 | `table tbody tr` + Claude fallback | 台北、新北 |
| 13 | Google Alerts | RSS Atom feed | 台北、新北、gov.tw |

> **Claude fallback**：部分 JS 渲染頁面無法直接解析時，改用 Claude API 從 HTML 文字擷取結構化資料。需設定 `ANTHROPIC_API_KEY`。

---

## Claude Code 分支自動化（`.claude/`）

| 檔案 | 用途 |
|------|------|
| `.claude/settings.json` | 設定 **Stop hook**：session 結束時自動執行下方腳本 |
| `.claude/merge-to-main.sh` | 將 `claude/*` session branch merge 回 main 並 push |

`merge-to-main.sh` 行為：

- 僅處理 `claude/*` 開頭的分支，其他分支直接跳過
- 沒有領先 `origin/main` 的 commit 時不動作
- merge 衝突若發生在 `state.json`，自動保留 main 版本（`--ours`）後續完成 commit
- 其他檔案衝突則 abort 並切回原分支，需人工處理

搭配 `cleanup-branches.yml`，整體循環為：

```
Claude 開 claude/* 分支做事 → session 結束自動 merge 回 main → 一週後分支自動刪除
```

> ⚠️ `.claude/settings.json` 中的 hook 指令為硬編碼路徑 `/home/user/tender-scraper/.claude/merge-to-main.sh`，
> 僅適用於原本的雲端執行環境。在其他機器上此 hook 不會生效（腳本本身以 `git rev-parse` 定位 repo，
> 是動態的，只有 settings 這行路徑需要調整）。

---

## Claude Code 例行 QA 流程

每次開啟 Claude Code session 時，依序執行以下監測：

### 步驟 1：QA 報告（每天）

```bash
python qa_report.py
```

檢查：
- 最新一次 Actions 執行是否正常（各來源 fetched 數量）
- 推播項目是否有噪音（標題格式異常、含黑名單詞）
- 是否有來源連線失敗（fetched=0）或全部空跑

### 步驟 2：parser 驗證（懷疑有問題時）

```bash
python dry_run_all_regions.py --debug --source 來源名稱
```

確認該來源的標題、公告日、URL 格式是否正確。

### 步驟 3：全來源測試（每週或修改後）

```bash
python dry_run_all_regions.py --non-taipei   # 看非雙北的標案有無格式問題
python dry_run_all_regions.py --debug        # 全台 raw data 檢視
```

---

## 優化路線圖

### 第一優先：確保基礎正確

目前階段以「debug 確認符合標準」為第一目標，確保每個 parser 能穩定產出正確的標題、公告日、URL。

| 項目 | 狀態 |
|------|------|
| 所有 parser 標題正確（非分類名稱、非噪音） | 進行中 |
| 日期解析涵蓋民國／西元／各種分隔符 | ✅ 已修（regex 字元類 bug） |
| URL 為個別文章連結（非列表頁） | 進行中 |
| sent_log 無論有無推播都寫入 | ✅ 已修 |
| 空跑（fetched=0）有備註標記 | ✅ 已加 |

### 第二優先：優化篩選精準度

| 項目 | 說明 |
|------|------|
| 監測無效案源 | 長期 fetched=0 或推播內容無關的來源，考慮移除或調整 |
| 白名單／黑名單細化 | 減少「停車場標租」「場地出租」等低相關案件 |
| 日期窗口可設定化 | 目前為 `scraper.py` 內的常數 `DATE_WINDOW_DAYS = 10`，可改為讀環境變數 |
| daily.yml 補 cron 備援 | 目前僅靠 Make.com 觸發，Make 停擺時爬蟲會靜默失效 |

### 第三優先：新增案源

| 候選來源 | 說明 |
|---------|------|
| 內政部不動產資訊平台 | 全國公有不動產標租公告 |
| 各縣市政府資產活化公告 | 台北市／新北市政府資產管理局 |
| 財政部國有財產署（南中東部分署） | 目前只收北區分署 |

---

## GitHub Actions 設定

`.github/workflows/daily.yml`（實際內容摘要）：

```yaml
on:
  workflow_dispatch:        # 由 Make.com 定時呼叫，或手動觸發
    inputs:
      dry_run:
        description: 'Dry run（不推播 LINE、不寫 state/sent_log）'
        type: boolean
        default: false

permissions:
  contents: write           # 允許將 state.json commit 回 main

jobs:
  scrape:
    runs-on: ubuntu-latest
    env:
      FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true
    steps:
      - uses: actions/checkout@v4
        with: {ref: main}
      - uses: actions/setup-python@v5
        with: {python-version: '3.11'}
      - run: pip install requests beautifulsoup4 lxml
      - run: python scraper.py 2>&1 | tee scraper_output.log
        env:
          LINE_CHANNEL_TOKEN: ${{ secrets.LINE_CHANNEL_TOKEN }}
          GITHUB_TOKEN:       ${{ secrets.GITHUB_TOKEN }}
          GITHUB_REPO:        ${{ github.repository }}
          ANTHROPIC_API_KEY:  ${{ secrets.ANTHROPIC_API_KEY }}
          DRY_RUN:            ${{ inputs.dry_run }}
      # 最後一步將 scraper_output.log 寫入 Job Summary，
      # 供人工或 Claude Code 自動 QA 閱讀
```

Secrets 設定位置：GitHub repo → **Settings → Secrets and variables → Actions**

從 Actions 頁面手動觸發時可勾選 `dry_run`，僅預覽篩選結果、不推播也不寫檔。

---

## 安裝（本地執行）

```bash
pip install requests beautifulsoup4 lxml
```

---

## 免責聲明

本工具由自動爬蟲產生，資料僅供參考，請以各機關官方公告為準。
