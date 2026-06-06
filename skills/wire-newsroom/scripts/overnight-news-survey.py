#!/usr/bin/env python3
# ruff: noqa: UP006, UP007 — supports older Python on fleet hosts (3.9)
from __future__ import annotations
"""Overnight news-survey trigger v1 — Piece 1 of WIRE recurring loop.

For each enabled beat config under notes/sutando-wire/source-list-*.md,
fetch the per-source URLs/APIs, score candidates, and emit a candidate-list
to notes/sutando-wire/candidates/YYYY-MM-DD-<bot>-<beat>.md.

Per spec (notes/sutando-wire/jun13-readiness-spec-3-pieces.md):
- No silent skip — empty list emits a "ran, found nothing" note.
- Rate-limited sources retry next night; don't crash the run.
- Same item across beats emits once with overlap recorded.

v1 source coverage (easy tier):
- GitHub releases via `gh api repos/{owner}/{repo}/releases`
- PyPI version-bumps via `pypi.org/pypi/{pkg}/json`
- HN front-page filter via Algolia search API

Skip in v1 (medium/hard tier — punted to v2):
- Discord/Slack platform changelogs (scrape)
- Vendor blog scraping
- X searches
- arxiv abstracts

Run nightly via cron (workspace cron schedule):
  03:07 local: python3 skills/wire-newsroom/scripts/overnight-news-survey.py

Outputs:
  notes/sutando-wire/candidates/YYYY-MM-DD-<bot>-<beat>.md
"""

import os
import sys
import json
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

WORKSPACE = Path(os.environ.get("SUTANDO_WORKSPACE", str(Path.home() / ".sutando" / "workspace")))
WIRE_DIR = WORKSPACE / "notes" / "sutando-wire"
CANDIDATES_DIR = WIRE_DIR / "candidates"
TIMEOUT = 10  # seconds per HTTP request


def http_get_json(url: str, headers: dict | None = None) -> dict | list | None:
    """Fetch JSON; return None on any error so one bad source doesn't kill the run."""
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read())
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
        print(f"  [warn] fetch failed: {url} → {e}", file=sys.stderr)
        return None


def score_item(*, fresh_hours: float, thesis_score: float, verifiability: float) -> float:
    """Per spec: 0.4 * fresh + 0.4 * thesis + 0.2 * verif. Threshold 0.7."""
    if fresh_hours <= 24:
        fresh_score = 1.0
    elif fresh_hours <= 168:  # 7 days
        fresh_score = 0.7
    elif fresh_hours <= 720:  # 30 days
        fresh_score = 0.4
    else:
        fresh_score = 0.2
    return 0.4 * fresh_score + 0.4 * thesis_score + 0.2 * verifiability


def fetch_github_releases(repo: str, since_hours: int = 48, thesis_score: float = 0.7) -> list[dict]:
    """List releases from a GitHub repo published in the last `since_hours`."""
    data = http_get_json(f"https://api.github.com/repos/{repo}/releases",
                         headers={"User-Agent": "sutando-wire-newsroom/0.1",
                                  "Accept": "application/vnd.github+json"})
    if not isinstance(data, list):
        return []
    now = datetime.now(timezone.utc)
    out = []
    for r in data[:5]:  # most-recent 5
        try:
            published = datetime.fromisoformat(r["published_at"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        delta_h = (now - published).total_seconds() / 3600
        if delta_h > since_hours:
            continue
        out.append({
            "url": r.get("html_url", ""),
            "title": f"{repo}: {r.get('name') or r.get('tag_name', '')}",
            "source": f"GH:{repo}",
            "fresh_hours": delta_h,
            "thesis_score": thesis_score,
            "verifiability": 1.0,  # release notes = primary
            "summary": (r.get("body") or "")[:200].strip().replace("\n", " "),
            "published": published.isoformat(),
        })
    return out


def fetch_pypi_release(pkg: str, since_hours: int = 168, thesis_score: float = 0.7) -> list[dict]:
    """Check PyPI for a newer release in the last `since_hours`."""
    data = http_get_json(f"https://pypi.org/pypi/{pkg}/json")
    if not data or "info" not in data:
        return []
    info = data["info"]
    version = info.get("version", "")
    releases = data.get("releases", {}).get(version, [])
    if not releases:
        return []
    try:
        upload = datetime.fromisoformat(releases[0]["upload_time_iso_8601"].replace("Z", "+00:00"))
    except (KeyError, ValueError, IndexError):
        return []
    now = datetime.now(timezone.utc)
    delta_h = (now - upload).total_seconds() / 3600
    if delta_h > since_hours:
        return []
    return [{
        "url": info.get("project_url") or info.get("home_page", "") or f"https://pypi.org/project/{pkg}/",
        "title": f"PyPI {pkg} {version}",
        "source": f"PyPI:{pkg}",
        "fresh_hours": delta_h,
        "thesis_score": thesis_score,
        "verifiability": 1.0,
        "summary": (info.get("summary") or "")[:200],
        "published": upload.isoformat(),
    }]


def fetch_hn_front_page(query: str, since_hours: int = 24, thesis_score: float = 0.5) -> list[dict]:
    """HN Algolia search for stories matching `query` within the window."""
    cutoff_ts = int((datetime.now(timezone.utc) - timedelta(hours=since_hours)).timestamp())
    url = (f"http://hn.algolia.com/api/v1/search?query={urllib.parse.quote(query)}"
           f"&tags=story&numericFilters=created_at_i>{cutoff_ts}&hitsPerPage=10")
    data = http_get_json(url)
    if not data or "hits" not in data:
        return []
    out = []
    now = datetime.now(timezone.utc)
    for hit in data["hits"][:5]:
        try:
            created = datetime.fromtimestamp(hit["created_at_i"], tz=timezone.utc)
        except (KeyError, ValueError):
            continue
        delta_h = (now - created).total_seconds() / 3600
        out.append({
            "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}",
            "title": hit.get("title", "")[:160],
            "source": f"HN:{query}",
            "fresh_hours": delta_h,
            "thesis_score": thesis_score,  # raw HN hits get a lower default thesis score
            "verifiability": 0.6,  # secondary source by default
            "summary": (hit.get("story_text") or hit.get("comment_text") or "")[:200].replace("\n", " "),
            "published": created.isoformat(),
            "hn_points": hit.get("points", 0),
        })
    return out


# Mini-substrate beat config (v1 hard-coded; future: parse source-list-*.md frontmatter)
MINI_SUBSTRATE_SOURCES = {
    "github_releases": [
        ("openai/swarm", 0.9),
        ("microsoft/autogen", 0.9),
        ("sonichi/sutando", 1.0),
        ("sonichi/bodhi_realtime_agent", 1.0),
        ("langchain-ai/langchain", 0.7),
        ("langchain-ai/langgraph", 0.8),
        ("crewAIInc/crewAI", 0.7),
        ("modelcontextprotocol/specification", 0.9),
    ],
    "pypi": [
        ("autogen-core", 0.9),
        ("autogen-agentchat", 0.9),
        ("autogen-ext", 0.9),
        ("langgraph", 0.8),
        ("crewai", 0.7),
    ],
    "hn_searches": [
        ("multi-agent", 0.5),
        ("agentic", 0.5),
        ("MCP", 0.4),
        ("autogen", 0.7),
    ],
}


def collect_mini_substrate() -> list[dict]:
    items: list[dict] = []
    for repo, thesis in MINI_SUBSTRATE_SOURCES["github_releases"]:
        items.extend(fetch_github_releases(repo, since_hours=48, thesis_score=thesis))
    for pkg, thesis in MINI_SUBSTRATE_SOURCES["pypi"]:
        items.extend(fetch_pypi_release(pkg, since_hours=168, thesis_score=thesis))
    for q, thesis in MINI_SUBSTRATE_SOURCES["hn_searches"]:
        items.extend(fetch_hn_front_page(q, since_hours=24, thesis_score=thesis))
    return items


def render_candidate_file(out_path: Path, bot: str, beat: str, items: list[dict]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sorted_items = sorted(items, key=lambda i: -i["score"])
    above_threshold = [i for i in sorted_items if i["score"] >= 0.7]
    with out_path.open("w") as f:
        f.write("---\n")
        f.write(f"title: {bot}/{beat} candidates {datetime.now(timezone.utc).date()}\n")
        f.write(f"generated_at: {now}\n")
        f.write(f"bot: {bot}\n")
        f.write(f"beat: {beat}\n")
        f.write(f"total_items_scored: {len(items)}\n")
        f.write(f"above_threshold: {len(above_threshold)}\n")
        f.write("tags: [sutando-wire, candidates, overnight-survey, " + bot + "]\n")
        f.write("---\n\n")
        if not items:
            f.write(f"# {bot}/{beat} candidates — {datetime.now(timezone.utc).date()}\n\n")
            f.write("**Ran successfully, found nothing.** No items matched the source list within the freshness window.\n\n")
            f.write("Possible reasons: no fresh releases this cycle, all sources rate-limited or down, source-list needs widening.\n")
            return
        f.write(f"# {bot}/{beat} candidates — {datetime.now(timezone.utc).date()}\n\n")
        f.write(f"Scored {len(items)} items; {len(above_threshold)} above 0.7 threshold.\n\n")
        if above_threshold:
            f.write("## Above-threshold (>= 0.7)\n\n")
            for i in above_threshold:
                f.write(f"- **[{i['title']}]({i['url']})** — score={i['score']:.2f} "
                        f"(fresh={i['fresh_hours']:.1f}h, thesis={i['thesis_score']:.2f}, verif={i['verifiability']:.2f}) "
                        f"[{i['source']}]\n")
                if i.get("summary"):
                    f.write(f"  > {i['summary']}\n")
        sub_threshold = [i for i in sorted_items if i["score"] < 0.7]
        if sub_threshold:
            f.write("\n## Sub-threshold (kept for re-scoring next cycle)\n\n")
            for i in sub_threshold[:10]:
                f.write(f"- [{i['title']}]({i['url']}) — score={i['score']:.2f} [{i['source']}]\n")


def main():
    bot = os.environ.get("SUTANDO_BOT_NAME", "mini")
    beat = "substrate"
    items = collect_mini_substrate()
    for i in items:
        i["score"] = score_item(fresh_hours=i["fresh_hours"],
                                thesis_score=i["thesis_score"],
                                verifiability=i["verifiability"])
    today = datetime.now(timezone.utc).date()
    out_path = CANDIDATES_DIR / f"{today}-{bot}-{beat}.md"
    render_candidate_file(out_path, bot, beat, items)
    print(f"wrote {out_path} ({len(items)} items, "
          f"{sum(1 for i in items if i['score'] >= 0.7)} above threshold)")


if __name__ == "__main__":
    main()
