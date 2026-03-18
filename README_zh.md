# Claude Code Skills 合集

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

[English](README.md)

一組可重複使用的 [Claude Code](https://claude.ai/claude-code) skill，用於本地 LLM 工作流、效能測試與專案發佈。

## Skills

| Skill | 說明 |
|-------|------|
| [prep-repo](prep-repo/) | 專案上 GitHub 前的準備：README 規範、commit 風格、敏感資訊掃描、broken link 檢查 |
| [llm-benchmark](llm-benchmark/) | Ollama 模型自動化 benchmark，含 CPU offload 偵測與 markdown 報告產生 |
| [searxng](searxng/) | 透過 SearXNG 的本地搜尋整合，適用於 OpenClaw 或任何支援 exec 的 AI agent |

## 安裝

Clone 此 repo，複製需要的 skill：

```bash
git clone https://github.com/KerberosClaw/kc_claude_skills.git

# 安裝 skill 到 Claude Code（使用者層級）
cp -r kc_claude_skills/prep-repo ~/.claude/skills/

# 安裝 skill 到 OpenClaw（workspace 層級）
cp -r kc_claude_skills/searxng ~/.openclaw/workspace/skills/
```

> **命名提示：** 複製時可自行加上前綴重新命名（如 `my_prep-repo`）。

## Skill 結構

每個 skill 遵循 Claude Code 規範：

```
skill-name/
├── SKILL.md          # Frontmatter（name, description, version）+ 指令
└── scripts/          # 可執行腳本（選用）
    └── script.py
```

## 相關專案

- [kc_tradfri_mcp](https://github.com/KerberosClaw/kc_tradfri_mcp) — IKEA TRADFRI MCP Server，AI 自然語言智慧家居控制
- [kc_openclaw_local_llm](https://github.com/KerberosClaw/kc_openclaw_local_llm) — OpenClaw + 本地 LLM 指南，13 個模型實測
