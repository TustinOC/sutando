#!/usr/bin/env python3
"""Battery stat must not render as "? ⚡" on battery-less Macs.

## Why this test exists

`get_system_stats()` in `src/dashboard.py` parses `pmset -g batt`. Two
pre-fix bugs in the same two lines:

1. On desktop Macs (mini / Studio / Pro) the output contains "Now
   drawing from 'AC Power'" and **no percentage line**, so the code set
   `battery = "?"` while `"ac power"` still set `charging = True` — and
   the dashboard's stat tile rendered the pair as `? ⚡` (glyph soup
   that reads like a sensor error). Fixed: em-dash, no charge bolt.
2. The charging check used a plain substring — and "discharging"
   contains "charging", so laptops running **on battery** also showed
   the ⚡ bolt. Fixed with a word-boundary match.

The test imports the real function and stubs `subprocess.run` with
captured pmset shapes for both machine classes.
"""

import importlib.util
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

spec = importlib.util.spec_from_file_location("dashboard", REPO / "src" / "dashboard.py")
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)

DESKTOP_PMSET = "Now drawing from 'AC Power'\n"
LAPTOP_AC_PMSET = (
    "Now drawing from 'AC Power'\n"
    " -InternalBattery-0 (id=1234)\t82%; charging; 1:07 remaining present: true\n"
)
LAPTOP_BATT_PMSET = (
    "Now drawing from 'Battery Power'\n"
    " -InternalBattery-0 (id=1234)\t64%; discharging; 3:12 remaining present: true\n"
)


def _stats_with(pmset_stdout, monkeypatch):
    def fake_run(cmd, **kwargs):
        return types.SimpleNamespace(stdout=pmset_stdout, stderr="", returncode=0)

    monkeypatch.setattr(dashboard.subprocess, "run", fake_run)
    return dashboard.get_system_stats()


def test_desktop_mac_shows_dash_and_no_bolt(monkeypatch):
    """No percentage in pmset output → em-dash, and charging must be False so the UI adds no ⚡."""
    stats = _stats_with(DESKTOP_PMSET, monkeypatch)
    assert stats["battery"] == "—"
    assert stats["charging"] is False


def test_laptop_on_ac_keeps_percent_and_bolt(monkeypatch):
    stats = _stats_with(LAPTOP_AC_PMSET, monkeypatch)
    assert stats["battery"] == "82%"
    assert stats["charging"] is True


def test_laptop_on_battery_keeps_percent_no_bolt(monkeypatch):
    stats = _stats_with(LAPTOP_BATT_PMSET, monkeypatch)
    assert stats["battery"] == "64%"
    assert stats["charging"] is False


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
