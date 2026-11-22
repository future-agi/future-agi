"""Whole-suite impact policy. Execute the blob from the event's immutable base.

No file-list API or per-test dependency guesses. Bytes are significant: Git paths
can contain newlines and need not be UTF-8. Unknown inputs widen the plan.
"""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

VERSION = 1
# Repository metadata, not application or CI inputs. Do not exempt docs/**.
METADATA = {b".git-blame-ignore-revs", b"BRANCH_NAMING_CONVENTION.md"}
PIN_INPUTS = {
    b"futureagi/code-executor/Dockerfile.base",
    b"futureagi/code-executor/requirements.sandbox.txt",
}
SUITES = ("backend", "metrics", "any", "collector", "ordinary_update")


def git(*args):
    return subprocess.check_output(["git", *args])


def sha(value):
    if not re.fullmatch(r"[0-9a-f]{40}", value) or value == "0" * 40:
        raise ValueError("missing or invalid event commit")
    return value


def changes(base, head):
    data = git(
        "diff",
        "--no-ext-diff",
        "--no-renames",
        "--ignore-submodules=none",
        "--name-only",
        "-z",
        base,
        head,
        "--",
    )
    if data and not data.endswith(b"\0"):
        raise ValueError("incomplete path list")
    return set(data.split(b"\0")[:-1])


def rehearsal_inputs(paths):
    tooling = built = False
    for path in paths:
        name = path.rsplit(b"/", 1)[-1]
        tooling |= (
            path
            in {
                b"bin/update",
                b"scripts/generate_update_manifest.py",
                b"scripts/check_update_safety.py",
                b"scripts/update-rehearsal-decision.sh",
                b"deploy/tests/test_update_e2e_automatic.py",
                b".github/workflows/update-ci.yml",
                b".github/scripts/ci_scope.py",
            }
            or path.startswith(b"deploy/tests/update_e2e/")
            or path.startswith(b"deploy/update/")
            and not path.endswith((b".md", b".json"))
            or re.fullmatch(rb"(deploy/)?docker-compose[^/]*\.yml", path, re.S)
            is not None
        )
        built |= (
            path == b"futureagi/uv.lock"
            or path.startswith(b"futureagi/tfc/settings/")
            or path.startswith(b"futureagi/")
            and b"/migrations/" in path
            or name.lower().startswith(b"dockerfile")
            or name.endswith((b".dockerignore", b"entrypoint.sh"))
        )
    return tooling, built


def plan(event, tested, base, head, mode, standalone=False):
    sha(tested)
    if git("rev-parse", "HEAD").decode().strip() != tested:
        raise ValueError("checkout does not match event SHA")
    tree = git("rev-parse", "HEAD^{tree}").decode().strip()
    candidate = event in ("merge_group", "pull_request")
    start = None
    paths = None
    if candidate:
        sha(base)
        sha(head)
        # The changes jobs keep full history and test the event merge checkout.
        parents = git("show", "-s", "--format=%P", tested).decode().split()
        if event == "merge_group":
            if (
                head != tested
                or subprocess.call(["git", "merge-base", "--is-ancestor", base, head])
                != 0
            ):
                raise ValueError("merge group head/base mismatch")
            start = base
        elif parents != [base, head]:
            raise ValueError("checkout is not the merge of the event PR endpoints")
        else:
            try:
                roots = git("merge-base", "--all", base, head).decode().split()
                if (
                    len(roots) == 1
                    and git("rev-parse", "--is-shallow-repository").strip() == b"false"
                ):
                    start = roots[0]
            except subprocess.CalledProcessError:
                pass  # Tested identity is known; unknown impact runs the full suite.
        if start:
            try:
                head_paths = changes(start, head)
                # Git may apply the head edit at a path renamed on the base.
                paths = (
                    head_paths | changes(base, tested)
                    if event == "pull_request"
                    else head_paths
                )
            except (subprocess.CalledProcessError, ValueError):
                pass
    proposed = paths is None or bool(paths - METADATA)
    active = mode == "qualified" and candidate
    # No narrowing until a trusted repository variable explicitly opts in.
    selected = proposed or not active
    flags = dict.fromkeys(SUITES, selected)
    tooling, built = (True, True) if paths is None else rehearsal_inputs(paths)
    # Group member labels are not available here. A group may carry a forced
    # Standalone proof even for metadata-only changes.
    flags["any"] |= event == "merge_group" or standalone
    flags.update(
        update_safety=paths is None or bool(paths),
        executor_base=candidate and (paths is None or bool(paths & PIN_INPUTS)),
        tooling=tooling,
        built=built,
        python=True,
    )
    # The pin check is a pre-merge invariant, not a suite: already-released
    # pins are expected to exist. Do not demand an unpublished pin on all PRs.
    return dict(
        version=VERSION,
        event=event,
        policy_base=base,
        diff_base=start,
        diff_head=head,
        tested_commit=tested,
        tested_tree=tree,
        path_count=None if paths is None else len(paths),
        policy_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        mode="qualified" if active else "full",
        proposed=proposed,
        forced_standalone=standalone or event == "merge_group",
        flags=flags,
        reason=(
            "full control"
            if not candidate
            else "impact unknown"
            if paths is None
            else "shared or unqualified input"
            if proposed
            else "only certified metadata or empty diff"
        ),
    )


def main():
    p = plan(
        os.environ["GITHUB_EVENT_NAME"],
        os.environ["GITHUB_SHA"],
        os.environ["BASE_SHA"],
        os.environ["HEAD_SHA"],
        os.environ.get("CI_SCOPE_MODE", ""),
        os.environ.get("STANDALONE_LABEL") == "true",
    )
    print(json.dumps(p, sort_keys=True))
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
        print("scope=" + json.dumps(p, separators=(",", ":")), file=f)
        for key, value in p["flags"].items():
            print(f"{key}={str(value).lower()}", file=f)


if __name__ == "__main__":
    main()
