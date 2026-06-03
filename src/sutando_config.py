"""Canonical loader for `sutando.config.json` / `sutando.config.local.json`.

The config file is the M0 contract for where Sutando's per-user runtime state
lives. This module is the SINGLE place that reads those files. All path-
resolution callers (`resolve_workspace`, vault sync engine, future services)
go through `load_config()` so that the contract is enforced in one place
rather than re-implemented per-service.

Resolution order (highest layer wins):
  1. `sutando.config.local.json` (per-clone override, gitignored)
  2. `sutando.config.json` (tracked defaults at repo root)
  3. Baked-in default (`{repo_root}/workspace`)

`$SUTANDO_WORKSPACE` is no longer honored (removed in v0.8). If set, a
one-time stderr warning fires pointing at `scripts/sutando-migrate.sh`
for relocation of any data still living at the env-pointed path.

Deep-merge semantics: `.local.json` is merged over the tracked defaults.
Dicts deep-merge; arrays REPLACE wholesale (not unioned). This matches the
`.example` file pattern documented in the docstrings — users override only
the keys they want.

`${REPO_DIR}` in any string value expands to the directory containing the
config file (== git toplevel for a sane checkout). Other ${VAR}s are not
expanded; this is config, not shell.

Comment convention: any top- or nested-level key whose name starts with `_`
is treated as a comment and stripped before validation. This lets the
`.example` file carry inline documentation without polluting the runtime
schema.
"""
from __future__ import annotations

import json
import os
import sys
import warnings
from pathlib import Path
from typing import Any, Dict, Optional

# --------------------------------------------------------------------------- #
#  File discovery                                                             #
# --------------------------------------------------------------------------- #

_CONFIG_FILENAME = "sutando.config.json"
_LOCAL_FILENAME = "sutando.config.local.json"

# Known top-level keys the loader understands. The matching JSON Schema
# (docs/sutando-config.schema.json) declares `additionalProperties: false`
# to teach IDEs strictness for autocomplete; the loader itself stays
# lenient (warn-only) so users with experimental or scratch keys don't
# break. Per Mini's review #8 on PR #1395.
_KNOWN_TOP_LEVEL_KEYS = {"workspace", "claude_sutando_config_dir", "vault"}


def _find_repo_root(start: Optional[Path] = None) -> Optional[Path]:
    """Walk upward from `start` (default: this module's parent) until we find
    a directory containing `sutando.config.json`. Returns None if not found
    within 6 hops — that's deep enough for any sane checkout layout.

    We anchor on the config file rather than `.git/` because:
      - app bundles + symlinked installs may lack `.git/` in the resolved path
      - users running outside a git checkout (CI, tarball install) still get
        a working loader as long as the config sits beside it

    Emits a one-line stderr diagnostic on miss (gated by `SUTANDO_DEBUG=1` to
    keep happy-path noise out of normal runs). Helps users diagnose "why is
    Sutando using the baked-in default" without strace, per Mini's review #3
    on PR #1395.
    """
    initial = (start or Path(__file__).resolve().parent).resolve()
    cur = initial
    for _ in range(6):
        if (cur / _CONFIG_FILENAME).is_file():
            return cur
        if cur == cur.parent:  # filesystem root
            break
        cur = cur.parent
    # Strict equality to "1" so SUTANDO_DEBUG=0 / "false" / "" don't accidentally
    # turn on the diagnostic. Mini called this out in the #1397 review — env
    # truthiness in Python treats any non-empty string as truthy, which would
    # silently emit on common "disable" values.
    if os.environ.get("SUTANDO_DEBUG") == "1":
        print(
            f"sutando config: _find_repo_root walked 6 hops from {initial} "
            f"and did not find {_CONFIG_FILENAME}; falling back to baked-in default.",
            file=sys.stderr,
        )
    return None


# --------------------------------------------------------------------------- #
#  JSON loading + comment stripping                                           #
# --------------------------------------------------------------------------- #


def _strip_comments(obj: Any) -> Any:
    """Recursively drop dict keys whose name starts with `_` (comment convention).

    Lists are walked element-wise; scalars pass through. Returns a new structure
    — the input is not mutated.
    """
    if isinstance(obj, dict):
        return {k: _strip_comments(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip_comments(v) for v in obj]
    return obj


def _load_json(path: Path) -> Dict[str, Any]:
    """Read + parse a JSON file, strip comment keys, return the resulting dict.

    Empty file is treated as `{}` (defensive: a freshly-touched `.local.json`
    is valid). Parse errors raise with a clear message.
    """
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"sutando config: failed to parse {path}: {e.msg} at line {e.lineno} col {e.colno}"
        ) from e
    if not isinstance(data, dict):
        raise RuntimeError(
            f"sutando config: {path} top-level must be a JSON object, got {type(data).__name__}"
        )
    return _strip_comments(data)


# --------------------------------------------------------------------------- #
#  Deep merge + variable expansion                                            #
# --------------------------------------------------------------------------- #


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge `override` into `base`. Dicts merge; everything else
    (lists, scalars, None) is REPLACED by the override.

    Returns a new dict; inputs are not mutated.
    """
    out: Dict[str, Any] = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _expand_vars(obj: Any, repo_dir: Path) -> Any:
    """Expand `${REPO_DIR}` in every string value of the config tree.

    Only `${REPO_DIR}` is recognized — other variables pass through untouched
    (the loader is not a shell). Walks dicts + lists; scalars other than strings
    are returned as-is.
    """
    token = "${REPO_DIR}"
    if isinstance(obj, str):
        return obj.replace(token, str(repo_dir))
    if isinstance(obj, dict):
        return {k: _expand_vars(v, repo_dir) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_vars(v, repo_dir) for v in obj]
    return obj


# --------------------------------------------------------------------------- #
#  Top-level loader                                                           #
# --------------------------------------------------------------------------- #


# Cache the resolved config per-process so repeated calls don't re-read disk
# or re-print warnings. None means "not yet loaded"; the cached value is the
# merged + expanded dict.
_CACHE: Optional[Dict[str, Any]] = None
_CACHE_REPO_ROOT: Optional[Path] = None
_LEGACY_ENV_WARN_PRINTED = False
_DOTENV_DRIFT_WARN_PRINTED = False
_UNKNOWN_KEYS_WARN_PRINTED = False


def _color_warn(msg: str) -> str:
    """Wrap `msg` in bold-red ANSI when stderr is a TTY; pass through otherwise.

    Keeps the v0.8 deprecation warnings (`$SUTANDO_WORKSPACE no longer honored`,
    `.env declares stale SUTANDO_WORKSPACE`) eye-catching in interactive
    terminals so operators actually notice and migrate, while keeping log
    captures (`script`, `tee`, journald, GitHub Actions) free of escape
    sequences. `NO_COLOR=1` honored as a hard opt-out (see no-color.org).
    """
    if os.environ.get("NO_COLOR"):
        return msg
    try:
        if sys.stderr.isatty():
            return f"\033[1;31m{msg}\033[0m"
    except Exception:
        pass
    return msg


def _warn_unknown_top_level_keys(cfg: Dict[str, Any], path: Path) -> None:
    """Emit a one-time stderr warning if the resolved config has top-level
    keys the loader doesn't recognize. Helps catch typos like `workspce`
    while leaving experimental keys harmlessly ignored. Per Mini's #8.
    """
    global _UNKNOWN_KEYS_WARN_PRINTED
    if _UNKNOWN_KEYS_WARN_PRINTED:
        return
    extras = sorted(k for k in cfg if k not in _KNOWN_TOP_LEVEL_KEYS)
    if not extras:
        return
    _UNKNOWN_KEYS_WARN_PRINTED = True
    print(
        f"sutando config: {path} has top-level keys the loader does not read: "
        f"{', '.join(repr(k) for k in extras)}. Known keys: "
        f"{sorted(_KNOWN_TOP_LEVEL_KEYS)!r}. Typo? Or experimental key — "
        f"the loader will ignore it either way.",
        file=sys.stderr,
    )


def _reset_cache_for_tests() -> None:
    """Test-only: clear the per-process cache so unit tests can swap configs.

    Production code never calls this. Importing in tests is intentional —
    keeps the public surface honest.
    """
    global _CACHE, _CACHE_REPO_ROOT, _LEGACY_ENV_WARN_PRINTED, _DOTENV_DRIFT_WARN_PRINTED, _UNKNOWN_KEYS_WARN_PRINTED
    _CACHE = None
    _CACHE_REPO_ROOT = None
    _LEGACY_ENV_WARN_PRINTED = False
    _DOTENV_DRIFT_WARN_PRINTED = False
    _UNKNOWN_KEYS_WARN_PRINTED = False


def load_config(repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Load + merge sutando config from disk. Memoized per-process.

    `repo_root` is the directory holding `sutando.config.json`; defaults to
    the result of `_find_repo_root()`. Pass an explicit Path in tests.

    Returns the deep-merged, ${REPO_DIR}-expanded config dict. Missing files
    are tolerated (defaults file optional too, in which case caller falls
    through to the hardcoded resolver default — see `resolve_workspace`).

    Raises `RuntimeError` only for parse errors (malformed JSON) or
    structurally-invalid top-level (non-object).
    """
    global _CACHE, _CACHE_REPO_ROOT
    if _CACHE is not None and (repo_root is None or repo_root == _CACHE_REPO_ROOT):
        return _CACHE

    root = repo_root or _find_repo_root()
    if root is None:
        # No config file anywhere in the search path; return an empty config.
        # Callers will fall back to baked-in defaults.
        _CACHE = {}
        _CACHE_REPO_ROOT = None
        return _CACHE

    defaults = _load_json(root / _CONFIG_FILENAME)
    overrides = _load_json(root / _LOCAL_FILENAME)
    merged = _deep_merge(defaults, overrides)
    expanded = _expand_vars(merged, root)

    _CACHE = expanded
    _CACHE_REPO_ROOT = root
    _warn_unknown_top_level_keys(expanded, root / _CONFIG_FILENAME)
    return expanded


# --------------------------------------------------------------------------- #
#  Public path resolvers                                                      #
# --------------------------------------------------------------------------- #


_HARDCODED_WORKSPACE_DEFAULT_REL = "workspace"  # relative to repo root


def resolve_workspace(repo_root: Optional[Path] = None) -> Path:
    """Resolve the workspace directory per the canonical contract.

    Order (v0.8 — `$SUTANDO_WORKSPACE` env override removed):
      1. `sutando.config.{json,local.json}` → `workspace.path` (deep-merged).
      2. `{repo_root}/workspace` baked-in default.

    Does NOT create the directory; the caller decides. Returns an absolute
    `Path`.

    If `$SUTANDO_WORKSPACE` is set in the environment, fires a one-time
    stderr migration-nag warning pointing at `scripts/sutando-migrate.sh`
    but does NOT honor the env value (the legacy escape hatch was removed
    in v0.8 per `docs/workspace-contract-v0.8.md`).
    """
    global _LEGACY_ENV_WARN_PRINTED, _DOTENV_DRIFT_WARN_PRINTED

    env_val = os.environ.get("SUTANDO_WORKSPACE", "").strip()
    if env_val and not _LEGACY_ENV_WARN_PRINTED:
        _LEGACY_ENV_WARN_PRINTED = True
        # The warning intentionally OMITS the env value. Embedding a path
        # in the message string makes the warning "path-shaped": a caller
        # that mishandles stderr (`$(... 2>&1)` then `mkdir -p "$captured"`)
        # would build a nested folder tree because bash tokenizes the `/`
        # chars inside the value. Discovered 2026-06-02 — rogue folder at
        # <repo>/sutando config: $SUTANDO_WORKSPACE is set ('/var/folders/.../...,
        # full diagnosis in workspace/results/task-1780442649943.txt.
        # Users debugging the value can `echo $SUTANDO_WORKSPACE`.
        print(
            _color_warn(
                "sutando config: $SUTANDO_WORKSPACE is set but NO LONGER HONORED "
                "(removed in v0.8). Workspace resolves only from "
                "sutando.config.{json,local.json} or the {repoRoot}/workspace "
                "baked-in default. If existing workspace data lives at the "
                "$SUTANDO_WORKSPACE path, run `bash scripts/sutando-migrate.sh "
                "--dry-run` then `--commit` to relocate. Unset the env to "
                "silence this warning."
            ),
            file=sys.stderr,
        )

    cfg = load_config(repo_root)
    root = repo_root or _CACHE_REPO_ROOT
    cfg_path = (cfg.get("workspace") or {}).get("path")
    if cfg_path:
        resolved = Path(cfg_path).expanduser().resolve()
    elif root is None:
        # No config and no repo root — fall back to the historic default so we
        # don't break ad-hoc invocations outside a checkout. workspace_default.py
        # has the same behavior; preserving it here avoids regressions on
        # services that import this loader before the cutover.
        resolved = Path.home().joinpath(".sutando", "workspace").resolve()
    else:
        resolved = (root / _HARDCODED_WORKSPACE_DEFAULT_REL).resolve()

    # .env-drift warning: if `.env` still carries a stale SUTANDO_WORKSPACE
    # line, surface it once per process so the operator can clean it up.
    # (v0.8: the env var is no longer honored regardless of whether it's set
    # in the shell or in .env; the warning is purely cleanup guidance.)
    if not _DOTENV_DRIFT_WARN_PRINTED:
        _DOTENV_DRIFT_WARN_PRINTED = True
        dotenv_val = detect_env_workspace_in_dotenv(repo_root)
        if dotenv_val:
            # Same path-shape risk — values omitted. Users debugging can
            # `grep SUTANDO_WORKSPACE .env` and `bash scripts/sutando-config.sh
            # workspace` to compare.
            print(
                _color_warn(
                    "sutando config: .env declares SUTANDO_WORKSPACE but the env var "
                    "is no longer honored (removed in v0.8). Workspace resolves "
                    "config-driven. Delete the .env line and, if needed, move the "
                    "value to sutando.config.local.json under workspace.path."
                ),
                file=sys.stderr,
            )

    return resolved


def resolve_vault(repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Return the resolved vault subtree from config.

    Schema (after deep-merge + expansion):
      {
        "enabled": bool,
        "remote_url": str,
        "sync": {"include": [str, ...], "exclude": [str, ...]},
        "interval_seconds": int,
      }

    Missing config returns `{"enabled": False, ...}` with safe defaults so
    callers can branch on `cfg["enabled"]` without KeyError. The vault sync
    engine (M2) is the primary consumer.
    """
    cfg = load_config(repo_root)
    vault = dict(cfg.get("vault") or {})
    vault.setdefault("enabled", False)
    vault.setdefault("remote_url", "")
    sync = dict(vault.get("sync") or {})
    sync.setdefault("include", [])
    sync.setdefault("exclude", [])
    vault["sync"] = sync
    vault.setdefault("interval_seconds", 1800)
    return vault


_DEFAULT_CLAUDE_SUTANDO_SUBDIR = ".claude-sutando"


def resolve_claude_sutando_config_dir(repo_root: Optional[Path] = None) -> Path:
    """Resolve the CLAUDE_CONFIG_DIR target for the `claude-sutando` shell alias.

    The path is always a sub-folder of `resolve_workspace()` — the M2 vault sync
    engine relies on this invariant to include the Claude config tree via a single
    workspace-relative glob. Absolute paths and `..` escapes in the config are
    rejected at load (schema pattern) AND asserted again here (defense in depth).

    Resolution order:
      1. `claude_sutando_config_dir.subdir` in sutando.config.{local,}.json
      2. Default: `.claude-sutando`

    Returns the absolute Path. Does NOT create the directory — callers (e.g.
    `scripts/sutando-shell-setup.sh`) are responsible for mkdir as part of the
    alias-setup flow.
    """
    cfg = load_config(repo_root)
    block = dict(cfg.get("claude_sutando_config_dir") or {})
    subdir = block.get("subdir") or _DEFAULT_CLAUDE_SUTANDO_SUBDIR

    # Defense in depth: re-validate the invariants the schema already enforces
    # at load time, in case config bypassed validation or was hand-edited.
    if not subdir or subdir.startswith("/") or ".." in Path(subdir).parts:
        raise ValueError(
            f"claude_sutando_config_dir.subdir={subdir!r} violates the "
            f"workspace-sub-folder invariant — must be a non-absolute, "
            f"non-escaping relative path (M2 sync coherence depends on this)."
        )

    workspace = resolve_workspace(repo_root)
    final = workspace / subdir

    # Final-path check — resolve() follows symlinks, so the CANONICAL form of
    # the result must still be inside the CANONICAL form of the workspace tree.
    # We use .resolve() ONLY for this invariant check; the returned path stays
    # in its un-canonicalized form so callers get a string prefix consistent
    # with resolve_workspace() (e.g. on macOS, `/tmp/...` doesn't become
    # `/private/tmp/...` just because we passed through .resolve()).
    try:
        final.resolve().relative_to(workspace.resolve())
    except ValueError:
        raise ValueError(
            f"claude_sutando_config_dir.subdir={subdir!r} resolves outside "
            f"the workspace ({final.resolve()} not under {workspace.resolve()}). "
            f"Likely a symlink escape; reject."
        )

    return final


def detect_env_workspace_in_dotenv(repo_root: Optional[Path] = None) -> Optional[str]:
    """Scan the repo's `.env` for `SUTANDO_WORKSPACE=` and return the value if
    found, else None. Used by the startup banner to warn users that their .env
    declares a workspace path that the loader is bypassing in favor of config.

    Best-effort: silent on file-not-found or read errors. Strips surrounding
    quotes and expands `~`.

    Triggers the .env warning bullet from Milestone 0.
    """
    root = repo_root or _find_repo_root()
    if root is None:
        return None
    env_file = root / ".env"
    if not env_file.is_file():
        return None
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("#") or "=" not in s:
                continue
            key, _, val = s.partition("=")
            if key.strip() != "SUTANDO_WORKSPACE":
                continue
            v = val.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                v = v[1:-1]
            return str(Path(v).expanduser()) if v else None
    except OSError:
        return None
    return None


# --------------------------------------------------------------------------- #
#  CLI: `python3 -m src.sutando_config` prints the merged config for debug    #
# --------------------------------------------------------------------------- #


if __name__ == "__main__":  # pragma: no cover — operator-facing CLI
    # All diagnostic output goes to stdout so a caller can suppress it with
    # `>/dev/null` while still seeing the WARNINGS that resolve_workspace()
    # itself emits to stderr (legacy env, .env drift, parse errors). That
    # gives `bash src/startup.sh` a clean "warnings only" surface from this
    # call.
    cfg = load_config()
    workspace = resolve_workspace()  # emits stderr warnings if any
    env_val = detect_env_workspace_in_dotenv()
    print(json.dumps(cfg, indent=2, default=str))
    print(f"\n# resolved workspace: {workspace}")
    if env_val:
        print(f"# .env declares SUTANDO_WORKSPACE={env_val!r}")
