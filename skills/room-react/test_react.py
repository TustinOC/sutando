#!/usr/bin/env python3
"""Unit tests for react — gate, react/unreact relay verbs, graceful degrade
(404/403/network), arg validation, ack mapping, CLI exit-0. No network."""
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import react as r  # noqa: E402

HS = "@agent.a:hs"
ROOM = "!roomA:hs"
EV = "$event1"
KEYS = ["RELAY_URL", "REMOTE_TASK_URL", "RELAY_TOKEN", "REMOTE_TASK_TOKEN"]


def _clear(keys):
    for k in keys:
        os.environ.pop(k, None)


class GateTests(unittest.TestCase):
    def test_no_gate_defers(self):
        self.assertTrue(r.gate_allows(HS, ROOM, None))

    def test_empty_denies(self):
        self.assertFalse(r.gate_allows(HS, ROOM, {}))

    def test_explicit_room(self):
        self.assertTrue(r.gate_allows(HS, ROOM, {HS: {"rooms": [ROOM]}}))

    def test_all_member(self):
        self.assertTrue(r.gate_allows(HS, ROOM, {HS: {"all_member_rooms": True}}))

    def test_malformed(self):
        self.assertFalse(r.gate_allows(HS, ROOM, {HS: 1}))

    def test_load_missing_none(self):
        self.assertIsNone(r.load_gate("/nonexistent/gate.json"))


class OpTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in KEYS}
        _clear(KEYS)

    def tearDown(self):
        _clear(KEYS)
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v

    def test_missing_args(self):
        self.assertFalse(r.react("", EV, "👀", HS)["ok"])
        self.assertFalse(r.react(ROOM, "", "👀", HS)["ok"])
        self.assertFalse(r.react(ROOM, EV, "", HS)["ok"])

    def test_gate_deny(self):
        os.environ["RELAY_URL"] = "https://relay"
        res = r.react(ROOM, EV, "👀", HS, gate={})
        self.assertIn("gate denied", res["reason"])

    def test_no_relay(self):
        res = r.react(ROOM, EV, "👀", HS, gate=None)
        self.assertEqual(res["reason"], "no RELAY_URL configured")

    def test_404_degrades(self):
        os.environ["RELAY_URL"] = "https://relay"
        err = urllib.error.HTTPError("u", 404, "nf", {}, None)
        with mock.patch.object(r, "_http_post", side_effect=err):
            res = r.react(ROOM, EV, "👀", HS, gate=None)
        self.assertIn("unimplemented", res["reason"])

    def test_403_degrades(self):
        os.environ["RELAY_URL"] = "https://relay"
        err = urllib.error.HTTPError("u", 403, "no", {}, None)
        with mock.patch.object(r, "_http_post", side_effect=err):
            res = r.react(ROOM, EV, "👀", HS, gate=None)
        self.assertIn("not a joined member", res["reason"])

    def test_network_degrades(self):
        os.environ["RELAY_URL"] = "https://relay"
        with mock.patch.object(r, "_http_post", side_effect=urllib.error.URLError("down")):
            res = r.react(ROOM, EV, "👀", HS, gate=None)
        self.assertIn("network", res["reason"])

    def test_react_success_hits_react_endpoint(self):
        os.environ["RELAY_URL"] = "https://relay"
        captured = {}

        def fake(url, headers, payload):
            captured["url"] = url
            captured["payload"] = payload
            return 200, b"{}"

        with mock.patch.object(r, "_http_post", side_effect=fake):
            res = r.react(ROOM, EV, "✅", HS, gate=None)
        self.assertTrue(res["ok"])
        self.assertTrue(captured["url"].endswith("/react"))
        self.assertEqual(captured["payload"], {"event_id": EV, "key": "✅"})

    def test_unreact_hits_unreact_endpoint(self):
        os.environ["RELAY_URL"] = "https://relay"
        captured = {}
        with mock.patch.object(r, "_http_post", side_effect=lambda u, h, p: (captured.setdefault("url", u), (200, b"{}"))[1]):
            res = r.unreact(ROOM, EV, "👀", HS, gate=None)
        self.assertTrue(res["ok"])
        self.assertTrue(captured["url"].endswith("/unreact"))


class CliTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in KEYS}
        _clear(KEYS)

    def tearDown(self):
        _clear(KEYS)
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v

    def test_ack_maps_to_emoji(self):
        os.environ["RELAY_URL"] = "https://relay"
        captured = {}
        with mock.patch.object(r, "_http_post", side_effect=lambda u, h, p: (captured.update(p), (200, b"{}"))[1]):
            rc = r._main(["react", ROOM, EV, "--ack", "done", "--agent", HS])
        self.assertEqual(rc, 0)
        self.assertEqual(captured["key"], r.ACK["done"])

    def test_exit_zero_on_no_context(self):
        # no relay -> ok:false, but CLI exits 0
        rc = r._main(["react", ROOM, EV, "--key", "👀", "--agent", HS])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
