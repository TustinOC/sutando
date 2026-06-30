#!/usr/bin/env python3
"""Unit tests for media — gate, outbound path allowlist + size, the relay
fetch/send verbs, and graceful degrade (404/403/network/oversize). No network."""
import base64
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import media as m  # noqa: E402

HS = "@agent.a:hs"
ROOM = "!roomA:hs"
RELAY_KEYS = ["RELAY_URL", "REMOTE_TASK_URL", "RELAY_TOKEN", "REMOTE_TASK_TOKEN",
              "ROOM_MEDIA_ALLOW", "ROOM_MEDIA_INBOX"]


def _clear(keys):
    for k in keys:
        os.environ.pop(k, None)


class GateTests(unittest.TestCase):
    def test_no_gate_defers(self):
        self.assertTrue(m.gate_allows(HS, ROOM, None))

    def test_empty_denies(self):
        self.assertFalse(m.gate_allows(HS, ROOM, {}))

    def test_explicit_room(self):
        self.assertTrue(m.gate_allows(HS, ROOM, {HS: {"rooms": [ROOM]}}))
        self.assertFalse(m.gate_allows(HS, "!x:hs", {HS: {"rooms": [ROOM]}}))

    def test_all_member_rooms(self):
        self.assertTrue(m.gate_allows(HS, ROOM, {HS: {"all_member_rooms": True}}))

    def test_malformed(self):
        self.assertFalse(m.gate_allows(HS, ROOM, {HS: "yes"}))

    def test_load_missing_is_none(self):
        self.assertIsNone(m.load_gate("/nonexistent/gate.json"))


class AllowlistTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in RELAY_KEYS}
        _clear(RELAY_KEYS)

    def tearDown(self):
        _clear(RELAY_KEYS)
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v

    def test_tempdir_allowed_by_default(self):
        with tempfile.NamedTemporaryFile() as tf:
            self.assertTrue(m._path_allowed(tf.name))

    def test_outside_allow_denied(self):
        os.environ["ROOM_MEDIA_ALLOW"] = "/some/allowed/dir"
        self.assertFalse(m._path_allowed("/etc/passwd"))


class FetchTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in RELAY_KEYS}
        _clear(RELAY_KEYS)
        self.tmp = tempfile.mkdtemp()
        os.environ["ROOM_MEDIA_INBOX"] = self.tmp

    def tearDown(self):
        _clear(RELAY_KEYS)
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v

    def test_no_ref(self):
        self.assertFalse(m.fetch_media("", HS)["ok"])

    def test_gate_deny(self):
        os.environ["RELAY_URL"] = "https://relay"
        res = m.fetch_media("mxc://x/y", HS, ROOM, gate={})
        self.assertFalse(res["ok"])
        self.assertIn("gate denied", res["reason"])

    def test_no_relay(self):
        res = m.fetch_media("mxc://x/y", HS, ROOM, gate=None)
        self.assertEqual(res["reason"], "no RELAY_URL configured")

    def test_404_degrades(self):
        os.environ["RELAY_URL"] = "https://relay"
        err = urllib.error.HTTPError("u", 404, "nf", {}, None)
        with mock.patch.object(m, "_http", side_effect=err):
            res = m.fetch_media("mxc://x/y.png", HS, ROOM, gate=None)
        self.assertFalse(res["ok"])
        self.assertIn("unimplemented", res["reason"])

    def test_403_degrades(self):
        os.environ["RELAY_URL"] = "https://relay"
        err = urllib.error.HTTPError("u", 403, "no", {}, None)
        with mock.patch.object(m, "_http", side_effect=err):
            res = m.fetch_media("mxc://x/y.png", HS, ROOM, gate=None)
        self.assertIn("not a joined member", res["reason"])

    def test_oversize_rejected(self):
        os.environ["RELAY_URL"] = "https://relay"
        big = b"x" * (m.MAX_BYTES + 1)
        with mock.patch.object(m, "_http", return_value=(200, big, {})):
            res = m.fetch_media("mxc://x/y.png", HS, ROOM, gate=None)
        self.assertFalse(res["ok"])
        self.assertIn("exceeds", res["reason"])

    def test_success_writes_file(self):
        os.environ["RELAY_URL"] = "https://relay"
        with mock.patch.object(m, "_http", return_value=(200, b"PNGDATA", {"X-Media-Filename": "pic.png"})):
            res = m.fetch_media("mxc://x/y", HS, ROOM, gate=None)
        self.assertTrue(res["ok"])
        self.assertTrue(os.path.isfile(res["path"]))
        self.assertEqual(open(res["path"], "rb").read(), b"PNGDATA")
        self.assertTrue(res["path"].endswith("pic.png"))


class SendTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in RELAY_KEYS}
        _clear(RELAY_KEYS)
        self.tmp = tempfile.mkdtemp()
        os.environ["ROOM_MEDIA_ALLOW"] = self.tmp
        self.f = os.path.join(self.tmp, "ok.png")
        with open(self.f, "wb") as fh:
            fh.write(b"IMG")

    def tearDown(self):
        _clear(RELAY_KEYS)
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v

    def test_no_room(self):
        self.assertFalse(m.send_media("", self.f, HS)["ok"])

    def test_file_not_found(self):
        res = m.send_media(ROOM, os.path.join(self.tmp, "nope.png"), HS)
        self.assertIn("not found", res["reason"])

    def test_path_not_allowed(self):
        os.environ["ROOM_MEDIA_ALLOW"] = "/some/other/dir"
        res = m.send_media(ROOM, self.f, HS, gate=None)
        self.assertIn("not in ROOM_MEDIA_ALLOW", res["reason"])

    def test_oversize(self):
        big = os.path.join(self.tmp, "big.bin")
        with open(big, "wb") as fh:
            fh.write(b"x" * (m.MAX_BYTES + 1))
        res = m.send_media(ROOM, big, HS, gate=None)
        self.assertIn("exceeds", res["reason"])

    def test_gate_deny(self):
        os.environ["RELAY_URL"] = "https://relay"
        res = m.send_media(ROOM, self.f, HS, gate={})
        self.assertIn("gate denied", res["reason"])

    def test_no_relay(self):
        res = m.send_media(ROOM, self.f, HS, gate=None)
        self.assertEqual(res["reason"], "no RELAY_URL configured")

    def test_404_degrades(self):
        os.environ["RELAY_URL"] = "https://relay"
        err = urllib.error.HTTPError("u", 404, "nf", {}, None)
        with mock.patch.object(m, "_http", side_effect=err):
            res = m.send_media(ROOM, self.f, HS, gate=None)
        self.assertIn("unimplemented", res["reason"])

    def test_success(self):
        os.environ["RELAY_URL"] = "https://relay"
        captured = {}

        def fake(method, url, headers=None, data=None):
            captured["data"] = data
            return 200, b'{"ok":true}', {}

        with mock.patch.object(m, "_http", side_effect=fake):
            res = m.send_media(ROOM, self.f, HS, gate=None, caption="hi")
        self.assertTrue(res["ok"])
        import json as _j
        sent = _j.loads(captured["data"])
        self.assertEqual(base64.b64decode(sent["content_b64"]), b"IMG")
        self.assertEqual(sent["caption"], "hi")


class CliExitTests(unittest.TestCase):
    def test_fetch_exits_zero_on_no_context(self):
        with mock.patch.object(m, "load_gate", return_value={}):
            self.assertEqual(m._main(["fetch", "mxc://x/y", "--agent", HS, "--room", ROOM]), 0)

    def test_send_exits_zero_on_no_context(self):
        self.assertEqual(m._main(["send", ROOM, "/nonexistent/file.png", "--agent", HS]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
