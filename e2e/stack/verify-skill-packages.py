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
PRODUCT_SCOPES = ("sql", "epoch", "skills")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def hex_digest(value, size):
    return isinstance(value, str) and re.fullmatch(rf"[0-9a-f]{{{size}}}", value) is not None


def verify_scoped_source(source, product=None, owned=None):
    """Validate x-contract-sources, including consistency between the product scopes.

    `owned` names the scopes synced from `product` in this pairing (default: all three). Those
    must name `product` and agree with each other; the others are the paired SDK commit's own
    records, which must be well formed and agree with each other."""
    owned = PRODUCT_SCOPES if owned is None else tuple(owned)
    if not owned or set(owned) - set(PRODUCT_SCOPES) or len(set(owned)) != len(owned):
        raise ValueError("Invalid scoped contract provenance: owned scopes")
    if not isinstance(source, dict) or set(source) != {*PRODUCT_SCOPES, "other"}:
        raise ValueError("Invalid scoped contract provenance: expected sql, epoch, skills and other")
    other = source["other"]
    if (not isinstance(other, dict) or set(other) != {"sdk_commit", "slice_sha256", "openapi_sha256"}
            or not hex_digest(other["sdk_commit"], 40) or not hex_digest(other["slice_sha256"], 64)
            or not hex_digest(other["openapi_sha256"], 64)):
        raise ValueError("Invalid scoped contract provenance: SDK baseline")
    hashes = ("openapi_sha256", "mcp_sha256", "tools_yaml_sha256")
    inputs = {}  # one product input tuple per side: the owned scopes, and the pinned ones
    seen_tools = set()
    for scope in PRODUCT_SCOPES:
        row = source[scope]
        mine = scope in owned
        if (not isinstance(row, dict) or set(row) != {"backend_commit", *hashes, "tools"}
                or not hex_digest(row["backend_commit"], 40) or not all(hex_digest(row[key], 64) for key in hashes)
                or (mine and product is not None and row["backend_commit"] != product)):
            raise ValueError(f"Invalid scoped contract provenance: {scope} product inputs")
        current = tuple(row[key] for key in ("backend_commit", *hashes))
        if inputs.setdefault(mine, current) != current:
            raise ValueError(f"Invalid scoped contract provenance: inconsistent {scope} product inputs")
        tools = row["tools"]
        if (not isinstance(tools, list) or not tools or not all(isinstance(t, str) and t.strip() for t in tools)
                or tools != sorted(set(tools)) or seen_tools.intersection(tools)):
            raise ValueError(f"Invalid scoped contract provenance: {scope} tool inventory")
        seen_tools.update(tools)


def verify_provenance(document, product):
    branches = document.get("branches", {})
    if set(branches) != {"sdk", "traceai"} or not all(isinstance(b, str) and b.strip() for b in branches.values()):
        raise ValueError("Missing resolved SDK or traceAI branch")
    contract = document.get("contract", {})
    source = contract.get("source", {})
    hashes = contract.get("sha256", {})
    mode = contract.get("sync_mode")
    if not isinstance(hashes, dict) or not isinstance(source, dict):
        raise ValueError("Invalid regenerated contract provenance")
    if source.keys() & {*PRODUCT_SCOPES, "other"}:
        owned = contract.get("owned_scopes")
        # Receipts without owned_scopes synced every scope from the product. Product pairing now
        # re-syncs Skills only, and nothing else may name itself product-owned.
        if owned is not None and owned != ["skills"]:
            raise ValueError("Invalid scoped contract provenance: owned scopes")
        verify_scoped_source(source, product, owned=owned)
        if mode != "scoped" or set(hashes) != {"swagger.json"} or not hex_digest(hashes["swagger.json"], 64):
            raise ValueError("Invalid scoped contract provenance: expected scoped mode and only a swagger.json digest")
        return

    # Older commit-pinned SDKs emit two sidecars, including on their Skills-only preview.
    if (mode != verify_legacy_source(source) or source["backend_commit"] != product
            or set(hashes) != {"swagger.json", "mcp-tools.json", "SOURCE.json"}
            or not all(hex_digest(value, 64) for value in hashes.values())):
        raise ValueError("Invalid regenerated contract provenance")


def verify_legacy_source(source):
    fields = {"backend_commit", "openapi_sha256", "mcp_sha256"}
    if (not isinstance(source, dict) or not hex_digest(source.get("backend_commit"), 40)
            or not all(hex_digest(source.get(key), 64) for key in ("openapi_sha256", "mcp_sha256"))):
        raise ValueError("Invalid legacy contract provenance")
    if set(source) == fields:
        return "full"
    if (set(source) == fields | {"non_skills_sdk_commit", "non_skills_slice_sha256"}
            and hex_digest(source.get("non_skills_sdk_commit"), 40)
            and hex_digest(source.get("non_skills_slice_sha256"), 64)):
        return "scoped"
    raise ValueError("Invalid or partial legacy contract provenance")


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
