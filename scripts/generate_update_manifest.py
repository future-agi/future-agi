#!/usr/bin/env python3
"""Write the release manifest bin/update consumes (unsigned; the workflow signs it).

The release workflow passes image digests it has already resolved; this
script never resolves a tag itself. It hashes the deployment files from the
checkout of the release tag, takes minimum_version from
deploy/update/upgrade-floor, and validates the result with the same rules the
updater applies, so a release can never publish a manifest hosts would reject.
When --repo-root is a git checkout it also refuses a release in which git
tracks a file below a listed deployment directory that the directory's digest
would leave out (a dot-name, 'name~', '#name#', Thumbs.db, desktop.ini,
__pycache__ or .pyc/.pyo): hosts would never report a local edit to it.

  python3 scripts/generate_update_manifest.py --version v1.52.0 \\
    --source-sha <40-hex> --published-at 2026-10-19T00:00:00Z \\
    --component standalone=docker.io/futureagi/standalone:v1.52.0@sha256:... \\
    ... one --component per manifest component ... \\
    --helm-chart oci://ghcr.io/future-agi/charts/futureagi --helm-digest sha256:... \\
    --output future-agi-update-manifest.json
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
# deploy/ has no __init__.py, so `import deploy` would let a regular `deploy`
# package anywhere on sys.path (PYTHONPATH, the interpreter's site-packages)
# win over the checkout's: the checkout's deploy/ is made the package first.
if "deploy" not in sys.modules:
    _spec = importlib.machinery.ModuleSpec("deploy", None, is_package=True)
    _spec.submodule_search_locations = [str(ROOT / "deploy")]
    sys.modules["deploy"] = importlib.util.module_from_spec(_spec)

from deploy.update.manifest import (  # noqa: E402
    BUNDLE_ASSET, COMPONENT_REPOSITORIES, DEPLOYMENT_FILE_LIST, PRODUCT, SCHEMA_VERSION, ManifestError,
    deployment_hashes, left_out_of_digests, read_deployment_file_list, read_upgrade_floor, validate_manifest,
)

from deploy.update.compatibility import (  # noqa: E402
    MAX_PROOF_BYTES, PROOF_ASSET, decode_document, inputs_hash, validate_release_proof,
)

PROG = "generate_update_manifest"
DEFAULT_POLICY = ROOT / "deploy" / "update" / "release-policy.json"


def read_evidence(path: Path) -> bytes:
    """Local release inputs only. Never download or execute a policy/proof."""
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PROOF_BYTES:
                raise ManifestError("release policy/proof must be a bounded regular file")
            raw = stream.read(MAX_PROOF_BYTES + 1)
    except OSError:
        raise ManifestError("release policy/proof is unreadable") from None
    if len(raw) > MAX_PROOF_BYTES:
        raise ManifestError("release policy/proof exceeds size limit")
    return raw


def apply_release_policy(manifest: dict, args: argparse.Namespace) -> dict:
    import hashlib

    version = getattr(args, "schema_version", SCHEMA_VERSION)
    policy_path = getattr(args, "release_policy", None)
    proof_path = getattr(args, "compatibility_proof", None)
    run_id = getattr(args, "proof_run_id", None)
    manifest["schema_version"] = version
    if version == 1:
        if policy_path or proof_path or run_id is not None:
            raise ManifestError("schema 1 cannot carry release certification")
        return validate_manifest(manifest)
    policy = decode_document(read_evidence(Path(policy_path) if policy_path else DEFAULT_POLICY))
    if (set(policy) != {"schema_version", "migration", "compatibility"}
            or type(policy["schema_version"]) is not int or policy["schema_version"] != 1):
        raise ManifestError("unsupported release policy")
    manifest["compatibility"] = policy["compatibility"]
    if isinstance(policy['compatibility'], dict) and policy['compatibility'].get('contract_version') == 2:
        if policy['migration'] != manifest['migration']:
            raise ManifestError('restore qualification cannot rewrite frozen legacy migration metadata')
    else:
        # Historical contract-v1 generation/manual semantics remain readable.
        manifest["migration"] = policy["migration"]
    if not isinstance(policy["compatibility"], dict) or policy["compatibility"].get("validation") is not None:
        raise ManifestError("release policy validation must be null; supply independently checked proof")
    raw = None
    if proof_path:
        if type(run_id) is not int or not 1 <= run_id <= 9007199254740991:
            raise ManifestError("compatibility proof requires canonical --proof-run-id")
        raw = read_evidence(Path(proof_path))
        manifest["compatibility"]["validation"] = {
            "asset": PROOF_ASSET, "sha256": hashlib.sha256(raw).hexdigest(),
            "inputs_hash": inputs_hash(manifest), "run_id": run_id,
        }
    elif run_id is not None:
        raise ManifestError("--proof-run-id requires --compatibility-proof")
    manifest = validate_manifest(manifest)
    if raw is not None:
        validate_release_proof(raw, manifest, repository=getattr(args, "proof_repository", "future-agi/future-agi"))
    return manifest


def tracked_files(repo: Path, directories: Sequence[str]) -> Optional[List[str]]:
    """The files git tracks below `directories` of the checkout at `repo`; None when `repo` is not a git checkout."""

    if not (repo / ".git").exists():
        return None
    # GIT_DIR, GIT_INDEX_FILE and the like (set inside a git hook, say) would point git at another repository.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    command = ["git", "-C", str(repo), "--literal-pathspecs", "ls-files", "-z", "--", *directories]
    try:
        result = subprocess.run(command, env=env, capture_output=True, check=False, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ManifestError(f"cannot run git to list the files it tracks in {repo}: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip() or f"exit status {result.returncode}"
        raise ManifestError(f"git ls-files failed in {repo}: {detail}")
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


def check_tracked_files_are_hashed(repo: Path, entries: Sequence[str]) -> None:
    """Refuse a release in which git tracks a file below a listed directory that no entry's digest covers."""

    directories = [entry for entry in entries if (repo / entry).is_dir()]
    if not directories:
        return
    files = tracked_files(repo, directories)
    if files is None:
        print(f"{PROG}: warning: {repo} is not a git checkout, so the check that every file git tracks below a "
              f"directory listed in {DEPLOYMENT_FILE_LIST} is part of its digest was skipped", file=sys.stderr)
        return
    left_out = left_out_of_digests(repo, entries, files)
    if left_out:
        raise ManifestError(
            f"git tracks {len(left_out)} file(s) below a directory listed in {DEPLOYMENT_FILE_LIST} that its digest "
            f"would leave out, so bin/update would never report a local edit to them: {', '.join(left_out)}. "
            "A directory's digest skips names that start with '.', end with '~' or start and end with '#', "
            "Thumbs.db, desktop.ini, __pycache__ and .pyc/.pyo files; rename each of these files, or list it "
            f"on its own line in {DEPLOYMENT_FILE_LIST}.")


def build(args: argparse.Namespace) -> dict:
    repo = Path(args.repo_root).resolve()
    components = {}
    for item in args.component:
        name, sep, ref = item.partition("=")
        if not sep or "@" not in ref:
            raise ManifestError(f"--component must be NAME=IMAGE:TAG@sha256:DIGEST, not {item!r}")
        if name in components:
            raise ManifestError(f"--component {name} given twice")
        image, digest = ref.rsplit("@", 1)
        components[name] = {"image": image, "digest": digest}
        if name in ("standalone", "backend", "frontend", "fi_collector", "agentcc_gateway", "serving", "code_executor"):
            components[name]["platforms"] = ["linux/amd64", "linux/arm64"]
        else:
            components[name]["platforms"] = ["linux/amd64"]
    missing = sorted(set(COMPONENT_REPOSITORIES) - set(components))
    if missing:
        raise ManifestError(f"missing --component for {', '.join(missing)}")
    minimum = args.minimum_version or read_upgrade_floor(repo)
    entries = read_deployment_file_list(repo)
    check_tracked_files_are_hashed(repo, entries)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "product": PRODUCT,
        "channel": args.channel,
        "version": args.version,
        "source_sha": args.source_sha,
        "published_at": args.published_at,
        "minimum_version": minimum,
        "release_notes": f"https://github.com/future-agi/future-agi/releases/tag/{args.version}",
        "signature": {"bundle": args.bundle_name},
        "components": components,
        "helm": {"chart": args.helm_chart, "version": args.version.lstrip("v"), "digest": args.helm_digest},
        "deployment": {"files": deployment_hashes(repo, entries)},
        "migration": {"requires_bootstrap": True, "rollback": "manual"},
    }
    if args.signature_identity:
        manifest["signature"]["identity"] = args.signature_identity
        manifest["signature"]["issuer"] = args.signature_issuer
    return apply_release_policy(manifest, args)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", required=True, help="release tag, vX.Y.Z")
    parser.add_argument("--channel", default="stable", choices=("stable", "beta"))
    parser.add_argument("--source-sha", required=True, help="40-hex commit of the release tag")
    parser.add_argument("--published-at", required=True, help="UTC timestamp, YYYY-MM-DDTHH:MM:SSZ")
    parser.add_argument("--minimum-version", help="default: deploy/update/upgrade-floor at --repo-root")
    parser.add_argument("--component", action="append", default=[], metavar="NAME=IMAGE:TAG@DIGEST")
    parser.add_argument("--helm-chart", required=True, help="untagged oci:// chart reference")
    parser.add_argument("--helm-digest", required=True)
    parser.add_argument("--bundle-name", default=BUNDLE_ASSET)
    parser.add_argument("--signature-identity", help="informational copy of the signing identity")
    parser.add_argument("--signature-issuer", default="https://token.actions.githubusercontent.com")
    parser.add_argument("--repo-root", default=str(ROOT), help="checkout of the release tag (deployment files)")
    parser.add_argument("--schema-version", type=int, choices=(1, 2), default=SCHEMA_VERSION)
    parser.add_argument("--release-policy", help="reviewed local policy; default is uncertified and requires initialization")
    parser.add_argument("--compatibility-proof", help="local machine-produced proof, never inferred from semver")
    parser.add_argument("--proof-run-id", type=int, help="expected same-repository Actions run ID")
    parser.add_argument("--proof-repository", default="future-agi/future-agi", help="expected proof producer repository")
    parser.add_argument("--output", help="write here instead of stdout")
    args = parser.parse_args(argv)
    try:
        manifest = build(args)
    except ManifestError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(manifest, sort_keys=True, indent=2) + "\n"
    if args.output:
        # The resolved candidate is frozen before the rehearsal DAG starts.
        # Exact retries are idempotent; neither stable nor beta bytes may move.
        path, raw = Path(args.output), text.encode("utf-8")
        try:
            try:
                with path.open("xb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                if read_evidence(path) != raw:
                    raise ManifestError("frozen manifest differs; refusing to replace")
        except (OSError, ManifestError) as exc:
            print(f"{PROG}: {exc}", file=sys.stderr)
            return 2
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
