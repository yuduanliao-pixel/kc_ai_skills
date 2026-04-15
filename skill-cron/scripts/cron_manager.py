#!/usr/bin/env python3
"""
skill-cron manager — manage scheduled skill jobs + Telegram push

Config: ~/.claude/configs/skill-cron.json
Scheduler: macOS launchd (LaunchAgents) or Windows Task Scheduler.
"""
import json
import os
import plistlib
import subprocess
import sys
import re
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime

CONFIG_DIR = Path.home() / ".claude" / "configs"
CONFIG_FILE = CONFIG_DIR / "skill-cron.json"
SKILL_DIR = Path.home() / ".claude" / "skills"
IS_WINDOWS = sys.platform.startswith("win")
RUNNER_SCRIPT = Path(__file__).parent / ("cron_runner.ps1" if IS_WINDOWS else "cron_runner.sh")
LAUNCHD_DIR = Path.home() / "Library" / "LaunchAgents"
LAUNCHD_PREFIX = "com.skill-cron."
SCHTASKS_PREFIX = "skill-cron-"


# ── Config ──────────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {"telegram": {}, "jobs": []}
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    if not IS_WINDOWS:
        CONFIG_FILE.chmod(0o600)
    print(f"[config] saved: {CONFIG_FILE}")


def get_python_cmd() -> str:
    return "python" if IS_WINDOWS else "python3"


def windows_task_name(job_id: str, index: int) -> str:
    return f"{SCHTASKS_PREFIX}{job_id}-{index}"


# ── Skill resolution ───────────────────────────────────────

def find_skill(name: str) -> Path | None:
    """Find a skill directory by name."""
    skill_path = SKILL_DIR / name
    if skill_path.exists() and (skill_path / "SKILL.md").exists():
        return skill_path
    return None


def read_headless_prompt(skill_name: str) -> str | None:
    """Read headless-prompt from SKILL.md frontmatter, or return None."""
    skill_path = find_skill(skill_name)
    if not skill_path:
        return None

    skill_md = skill_path / "SKILL.md"
    content = skill_md.read_text(encoding="utf-8")

    # Parse YAML frontmatter
    if not content.startswith("---"):
        return None

    end = content.find("---", 3)
    if end == -1:
        return None

    frontmatter = content[3:end]

    # Look for headless-prompt field
    match = re.search(r'headless-prompt:\s*["\'](.+?)["\']', frontmatter)
    if match:
        return match.group(1)

    # Multi-line headless-prompt with |
    match = re.search(r'headless-prompt:\s*\|\s*\n((?:\s+.+\n?)+)', frontmatter)
    if match:
        lines = match.group(1).split("\n")
        return "\n".join(line.strip() for line in lines if line.strip())

    return None


# ── Job management ─────────────────────────────────────────

def make_job_id(skill: str, label: str) -> str:
    """Generate a job ID from skill name and label."""
    clean_skill = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff]', '-', skill).strip('-')
    clean_label = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff]', '-', label).strip('-')
    return f"{clean_skill}-{clean_label}" if clean_label else clean_skill


def cmd_add(args: list[str]) -> None:
    if len(args) < 3:
        print("Usage: add <skill> <cron_expr> <label>")
        print('Example: add banini "7,37 9-12 * * 1-5" 盤中')
        sys.exit(1)

    skill = args[0]
    cron_expr = args[1]
    label = args[2]

    # Validate skill exists
    if not find_skill(skill):
        print(f"[error] skill not found: {skill}")
        print(f"[error] looked in: {SKILL_DIR / skill}")
        sys.exit(1)

    # Check for headless prompt
    prompt = read_headless_prompt(skill)
    if not prompt:
        print(f"[error] skill '{skill}' has no headless-prompt in SKILL.md frontmatter")
        print(f"[hint] add this to {SKILL_DIR / skill / 'SKILL.md'} frontmatter:")
        print(f'  headless-prompt: "Run python ... and analyze the output"')
        sys.exit(1)

    config = load_config()
    job_id = make_job_id(skill, label)

    # Check duplicate
    if any(j["id"] == job_id for j in config["jobs"]):
        print(f"[error] job already exists: {job_id}")
        sys.exit(1)

    task_names = []
    if IS_WINDOWS:
        intervals = parse_cron_to_task_intervals(cron_expr)
        if not intervals:
            print(f"[error] unsupported cron expression for Windows Task Scheduler: {cron_expr}")
            sys.exit(1)
        task_names = [windows_task_name(job_id, idx + 1) for idx in range(len(intervals))]

    job = {
        "id": job_id,
        "skill": skill,
        "cron": cron_expr,
        "label": label,
        "enabled": True,
        "created": datetime.now().isoformat(),
        "task_names": task_names,
    }

    config["jobs"].append(job)
    save_config(config)
    sync_schedule(config)
    print(f"[added] {job_id}: {cron_expr} ({label})")


def cmd_remove(args: list[str]) -> None:
    if not args:
        print("Usage: remove <job_id>")
        sys.exit(1)

    job_id = args[0]
    config = load_config()
    before = len(config["jobs"])
    config["jobs"] = [j for j in config["jobs"] if j["id"] != job_id]

    if len(config["jobs"]) == before:
        print(f"[error] job not found: {job_id}")
        sys.exit(1)

    save_config(config)
    sync_schedule(config)
    print(f"[removed] {job_id}")


def cmd_enable_disable(args: list[str], enabled: bool) -> None:
    if not args:
        print(f"Usage: {'enable' if enabled else 'disable'} <job_id>")
        sys.exit(1)

    job_id = args[0]
    config = load_config()

    for job in config["jobs"]:
        if job["id"] == job_id:
            job["enabled"] = enabled
            save_config(config)
            sync_schedule(config)
            print(f"[{'enabled' if enabled else 'disabled'}] {job_id}")
            return

    print(f"[error] job not found: {job_id}")
    sys.exit(1)


def cmd_list(_args: list[str]) -> None:
    config = load_config()

    # Telegram status
    tg = config.get("telegram", {})
    if tg.get("bot_token") and tg.get("channel_id"):
        masked_token = tg["bot_token"][:8] + "..." + tg["bot_token"][-4:]
        print(f"Telegram: {masked_token} → {tg['channel_id']}")
    else:
        print("Telegram: not configured")

    print()

    jobs = config.get("jobs", [])
    if not jobs:
        print("No scheduled jobs.")
        return

    print(f"{'ID':<25} {'Schedule':<25} {'Label':<10} {'Status':<10}")
    print("-" * 70)
    for j in jobs:
        status = "enabled" if j.get("enabled", True) else "disabled"
        print(f"{j['id']:<25} {j['cron']:<25} {j['label']:<10} {status:<10}")


# ── Telegram ───────────────────────────────────────────────

def cmd_telegram_set(args: list[str]) -> None:
    if len(args) < 2:
        print("Usage: telegram-set <bot_token> <channel_id>")
        sys.exit(1)

    config = load_config()
    config["telegram"] = {"bot_token": args[0], "channel_id": args[1]}
    save_config(config)
    print("[telegram] credentials saved")


def cmd_telegram_test(_args: list[str]) -> None:
    config = load_config()
    tg = config.get("telegram", {})

    if not tg.get("bot_token") or not tg.get("channel_id"):
        print("[error] telegram not configured. Run: telegram-set <token> <channel_id>")
        sys.exit(1)

    msg = f"skill-cron test message\n{datetime.now().isoformat()}"
    success = send_telegram(tg["bot_token"], tg["channel_id"], msg)
    if success:
        print("[telegram] test message sent successfully")
    else:
        print("[telegram] failed to send test message")
        sys.exit(1)


def cmd_telegram_remove(_args: list[str]) -> None:
    config = load_config()
    config["telegram"] = {}
    save_config(config)
    print("[telegram] credentials removed")


def send_telegram(bot_token: str, channel_id: str, text: str) -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = json.dumps({
        "chat_id": channel_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()

    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[telegram] error: {e}", file=sys.stderr)
        return False


# ── scheduler sync ─────────────────────────────────────────

def parse_cron_to_task_intervals(cron_expr: str) -> list[dict]:
    """Convert a cron expression to Windows Task Scheduler intervals.

    Supports standard 5-field cron: minute hour dom month dow
    Handles comma-separated values and ranges (e.g. 1-5, 9-12).
    Only explicit hour/minute values are supported for Windows.
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return []

    def expand_field(field: str, lo_bound: int, hi_bound: int) -> list[int] | None:
        if field == "*":
            return None
        values = set()
        for token in field.split(","):
            if "-" in token:
                try:
                    lo, hi = token.split("-", 1)
                    lo_val, hi_val = int(lo), int(hi)
                except ValueError:
                    return []
                if not (lo_bound <= lo_val <= hi_bound and lo_bound <= hi_val <= hi_bound):
                    return []
                values.update(range(lo_val, hi_val + 1))
            elif token.isdigit():
                val = int(token)
                if not (lo_bound <= val <= hi_bound):
                    return []
                values.add(val)
            else:
                return []
        return sorted(values)

    minutes = expand_field(parts[0], 0, 59)
    hours = expand_field(parts[1], 0, 23)
    dom = parts[2]
    month = parts[3]
    weekdays = expand_field(parts[4], 0, 7)

    if dom != "*" or month != "*":
        return []
    if minutes is None or hours is None:
        return []

    intervals = []
    for h in hours:
        for m in minutes:
            intervals.append({"Hour": h, "Minute": m, "Weekdays": weekdays})
    return intervals


def cron_weekdays_to_schtasks_days(days: list[int] | None) -> str | None:
    if days is None:
        return None

    normalized = {0 if d in (0, 7) else d for d in days}
    if normalized == {0, 1, 2, 3, 4, 5, 6}:
        return None

    names = {0: "SUN", 1: "MON", 2: "TUE", 3: "WED", 4: "THU", 5: "FRI", 6: "SAT"}
    ordered = sorted(normalized, key=lambda d: 7 if d == 0 else d)
    return ",".join(names[d] for d in ordered)


def delete_windows_task(task_name: str) -> None:
    subprocess.run(
        ["schtasks", "/Delete", "/TN", task_name, "/F"],
        capture_output=True,
        text=True,
    )


def disable_windows_task(task_name: str) -> None:
    subprocess.run(
        ["schtasks", "/Change", "/TN", task_name, "/DISABLE"],
        capture_output=True,
        text=True,
    )


def enable_windows_task(task_name: str) -> None:
    subprocess.run(
        ["schtasks", "/Change", "/TN", task_name, "/ENABLE"],
        capture_output=True,
        text=True,
    )


def create_windows_tasks(job: dict, intervals: list[dict], enabled: bool) -> int:
    created = 0
    for idx, interval in enumerate(intervals, start=1):
        task_name = windows_task_name(job["id"], idx)
        delete_windows_task(task_name)

        schedule = cron_weekdays_to_schtasks_days(interval["Weekdays"])
        if schedule is None:
            sc_args = ["/SC", "DAILY"]
        else:
            sc_args = ["/SC", "WEEKLY", "/D", schedule]

        command = (
            f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{RUNNER_SCRIPT}" '
            f'-JobId "{job["id"]}"'
        )

        result = subprocess.run(
            [
                "schtasks",
                "/Create",
                "/TN",
                task_name,
                "/TR",
                command,
                *sc_args,
                "/ST",
                f"{interval['Hour']:02d}:{interval['Minute']:02d}",
                "/F",
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            print(f"[schtasks] failed creating {task_name}: {result.stderr.strip()}")
            continue

        if not enabled:
            disable_windows_task(task_name)

        created += 1
    return created


def delete_job_tasks(job: dict) -> None:
    task_names = job.get("task_names") or []
    if not task_names:
        intervals = parse_cron_to_task_intervals(job["cron"])
        task_names = [windows_task_name(job["id"], idx + 1) for idx in range(len(intervals))]

    for task_name in task_names:
        delete_windows_task(task_name)


def sync_windows(config: dict) -> None:
    loaded = 0
    updated = False

    for job in config.get("jobs", []):
        delete_job_tasks(job)

        if not job.get("enabled", True):
            continue

        prompt = read_headless_prompt(job["skill"])
        if not prompt:
            continue

        intervals = parse_cron_to_task_intervals(job["cron"])
        if not intervals:
            print(f"[error] unsupported cron expression for Windows Task Scheduler: {job['cron']}")
            continue

        task_names = [windows_task_name(job["id"], idx + 1) for idx in range(len(intervals))]
        if job.get("task_names") != task_names:
            job["task_names"] = task_names
            updated = True

        created = create_windows_tasks(job, intervals, job.get("enabled", True))
        loaded += created

    if updated:
        save_config(config)

    print(f"[schtasks] synced ({loaded} tasks)")


def parse_cron_to_calendar_intervals(cron_expr: str) -> list[dict]:
    """Convert a cron expression to launchd StartCalendarInterval dicts.

    Supports standard 5-field cron: minute hour dom month dow
    Handles comma-separated values and ranges (e.g. 1-5, 9-12).
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return []

    def expand_field(field: str, lo_bound: int, hi_bound: int) -> list[int] | None:
        """Expand a cron field to a list of ints, or None for '*'.
        Returns [] on unsupported syntax (step expressions, out-of-range values).
        """
        if field == "*":
            return None
        values = set()
        for token in field.split(","):
            if "-" in token:
                try:
                    lo, hi = token.split("-", 1)
                    lo_val, hi_val = int(lo), int(hi)
                except ValueError:
                    return []
                if not (lo_bound <= lo_val <= hi_bound and lo_bound <= hi_val <= hi_bound):
                    return []
                values.update(range(lo_val, hi_val + 1))
            elif token.isdigit():
                val = int(token)
                if not (lo_bound <= val <= hi_bound):
                    return []
                values.add(val)
            else:
                return []  # step expressions (*/5) or other unsupported syntax
        return sorted(values)

    minutes = expand_field(parts[0], 0, 59)
    hours = expand_field(parts[1], 0, 23)
    # dom and month are rarely used in skill-cron, skip for now
    weekdays = expand_field(parts[4], 0, 7)

    # [] means invalid input (e.g. step syntax, out-of-range); None means '*' (wildcard)
    if minutes == [] or hours == [] or weekdays == []:
        return []

    # Build cartesian product of all specified values
    minute_list = minutes if minutes is not None else [None]
    hour_list = hours if hours is not None else [None]
    weekday_list = weekdays if weekdays is not None else [None]

    intervals = []
    for m in minute_list:
        for h in hour_list:
            for w in weekday_list:
                entry = {}
                if m is not None:
                    entry["Minute"] = m
                if h is not None:
                    entry["Hour"] = h
                if w is not None:
                    entry["Weekday"] = w
                intervals.append(entry)
    return intervals


def plist_path_for_job(job_id: str) -> Path:
    return LAUNCHD_DIR / f"{LAUNCHD_PREFIX}{job_id}.plist"


def build_plist(job: dict, prompt: str) -> dict:
    """Build a launchd plist dict for a job."""
    job_id = job["id"]
    runner = str(RUNNER_SCRIPT)
    intervals = parse_cron_to_calendar_intervals(job["cron"])

    plist = {
        "Label": f"{LAUNCHD_PREFIX}{job_id}",
        "ProgramArguments": [runner, prompt, job_id],
        "StandardErrorPath": str(
            Path.home() / ".claude" / "logs" / "skill-cron"
            / f"launchd-{job_id}-stderr.log"
        ),
    }

    if len(intervals) == 1:
        plist["StartCalendarInterval"] = intervals[0]
    elif intervals:
        plist["StartCalendarInterval"] = intervals

    return plist


def sync_launchd(config: dict) -> None:
    """Sync enabled jobs to launchd LaunchAgents."""
    LAUNCHD_DIR.mkdir(parents=True, exist_ok=True)

    # Unload and remove all existing skill-cron plists
    for plist_file in LAUNCHD_DIR.glob(f"{LAUNCHD_PREFIX}*.plist"):
        subprocess.run(
            ["launchctl", "unload", str(plist_file)],
            capture_output=True,
        )
        plist_file.unlink()

    # Create and load plists for enabled jobs
    enabled_jobs = [j for j in config.get("jobs", []) if j.get("enabled", True)]
    loaded = 0
    for job in enabled_jobs:
        prompt = read_headless_prompt(job["skill"])
        if not prompt:
            continue

        plist = build_plist(job, prompt)
        plist_file = plist_path_for_job(job["id"])

        with open(plist_file, "wb") as f:
            plistlib.dump(plist, f)

        result = subprocess.run(
            ["launchctl", "load", str(plist_file)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            loaded += 1
        else:
            print(f"[launchd] error loading {job['id']}: {result.stderr}")

    print(f"[launchd] synced ({loaded} jobs)")


def sync_schedule(config: dict) -> None:
    if IS_WINDOWS:
        sync_windows(config)
    else:
        sync_launchd(config)


def cmd_sync(_args: list[str]) -> None:
    config = load_config()
    sync_schedule(config)


# ── Manual run ─────────────────────────────────────────────

def cmd_run(args: list[str]) -> None:
    if not args:
        print("Usage: run <job_id>")
        sys.exit(1)

    job_id = args[0]
    config = load_config()
    job = next((j for j in config["jobs"] if j["id"] == job_id), None)

    if not job:
        print(f"[error] job not found: {job_id}")
        sys.exit(1)

    prompt = read_headless_prompt(job["skill"])
    if not prompt:
        print(f"[error] no headless-prompt for skill: {job['skill']}")
        sys.exit(1)

    print(f"[run] executing {job_id}...")
    result = subprocess.run(
        ["claude", "-p", prompt, "--allowedTools", "Bash,Read,Glob,Grep"],
        capture_output=True,
        text=True,
        timeout=300,
    )

    output = result.stdout.strip()
    if output:
        print(output)

        # Push to Telegram if configured
        tg = config.get("telegram", {})
        if tg.get("bot_token") and tg.get("channel_id"):
            # Truncate to Telegram's 4096 char limit
            tg_text = f"<b>[{job['label']}]</b>\n\n{output}"
            if len(tg_text) > 4096:
                tg_text = tg_text[:4090] + "\n..."
            if send_telegram(tg["bot_token"], tg["channel_id"], tg_text):
                print("\n[telegram] sent")
            else:
                print("\n[telegram] send failed")
    else:
        print("[run] no output")
        if result.stderr:
            print(f"[stderr] {result.stderr[:500]}")


# ── Main ───────────────────────────────────────────────────

COMMANDS = {
    "list": cmd_list,
    "add": cmd_add,
    "remove": cmd_remove,
    "enable": lambda a: cmd_enable_disable(a, True),
    "disable": lambda a: cmd_enable_disable(a, False),
    "telegram-set": cmd_telegram_set,
    "telegram-test": cmd_telegram_test,
    "telegram-remove": cmd_telegram_remove,
    "run": cmd_run,
    "sync": cmd_sync,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("skill-cron — scheduled skill runner + Telegram push")
        print()
        print("Commands:")
        print("  list                              Show all jobs + Telegram status")
        print("  add <skill> <cron> <label>        Register a scheduled job")
        print("  remove <job_id>                   Remove a job")
        print("  enable <job_id>                   Enable a job")
        print("  disable <job_id>                  Disable a job")
        print("  telegram-set <token> <channel>    Set Telegram credentials")
        print("  telegram-test                     Send test message")
        print("  telegram-remove                   Remove Telegram credentials")
        print("  run <job_id>                      Manually trigger a job")
        print("  sync                              Sync config → scheduler")
        sys.exit(0)

    cmd = sys.argv[1]
    COMMANDS[cmd](sys.argv[2:])
