"""Verify the producer's contract and package receipt before consuming its archives."""

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path


# Restrict the types, distribution names and local filenames before opening any
# archive. Versions come from the actual candidate packages, never a registry.
ROLES = {
    "sdk_python": ("native-runtime/wheels", r"futureagi-\d[\w.]*-py3-none-any\.whl"),
    "traceai_python": ("native-runtime/wheels", r"fi_instrumentation_otel-\d[\w.]*-py3-none-any\.whl"),
    "sdk_ts": ("skills-ts-runtime/packages", r"future-agi-sdk-\d[\w.+-]*\.tgz"),
    "traceai_ts": ("skills-ts-runtime/packages", r"traceai-fi-core-\d[\w.+-]*\.tgz"),
    "semconv_ts": ("skills-ts-runtime/packages", r"traceai-fi-semantic-conventions-\d[\w.+-]*\.tgz"),
}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_provenance(document, product):
    branches = document.get("branches", {})
    if set(branches) != {"sdk", "traceai"} or not all(isinstance(b, str) and b.strip() for b in branches.values()):
        raise ValueError("Missing resolved SDK or traceAI branch")
    contract = document.get("contract", {})
    source = contract.get("source", {})
    hashes = contract.get("sha256", {})
    baseline_fields = {"non_skills_sdk_commit", "non_skills_slice_sha256"}
    mode = contract.get("sync_mode")
    if mode == "scoped":
        if (not re.fullmatch(r"[0-9a-f]{40}", source.get("non_skills_sdk_commit", ""))
                or not re.fullmatch(r"[0-9a-f]{64}", source.get("non_skills_slice_sha256", ""))):
            raise ValueError("Invalid scoped contract provenance")
    elif mode != "full" or baseline_fields.intersection(source):
        raise ValueError("Invalid contract provenance for sync mode")
    if (source.get("backend_commit") != product
            or set(hashes) != {"swagger.json", "mcp-tools.json", "SOURCE.json"}
            or not all(re.fullmatch(r"[0-9a-f]{64}", value) for value in (
                *hashes.values(), source.get("openapi_sha256", ""), source.get("mcp_sha256", ""),
            ))):
        raise ValueError("Invalid regenerated contract provenance")


def verify(dist, lock_sha, sources):
    lock = (dist / "pair.json").read_bytes()
    if not re.fullmatch(r"[a-f0-9]{64}", lock_sha) or hashlib.sha256(lock).hexdigest() != lock_sha:
        raise ValueError("Pair lock does not match the independently recorded SHA-256")
    document = json.loads(lock)
    if document.get("schema") != 2 or document.get("sources") != sources or not all(
        re.fullmatch(r"[a-f0-9]{40}", value) for value in sources.values()
    ):
        raise ValueError("Pair lock does not match the three expected source revisions")
    verify_provenance(document, sources["product"])
    artifacts = document.get("artifacts", {})
    if set(artifacts) != ROLES.keys():
        raise ValueError("Expected exactly two wheels and three npm archives")
    for role, (_, pattern) in ROLES.items():
        record = artifacts[role]
        if not re.fullmatch(pattern, record["file"], re.ASCII):
            raise ValueError(f"Unexpected filename for {role}")
        path = dist / record["file"]
        if path.is_symlink() or not path.is_file() or digest(path) != record["sha256"]:
            raise ValueError(f"Package does not match the lock for {role}")
    return artifacts


def verify_sums(dist, artifacts):
    sums = dist / "SHA256SUMS"
    expected = sorted(f"{record['sha256']}  {record['file']}" for record in artifacts.values())
    if sums.is_symlink() or sorted(sums.read_text().splitlines()) != expected:
        raise ValueError("SHA256SUMS does not match the verified pair")


def stage(dist, root, artifacts, *, typescript_only=False):
    for directory in sorted({directory for directory, _ in ROLES.values()}):
        if typescript_only and directory != ROLES["sdk_ts"][0]:
            continue
        target = root / directory
        if target.is_symlink():
            raise ValueError(f"Use a real staging directory: {directory}")
        target.mkdir(parents=True, exist_ok=True)
        group = [artifacts[role] for role, (folder, _) in ROLES.items() if folder == directory]
        expected = {record["file"] for record in group} | {"SHA256SUMS"}
        if any(path.name not in expected or path.is_symlink() for path in target.iterdir()):
            raise ValueError(f"Use a clean {directory} for this candidate")
        for record in group:
            source = dist / record["file"]
            output = target / record["file"]
            if output.exists():
                if digest(output) != record["sha256"]:
                    raise ValueError(f"Different artifact already staged: {output.name}")
            else:
                shutil.copyfile(source, output)
            if digest(output) != record["sha256"]:
                raise ValueError(f"Staged checksum mismatch: {output.name}")
        (target / "SHA256SUMS").write_text("".join(
            f"{record['sha256']}  ./{record['file']}\n" for record in group
        ))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path)
    parser.add_argument("sha256")
    for component in ("product", "sdk", "traceai"):
        parser.add_argument(component)
    parser.add_argument("--stage", type=Path)
    parser.add_argument("--typescript-only", action="store_true", help="stage only the verified npm archives")
    parser.add_argument("--require-sums", action="store_true", help="require the source build SHA256SUMS")
    args = parser.parse_args()
    items = verify(args.dist, args.sha256, {k: getattr(args, k) for k in ("product", "sdk", "traceai")})
    if args.require_sums:
        verify_sums(args.dist, items)
    if args.stage is not None:
        stage(args.dist, args.stage, items, typescript_only=args.typescript_only)
    for role in ("sdk_python", "traceai_python"):
        print(items[role]["file"])
