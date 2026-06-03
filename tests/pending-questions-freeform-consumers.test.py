#!/usr/bin/env python3
"""
Regression tests for #1404 — pending-questions.md free-form consumers must NOT
undercount to 0.

`pending-questions.md` is maintained in free-form prose: each question is a
`## ` section with NO `**Status:**` field (the #1265 convention, "no status =
unanswered"), and answered questions are moved below a top-level `# Resolved`
divider (#1402). Three consumers used to REQUIRE `**Status:**` markers and so
silently counted every free-form file as 0:

  - src/dashboard.py        get_pending_count()        -> {'open': 0, 'done': 0}
  - src/friction-detector.py check_pending_questions() -> [] (never alerts)
  - src/agent-api.py        pending-questions block     -> [] (empty web UI)

This test pins the fix: a free-form file with N active `## ` sections (above the
`# Resolved` divider) is counted as N open, and resolved sections below the
divider are not counted as open.

Run: python3 tests/pending-questions-freeform-consumers.test.py
Exit: 0 on pass, 1 on fail.
"""

import importlib.util
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

FREEFORM = """\
## [2026-06-02] E = meeting buddy push decision
Awaiting her call on the branch. No **Status:** field — free-form, per #1265.

## [2026-06-02] D consolidation — approval cluster plan
MEMORY.md reduction planned; needs OK + tar backup.

## [2026-06-03] Merge the held trio
Approved + held; say "merge" whenever.

# Resolved
(audit trail — kept non-lossy; #1402 divider)

- [2026-06-02 RESOLVED] Triage skill shipped. Done.
- [2026-06-03 RESOLVED] #1389 -> #1409 verified + approved.
## [2026-06-01] An answered question moved below the divider
This `## ` section is below `# Resolved` and must NOT be counted as open.
"""

failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def load(modname, filename):
    spec = importlib.util.spec_from_file_location(modname, str(REPO / "src" / filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    # Point the consumers at a temp workspace holding our free-form file.
    tmp = tempfile.mkdtemp(prefix="pq1404-")
    pq = Path(tmp) / "pending-questions.md"
    pq.write_text(FREEFORM)
    os.environ["SUTANDO_WORKSPACE"] = tmp

    print("Fixture: 3 active `## ` questions above `# Resolved`, 1 `## ` below it.")

    # --- dashboard.get_pending_count() ---
    try:
        dash = load("dashboard_1404", "dashboard.py")
        cnt = dash.get_pending_count()
        check("dashboard counts 3 open (not 0)", cnt.get("open") == 3, f"got {cnt}")
        check("dashboard excludes the resolved `##` below divider", cnt.get("open") == 3, f"got {cnt}")
    except Exception as e:  # pragma: no cover
        check("dashboard.get_pending_count import/run", False, f"{type(e).__name__}: {e}")

    # --- friction-detector.check_pending_questions() ---
    try:
        fd = load("frictiondetector_1404", "friction-detector.py")
        res = fd.check_pending_questions()
        check("friction-detector flags 3 open (not 0)", len(res) == 3, f"got {len(res)}: {res}")
        check("friction-detector ignores resolved section below divider",
              all("answered question moved" not in r for r in res))
    except Exception as e:  # pragma: no cover
        check("friction-detector.check_pending_questions import/run", False, f"{type(e).__name__}: {e}")

    # --- agent-api parsing logic (replicates the in-handler block at src/agent-api.py) ---
    # The agent-api block is inline in a request handler; assert the same parse the fix uses.
    content = pq.read_text()
    content = re.split(r'^#\s+Resolved\b', content, maxsplit=1, flags=re.MULTILINE)[0]
    sections = re.split(r'^## ', content, flags=re.MULTILINE)
    parsed = [s for i, s in enumerate(sections) if i != 0 and s.strip()
              and not re.search(r'\*\*Status:\*\*\s*(resolved|answered|done|complete)', s, re.IGNORECASE)]
    check("agent-api parse yields 3 questions (not 0)", len(parsed) == 3, f"got {len(parsed)}")

    print(f"\n{'ALL PASS' if not failures else f'{len(failures)} FAILURE(S): ' + ', '.join(failures)}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
