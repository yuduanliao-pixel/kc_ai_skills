# banini — 為什麼要改寫，以及我們踩了哪些坑

> **English summary:** Design rationale for rewriting [cablate/banini-tracker](https://github.com/cablate/banini-tracker) as a Claude Code skill. Key insight: Apify is just a headless browser ($10.5/mo) — we do the same locally with Playwright for free. Claude Max subscribers already have a better LLM than the original's MiniMax-M2.5 — so Claude analyzes directly, saving another $1/mo. Facebook source dropped (redundant content, aggressive anti-scraping). Total savings: $11/mo → $0.

## 原版在幹嘛

[cablate/banini-tracker](https://github.com/cablate/banini-tracker) 是一個追蹤巴逆逆（8zz）社群貼文的 Node.js 程式。巴逆逆是台灣知名的「股海冥燈」— 買什麼跌什麼，賣什麼漲什麼，空什麼就飆漲。這個 tracker 的核心邏輯就是：抓她的貼文 → 用 LLM 做反指標分析 → 推 Telegram。

原始架構很直覺：

```
Apify API（付費爬蟲）→ LLM API（付費模型）→ Telegram
```

問題是，**每個月要 $11 美元**。對一個「看冥燈照到誰」的娛樂工具來說，這個價格有點不合理。尤其當你已經在付 Claude Max 月費的時候。

| 項目 | 月費 | 用途 |
|------|------|------|
| Apify Threads Scraper | ~$4.5 | 抓 Threads 貼文 |
| Apify Facebook Scraper | ~$6 | 抓 FB 粉專貼文 |
| DeepInfra LLM API | ~$1 | 用 MiniMax-M2.5 分析 |
| **合計** | **~$11** | 看冥燈。每個月。 |

---

## 三個核心洞察

### 1. Apify 本質就是無頭瀏覽器

我們去翻了 Apify 的開源 Threads scraper（[the-ai-entrepreneur-ai-hub/threads-scraper](https://github.com/the-ai-entrepreneur-ai-hub/threads-scraper)），發現它底層就是 Puppeteer + CDP 攔截 GraphQL 回應。

你付 $10.5/月 買的，是一個跑在別人機器上的 Chrome。

我們用 Playwright 在本地做一模一樣的事。省 $10.5。

### 2. 你已經在付 LLM 的錢了

原版用 OpenAI SDK 呼叫 DeepInfra 的 MiniMax-M2.5 — 一個「中等偏弱」的模型，被塞在一個固定的 JSON schema 裡分析貼文。

如果你有 Claude Max，Claude Code 本身就是一個比 MiniMax-M2.5 強很多的 LLM。把分析邏輯寫進 skill prompt，Claude 自己讀完貼文直接分析。不用再花 $1/月呼叫別人的 API，而且分析品質大幅提升。

| | MiniMax-M2.5 | Claude |
|---|---|---|
| 費用 | $1/月 | Max 訂閱內 |
| 輸出 | 固定 JSON schema | 自由格式，更詳細 |
| 中文理解 | 普通 | 好 |
| 反指標判斷 | 偶爾搞混被套和停損 | 精確 |

### 3. Facebook 可以砍掉

巴逆逆的 FB 粉專和 Threads 內容高度重複。而 FB 的反爬機制比 Threads 嚴格很多 — 開源方案幾乎沒有能穩定跑的。砍掉 FB，只追蹤 Threads，影響趨近於零。

---

## 改寫了什麼

```
原版：Apify（$10.5）→ LLM API（$1）→ Telegram
改寫：Playwright（$0）→ Claude 自己（$0）→ 對話輸出（$0）
```

### Playwright 取代 Apify

自寫 `scripts/scrape_threads.py`。原理：

1. 啟動無頭 Chromium
2. 開 `https://www.threads.com/@banini31`
3. 攔截所有含 `graphql` 或 `barcelona` 的背景 API response
4. 用 `nested_lookup` 從巢狀 JSON 裡撈出 `post` 物件
5. 往下捲幾次，觸發更多貼文載入
6. 順便從 HTML 的 `<script>` tag 裡的內嵌 JSON 也撈一遍（雙重保險）

實測穩定抓 10-20 篇。不需要 API key。在家用 IP 上跑完全沒問題 — Threads 目前不太擋一般住宅 IP。

### Claude 取代 LLM API

不呼叫任何外部模型。Claude Code 自己就是分析引擎 — 讀完貼文後，依照 SKILL.md 裡的反指標規則分析。這些規則從原始專案的 `src/analyze.ts` 提取而來：

- **反指標規則表**：買入→可能跌、停損→可能漲、被套→續跌
- **被套 vs 停損的方向區分**：這兩個方向完全相反，原作者特別強調過，我們保留了這個邏輯
- **連鎖效應推導**：她停損油正二 → 油價可能反彈 → 通膨壓力回來
- **冥燈指數**：她越篤定，反指標信號越強
- **時序意識**：今天的貼文比昨天重要，最新的為準

---

## 技術選型的踩坑紀錄

### 「用 curl 抓就好了吧？」

不行。Threads 是純 client-side rendering。`curl` 和 `WebFetch` 抓到的是一堆 CSS custom properties 和 React bootstrap code。連一個字的貼文內容都沒有。我們花了 15 分鐘確認這件事。

### 「那用 Meta 官方 API？」

可以，但要申請 Meta 開發者帳號、建 app、走 OAuth、拿 long-lived token。免費但麻煩。如果你追求零設定，Playwright 更直接。

### 「RSSHub 有 Threads 的 route」

有，但被標為 broken。我們也試了 RSS-Bridge，一樣不穩。

### 「那 GraphQL endpoint 直接打？」

2023 年有人逆向工程出 `doc_id`，但 Meta 會定期換。你需要自己開 Chrome DevTools 攔截新的 `doc_id`。每次 Meta 工程師改版你就要重來一次。不如直接用 Playwright 攔截，同樣的原理但自動化了。

---

## 限制（誠實版）

1. **Threads 改版就壞** — Meta 哪天改 GraphQL schema 或 DOM 結構，爬蟲就需要更新。這不是「如果」的問題，是「什麼時候」
2. **純圖片抓不到** — 只能抓文字。圖片裡寫的「明天 ALL IN 台積電」抓不到。原版透過 Apify FB Scraper 有 OCR，但那個花 $6/月
3. **不是即時的** — 不像原版每 30 分鐘自動跑，你要手動 `/banini` 或搭配 [skill-cron](../skill-cron/) 設排程
4. **高頻抓取可能被擋** — 一天跑個幾次沒問題，但每分鐘跑一次可能會被 Threads 封 IP

---

## 參考

- 原始專案：[cablate/banini-tracker](https://github.com/cablate/banini-tracker)
- 爬蟲技術參考：[vdite/threads-scraper](https://github.com/vdite/threads-scraper)
- Apify 開源 scraper 分析：[the-ai-entrepreneur-ai-hub/threads-scraper](https://github.com/the-ai-entrepreneur-ai-hub/threads-scraper)
