# Slides

Host static slide decks at `https://qingyun.sutando.ag2.chat/slides/<tier>/<deck>/`
via the web-client skill-mount.

The web-client (`src/web-client.ts`) discovers this skill through `manifest.json`
and proxies any request under `/slides/...` to the local server this skill
launches on `127.0.0.1:7877`. Cloudflare Tunnel + Cloudflare Access handle
TLS and authentication at the edge; this server is bound to loopback.

## Adding a deck

Drop a self-contained folder under `sutando-resources/slides/<tier>/<deck>/`
with an `index.html` and any assets it references via simple relative paths
(`<img src="qingyun_wu.jpeg">`, not `<img src="../foo/qingyun_wu.jpeg">`).

```
sutando-resources/slides/
  board/
    may-2026/
      index.html
      qingyun_wu.jpeg
      chi_wang.jpeg
  team/
    eng-allhands/
      index.html
      ...
```

If the canonical HTML lives elsewhere (e.g. an editable copy under
`notes/-project-board-meeting/`), symlink it in:

```sh
cd sutando-resources/slides/board/may-2026
ln -sf ../../../notes/-project-board-meeting/board-meeting-may-2026.html index.html
ln -sf ../../../notes/-asset-ag2wiki-public/people/qingyun_wu.jpeg qingyun_wu.jpeg
```

Restart the server to pick up new decks: `pkill -f 'slides/scripts/server.py'`
then either rerun `bash src/startup.sh` or
`python3 skills/slides/scripts/server.py > logs/slides.log 2>&1 &`.

## URL surface

- `GET /slides` — auto-generated index of every folder containing `index.html`.
- `GET /slides/<tier>/<deck>/` — serves that folder's `index.html`.
- `GET /slides/<tier>/<deck>/<asset>` — serves the asset as a static file.
- Anything outside the slide root → 403. Anything missing → 404.

## Audience tiers (recommended)

Top-level subfolders should encode the audience so Cloudflare Access can
attach one policy per tier. Each tier is a separate CF Access application.

| Folder        | Audience                     | Suggested CF Access policy                |
|---------------|------------------------------|-------------------------------------------|
| `board/*`     | Board members                | Allow specific board emails               |
| `team/*`      | AG2 team                     | Allow `@ag2.ai`                           |
| `public/*`    | Anyone with the link         | Bypass (audit before using)               |
| `investor/*`  | Active investor conversations| Allow specific investor emails per deck   |

## Configuration

- `SUTANDO_SLIDES_ROOT` (env) — override the slide root. Defaults to
  `<repo>/sutando-resources/slides`.
- Port `127.0.0.1:7877`. Mount path `/slides` (declared in `manifest.json`).

## Notes

- Discovery is cached at startup. Restart the server after adding decks.
- The server does NOT authenticate — all access control is at CF Access.
- Path traversal is blocked via lexical normalization + prefix check.
  `Path.resolve()` is avoided because it walks every component through the
  macOS iCloud daemon for paths under `~/Documents`, which can block the
  single-threaded request loop.
