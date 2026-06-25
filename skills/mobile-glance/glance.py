#!/usr/bin/env python3
"""mobile-glance — a compact, glanceable status panel for Discord, built for
checking PR/task status hands-free while running/jogging (Susan 2026-06-23).

Renders ONE short message: what the agent is doing now, open PRs with a single
CI/review status glyph each, and task counts. Big markdown headers (#/##) render
large on the Discord mobile app regardless of the device font, blank lines give
real spacing, and each line is a few words — readable at a glance, not a wall.

Usage:
  python3 skills/mobile-glance/glance.py            # print the panel (dry run)
  python3 skills/mobile-glance/glance.py --post     # post to susan_private
  python3 skills/mobile-glance/glance.py --post --channel <id>

Reads the Discord token from $DISCORD_BOT_TOKEN (or ~/.claude/channels/discord/.env).
PR data via `gh`; current activity via <workspace>/state/core-status.json.
"""
import os, sys, json, time, subprocess, urllib.request, urllib.error
from pathlib import Path

REPO = "sonichi/sutando"
SUSAN_PRIVATE = "1507122184576045206"
PR_LIMIT = 8


def _workspace() -> Path:
    try:
        here = Path(__file__).resolve()
        repo = here.parents[2]  # skills/mobile-glance/glance.py -> repo root
        out = subprocess.run(["bash", str(repo / "scripts" / "sutando-config.sh"), "workspace"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except Exception:
        pass
    return Path(__file__).resolve().parents[2] / "workspace"


def _current_activity(ws: Path) -> str:
    try:
        d = json.loads((ws / "state" / "core-status.json").read_text())
        status = d.get("status", "?")
        step = d.get("step")
        return f"{step}" if (status == "running" and step) else status
    except Exception:
        return "—"


def _pr_glyph(pr: dict) -> str:
    # Review decision takes visual priority (it's the actionable signal).
    rd = (pr.get("reviewDecision") or "").upper()
    if rd == "CHANGES_REQUESTED":
        return "💬"  # changes requested
    if rd == "APPROVED":
        return "✅"
    # else fall back to CI rollup
    rollup = pr.get("statusCheckRollup") or []
    concs = [(c.get("conclusion") or c.get("status") or "").upper() for c in rollup]
    if any(c in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT") for c in concs):
        return "❌"  # CI failing
    if any(c in ("IN_PROGRESS", "QUEUED", "PENDING", "EXPECTED") for c in concs):
        return "⏳"  # CI running
    if concs and all(c == "SUCCESS" for c in concs if c):
        return "🟢"  # CI green, no review yet
    return "·"


def _open_prs() -> list:
    try:
        out = subprocess.run(
            ["gh", "pr", "list", "--repo", REPO, "--state", "open", "--limit", str(PR_LIMIT),
             "--json", "number,title,reviewDecision,statusCheckRollup"],
            capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return []
        return json.loads(out.stdout)
    except Exception:
        return []


def _task_counts(ws: Path) -> tuple:
    try:
        pending = len(list((ws / "tasks").glob("task-*.txt")))
    except Exception:
        pending = 0
    try:
        done = len(list((ws / "results").glob("task-*.txt")))
    except Exception:
        done = 0
    return done, pending


def render() -> str:
    ws = _workspace()
    now = _current_activity(ws)
    prs = _open_prs()
    done, pending = _task_counts(ws)

    # Blank line between every row — Susan wants generous spacing so it's
    # scannable at a glance while moving ("行距太近" = too tight, 2026-06-23).
    lines = [f"# 🏃 Sutando", "", f"## Now · {now}", "", ""]
    if prs:
        lines.append("## PRs")
        lines.append("")
        for p in prs:
            title = p["title"]
            # trim the conventional-commit prefix noise for glance width
            short = title.split(":", 1)[1].strip() if ":" in title[:24] else title
            lines.append(f"{_pr_glyph(p)} **#{p['number']}** {short[:46]}")
            lines.append("")  # spacing between rows
        lines.append("")
    lines.append(f"## Tasks · ✅ {done} · ⏳ {pending}")
    lines.append("")
    lines.append(f"`{time.strftime('%H:%M')}`  ✅green 💬changes ❌fail ⏳running")
    return "\n".join(lines)


def _token() -> str:
    t = os.environ.get("DISCORD_BOT_TOKEN", "")
    if t:
        return t
    for p in (Path(os.environ.get("CLAUDE_CONFIG_DIR", "")) / "channels/discord/.env",
              Path.home() / ".claude/channels/discord/.env"):
        try:
            for ln in p.read_text().splitlines():
                if ln.startswith("DISCORD_BOT_TOKEN="):
                    return ln.split("=", 1)[1].strip().strip('"')
        except Exception:
            continue
    return ""


def post(body: str, channel: str) -> None:
    tok = _token()
    if not tok:
        print("no DISCORD_BOT_TOKEN", file=sys.stderr); sys.exit(1)
    data = json.dumps({"content": body}).encode()
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel}/messages", data=data,
        headers={"Authorization": f"Bot {tok}", "Content-Type": "application/json",
                 "User-Agent": "DiscordBot (https://github.com/sonichi/sutando, 1.0)"},
        method="POST")
    resp = json.load(urllib.request.urlopen(req, timeout=20))
    print("posted msg", resp.get("id"))


def render_messages() -> list:
    """SEPARATE-message variant (Susan 2026-06-23 "每个 task 单独一条 message 但仍然
    保持行距"): a header card, then ONE message per PR so each is individually
    scannable / reactable on mobile. Each PR message keeps multi-line spacing."""
    ws = _workspace()
    now = _current_activity(ws)
    prs = _open_prs()
    done, pending = _task_counts(ws)
    msgs = [f"# 🏃 Sutando\n\n## Now · {now}\n\n## Tasks · ✅ {done} · ⏳ {pending}\n\n"
            f"`{time.strftime('%H:%M')}`  ✅green 💬changes ❌fail ⏳running"]
    for p in prs:
        title = p["title"]
        short = title.split(":", 1)[1].strip() if ":" in title[:24] else title
        # multi-line, spaced: glyph+number on one line, title on the next.
        msgs.append(f"{_pr_glyph(p)}  **#{p['number']}**\n\n{short[:60]}")
    return msgs


if __name__ == "__main__":
    args = sys.argv[1:]
    ch = SUSAN_PRIVATE
    if "--channel" in args:
        ch = args[args.index("--channel") + 1]
    separate = "--separate" in args
    if "--post" in args:
        if separate:
            msgs = render_messages()
            for i, m in enumerate(msgs):
                if i:
                    time.sleep(1.3)  # pace to stay under Discord's per-channel rate limit
                post(m, ch)
        else:
            post(render(), ch)
    else:
        if separate:
            print("\n\n--- (separate messages) ---\n\n".join(render_messages()))
        else:
            print(render())
