#!/usr/bin/env python3
"""Unit tests for src/tenant.py — mirrors tests/tenant.test.ts shape.

Run: python3 tests/tenant.test.py
"""
import os
import sys
import socket
import tempfile
import shutil
import unittest
from pathlib import Path

# Make `src/` importable as a flat package (mirrors how scripts under src/
# already do `from util_paths import ...`).
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from tenant import tenant_id, tenant_path, _reset_for_tests  # type: ignore  # noqa: E402

HOST = socket.gethostname().split(".")[0]


class TenantIdResolution(unittest.TestCase):
    def setUp(self) -> None:
        self.saved = {k: os.environ.get(k) for k in ("SUTANDO_TENANT_ID", "SUTANDO_PRIVATE_DIR", "SUTANDO_WORKSPACE")}
        _reset_for_tests()
        self.tmp = tempfile.mkdtemp(prefix="sutando-tenant-py-")
        os.environ.pop("SUTANDO_TENANT_ID", None)
        os.environ.pop("SUTANDO_PRIVATE_DIR", None)
        os.environ["SUTANDO_WORKSPACE"] = self.tmp

    def tearDown(self) -> None:
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_for_tests()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_when_no_env_no_state(self) -> None:
        self.assertEqual(tenant_id(), "default")

    def test_env_var_when_valid(self) -> None:
        os.environ["SUTANDO_TENANT_ID"] = "acme"
        self.assertEqual(tenant_id(), "acme")

    def test_invalid_env_falls_back(self) -> None:
        os.environ["SUTANDO_TENANT_ID"] = "BAD ID!"
        self.assertEqual(tenant_id(), "default")

    def test_reads_state_file(self) -> None:
        state_dir = Path(self.tmp) / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "tenant.json").write_text('{"id":"beta-tenant"}')
        self.assertEqual(tenant_id(), "beta-tenant")

    def test_env_wins_over_state(self) -> None:
        state_dir = Path(self.tmp) / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "tenant.json").write_text('{"id":"from-file"}')
        os.environ["SUTANDO_TENANT_ID"] = "from-env"
        self.assertEqual(tenant_id(), "from-env")

    def test_corrupt_state_falls_back(self) -> None:
        state_dir = Path(self.tmp) / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "tenant.json").write_text("not json {{")
        self.assertEqual(tenant_id(), "default")

    def test_cached_after_first_call(self) -> None:
        os.environ["SUTANDO_TENANT_ID"] = "first"
        self.assertEqual(tenant_id(), "first")
        os.environ["SUTANDO_TENANT_ID"] = "second"
        self.assertEqual(tenant_id(), "first")
        _reset_for_tests()
        self.assertEqual(tenant_id(), "second")


class TenantPathResolution(unittest.TestCase):
    def setUp(self) -> None:
        self.saved = {k: os.environ.get(k) for k in ("SUTANDO_TENANT_ID", "SUTANDO_PRIVATE_DIR", "SUTANDO_WORKSPACE")}
        _reset_for_tests()
        self.priv = tempfile.mkdtemp(prefix="sutando-priv-py-")
        self.ws = tempfile.mkdtemp(prefix="sutando-ws-py-")
        os.environ["SUTANDO_PRIVATE_DIR"] = self.priv
        os.environ["SUTANDO_WORKSPACE"] = self.ws
        os.environ.pop("SUTANDO_TENANT_ID", None)

    def tearDown(self) -> None:
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_for_tests()
        shutil.rmtree(self.priv, ignore_errors=True)
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_shared_under_tenant_when_present(self) -> None:
        os.environ["SUTANDO_TENANT_ID"] = "acme"
        _reset_for_tests()
        tenant_dir = Path(self.priv) / "tenant-acme"
        tenant_dir.mkdir(parents=True)
        (tenant_dir / "build_log.md").write_text("acme log")
        p = tenant_path("build_log.md", "shared")
        self.assertEqual(p, tenant_dir / "build_log.md")

    def test_device_under_tenant_machine_when_present(self) -> None:
        os.environ["SUTANDO_TENANT_ID"] = "acme"
        _reset_for_tests()
        machine_dir = Path(self.priv) / "tenant-acme" / f"machine-{HOST}"
        machine_dir.mkdir(parents=True)
        (machine_dir / "pending-questions.md").write_text("q")
        p = tenant_path("pending-questions.md", "device")
        self.assertEqual(p, machine_dir / "pending-questions.md")

    def test_legacy_fallback_shared(self) -> None:
        (Path(self.priv) / "notes.md").write_text("legacy notes")
        p = tenant_path("notes.md", "shared")
        self.assertEqual(p, Path(self.priv) / "notes.md")

    def test_legacy_fallback_device(self) -> None:
        legacy = Path(self.priv) / f"machine-{HOST}"
        legacy.mkdir()
        (legacy / "stand-identity.json").write_text("{}")
        p = tenant_path("stand-identity.json", "device")
        self.assertEqual(p, legacy / "stand-identity.json")

    def test_workspace_fallback(self) -> None:
        (Path(self.ws) / "README.md").write_text("in workspace")
        p = tenant_path("README.md", "shared")
        self.assertEqual(p, Path(self.ws) / "README.md")

    def test_preferred_private_path_on_miss(self) -> None:
        os.environ["SUTANDO_TENANT_ID"] = "acme"
        _reset_for_tests()
        p = tenant_path("does-not-exist.md", "shared")
        self.assertEqual(p, Path(self.priv) / "tenant-acme" / "does-not-exist.md")

    def test_no_private_dir_returns_workspace(self) -> None:
        os.environ.pop("SUTANDO_PRIVATE_DIR", None)
        (Path(self.ws) / "x.md").write_text("x")
        self.assertEqual(tenant_path("x.md", "shared"), Path(self.ws) / "x.md")


if __name__ == "__main__":
    unittest.main(verbosity=2)
