# banini 使用指南

## 快速開始（3 分鐘）

### 1. 安裝 skill

```bash
git clone https://github.com/KerberosClaw/kc_ai_skills.git
cp -r kc_ai_skills/banini ~/.claude/skills/
```

### 2. 安裝依賴

```bash
pip3 install playwright parsel nested-lookup jmespath
python3 -m playwright install chromium
```

### 3. 使用

在 Claude Code 互動模式中：

```
/banini
```

Claude 會自動抓取巴逆逆的 Threads 貼文並進行反指標分析，結果直接顯示在對話中。

**這樣就能用了。** 以下是進階設定。

---

## 完整還原原版功能

原版 [cablate/banini-tracker](https://github.com/cablate/banini-tracker) 有三個核心功能：

| 功能 | 原版做法 | 本 skill 做法 |
|------|---------|-------------|
| 抓取貼文 | Apify API（$10.5/月） | 本地 Playwright（免費） |
| AI 分析 | LLM API（$1/月） | Claude 自己分析（Max 訂閱內） |
| 定時排程 + Telegram 通知 | Node.js + node-cron | **skill-cron**（見下方） |

前兩個裝完 `/banini` 就有了。要還原第三個（排程 + 通知），需要搭配 [skill-cron](../../skill-cron/)。

### Step 1：安裝 skill-cron

```bash
cp -r kc_ai_skills/skill-cron ~/.claude/skills/
```

### Step 2：設定 Telegram

在 Claude Code 中：

```
/skill-cron
```

選 `4. Telegram 設定` → `a. 設定 Token + Channel`，依序輸入：

- **Bot Token** — 在 Telegram 找 [@BotFather](https://t.me/BotFather)，發 `/newbot` 建立一個 bot，拿到 token
- **Chat ID** — 在 Telegram 找 [@userinfobot](https://t.me/userinfobot)，發 `/start`，拿到你的數字 ID

設定完會詢問是否發送測試訊息，確認能收到就 OK。

### Step 3：註冊排程

在 Claude Code 中：

```
/skill-cron
```

選 `2. 新增排程`，用自然語言描述排程時間，例如：

```
平日 9:00, 13:00 每天 23:00
```

Claude 會自動轉換成 cron 表達式、顯示確認表格，確認後寫入系統 crontab。

### 原版排程對照

原版 banini-tracker 的排程是：

| 排程 | 時間（台北） | 原版做法 | skill-cron 輸入 |
|------|------------|---------|----------------|
| 盤中 | 週一~五 09:07-13:07 每 30 分 | node-cron | `平日 9:07, 9:37, 10:07, 10:37, 11:07, 11:37, 12:07, 12:37, 13:07` |
| 盤後 | 每天 23:03 | node-cron | `每天 23:03` |

你可以完全照搬，也可以自訂。例如簡化為：

```
平日 9:00, 13:00, 23:00
```

---

## 各層獨立，自由組合

```
┌─────────────────────────────────────────┐
│  Layer 1：/banini（核心）                │
│  手動跑一次，看結果                       │
│  ✓ 只裝 banini 就能用                    │
├─────────────────────────────────────────┤
│  Layer 2：+ skill-cron（排程）           │
│  自動定時執行                            │
│  ✓ 需額外安裝 skill-cron                 │
├─────────────────────────────────────────┤
│  Layer 3：+ Telegram（通知）             │
│  結果推送到手機                           │
│  ✓ 需在 skill-cron 中設定 Telegram       │
└─────────────────────────────────────────┘
```

每一層都是選配。只裝 Layer 1 就是一個完全可用的反指標分析工具。

---

## 費用比較

| | 原版 | 本 skill |
|---|---|---|
| 爬蟲 | Apify ~$10.5/月 | 免費 |
| LLM | DeepInfra ~$1/月 | Claude Max 訂閱內 |
| 部署 | 需長駐 Node.js | 系統 crontab |
| **月成本** | **~$11** | **$0** |

---

## 常見問題

### Q: 爬蟲跑不動 / 沒抓到貼文

Playwright 需要 Chromium。確認已安裝：

```bash
python3 -m playwright install chromium
```

如果被 Threads 封鎖（通常不會，家用 IP 不太會觸發），等幾小時再試。

### Q: crontab 排程沒觸發

檢查 log：

```bash
ls ~/.claude/logs/skill-cron/
cat ~/.claude/logs/skill-cron/banini-*.log
```

常見問題：
- `claude: command not found` → cron_runner.sh 的 PATH 設定問題，確認 `~/.local/bin` 在 PATH 中
- 沒有 log 檔 → crontab 沒寫入，跑 `/skill-cron` 選 `sync`

### Q: Telegram 收不到通知

```
/skill-cron
```

選 `4. Telegram 設定` → `b. 發送測試訊息`，確認 token 和 chat ID 正確。

### Q: 可以追蹤其他人嗎？

可以。`/banini` 預設追蹤 `banini31`，但爬蟲支援任何 Threads 帳號。修改 SKILL.md 裡的 username 或 headless-prompt 即可。
