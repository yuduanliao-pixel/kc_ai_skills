# skill-cron — 因為 `claude -p "/banini"` 會卡住

## 這東西為什麼存在

Claude Code 的 skill 在互動模式下很好用 — 打 `/banini` 就跑。但如果你想用 crontab 定時跑呢？

```bash
# 你以為可以這樣
claude -p "/banini"

# 實際上會無限卡住，沒有任何輸出
# 我們在凌晨一點 debug 了 40 分鐘才搞懂為什麼
```

官方文件寫了，但埋在一段不起眼的地方：

> "User-invoked skills like `/commit` are only available in interactive mode. In `-p` mode, describe the task you want to accomplish instead."

翻譯：**`claude -p` 不支援 `/skill` 語法。** 它不會報錯，它只是卡住。靜靜地。直到你 `Ctrl+C`。

所以你得用直接描述的方式：

```bash
# 這個能跑
claude -p "Run python3 ~/.claude/skills/banini/scripts/scrape_threads.py banini31 5, then analyze..."
```

但你不可能把這種一百字的 prompt 塞進 crontab。於是 skill-cron 誕生了。

---

## 設計思路

### 職責分離

```
Skill（如 /banini）
  = 純核心功能：爬蟲 + 分析 + 輸出
  = 只管做事，不管什麼時候做

skill-cron（管理器）
  = 什麼時候做：crontab 排程
  = 做完通知誰：Telegram 推播
  = 怎麼在 -p 模式跑 skill：headless-prompt 橋接
```

一個 skill 不需要知道自己會被排程。一個排程系統不需要知道 skill 在做什麼。中間靠一個叫 `headless-prompt` 的欄位連接。

### headless-prompt：給 `-p` 模式用的翻譯層

在 SKILL.md 的 frontmatter 加一個欄位：

```yaml
---
name: banini
headless-prompt: "Run python3 ~/.claude/skills/banini/scripts/scrape_threads.py banini31 5, then analyze..."
---
```

規則是從踩坑中學來的：

- **必須用絕對路徑** — `${CLAUDE_SKILL_DIR}` 在 `-p` 模式不展開。我們試過。
- **不能用 `/skill` 語法** — 前面說了，會卡住。
- **要寫完整指令** — `-p` 模式下 Claude 看不到 SKILL.md 的內容，它只有你給的 prompt。

### 執行鏈

```
crontab（系統排程）
  → cron_runner.sh（我們的 wrapper）
    → 設好環境變數（PATH, USER, SHELL — crontab 什麼都沒有）
    → claude --dangerously-skip-permissions -p "{prompt}"
    → 拿到分析結果
    → 如有 Telegram config → urllib 推送
    → 寫 log，清理舊 log
```

中間那個 `--dangerously-skip-permissions` 是因為 crontab 環境沒有人可以按「允許」。

---

## crontab 環境的三個坑

我們全踩了，這樣你就不用踩：

### 坑 1：`claude: command not found`

crontab 的 PATH 只有 `/usr/bin:/bin`。你的 `claude` 裝在 `~/.local/bin`。

```bash
# cron_runner.sh 裡的修正
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"
```

### 坑 2：`Not logged in`

crontab 環境沒有 `USER` 和 `SHELL` 環境變數。claude CLI 需要這兩個來找到它的 auth 設定。

```bash
export USER="${USER:-$(whoami)}"
export SHELL="${SHELL:-/bin/bash}"
```

### 坑 3：Telegram 推送的引號地獄

原本用 `curl` + inline Python 構造 JSON payload，在 shell 的 heredoc 裡嵌 Python，Python 裡面有 shell 變數。三層引號互相打架。

最後放棄 curl，改用純 Python `urllib.request` 一行解決。

---

## 設定檔放哪

`~/.claude/configs/skill-cron.json`

考慮過 `~/.config/skill-cron/`（XDG 標準），但這個 skill 就是 Claude Code 生態系的一部分，放 `~/.claude/` 下更自然。而且這個檔案存了 Telegram bot token，不能被 git 追蹤 — `~/.claude/` 本來就不在任何 repo 裡。

## Crontab 管理

skill-cron 在 crontab 裡用標記區塊圍住自己的 entries：

```crontab
# 你自己的 crontab...

# SKILL-CRON-BEGIN — managed by skill-cron, do not edit
7,37 9-12 * * 1-5 /path/to/cron_runner.sh 'prompt...' 'banini-盤中'
3 23 * * * /path/to/cron_runner.sh 'prompt...' 'banini-盤後'
# SKILL-CRON-END
```

不會動你自己的 crontab。`add` 的時候自動寫入，`remove` 的時候自動移除。

## 日誌

`~/.claude/logs/skill-cron/{job_id}-{timestamp}.log`

每個 job 留最近 50 筆。debug 的時候第一件事就是 `cat` 最新的 log — 如果裡面寫 `command not found`，你就知道是 PATH 的問題。

## 支援 skill-cron 的 skill

| Skill | 做什麼 | 建議排程 |
|-------|--------|---------|
| [banini](../banini/) | 冥燈追蹤 | 盤中 `平日 9:07-13:07 每 30 分`、盤後 `每天 23:03` |

想讓你的 skill 也支援排程？在 SKILL.md frontmatter 加 `headless-prompt` 就好。一行。

## 限制

1. **crontab 是本地的** — 你的 Mac 關機了就不會跑。沒有雲端 fallback
2. **Claude Max 訂閱** — `claude -p` 需要有效的登入狀態。token 過期了要重新 `claude login`
3. **Telegram 4096 字元** — 超長報告會被截斷。但說真的，你不需要在手機上看一萬字的分析報告
4. **`--dangerously-skip-permissions`** — 名字聽起來很危險，但在 crontab 裡沒有別的選擇。它只是跳過互動式的權限確認
