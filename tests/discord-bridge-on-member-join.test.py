#!/usr/bin/env python3
"""
Tests for the on_member_join helper logic in discord-bridge.py.

The async event handler itself is tested via integration in a live bridge —
here we lock in the pure-function helpers it composes:
  - _load_welcome_channel(): per-guild lookup from access.json
  - _read_welcome_template(): canonical-template file read
  - _should_welcome_member(): bot-filter + account-age-floor + raid-burst gate

The on_member_join callback is a thin wrapper that calls these in sequence;
its happy/skip paths are covered by the helper invariants.

Run: python3 tests/discord-bridge-on-member-join.test.py
Exit 0 on pass, 1 on fail.
"""

from __future__ import annotations
import importlib.util
import json
import sys
import tempfile
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Load src/discord-bridge.py as `bridge` (filename has a hyphen, can't import
# directly). We don't run the full module — Discord client init would try to
# read the bot token. Instead we exec the file in a sandbox where the
# discord library is stubbed.

# Stub minimal discord module BEFORE the bridge is exec'd, so that
# `import discord` + `discord.Intents.default()` work without the real lib.
_discord_stub = types.ModuleType("discord")


class _Intents:
    @classmethod
    def default(cls):
        i = cls()
        i.message_content = False
        i.members = False
        return i


class _Client:
    def __init__(self, *args, **kwargs):
        self.user = None
        self.loop = types.SimpleNamespace(create_task=lambda *a, **kw: None)
    def event(self, fn):
        return fn  # decorator passthrough
    def get_channel(self, _id):
        return None


_discord_stub.Intents = _Intents
_discord_stub.Client = _Client
_discord_stub.MessageType = types.SimpleNamespace(default=0, reply=1)
_discord_stub.File = lambda *a, **kw: None
sys.modules["discord"] = _discord_stub


def load_bridge():
    """Exec the bridge module without running its main()."""
    src = (REPO / "src" / "discord-bridge.py").read_text()
    # The file calls `client.event` decorators at import time. Our stub's
    # decorator is a passthrough so this is safe. The `if not TOKEN: exit(1)`
    # guard would trip — write a temp .env to satisfy it.
    import os
    os.environ.setdefault("DISCORD_BOT_TOKEN", "test-stub-token")
    # Patch the bridge's token-loading logic by writing a temp .env
    fake_env_dir = Path.home() / ".claude" / "channels" / "discord"
    fake_env = fake_env_dir / ".env"
    if not fake_env.exists():
        fake_env_dir.mkdir(parents=True, exist_ok=True)
        fake_env.write_text("DISCORD_BOT_TOKEN=test-stub-token\n")
    spec = importlib.util.spec_from_loader("bridge", loader=None)
    bridge = importlib.util.module_from_spec(spec)
    bridge.__file__ = str(REPO / "src" / "discord-bridge.py")
    exec(src, bridge.__dict__)
    return bridge


bridge = load_bridge()


def case_should_welcome_real_user() -> list[str]:
    """Adult, non-bot account, no burst → welcome."""
    fails = []
    member = types.SimpleNamespace(
        bot=False,
        created_at=types.SimpleNamespace(timestamp=lambda: time.time() - 30 * 86400),
    )
    do, reason = bridge._should_welcome_member(member, time.time(), [])
    if not do:
        fails.append(f"a) real user should welcome, reason={reason}")
    return fails


def case_skip_bot_account() -> list[str]:
    fails = []
    member = types.SimpleNamespace(
        bot=True,
        created_at=types.SimpleNamespace(timestamp=lambda: time.time() - 30 * 86400),
    )
    do, reason = bridge._should_welcome_member(member, time.time(), [])
    if do:
        fails.append("b) bot account should be skipped")
    if reason != "bot_account":
        fails.append(f"b) wrong reason: {reason}")
    return fails


def case_skip_very_new_account() -> list[str]:
    """Account 5 minutes old → skipped (under 12h floor)."""
    fails = []
    member = types.SimpleNamespace(
        bot=False,
        created_at=types.SimpleNamespace(timestamp=lambda: time.time() - 5 * 60),
    )
    do, reason = bridge._should_welcome_member(member, time.time(), [])
    if do:
        fails.append("c) 5min-old account should be skipped (raid-throwaway pattern)")
    if not reason.startswith("account_age_"):
        fails.append(f"c) wrong reason: {reason}")
    return fails


def case_account_at_floor_boundary() -> list[str]:
    """Account exactly at the 12h floor — strict-less-than: 12h+1s passes,
    12h-1s fails."""
    fails = []
    now = time.time()
    just_above = types.SimpleNamespace(
        bot=False,
        created_at=types.SimpleNamespace(timestamp=lambda: now - bridge.WELCOME_MIN_ACCOUNT_AGE_S - 1),
    )
    just_below = types.SimpleNamespace(
        bot=False,
        created_at=types.SimpleNamespace(timestamp=lambda: now - bridge.WELCOME_MIN_ACCOUNT_AGE_S + 1),
    )
    if not bridge._should_welcome_member(just_above, now, [])[0]:
        fails.append("d) account at 12h+1s should pass the floor")
    if bridge._should_welcome_member(just_below, now, [])[0]:
        fails.append("d) account at 12h-1s should fail the floor")
    return fails


def case_raid_burst_suppression() -> list[str]:
    """If WELCOME_BURST_LIMIT joins already in window, next is suppressed."""
    fails = []
    now = time.time()
    member = types.SimpleNamespace(
        bot=False,
        created_at=types.SimpleNamespace(timestamp=lambda: now - 30 * 86400),
    )
    # Fill the window — exactly LIMIT entries should block the next.
    burst = [now - i for i in range(bridge.WELCOME_BURST_LIMIT)]
    do, reason = bridge._should_welcome_member(member, now, burst)
    if do:
        fails.append("e) burst-window full should suppress")
    if not reason.startswith("raid_burst_"):
        fails.append(f"e) wrong reason: {reason}")
    return fails


def case_burst_outside_window_doesnt_suppress() -> list[str]:
    """Old joins outside the rolling window are excluded from the count."""
    fails = []
    now = time.time()
    member = types.SimpleNamespace(
        bot=False,
        created_at=types.SimpleNamespace(timestamp=lambda: now - 30 * 86400),
    )
    # 10 joins, all OLDER than the burst window.
    old_burst = [now - bridge.WELCOME_BURST_WINDOW_S - 10 - i for i in range(10)]
    do, reason = bridge._should_welcome_member(member, now, old_burst)
    if not do:
        fails.append(f"f) old burst should not suppress current join, reason={reason}")
    return fails


def case_load_welcome_channel_per_guild() -> list[str]:
    """access.json `guilds` map keyed by guild id supplies per-guild
    welcome_channel lookup. Other guilds return None."""
    fails = []
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({
            "guilds": {
                "1153072414184452236": {"welcome_channel": "1307539994751139893"},
            },
        }, f)
        path = Path(f.name)
    orig = bridge.ACCESS_FILE
    try:
        bridge.ACCESS_FILE = path
        if bridge._load_welcome_channel(1153072414184452236) != 1307539994751139893:
            fails.append("g) configured guild should resolve to int channel id")
        if bridge._load_welcome_channel(99999999999999999) is not None:
            fails.append("g) unconfigured guild should return None")
    finally:
        bridge.ACCESS_FILE = orig
        path.unlink(missing_ok=True)
    return fails


def case_load_welcome_channel_missing_file() -> list[str]:
    fails = []
    orig = bridge.ACCESS_FILE
    try:
        bridge.ACCESS_FILE = Path("/nonexistent/path/access.json")
        if bridge._load_welcome_channel(123) is not None:
            fails.append("h) missing access.json should return None")
    finally:
        bridge.ACCESS_FILE = orig
    return fails


def case_load_welcome_channel_malformed() -> list[str]:
    fails = []
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write("{ this is not json")
        path = Path(f.name)
    orig = bridge.ACCESS_FILE
    try:
        bridge.ACCESS_FILE = path
        if bridge._load_welcome_channel(123) is not None:
            fails.append("i) malformed access.json should return None (defensive)")
    finally:
        bridge.ACCESS_FILE = orig
        path.unlink(missing_ok=True)
    return fails


def case_read_welcome_template() -> list[str]:
    fails = []
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write("Welcome body here\n")
        path = Path(f.name)
    orig = bridge.WELCOME_TEMPLATE_PATH
    try:
        bridge.WELCOME_TEMPLATE_PATH = path
        if bridge._read_welcome_template() != "Welcome body here\n":
            fails.append("j) template content not returned")
    finally:
        bridge.WELCOME_TEMPLATE_PATH = orig
        path.unlink(missing_ok=True)
    return fails


def case_read_welcome_template_missing() -> list[str]:
    fails = []
    orig = bridge.WELCOME_TEMPLATE_PATH
    try:
        bridge.WELCOME_TEMPLATE_PATH = Path("/nonexistent/welcome.md")
        if bridge._read_welcome_template() != "":
            fails.append("k) missing template should return empty string (skip-welcome signal)")
    finally:
        bridge.WELCOME_TEMPLATE_PATH = orig
    return fails


def main() -> int:
    cases = [
        ("a-real", case_should_welcome_real_user),
        ("b-bot", case_skip_bot_account),
        ("c-young", case_skip_very_new_account),
        ("d-floor", case_account_at_floor_boundary),
        ("e-burst", case_raid_burst_suppression),
        ("f-old-burst", case_burst_outside_window_doesnt_suppress),
        ("g-perguild", case_load_welcome_channel_per_guild),
        ("h-no-file", case_load_welcome_channel_missing_file),
        ("i-malformed", case_load_welcome_channel_malformed),
        ("j-tmpl", case_read_welcome_template),
        ("k-tmpl-missing", case_read_welcome_template_missing),
    ]
    failures: list[str] = []
    for label, fn in cases:
        try:
            fails = fn()
        except Exception as e:
            fails = [f"{label}) raised {type(e).__name__}: {e}"]
        if fails:
            failures.extend(fails)
            print(f"  ✗ case {label}")
            for f in fails:
                print(f"      {f}")
        else:
            print(f"  ✓ case {label}")
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nAll on_member_join helper invariants hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
