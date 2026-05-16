#!/usr/bin/env python3
"""Unit tests for src/util_paths.py — mirrors tests/util_paths.test.ts.

Run: python3 tests/util_paths.test.py
"""
import os
import sys
import socket
import tempfile
import shutil
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from util_paths import personal_path, shared_personal_path  # type: ignore  # noqa: E402
from tenant import _reset_for_tests  # type: ignore  # noqa: E402

HOST = socket.gethostname().split(".")[0]


class UtilPathsDelegation(unittest.TestCase):
    def setUp(self) -> None:
        self.saved = {k: os.environ.get(k) for k in ("SUTANDO_TENANT_ID", "SUTANDO_PRIVATE_DIR", "SUTANDO_WORKSPACE")}
        _reset_for_tests()
        self.priv = tempfile.mkdtemp(prefix="sutando-paths-priv-py-")
        self.ws = tempfile.mkdtemp(prefix="sutando-paths-ws-py-")
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

    def test_legacy_personal_path(self) -> None:
        legacy = Path(self.priv) / f"machine-{HOST}"
        legacy.mkdir()
        (legacy / "stand-identity.json").write_text("{}")
        self.assertEqual(
            personal_path("stand-identity.json"),
            legacy / "stand-identity.json",
        )

    def test_legacy_shared_path(self) -> None:
        (Path(self.priv) / "build_log.md").write_text("log")
        self.assertEqual(
            shared_personal_path("build_log.md"),
            Path(self.priv) / "build_log.md",
        )

    def test_migrated_personal_path(self) -> None:
        tenant_machine = Path(self.priv) / "tenant-default" / f"machine-{HOST}"
        tenant_machine.mkdir(parents=True)
        (tenant_machine / "stand-identity.json").write_text("{}")
        self.assertEqual(
            personal_path("stand-identity.json"),
            tenant_machine / "stand-identity.json",
        )

    def test_migrated_shared_path(self) -> None:
        tenant_dir = Path(self.priv) / "tenant-default"
        tenant_dir.mkdir()
        (tenant_dir / "build_log.md").write_text("log")
        self.assertEqual(
            shared_personal_path("build_log.md"),
            tenant_dir / "build_log.md",
        )

    def test_stand_avatar_assets_fallback(self) -> None:
        assets = Path(self.ws) / "assets"
        assets.mkdir()
        (assets / "stand-avatar.png").write_text("png")
        self.assertEqual(
            personal_path("stand-avatar.png", workspace=Path(self.ws)),
            assets / "stand-avatar.png",
        )

    def test_workspace_fallback_no_private(self) -> None:
        os.environ.pop("SUTANDO_PRIVATE_DIR", None)
        (Path(self.ws) / "config.json").write_text("{}")
        self.assertEqual(
            shared_personal_path("config.json", workspace=Path(self.ws)),
            Path(self.ws) / "config.json",
        )

    def test_non_default_tenant(self) -> None:
        os.environ["SUTANDO_TENANT_ID"] = "acme"
        _reset_for_tests()
        tenant_machine = Path(self.priv) / "tenant-acme" / f"machine-{HOST}"
        tenant_machine.mkdir(parents=True)
        (tenant_machine / "pending-questions.md").write_text("q")
        self.assertEqual(
            personal_path("pending-questions.md"),
            tenant_machine / "pending-questions.md",
        )

    def test_preferred_write_target_on_miss(self) -> None:
        p = shared_personal_path("new-file.md")
        self.assertEqual(p, Path(self.priv) / "tenant-default" / "new-file.md")


if __name__ == "__main__":
    unittest.main(verbosity=2)
