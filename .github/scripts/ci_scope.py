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
# The paths for which a pull request runs Update CI's ordinary jobs. It is the
# workflow's push path list (test_ci_scope.py keeps the two equal). Pull
# requests have no path filter in the workflow, so the Real rehearsals can
# still run for a release, a label or a tooling change whatever the files.
UPDATE_CI_PATHS = (
    "bin/update",
    "deploy/update/**",
    "api_contracts/host-update/**",
    "deploy/tests/test_update_*.py",
    "deploy/tests/update_e2e/**",
    "scripts/generate_update_manifest.py",
    "scripts/check_update_safety.py",
    "**/*.py",
    "**/*.go",
    "**/*.sql",
    "**/*.sh",
    "bin/**",
    "scripts/**",
    "deploy/**",
    "fi-collector/**",
    "agentcc-gateway/**",
    "docker-compose*.yml",
    "futureagi/**",
    "bin/lib/**",
    ".github/workflows/backend-ci.yml",
    "docker-compose.yml",
    "docker-compose.distributed.yml",
    "deploy/docker-compose.production.yml",
    "deploy/docker-compose.host-status.standalone.yml",
    "deploy/docker-compose.host-status.distributed.yml",
    "bin/install",
    "deploy/setup.sh",
    ".env.example",
    "deploy/.env.production.example",
    "deploy/env-reference.toml",
    ".gitignore",
    "**/.dockerignore",
    "**/*.dockerignore",
    "**/Dockerfile*",
    ".github/actions/setup-helm-tools/**",
    ".github/workflows/release-update-manifest.yml",
    ".github/workflows/release-images.yml",
    ".github/workflows/update-ci.yml",
    ".github/workflows/build-image-multiarch.yml",
    ".github/workflows/helm-release.yml",
    "agentcc-gateway/config.example.yaml",
    "deploy/clickhouse/users.d/**",
    "deploy/standalone/clickhouse/**",
    "futureagi/.ci/clickhouse-storage-policy.xml",
    "futureagi/config/peerdb/temporal-dynamicconfig.yaml",
    "futureagi/scripts/property_catalog_oss/**",
    "futureagi/tracer/services/clickhouse/v2/observed_catalog/**",
    ".gitattributes",
)


def glob_match(pattern, path):
    """A GitHub path filter pattern against a path: ** crosses directories, * does not."""
    out = ""
    at = 0
    while at < len(pattern):
        if pattern.startswith("**/", at):
            out, at = out + "(?:.*/)?", at + 3
        elif pattern.startswith("**", at):
            out, at = out + ".*", at + 2
        elif pattern[at] == "*":
            out, at = out + "[^/]*", at + 1
        else:
            out, at = out + re.escape(pattern[at]), at + 1
    return re.fullmatch(out, path, re.DOTALL) is not None


def update_ci_relevant(paths):
    """Whether a pull request touches anything Update CI's ordinary jobs cover."""
    return any(
        glob_match(pattern, path.decode("utf-8", "surrogateescape"))
        for path in paths
        for pattern in UPDATE_CI_PATHS
    )


# The JSON under deploy/update that the update tool or the qualifier reads at
# run time: a change to one runs both rehearsals. Listed, not inferred: a new
# JSON file there (a review record, say) starts no rehearsal until it is added
# here. test_ci_scope.py derives the set from the names the rehearsal modules
# open and keeps the two equal.
RUNTIME_JSON = {
    b"deploy/update/automatic-layout.json",
    b"deploy/update/backup-proof.schema.json",
    b"deploy/update/compatibility-proof.schema.json",
    b"deploy/update/high-volume-tables.json",
    b"deploy/update/migration-apps.json",
    b"deploy/update/release-manifest.schema.json",
    b"deploy/update/release-policy.json",
}
# Excluded by policy: the static gate (scripts/check_update_safety.py) reads
# these, and the proof inventory still hashes their bytes, but no rehearsal
# outcome depends on their content. Ordinary pull requests edit them to
# record a review.
STATIC_GATE_REGISTERS = {
    b"deploy/update/clickhouse-sources.json",
    b"deploy/update/clickhouse-change-reviews.json",
    b"deploy/update/env-generators.json",
}
# The ClickHouse schema that a boot installs (manage.py bootstrap_install;
# oss_native_bootstrap.py, which selects the native DDL; oss_cdc_bootstrap.py,
# the spans DDL source), and the other
# things only latest-stable -> PR takes from the pull request beside its
# migrations, image builds and settings: the Standalone image's own directory
# and the chart the job packages (CHART, below).
BOOT_INPUTS = (
    b"deploy/standalone/",
    b"futureagi/tracer/services/clickhouse/v2/schema/",
    b"futureagi/tracer/services/clickhouse/v2/observed_catalog/",
    b"futureagi/scripts/property_catalog_oss/",
)
BOOT_FILES = re.compile(
    rb"futureagi/(tfc/management/commands/bootstrap_install"
    rb"|tracer/services/clickhouse/(oss_cdc_install|oss_cdc_bootstrap|oss_native_bootstrap"
    rb"|v2/apply_schema[^/]*|v2/schema_topology))\.py"
)
# The chart, as deploy/tests/update_e2e/acceptance.py packages it: without its
# hack/ and ci/ directories and without dot-files. Its prose (Markdown and the
# licence) is packaged too but changes nothing the job runs.
CHART = b"deploy/helm/futureagi/"
# The updater's two frozen runtime schemas, outside deploy/update.
UPDATER_SCHEMAS = {
    b"api_contracts/host-update/api-response.v1.schema.json",
    b"api_contracts/host-update/public-status.v1.schema.json",
}


def packaged_chart_file(path):
    if not path.startswith(CHART):
        return False
    parts = path[len(CHART) :].split(b"/")
    prose = parts[-1].endswith(b".md") or parts[-1] == b"LICENSE"
    return (
        parts[0] not in (b"hack", b"ci")
        and not any(part.startswith(b".") for part in parts)
        and not prose
    )


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
    """(tooling, built): what the two Real rehearsals take from the candidate.

    tooling is what both execute: bin/update with deploy/update, the
    acceptance driver and every module it imports from scripts/ (the manifest
    generator, the qualifier and its update_rehearsal_* modules;
    test_ci_scope.py walks those imports), the Compose files, and the action
    and workflow that set the jobs up.

    built is what only latest-stable -> PR takes: it builds five images from
    the candidate and boots the Standalone one on real data. The list is a
    choice, not a derivation: the rest of the product source is in those
    images too and waits for the nightly run, because a three-hour job on
    every backend change is what this decision exists to avoid.
    """
    tooling = built = False
    for path in paths:
        name = path.rsplit(b"/", 1)[-1]
        tooling |= (
            path
            in {
                b"bin/update",
                b"scripts/generate_update_manifest.py",
                b"scripts/check_update_safety.py",
                b"scripts/rehearse_update_compatibility.py",
                b"scripts/build_update_compatibility_proof.py",
                b"scripts/update-rehearsal-decision.sh",
                b"deploy/tests/test_update_e2e_automatic.py",
                b".github/workflows/update-ci.yml",
                b".github/scripts/ci_scope.py",
                *UPDATER_SCHEMAS,
            }
            or re.fullmatch(rb"scripts/update_rehearsal_[^/]*\.py", path) is not None
            or path.startswith(
                (b"deploy/tests/update_e2e/", b".github/actions/setup-helm-tools/")
            )
            or path.startswith(b"deploy/update/")
            and not path.endswith((b".md", b".json"))
            or path in RUNTIME_JSON
            or re.fullmatch(rb"(deploy/)?docker-compose[^/]*\.yml", path, re.DOTALL)
            is not None
        )
        built |= (
            path == b"futureagi/uv.lock"
            or path.startswith(b"futureagi/tfc/settings/")
            or path.startswith(b"futureagi/")
            and b"/migrations/" in path
            or name.lower().startswith(b"dockerfile")
            or name.endswith((b".dockerignore", b"entrypoint.sh"))
            or path.startswith(BOOT_INPUTS)
            or packaged_chart_file(path)
            or BOOT_FILES.fullmatch(path) is not None
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
    if event == "pull_request" and paths is not None:
        # What the workflow's path filter decided before; unknown impact runs.
        flags["ordinary_update"] = flags["ordinary_update"] and update_ci_relevant(
            paths
        )
    flags.update(
        update_safety=paths is None or bool(paths),
        executor_base=candidate and (paths is None or bool(paths & PIN_INPUTS)),
        tooling=tooling,
        built=built,
        python=True,
    )
    # The pin check is a pre-merge invariant, not a suite: already-released
    # pins are expected to exist. Do not demand an unpublished pin on all PRs.
    return {
        "version": VERSION,
        "event": event,
        "policy_base": base,
        "diff_base": start,
        "diff_head": head,
        "tested_commit": tested,
        "tested_tree": tree,
        "path_count": None if paths is None else len(paths),
        "policy_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "mode": "qualified" if active else "full",
        "proposed": proposed,
        "forced_standalone": standalone or event == "merge_group",
        "flags": flags,
        "reason": (
            "full control"
            if not candidate
            else "impact unknown"
            if paths is None
            else "shared or unqualified input"
            if proposed
            else "only certified metadata or empty diff"
        ),
    }


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
