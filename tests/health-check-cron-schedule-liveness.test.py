#!/usr/bin/env python3
"""The cron-schedule probe: a registered schedule that stopped firing.

`session-crons` checks that registration HAPPENED; nothing checks that it is
still happening. CronCreate jobs expire after 7 days, so a host can hold an
honest registration stamp and a dead schedule at the same time.

Run: python3 tests/health-check-cron-schedule-liveness.test.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("health_check", REPO / "src" / "health-check.py")
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)


class CronScheduleLivenessTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name).resolve()
        (self.ws / "state").mkdir(parents=True)
        self._orig = hc.WORKSPACE_DIR
        hc.WORKSPACE_DIR = self.ws

    def tearDown(self) -> None:
        hc.WORKSPACE_DIR = self._orig
        self._tmp.cleanup()

    def _age_state_file(self, rel: str, age_h: float) -> None:
        """A state/ file of a given age, standing in for prior workspace use."""
        f = self.ws / "state" / rel
        f.touch()
        t = time.time() - age_h * 3600
        os.utime(f, (t, t))

    def _mark(self, age_h: float) -> None:
        m = self.ws / "state" / "last-loop-ok"
        m.touch()
        t = time.time() - age_h * 3600
        os.utime(m, (t, t))

    # --- the three bands -------------------------------------------------

    def test_a_recent_pass_is_ok(self) -> None:
        self._mark(0.2)
        r = hc.check_cron_schedule()
        self.assertEqual(r["status"], "ok", r)

    def test_a_schedule_quiet_past_the_warn_band_warns(self) -> None:
        self._mark(9)
        r = hc.check_cron_schedule()
        self.assertEqual(r["status"], "warn", r)
        self.assertIn("9.0h", r["detail"])

    def test_a_schedule_quiet_past_the_fail_band_fails(self) -> None:
        """The band that matters: CronCreate jobs expire after 7 days."""
        self._mark(101)
        r = hc.check_cron_schedule()
        self.assertEqual(r["status"], "fail", r)
        self.assertIn("101.0h", r["detail"])
        self.assertIn("expire", r["detail"])

    # --- the failure this probe exists for -------------------------------

    def test_a_fresh_core_status_does_not_mask_a_dead_schedule(self) -> None:
        """core-status.json is refreshed by owner turns; the marker is not.

        This is the whole reason the probe reads its own marker: a host in
        conversation looks healthiest to a core-status-based check exactly
        when its schedule is dead.
        """
        self._mark(101)
        (self.ws / "state" / "core-status.json").write_text(
            '{"status": "running", "ts": %d}' % int(time.time()))
        r = hc.check_cron_schedule()
        self.assertEqual(r["status"], "fail", r)

    # --- absence is not staleness ----------------------------------------

    def test_a_missing_marker_is_ok_not_a_failure(self) -> None:
        """Fresh install, or no pass has closed yet. Never louder than stale."""
        r = hc.check_cron_schedule()
        self.assertEqual(r["status"], "ok", r)
        self.assertIn("no loop marker", r["detail"])

    def test_absent_marker_on_a_worked_in_state_dir_warns_never_started(self) -> None:
        """The failure the docstring names but the probe could not see.

        The deadlock is: /schedule-crons is itself one of the expiring crons, so
        when it lapses the loop never closes a pass and NO marker is ever written.
        Reporting ok there means the motivating case is the one blind spot.
        """
        self._age_state_file("core-status.json", 30)
        r = hc.check_cron_schedule()
        self.assertEqual(r["status"], "warn", r)
        self.assertIn("never have STARTED", r["detail"])
        self.assertIn("30.0h", r["detail"])

    def test_absent_marker_on_a_young_state_dir_stays_ok(self) -> None:
        """The discriminator: a real fresh install must not warn on day one."""
        self._age_state_file("core-status.json", 0.5)
        r = hc.check_cron_schedule()
        self.assertEqual(r["status"], "ok", r)
        self.assertIn("no loop marker", r["detail"])

    def test_a_peers_synced_core_file_does_not_age_a_fresh_workspace(self) -> None:
        """state/cores/ is synced across hosts, so it cannot date THIS host."""
        cores = self.ws / "state" / "cores"
        cores.mkdir()
        self._age_state_file("cores/peer.alive", 200)
        self.assertEqual(hc.check_cron_schedule()["status"], "ok")

    def test_an_unreadable_state_dir_is_ok_not_a_warn(self) -> None:
        """Unknowable age must fail toward quiet, like the missing marker itself."""
        self.assertIsNone(hc._state_in_use_age_h(self.ws / "state" / "nope"))

    def test_the_never_started_band_follows_the_warn_override(self) -> None:
        """It reuses warn_h, so widening the band must widen this too."""
        self._age_state_file("core-status.json", 9)
        self.assertEqual(hc.check_cron_schedule()["status"], "warn")
        with unittest.mock.patch.dict(os.environ, {"SUTANDO_CRON_STALE_WARN_H": "12"}):
            self.assertEqual(hc.check_cron_schedule()["status"], "ok")

    # --- band overrides ---------------------------------------------------

    def test_the_bands_are_env_tunable(self) -> None:
        """A host with a sparse loop must be able to widen the warn band."""
        self._mark(9)
        with unittest.mock.patch.dict(os.environ, {"SUTANDO_CRON_STALE_WARN_H": "12"}):
            self.assertEqual(hc.check_cron_schedule()["status"], "ok")

    def test_an_unparseable_band_falls_back_to_the_default(self) -> None:
        """A typo in the env must not silence the probe, nor crash the run."""
        self._mark(9)
        with unittest.mock.patch.dict(os.environ, {"SUTANDO_CRON_STALE_WARN_H": "soon"}):
            self.assertEqual(hc.check_cron_schedule()["status"], "warn")

    # --- the writer half --------------------------------------------------

    def _minimal_repo(self) -> Path:
        """A throwaway checkout whose config points `core-status.sh` at self.ws.

        `$SUTANDO_WORKSPACE` cannot do this: v0.8 (#1440) removed it from the
        resolution order, so setting it leaves the write landing in the REAL
        workspace while the test reads an empty temp dir. The supported
        override is `sutando.config.local.json`, and `_find_repo_root` anchors
        on the TRACKED `sutando.config.json`, so both files must be present.
        """
        self._repo_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._repo_tmp.cleanup)
        repo = Path(self._repo_tmp.name) / "repo"
        (repo / "src").mkdir(parents=True)
        (repo / "scripts").mkdir(parents=True)
        for rel in ("src/workspace_default.py", "src/sutando_config.py",
                    "scripts/python-binary.sh", "scripts/core-status.sh"):
            shutil.copy(REPO / rel, repo / rel)
        shutil.copy(REPO / "sutando.config.json", repo / "sutando.config.json")
        (repo / "sutando.config.local.json").write_text(
            json.dumps({"workspace": {"path": str(self.ws)}}))
        return repo

    def _core_status(self, repo: Path, *args: str) -> None:
        done = subprocess.run(["bash", str(repo / "scripts" / "core-status.sh"), *args],
                              cwd=repo, capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0,
                         f"core-status.sh {args} failed: {done.stderr}")
        self.assertTrue(done.stdout.startswith(str(self.ws)),
                        f"wrote outside the temp workspace: {done.stdout!r}")


    def test_core_status_idle_stamps_the_marker_and_running_does_not(self) -> None:
        """The probe is only as good as the stamp; assert both polarities."""
        repo = self._minimal_repo()
        marker = self.ws / "state" / "last-loop-ok"

        self._core_status(repo, "running", "x")
        running_stamped = marker.exists()

        self._core_status(repo, "idle")
        idle_stamped = marker.exists()

        self.assertFalse(running_stamped, "running must not stamp the pass-closed marker")
        self.assertTrue(idle_stamped, "idle closes a pass and must stamp it")

    # --- wiring -----------------------------------------------------------

    def test_probe_is_registered(self) -> None:
        """An unregistered probe is indistinguishable from green."""
        src = (REPO / "src" / "health-check.py").read_text()
        # Boolean first: assertIn on the whole file dumps 578KB into the report.
        self.assertTrue("checks.append(check_cron_schedule())" in src,
                        "check_cron_schedule is defined but never appended to checks")


if __name__ == "__main__":
    unittest.main(verbosity=2)
