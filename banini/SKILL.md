---
name: banini
description: "巴逆逆（8zz）反指標追蹤器 — 抓取 Threads 貼文並進行台股反指標分析。Use when user says '/banini', '巴逆逆', '反指標', '冥燈' or similar."
version: 0.1.0
headless-prompt: |
  Run ~/.claude/skills/banini/.venv/bin/python ~/.claude/skills/banini/scripts/scrape_threads.py banini31 5, parse the JSON output, then perform 反指標 contrarian analysis. Rules: 買入=可能跌, 停損=可能反彈, 被套=續跌, 看多=可能跌, 看空=可能漲, 買put=可能飆漲. Output a CONCISE report in Traditional Chinese, optimized for Telegram reading. Format:
  1) 一句話總結（今日冥燈狀態）
  2) 冥燈指數 N/10
  3) 標的表格（標的 | 她的動作 | 反指標方向 | 信心），每列一行，不要用 markdown table
  4) 一段 2-3 句的綜合判斷
  5) 分隔線後，附上 3-5 則關鍵原文摘錄（只取投資相關的），格式：「原文節錄」— 讚數
  End with 僅供娛樂參考，不構成投資建議. Keep total output under 3000 chars.
---

# banini

追蹤「反指標女神」巴逆逆（8zz）的 Threads 貼文，直接進行反指標分析。

## Trigger

```
/banini [username]
```

- 預設 username: `banini31`
- 可指定其他 Threads 帳號

## Prerequisites Check

執行前確認 venv 與 Playwright 環境：

```bash
${CLAUDE_SKILL_DIR}/.venv/bin/python -c "from playwright.async_api import async_playwright; from nested_lookup import nested_lookup; from parsel import Selector; print('OK')"
```

缺少 venv 或套件時顯示安裝指令並停止（首次安裝會建立隔離 venv，避免踩到 PEP 668）：
```bash
python3 -m venv ${CLAUDE_SKILL_DIR}/.venv
${CLAUDE_SKILL_DIR}/.venv/bin/pip install playwright parsel nested-lookup jmespath
${CLAUDE_SKILL_DIR}/.venv/bin/python -m playwright install chromium
```

---

## 執行流程

### Step 1：抓取 Threads 貼文

用本 skill 自帶的爬蟲腳本抓取貼文：

```bash
${CLAUDE_SKILL_DIR}/.venv/bin/python ${CLAUDE_SKILL_DIR}/scripts/scrape_threads.py banini31 5
```

- 輸出為 JSON array（stdout），按時間從新到舊排列
- `5` 是捲動次數，通常能抓 10-20 篇
- 進度訊息輸出到 stderr，不影響 JSON 解析

拿到 JSON 後，讀取並整理為以下格式供分析用：

```
貼文 N【今天/昨天/MM/DD】（YYYY/MM/DD HH:MM）
{貼文文字}
讚: {likes} | 回覆: {reply_count}
```

- `taken_at` 為 Unix timestamp，轉換為台北時間（UTC+8）
- 標記「今天」「昨天」的貼文，分析時優先處理

### Step 2：反指標分析

你（Claude）就是分析引擎。讀完整理好的貼文後，依以下邏輯進行分析。

#### 反指標核心規則

| 她的狀態 | 反指標解讀 | 原因 |
|---------|-----------|------|
| 買入/加碼 | 該標的可能下跌 | 她買什麼跌什麼 |
| 持有中/被套（還沒賣） | 該標的可能繼續跌 | 她還沒認輸，底部還沒到 |
| 停損/賣出 | 該標的可能反彈上漲 | 她認輸出場 = 底部訊號 |
| 看多/喊買 | 該標的可能下跌 | 她看好的通常會跌 |
| 看空/喊賣 | 該標的可能上漲 | 她看衰的通常會漲 |
| 空單/買 put | 該標的可能飆漲 | 她空什麼就漲什麼 |

#### 特別注意

- **被套 vs 停損方向完全相反**：被套（還抱著）= 可能續跌；停損（認賠賣出）= 可能反彈
- **只根據貼文明確提到的操作判斷**，不要自行推測或腦補
- **標的名稱用正式名稱**（如「信驊」「旺宏」），不要用她的暱稱（如「王」「渣男」）
- **當天貼文最重要**，前幾天的參考價值遞減
- 如果她先說要買 A，後來又說停損 A，以最新的為準

#### 分析流程

1. **辨識標的**：她提到了哪些股票、產業、原物料、ETF？
2. **判斷操作狀態**：買入、被套、停損、看多、看空？
3. **反轉推導**：根據上表反轉，說清楚方向和原因
4. **連鎖效應**：反轉後會影響哪些相關板塊？具體講出影響鏈
5. **冥燈指數**：她語氣越篤定/興奮 → 反指標越強；越崩潰/後悔 → 趨勢即將反轉

### Step 3：輸出報告

使用以下格式輸出：

```
## 巴逆逆反指標分析報告

**分析時間：** {現在時間}（台北時間）
**資料來源：** Threads @{username}，共 {N} 篇貼文（{最舊日期} ~ {最新日期}）

---

### 提及標的

（對每個辨識出的標的，輸出以下格式）

#### {↑↓→} {標的名稱}（{類型}）— `{日期 HH:MM}`

> 「{原文摘錄}」

- **她的操作：** {操作描述}
- **反指標：** {反轉推導}
- **信心：** {高/中/低} — {原因}
- **相關標的：** {如適用}

---

### 連鎖推導

（2-4 點，用 1. 2. 3. 編號）

### 建議方向

（1-2 段文字）

### 冥燈指數：{N}/10

{一句話解釋}

---

*僅供娛樂參考，不構成投資建議*
```

#### 輸出規則

- 箭頭方向：`↑` = 反指標看漲、`↓` = 反指標看跌、`→` = 方向不明
- 與投資無關的貼文（生活、搞笑）不列入標的分析，但可在開頭簡要提及
- 沒有投資相關貼文時，直接說明「本批貼文與投資無關」，省略標的分析
- 全程使用繁體中文

---

## skill-cron 整合

本 skill 支援透過 [skill-cron](../skill-cron/) 進行排程執行與 Telegram 推播。

SKILL.md frontmatter 已包含 `headless-prompt`，可直接用 skill-cron 註冊：

```
/skill-cron add banini "7,37 9-12 * * 1-5" 盤中
/skill-cron add banini "3 23 * * *" 盤後
```

詳見 [skill-cron SKILL.md](../skill-cron/SKILL.md)。

---

## 注意事項

- 此 skill 不執行任何交易操作
- 爬蟲使用本地 Playwright（無頭 Chromium），不需要任何 API key
- 分析由 Claude 直接完成，不呼叫外部 LLM API
- 互動模式用 `/banini`，非互動排程用 skill-cron（`claude -p` 不支援 `/skill` 語法）
- 僅供娛樂參考，不構成投資建議
