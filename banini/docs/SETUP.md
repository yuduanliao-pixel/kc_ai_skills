# banini 使用指南

> **English summary:** Step-by-step setup guide for the banini skill. Layer 1: install skill + Playwright deps, run `/banini`. Layer 2: add skill-cron for scheduled runs via crontab. Layer 3: configure Telegram for push notifications. Each layer is optional — Layer 1 alone is a fully functional reverse-indicator analyzer.

## 快速開始（3 分鐘，我計時了）

### 1. 裝 skill

```bash
git clone https://github.com/KerberosClaw/kc_ai_skills.git
ln -sfn "$(pwd)/kc_ai_skills/banini" ~/.claude/skills/banini
# 或者 cp -r kc_ai_skills/banini ~/.claude/skills/ 也可以，symlink 只是比較好跟 git pull
```

### 2. 建 venv + 裝依賴

```bash
python3 -m venv ~/.claude/skills/banini/.venv
~/.claude/skills/banini/.venv/bin/pip install playwright parsel nested-lookup jmespath
~/.claude/skills/banini/.venv/bin/python -m playwright install chromium
```

為什麼要 venv 不直接 `pip3 install`？因為新版 Homebrew Python 會把自己標記為 externally-managed，全域和 `--user` 安裝都會被 PEP 668 擋掉。venv 是 Python 生態對這件事的標準答案，完全不碰 host Python、每個 skill 一個隔離環境、要砍掉重來直接 `rm -rf .venv`。

對，你需要下載一整個 Chromium。因為 Threads 是純 client-side rendering — 用 `curl` 抓到的只有一坨 CSS 變數和 React bootstrap，連一個字的貼文內容都沒有。我們試過了。

### 3. 開始追蹤冥燈

```
/banini
```

Claude 會啟動無頭 Chromium、假裝是真人滑 Threads、攔截背景的 GraphQL 回應、解析貼文、然後用它自己的腦子做反指標分析。

就這樣。沒有 API key。沒有付費服務。沒有 `.env` 要填。

**以下是進階設定 — 如果你想要完全還原原版的功能。**

---

## 我想要排程 + Telegram 通知（完整還原原版）

原版 [cablate/banini-tracker](https://github.com/cablate/banini-tracker) 是一個 Node.js 常駐程式，每 30 分鐘抓一次貼文、用 LLM API 分析、推 Telegram。每月花 $11 美元。

我們把這三件事拆成三層，每層獨立，不想裝的可以不裝：

```
┌─────────────────────────────────────────┐
│  Layer 1：/banini                       │
│  手動跑一次，在對話裡看結果              │
│  裝完上面三步就有了                       │
├─────────────────────────────────────────┤
│  Layer 2：+ skill-cron                  │
│  自動定時執行，你不用記得開 Claude        │
│  嗯，你還是要記得不能關機                 │
├─────────────────────────────────────────┤
│  Layer 3：+ Telegram                    │
│  分析結果直接推到手機                    │
│  在捷運上就能看今天冥燈照到誰             │
└─────────────────────────────────────────┘
```

### Step 1：裝 skill-cron

```bash
cp -r kc_ai_skills/skill-cron ~/.claude/skills/
```

### Step 2：設定 Telegram

```
/skill-cron
```

選 `4. Telegram 設定`，它會一步一步問你要什麼：

1. **Bot Token** — 去 Telegram 找 [@BotFather](https://t.me/BotFather)，發 `/newbot`，它會給你一串 `123:ABC...` 的 token
2. **Chat ID** — 去 Telegram 找 [@userinfobot](https://t.me/userinfobot)，發 `/start`，它會回你一個數字

貼進去，選「發送測試訊息」，手機叮一聲就對了。

### Step 3：設排程

```
/skill-cron
```

選 `2. 新增排程`，用人話描述你要的時間。不用寫 cron 語法，那是給機器看的：

```
平日 9:00, 13:00, 23:00
```

Claude 會自己翻譯成 cron、列表格給你看、你說 OK 它才寫入 crontab。

### 想照搬原版的排程？

| 原版 | 時間 | 你可以輸入 |
|------|------|----------|
| 盤中 | 週一~五 09:07-13:07 每 30 分 | `平日 9:07, 9:37, 10:07, 10:37, 11:07, 11:37, 12:07, 12:37, 13:07` |
| 盤後 | 每天 23:03 | `每天 23:03` |

或者你可以簡化。巴逆逆不是每 30 分鐘都在發廢文。

---

## 費用比較

| | 原版 | 這個 skill |
|---|---|---|
| 爬蟲 | Apify ~$10.5/月 | 你的電腦跑 Chromium，免費 |
| LLM | DeepInfra ~$1/月 | Claude 自己就是 LLM，Max 訂閱內 |
| 部署 | 需要一台一直開著的機器跑 Node.js | 系統 crontab，關機前都會跑 |
| **月成本** | **~$11** | **$0**，除非你算電費 |

---

## 東西壞了怎麼辦

### 爬蟲跑不動

十之八九是 Chromium 沒裝：

```bash
~/.claude/skills/banini/.venv/bin/python -m playwright install chromium
```

如果裝了還是不行，可能是 Threads 改版了。這件事遲早會發生 — Meta 的工程師總是需要一些存在感。開 issue 或自己改 `scrape_threads.py`，核心邏輯就是攔截 GraphQL response 然後 parse JSON。

### 排程沒在跑

看 log：

```bash
cat ~/.claude/logs/skill-cron/banini-*.log
```

- 看到 `claude: command not found` → crontab 找不到 claude binary，`cron_runner.sh` 裡的 PATH 可能需要調整
- 看到 `Not logged in` → crontab 環境缺少 `USER` 或 `SHELL` 環境變數，`cron_runner.sh` 裡已經有修正，確認你用的是最新版
- 連 log 檔都沒有 → crontab 根本沒寫入，跑 `/skill-cron` 選 `sync`

### Telegram 收不到

跑 `/skill-cron` → `4. Telegram 設定` → `b. 發送測試訊息`。

如果測試訊息也收不到，token 或 chat ID 填錯了。重新設定一次。

### 我想追蹤別人

改 SKILL.md 裡的 username 就好。爬蟲支援任何 Threads 帳號。不過反指標分析的 prompt 是針對巴逆逆寫的 — 如果你要追蹤的人不是股海冥燈，分析結果可能會很好笑。
