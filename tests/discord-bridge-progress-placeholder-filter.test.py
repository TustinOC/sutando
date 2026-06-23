"""Regression: the discord bridge must NOT ingest live-status "⏳ <step> (Ns)"
placeholders as tasks.

Context (2026-06-23, Lucy/Susan quota-drain debug): the live status message
(src/progress_stream.py, behind SUTANDO_PROGRESS_STREAM) posts a self-updating
"⏳ <step> (Ns)" placeholder. In a requireMention=false channel (role:"bot2bot")
those sibling/own placeholders were being ingested as TASKS — each a full
~50K-context turn, a pure token-burn loop (Lucy on Studio measured ~11 such
turns in one session). `_is_progress_placeholder` + the drop in
`_handle_discord_message` kill that loop. This test pins the matcher's behavior
and source-grep-asserts the filter is actually wired into the ingestion path.
"""
import importlib.util
import os
import re
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_WORKSPACE_TMP = tempfile.mkdtemp(prefix="sutando-progress-filter-test-")
os.environ["SUTANDO_WORKSPACE"] = _WORKSPACE_TMP
os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token-not-real")
(Path(_WORKSPACE_TMP) / "state").mkdir(parents=True, exist_ok=True)


def _load(name: str, path: Path):
    if "discord" not in sys.modules:
        stub = types.ModuleType("discord")
        stub.Intents = type("Intents", (), {"default": staticmethod(lambda: type("I", (), {"message_content": False})())})
        stub.Client = type("Client", (), {"__init__": lambda self, **kw: None, "event": staticmethod(lambda fn: fn)})
        stub.File = type("File", (), {})
        stub.DMChannel = type("DMChannel", (), {})
        stub.Object = lambda id: type("Object", (), {"id": id})()
        sys.modules["discord"] = stub
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


bridge = _load("discord_bridge", REPO / "src" / "discord-bridge.py")


def test_real_placeholders_match():
    # the exact shapes progress_stream.format_progress emits + ones observed
    # as tasks on a real node
    for s in (
        "⏳ working… (8s)",
        "⏳ working… (78s)",
        "⏳ Helping Susan debug Lucy/Studio quota drain (142s)",
        "  ⏳ rendering 1840/3180 (12s)  ",  # leading/trailing space (chunker)
    ):
        assert bridge._is_progress_placeholder(s), f"should match: {s!r}"


def test_real_messages_do_not_match():
    # genuine task/coord messages must NOT be swallowed
    for s in (
        "done: shipped PR #205",
        "Reviewed #1751 (read the bridge routing)",
        "⏳ but this is a real task about (something)",  # ⏳ start but no (Ns) tail
        "⏳ working…",  # no elapsed tail
        "",
        None,
    ):
        assert not bridge._is_progress_placeholder(s), f"should NOT match: {s!r}"


def test_filter_is_wired_into_ingestion():
    # source-grep-assert the drop actually runs in _handle_discord_message,
    # gated on author.bot — a matcher nobody calls would be dead code.
    src = (REPO / "src" / "discord-bridge.py").read_text()
    assert "_is_progress_placeholder(message.content)" in src, \
        "the placeholder filter must be invoked in the message handler"
    # it must sit in the bot-author path (a progress msg is always from a bot)
    assert re.search(r"message\.author\.bot and _is_progress_placeholder", src), \
        "filter must be gated on message.author.bot"


if __name__ == "__main__":
    test_real_placeholders_match()
    test_real_messages_do_not_match()
    test_filter_is_wired_into_ingestion()
    print("OK — progress-placeholder filter pins behavior + wiring")
