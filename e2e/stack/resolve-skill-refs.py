"""Choose branch tips for the Skills producer. Never resolve a tag or PR merge ref."""

import argparse
import json
import re
import subprocess
import sys
import time


REPOSITORIES = {"sdk": "futureagi-sdk", "traceai": "traceAI"}
RETRY_DELAYS = (5, 15, 45)  # seconds between attempts at listing one repository


def choose(repository, candidates, listing):
    heads = {ref: sha for sha, ref in (line.split("\t") for line in listing.splitlines())}
    for branch in candidates:
        ref = f"refs/heads/{branch}"
        if ref in heads:
            sha = heads[ref]
            if not re.fullmatch(r"[0-9a-f]{40}", sha) or sha == "0" * 40:
                raise ValueError(f"Invalid branch tip for {repository} {ref}")
            return {"branch": branch, "commit": sha}
    raise ValueError(f"No matching branch in {repository}: {', '.join(candidates)}")


def advertised_heads(repository):
    """`git ls-remote --heads`, retried with backoff. A transport error or timeout is never read
    as an absent branch: after the last attempt it fails the job, naming the cause."""
    url = f"https://github.com/future-agi/{repository}.git"
    for attempt, delay in enumerate((*RETRY_DELAYS, None), 1):
        try:
            return subprocess.check_output(["git", "ls-remote", "--heads", url], text=True, timeout=60)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            if delay is None:
                raise RuntimeError(f"Could not list {repository} branches after {attempt} attempts "
                                   f"(transport error, not an absent branch): {error}") from error
            print(f"git ls-remote {repository} failed (attempt {attempt}); retrying in {delay}s", file=sys.stderr)
            time.sleep(delay)


def resolve(head, base):
    candidates = list(dict.fromkeys(branch for branch in (head, base, "dev", "main") if branch))
    result = {}
    for component, repository in REPOSITORIES.items():
        # One advertisement per repository. A transport error is not an absent branch.
        result[component] = choose(repository, candidates, advertised_heads(repository))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("head", help="PR head branch, pushed branch or merge-group target branch")
    parser.add_argument("base", nargs="?", default="", help="PR base branch")
    args = parser.parse_args()
    print(json.dumps(resolve(args.head, args.base)))
