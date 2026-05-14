#!/usr/bin/env python3
"""
Slides skill — thin static-file server over a dedicated slide root.

Layout convention:
  sutando-resources/slides/
    <tier>/<deck>/
      index.html
      <assets ...>

Each deck folder is fully self-contained — HTML uses simple relative paths
(`src="qingyun_wu.jpeg"`), no `../` references. Anything not under the
served root is invisible to this server. There is no allow-list and no HTML
rewriting — discovery is filesystem-driven, access control is at the
Cloudflare Access edge.

URL surface (paths arrive with the /slides prefix intact — web-client.ts
proxies req.url unchanged):
  GET /slides                  — index of discovered decks
  GET /slides/<tier>/<deck>    — serves <tier>/<deck>/index.html
  GET /slides/<tier>/<deck>/   — same
  GET /slides/<tier>/<deck>/<file>
                               — serves the asset file
  *                            — 404 (or 403 on path traversal)
"""

import http.server
import mimetypes
import os
import posixpath
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

PORT = 7877
HOST = "127.0.0.1"
MOUNT = "/slides"

REPO_ROOT = Path(__file__).resolve().parents[3]
# Slide root is configurable via env so private deployments can point at
# their own folder without editing source.
SLIDE_ROOT = Path(os.environ.get(
    "SUTANDO_SLIDES_ROOT",
    REPO_ROOT / "sutando-resources" / "slides",
)).resolve()


def discover_decks() -> list[str]:
    """Find every folder under SLIDE_ROOT that contains an index.html. The
    returned list is sorted, with the path RELATIVE to SLIDE_ROOT (so a deck
    at sutando-resources/slides/board/may-2026/index.html shows up as
    'board/may-2026'). Cached at startup; for new decks, restart the server."""
    if not SLIDE_ROOT.is_dir():
        return []
    decks: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(SLIDE_ROOT, followlinks=True):
        if "index.html" in filenames:
            rel = os.path.relpath(dirpath, SLIDE_ROOT)
            decks.append(rel.replace(os.sep, "/"))
    return sorted(decks)


def safe_resolve(rel: str) -> Path | None:
    """Resolve `rel` under SLIDE_ROOT, returning None if it escapes the root.

    Uses POSIX lexical normalization (collapses `..`, `.`, `//`) and verifies
    the resulting path stays inside SLIDE_ROOT. We deliberately do NOT call
    Path.resolve() — that walks every component through the filesystem and
    on macOS, paths under ~/Documents go through the iCloud sync daemon,
    which can block the single-threaded request loop for seconds at a time.
    The lexical check is sufficient because SLIDE_ROOT itself was resolved
    once at startup."""
    rel = unquote(rel).lstrip("/")
    if "\x00" in rel:
        return None
    normalized = posixpath.normpath("/" + rel).lstrip("/")
    candidate = SLIDE_ROOT / normalized
    root_str = str(SLIDE_ROOT)
    cand_str = str(candidate)
    if cand_str != root_str and not cand_str.startswith(root_str + os.sep):
        return None
    return candidate


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: N802 (stdlib API)
        sys.stderr.write(f"[slides] {self.address_string()} {fmt % args}\n")

    def do_HEAD(self):  # noqa: N802 (stdlib API)
        self._handle(write_body=False)

    def do_GET(self):  # noqa: N802 (stdlib API)
        self._handle(write_body=True)

    def _handle(self, write_body: bool):
        path = urlparse(self.path).path

        if path == MOUNT or path == MOUNT + "/":
            return self._index(write_body)

        if not path.startswith(MOUNT + "/"):
            return self._error(404, "Not under /slides")

        rel = path[len(MOUNT) + 1:]
        resolved = safe_resolve(rel)
        if resolved is None:
            return self._error(403, "Forbidden")

        # Directory → index.html (deck root). If neither exists, 404.
        if resolved.is_dir():
            resolved = resolved / "index.html"
        if not resolved.is_file():
            return self._error(404, f"Not found: {rel}")

        try:
            data = resolved.read_bytes()
        except Exception as e:
            return self._error(500, f"Read failed: {e}")

        mime, _ = mimetypes.guess_type(str(resolved))
        if mime is None:
            mime = "application/octet-stream"
        # HTML is hot; assets get a brief cache.
        cache = "no-cache, no-store, must-revalidate" if mime.startswith("text/html") \
            else "public, max-age=300"

        self.send_response(200)
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        if write_body:
            self.wfile.write(data)

    def _index(self, write_body: bool):
        items = "".join(
            f'<li><a href="{MOUNT}/{deck}/">{deck}</a></li>'
            for deck in DECKS
        ) or "<li><em>No decks discovered under " + str(SLIDE_ROOT) + ".</em></li>"
        body = (
            "<!doctype html><meta charset=utf-8>"
            "<title>Sutando · Slides</title>"
            "<style>body{font:14px/1.5 -apple-system,system-ui,sans-serif;"
            "max-width:40em;margin:3rem auto;color:#222}"
            "a{color:#0a5}</style>"
            "<h1>Slides</h1><ul>" + items + "</ul>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if write_body:
            self.wfile.write(body)

    def _error(self, code: int, msg: str):
        body = msg.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


DECKS: list[str] = []


def main():
    global DECKS
    os.chdir(REPO_ROOT)
    DECKS = discover_decks()
    server = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[slides] serving on http://{HOST}:{PORT}{MOUNT}", file=sys.stderr)
    print(f"[slides] root: {SLIDE_ROOT} ({len(DECKS)} deck/s)", file=sys.stderr)
    for d in DECKS:
        print(f"[slides]   {MOUNT}/{d}/", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
