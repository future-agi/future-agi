"""
pact GitHub corpus scanner.

Searches GitHub for Python repositories, fetches source files via the
contents API, runs pact constraint analysis, and streams a labeled
violation corpus as JSONL to stdout.

Each output line is a JSON object:
    {repo, stars, file, line, mode, call, message, code_context, scanned_at}

This corpus is the training signal that makes pact self-improving:
violations are formally grounded (Z3-verified), not heuristic guesses.
Running on GitHub Archive at scale produces labeled (code, bug) pairs
that don't exist anywhere else.

Usage
-----
    python -m tools.pact.scan_github --query "language:python stars:>500" \\
        --limit 100 --token $GITHUB_TOKEN > corpus.jsonl

    # Scan a specific list of repos:
    python -m tools.pact.scan_github --repos owner/repo1,owner/repo2 \\
        --token $GITHUB_TOKEN >> corpus.jsonl

    # Dry-run: show stats only, no corpus output
    python -m tools.pact.scan_github --query "language:python topic:llm" \\
        --limit 20 --stats-only --token $GITHUB_TOKEN

Requirements: requests, z3-solver (same as pact)
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

import requests

from .checker import check_codebase
from .extractor import extract_from_file


# ---------------------------------------------------------------------------
# GitHub API client
# ---------------------------------------------------------------------------

_API = "https://api.github.com"
_RAW = "https://raw.githubusercontent.com"

_SKIP_PATHS = frozenset({
    "migrations", ".venv", "venv", "node_modules", "__pycache__",
    ".git", "dist", "build", ".eggs", ".tox",
})


def _gh_session(token: Optional[str]) -> requests.Session:
    s = requests.Session()
    s.headers["Accept"] = "application/vnd.github+json"
    s.headers["X-GitHub-Api-Version"] = "2022-11-28"
    s.headers["User-Agent"] = "pact-corpus-scanner/1.0"
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    return s


def _rate_check(resp: requests.Response, session: requests.Session) -> None:
    """Sleep if we're near the rate limit."""
    remaining = int(resp.headers.get("X-RateLimit-Remaining", 999))
    if remaining < 10:
        reset_at = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
        wait = max(0, reset_at - time.time()) + 2
        print(f"[pact] rate limit near ({remaining} left), sleeping {wait:.0f}s", file=sys.stderr)
        time.sleep(wait)


def search_repos(
    query: str,
    limit: int,
    session: requests.Session,
) -> Iterator[dict]:
    """Yield repo metadata dicts from GitHub search (up to `limit` repos)."""
    per_page = min(limit, 100)
    page = 1
    yielded = 0

    while yielded < limit:
        resp = session.get(
            f"{_API}/search/repositories",
            params={"q": query, "sort": "stars", "order": "desc",
                    "per_page": per_page, "page": page},
        )
        _rate_check(resp, session)
        if resp.status_code != 200:
            print(f"[pact] search error {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
            break

        data = resp.json()
        items = data.get("items", [])
        if not items:
            break

        for item in items:
            if yielded >= limit:
                return
            yield item
            yielded += 1
        page += 1


def list_python_files(
    owner: str,
    repo: str,
    ref: str,
    session: requests.Session,
) -> list[str]:
    """Return paths of all .py files in the repo tree (skipping noisy dirs)."""
    resp = session.get(
        f"{_API}/repos/{owner}/{repo}/git/trees/{ref}",
        params={"recursive": "1"},
    )
    _rate_check(resp, session)
    if resp.status_code != 200:
        return []

    tree = resp.json().get("tree", [])
    paths = []
    for node in tree:
        if node.get("type") != "blob":
            continue
        path = node["path"]
        if not path.endswith(".py"):
            continue
        parts = path.split("/")
        if any(p in _SKIP_PATHS for p in parts):
            continue
        paths.append(path)
    return paths


def fetch_file_content(
    owner: str,
    repo: str,
    path: str,
    ref: str,
    session: requests.Session,
) -> Optional[str]:
    """Fetch a file's text content via the raw URL."""
    url = f"{_RAW}/{owner}/{repo}/{ref}/{path}"
    try:
        resp = session.get(url, timeout=10)
        if resp.status_code == 200:
            return resp.text
    except requests.RequestException:
        pass
    return None


# ---------------------------------------------------------------------------
# Corpus generation
# ---------------------------------------------------------------------------

def _code_context(source: str, line: int, window: int = 2) -> str:
    """Return `window` lines around `line` (1-indexed) with a marker."""
    lines = source.splitlines()
    lo = max(0, line - 1 - window)
    hi = min(len(lines), line + window)
    result = []
    for i, ln in enumerate(lines[lo:hi], start=lo + 1):
        marker = " -> " if i == line else "    "
        result.append(f"{i:4d}{marker}{ln}")
    return "\n".join(result)


def scan_repo(
    owner: str,
    repo: str,
    stars: int,
    session: requests.Session,
    max_files: int = 200,
) -> Iterator[dict]:
    """
    Yield violation corpus records for a single repository.

    Each record is a flat dict suitable for JSONL serialization.
    """
    # Get default branch
    repo_resp = session.get(f"{_API}/repos/{owner}/{repo}")
    _rate_check(repo_resp, session)
    if repo_resp.status_code != 200:
        return
    repo_data = repo_resp.json()
    default_branch = repo_data.get("default_branch", "main")

    py_files = list_python_files(owner, repo, default_branch, session)
    if not py_files:
        return

    # Process up to max_files to avoid runaway on monorepos
    for path in py_files[:max_files]:
        source = fetch_file_content(owner, repo, path, default_branch, session)
        if not source:
            continue

        # Write to a temp path for the extractor (needs a real file)
        import tempfile, os
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(source)
            tmp_path = tmp.name

        try:
            from pathlib import Path as _Path
            models, functions, calls = extract_from_file(_Path(tmp_path))

            from .checker import check_codebase
            from .failure_mode import DEFAULT_MODES
            from .extractor import ModelManifest, FunctionManifest
            from .checker import check_codebase as _cc

            # Run failure mode checks on this single file
            model_index = {m.name: m for m in models}
            func_index = {f.name: f for f in functions}
            seen: set[tuple] = set()

            from .failure_mode import FailureEvidence

            def _add_ev(ev: FailureEvidence) -> Optional[dict]:
                key = (ev.file, ev.line, ev.mode_name, ev.call)
                if key in seen:
                    return None
                seen.add(key)
                return {
                    "repo": f"{owner}/{repo}",
                    "stars": stars,
                    "file": path,
                    "line": ev.line,
                    "mode": ev.mode_name,
                    "call": ev.call,
                    "message": ev.message,
                    "code_context": _code_context(source, ev.line),
                    "scanned_at": datetime.now(timezone.utc).isoformat(),
                }

            for call in calls:
                for mode in DEFAULT_MODES:
                    for ev in mode.check(call, model_index, func_index):
                        rec = _add_ev(ev)
                        if rec:
                            yield rec

            all_files_set = {tmp_path}
            file_modes = [m for m in DEFAULT_MODES if m.file_check is not None]
            for mode in file_modes:
                for ev in mode.file_check(tmp_path):  # type: ignore[misc]
                    # Rewrite temp path → real path for corpus
                    ev_real = FailureEvidence(
                        mode_name=ev.mode_name,
                        file=path,
                        line=ev.line,
                        call=ev.call,
                        message=ev.message,
                        missing=ev.missing,
                    )
                    rec = _add_ev(ev_real)
                    if rec:
                        yield rec
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="pact-scan-github",
        description="Scan GitHub Python repos and emit a labeled violation corpus as JSONL.",
    )
    p.add_argument("--token", metavar="TOKEN",
                   help="GitHub personal access token (or set GITHUB_TOKEN env var)")
    p.add_argument("--query", metavar="Q",
                   default="language:python stars:>100",
                   help='GitHub search query (default: "language:python stars:>100")')
    p.add_argument("--repos", metavar="OWNER/REPO,...",
                   help="Comma-separated list of specific repos to scan (overrides --query)")
    p.add_argument("--limit", type=int, default=20, metavar="N",
                   help="Max repos to scan (default: 20)")
    p.add_argument("--max-files", type=int, default=100, metavar="N",
                   help="Max Python files per repo (default: 100)")
    p.add_argument("--stats-only", action="store_true",
                   help="Print summary stats; suppress JSONL violation output")
    p.add_argument("--out", metavar="FILE",
                   help="Write JSONL to FILE instead of stdout")
    args = p.parse_args(argv)

    import os
    token = args.token or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("[pact] warning: no GitHub token — rate limited to 60 req/hr", file=sys.stderr)

    session = _gh_session(token)
    out = open(args.out, "w") if args.out else sys.stdout

    stats = {"repos": 0, "files": 0, "violations": 0, "by_mode": {}}

    def _emit(rec: dict) -> None:
        stats["violations"] += 1
        stats["by_mode"][rec["mode"]] = stats["by_mode"].get(rec["mode"], 0) + 1
        if not args.stats_only:
            print(json.dumps(rec), file=out)

    try:
        if args.repos:
            repo_list = [r.strip() for r in args.repos.split(",") if r.strip()]
            repo_iter = (
                {"full_name": r, "stargazers_count": 0}
                for r in repo_list
            )
        else:
            repo_iter = search_repos(args.query, args.limit, session)

        for repo_meta in repo_iter:
            full_name = repo_meta["full_name"]
            owner, repo_name = full_name.split("/", 1)
            stars = repo_meta.get("stargazers_count", 0)
            stats["repos"] += 1
            file_count = 0

            print(f"[pact] scanning {full_name} ({stars}★) …", file=sys.stderr)
            try:
                for rec in scan_repo(owner, repo_name, stars, session, args.max_files):
                    if rec["file"] not in {r.get("file") for r in []}:
                        file_count += 1
                    _emit(rec)
            except Exception as exc:
                print(f"[pact] error scanning {full_name}: {exc}", file=sys.stderr)
                continue

            print(
                f"[pact] {full_name}: {stats['violations']} violations so far",
                file=sys.stderr,
            )

    finally:
        if args.out:
            out.close()

    # Summary
    print(f"\n[pact] corpus scan complete", file=sys.stderr)
    print(f"  repos scanned : {stats['repos']}", file=sys.stderr)
    print(f"  violations    : {stats['violations']}", file=sys.stderr)
    if stats["by_mode"]:
        print("  by mode:", file=sys.stderr)
        for mode, count in sorted(stats["by_mode"].items(), key=lambda x: -x[1]):
            print(f"    {mode:<35} {count}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
