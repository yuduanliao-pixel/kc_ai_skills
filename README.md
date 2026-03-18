# Claude Code Skills Collection

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

[正體中文](README_zh.md)

A collection of reusable [Claude Code](https://claude.ai/claude-code) skills for local LLM workflows, benchmarking, and project publishing.

## Skills

| Skill | Description |
|-------|-------------|
| [prep-repo](prep-repo/) | Prepare a project for GitHub: README conventions, commit style, sensitive data scan, broken link check |
| [llm-benchmark](llm-benchmark/) | Automated Ollama model benchmark with CPU offload detection and markdown report generation |
| [searxng](searxng/) | Local search integration via SearXNG for OpenClaw or any exec-based AI agent |

## Installation

Clone this repo, then copy the skills you need:

```bash
git clone https://github.com/KerberosClaw/kc_claude_skills.git

# Install a skill for Claude Code (user-level)
cp -r kc_claude_skills/prep-repo ~/.claude/skills/

# Install a skill for OpenClaw (workspace-level)
cp -r kc_claude_skills/searxng ~/.openclaw/workspace/skills/
```

> **Naming tip:** Feel free to rename the skill folder with your own prefix when copying (e.g. `my_prep-repo`).

## Skill Structure

Each skill follows the Claude Code convention:

```
skill-name/
├── SKILL.md          # Frontmatter (name, description, version) + instructions
└── scripts/          # Executable scripts (optional)
    └── script.py
```

## Related Projects

- [kc_tradfri_mcp](https://github.com/KerberosClaw/kc_tradfri_mcp) — IKEA TRADFRI MCP Server for AI smart home control
- [kc_openclaw_local_llm](https://github.com/KerberosClaw/kc_openclaw_local_llm) — OpenClaw + local LLM guide with 13 models tested
