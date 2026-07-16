"""
Scan all open PRs for pact violations by checking out each branch
into a temporary git worktree, running pact --diff, and reporting.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
MAX_WORKERS = 5

# Use the venv python if available, otherwise fall back to current interpreter
_VENV_PYTHON = REPO_ROOT / "futureagi" / ".venv" / "bin" / "python3"
PYTHON = str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable


def get_open_prs() -> list[dict]:
    result = subprocess.run(
        ["gh", "pr", "list", "--repo", "future-agi/future-agi",
         "--author", "@me", "--state", "open",
         "--json", "number,title,headRefName"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def scan_branch(pr: dict, worktree_base: Path) -> dict:
    num = pr["number"]
    branch = pr["headRefName"]
    worktree = worktree_base / f"pr-{num}"

    try:
        # Create worktree checked out to the fork branch
        subprocess.run(
            ["git", "worktree", "add", "--quiet", str(worktree), f"fork/{branch}"],
            cwd=REPO_ROOT, capture_output=True, check=True,
        )

        result = subprocess.run(
            [PYTHON, "-m", "tools.pact.cli", "--diff", "--json", str(worktree)],
            cwd=worktree,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            return {
                "number": num,
                "branch": branch,
                "title": pr["title"],
                "violations": [],
                "error": detail or f"pact exited with status {result.returncode}",
            }

        try:
            violations = json.loads(result.stdout) if result.stdout.strip() else []
        except json.JSONDecodeError as exc:
            detail = (result.stderr or result.stdout or "").strip()
            return {
                "number": num,
                "branch": branch,
                "title": pr["title"],
                "violations": [],
                "error": f"invalid pact JSON: {detail or exc}",
            }

        return {
            "number": num,
            "branch": branch,
            "title": pr["title"],
            "violations": violations,
            "error": None,
        }

    except subprocess.CalledProcessError as e:
        return {
            "number": num,
            "branch": branch,
            "title": pr["title"],
            "violations": [],
            "error": e.stderr.strip() if e.stderr else str(e),
        }
    except Exception as e:
        return {
            "number": num,
            "branch": branch,
            "title": pr["title"],
            "violations": [],
            "error": str(e),
        }
    finally:
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=REPO_ROOT, capture_output=True,
            )
        except Exception:
            pass


def main():
    print("Fetching open PRs...")
    prs = get_open_prs()
    print(f"Found {len(prs)} open PRs. Scanning with {MAX_WORKERS} parallel workers...\n")

    worktree_base = Path(tempfile.mkdtemp(prefix="pact-scan-"))

    try:
        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(scan_branch, pr, worktree_base): pr for pr in prs}
            for future in as_completed(futures):
                r = future.result()
                results.append(r)
                n_v = len(r["violations"])
                status = f"{'✗' if n_v else '✓'} #{r['number']:4d}  {n_v:3d} violation(s)  {r['branch']}"
                if r["error"]:
                    status += f"  [error: {r['error'][:60]}]"
                print(status)

        print("\n" + "=" * 70)
        print("SUMMARY — PRs with violations\n")

        dirty = sorted([r for r in results if r["violations"]], key=lambda x: -len(x["violations"]))
        if not dirty:
            print("✓  All PRs clean.")
        else:
            for r in dirty:
                print(f"\n#{r['number']}  {r['branch']}")
                print(f"  {r['title'][:70]}")
                for v in r["violations"]:
                    missing = ", ".join(v["missing"])
                    short_file = v["file"].replace(str(worktree_base), "").lstrip("/")
                    # strip the pr-NNN/ prefix
                    parts = short_file.split("/", 1)
                    short_file = parts[1] if len(parts) > 1 else short_file
                    print(f"  {short_file}:{v['line']}  {v['call']}()  missing: {missing}")

    finally:
        shutil.rmtree(worktree_base, ignore_errors=True)


if __name__ == "__main__":
    main()
