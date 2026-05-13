# 每日政府標案爬蟲

自動抓取 8 個政府網站的最新標案，每日整理成 Email 通知。

## 涵蓋網站

| 機關 | 網址 |
|------|------|
| 台北自來水處 | https://www.water.gov.taipei |
| 國營台鐵 | https://www.railway.gov.tw |
| 新北市政府財政局 | https://www.finance.ntpc.gov.tw |
| 農業部 瑠公管理處 | https://www.ialgo.nat.gov.tw |
| 郵局房地產出租 | https://www.post.gov.tw |
| 台北市財政局 | https://dof.gov.taipei |
| 國家住宅及都市更新中心 | https://www.hurc.org.tw |
| 國有財產署 | https://esvc.fnp.gov.tw |

---

## 快速開始

### 1. 安裝相依套件

```bash
pip install requests beautifulsoup4 lxml
```

### 2. 設定 Email（Gmail）

需要開啟 Gmail **兩步驟驗證** 並產生 **應用程式密碼**：
1. Google 帳戶 → 安全性 → 兩步驟驗證（開啟）
2. Google 帳戶 → 安全性 → 應用程式密碼 → 產生（選「郵件」）
3. 複製產生的 16 碼密碼

```bash
export EMAIL_FROM="你的Gmail@gmail.com"
export EMAIL_TO="收件者@gmail.com"
export EMAIL_PASSWORD="xxxx xxxx xxxx xxxx"   # 16 碼應用程式密碼
```

### 3. 執行一次測試

```bash
python scraper.py
```

首次執行若未設定 Email，會將結果存成 `tender_preview.html` 供預覽。

---

## 自動化方式（三種選擇）

### 方式 A：Claude Code Routine（推薦，不需電腦開著）

1. 前往 https://claude.ai/code/routines → **New routine**
2. **Instructions（Prompt）** 填入：

```
請執行以下步驟：
1. 執行 scraper.py：python scraper.py
2. 確認執行成功並顯示各網站抓取結果
```

3. **Environment** → Network Access 改為 **Custom**，加入以下網域：
```
water.gov.taipei
www.railway.gov.tw
www.finance.ntpc.gov.tw
www.ialgo.nat.gov.tw
www.post.gov.tw
dof.gov.taipei
www.hurc.org.tw
esvc.fnp.gov.tw
smtp.gmail.com
```

4. **Environment Variables** 新增：
   - `EMAIL_FROM` = 你的Gmail
   - `EMAIL_TO` = 收件者Email
   - `EMAIL_PASSWORD` = 應用程式密碼

5. **Trigger** → Schedule → 每天（例如早上 8 點）

6. 點 **Create** 完成！

---

### 方式 B：Claude Cowork Desktop（需電腦開著）

1. 開啟 Claude Desktop → 切換到 **Cowork**
2. 新增任務，描述：
   > 「每天早上 8 點，請執行 scraper.py 並確認 Email 寄出成功」
3. 在任務中輸入 `/schedule` 設定排程

---

### 方式 C：本機 cron（最輕量）

```bash
# 編輯 crontab
crontab -e

# 加入這行（每天早上 8:00 執行）
0 8 * * * EMAIL_FROM="你的gmail" EMAIL_TO="收件者" EMAIL_PASSWORD="密碼" /usr/bin/python3 /path/to/scraper.py >> /tmp/tender.log 2>&1
```

---

## Email 預覽樣式

寄出的 Email 包含：
- 每個機關的最新標案清單（最多 10 筆）
- 標案標題（可點擊連結）
- 日期資訊
- 整齊的 HTML 排版

---

## 常見問題

**Q：某個網站抓不到資料？**
A：政府網站結構常更新，可能需要調整對應的 `scrape_*` 函式中的 CSS selector。

**Q：Gmail 登入失敗？**
A：確認使用的是「應用程式密碼」（16 碼），不是 Gmail 登入密碼。

**Q：想加入其他網站？**
A：複製任一 `scrape_*` 函式，修改 URL 和 CSS selector，再加入 `SOURCES` 清單即可。
