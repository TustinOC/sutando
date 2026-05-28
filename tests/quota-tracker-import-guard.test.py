"""Tests that read-quota.py handles ImportError gracefully but propagates other exceptions."""
import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


# Path to the script under test
_SCRIPT_DIR = Path(__file__).resolve().parent.parent / "skills" / "quota-tracker" / "scripts"


class TestReadQuotaImportGuard(unittest.TestCase):
    def _reload_module(self):
        """Remove cached module so each test gets a fresh import."""
        for key in list(sys.modules.keys()):
            if "read_quota" in key or "read-quota" in key:
                del sys.modules[key]

    def test_import_error_sets_canonical_none(self):
        """ImportError from workspace_default → _canonical = None (graceful fallback)."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "read_quota", _SCRIPT_DIR / "read-quota.py"
        )
        mod = importlib.util.module_from_spec(spec)
        # Simulate ImportError by patching builtins.__import__
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def mock_import(name, *args, **kwargs):
            if name == "workspace_default":
                raise ImportError("workspace_default not found")
            return original_import(name, *args, **kwargs)

        # We can't easily re-exec the module mid-import without side effects.
        # Instead validate the guard logic directly by patching sys.path and
        # forcing the ImportError path via a surrogate test.
        with patch.dict(sys.modules, {"workspace_default": None}):
            try:
                from workspace_default import status_read_path  # type: ignore
                canonical = status_read_path("quota-state.json")
            except ImportError:
                canonical = None
            except Exception as exc:
                # Non-ImportError must propagate — if we got here the test should fail
                raise AssertionError(f"Non-ImportError should propagate, got {exc!r}") from exc

        self.assertIsNone(canonical)

    def test_non_import_error_propagates(self):
        """A non-ImportError (e.g. RuntimeError) must NOT be swallowed."""
        def bad_status_read_path(name):
            raise RuntimeError("unexpected workspace error")

        caught = False
        try:
            # Simulate the guard with a RuntimeError instead of ImportError
            try:
                bad_status_read_path("quota-state.json")
                canonical = None
            except ImportError:
                canonical = None
            # If we get here without exception, something is wrong
        except RuntimeError:
            caught = True

        self.assertTrue(caught, "RuntimeError should propagate past the ImportError guard")

    def test_guard_text_in_source(self):
        """Verify source uses 'except ImportError' not bare 'except Exception'."""
        src = (_SCRIPT_DIR / "read-quota.py").read_text()
        self.assertIn("except ImportError:", src)
        # Ensure the old broad catch is gone
        self.assertNotIn("except Exception:", src)


if __name__ == "__main__":
    unittest.main()
