"""Release manifest: parsing, strict validation and deployment-file hashing.

The release workflow (scripts/generate_update_manifest.py) and the host-side
updater share this module, so both sides enforce one contract. The manifest
never changes which images a host pulls: those always come from the host's
own compose files, and bin/update refuses an install whose compose files pull
a component from another image (registry included) than the manifest names,
so registry mirrors are not supported yet. Both readable schema versions pin
release/image/deployment identity. Schema 2 additionally carries closed signed
compatibility claims; these are not authorization without independently checked
proof bytes and fresh host observations. Schema 1 manual use remains unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = (1, 2)
PRODUCT = "future-agi"
CHANNELS = ("stable", "beta")
MANIFEST_ASSET = "future-agi-update-manifest.json"
BUNDLE_ASSET = "future-agi-update-manifest.sigstore.json"
DEPLOYMENT_FILE_LIST = "deploy/update/deployment-files.txt"
UPGRADE_FLOOR_FILE = "deploy/update/upgrade-floor"

# Component -> repository path. A manifest may name any registry host, but not
# another path: one that names another repository for a component is invalid.
# A host updates only from the registry its manifest names (see above).
COMPONENT_REPOSITORIES: Dict[str, str] = {
    "standalone": "futureagi/standalone",
    "backend": "futureagi/future-agi",
    "frontend": "futureagi/frontend",
    "fi_collector": "futureagi/fi-collector",
    "agentcc_gateway": "futureagi/agentcc-gateway",
    "serving": "futureagi/serving",
    "serving_gpu": "futureagi/serving",
    "code_executor": "futureagi/code-executor",
    "simulation_runner": "futureagi/future-agi-simulation-runner",
}
# Tag suffix of a component's image relative to the release version.
COMPONENT_TAG_SUFFIX: Dict[str, str] = {"serving_gpu": "-gpu"}

# Digits are spelled [0-9]: \d also matches non-ASCII digits, so 'v1٣.0.0'
# would parse as v13.0.0. release-manifest.schema.json uses the same patterns.
# Each number has at most VERSION_NUMBER_DIGITS digits: int() refuses a
# string of more than 4300 digits (Python 3.11+), and no version needs more.
VERSION_NUMBER_DIGITS = 18
_VERSION_NUMBER = rf"(0|[1-9][0-9]{{0,{VERSION_NUMBER_DIGITS - 1}}})"
_VERSION_RE = re.compile(
    rf"^v{_VERSION_NUMBER}\.{_VERSION_NUMBER}\.{_VERSION_NUMBER}(?:-(beta|rc)\.{_VERSION_NUMBER})?$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_PATH_COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
_HOST_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
_HOST = _HOST_LABEL + r"(?:\." + _HOST_LABEL + r")*(?::[0-9]{1,5})?"
_PATH_COMPONENT_RE = re.compile(f"^{_PATH_COMPONENT}$")
_REGISTRY_RE = re.compile(f"^{_HOST}$")
# oci://<registry host[:port]>/<path>: at least one path segment, no tag, no digest.
_CHART_RE = re.compile(f"^oci://{_HOST}(?:/{_PATH_COMPONENT})+$")
_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_BUNDLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
_HTTPS_URL_RE = re.compile(r"^https://\S+$")
_PRERELEASE_ORDER = {"beta": 0, "rc": 1}
# Type tags of the deployment-file digests that are not a file's own sha256.
_DIRECTORY_TAG = b"futureagi-deployment-directory-v1\0"
_MISSING_TAG = b"futureagi-deployment-missing-v1\0"


class ManifestError(ValueError):
    """A manifest, version or image reference cannot be trusted or understood."""


# ---------------------------------------------------------------- versions --

def parse_version(value: str) -> Tuple[int, int, int, int, int]:
    """Return a sortable key for vX.Y.Z or vX.Y.Z-(beta|rc).N.

    A release sorts after every prerelease of the same version.
    """

    if not isinstance(value, str):
        raise ManifestError(f"invalid version: {value!r}")
    match = _VERSION_RE.fullmatch(value.strip())
    if not match:
        shown = value if len(value) <= 64 else f"{value[:32]}...{value[-8:]}"
        if re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-(?:beta|rc)\.[0-9]+)?", value.strip()):
            raise ManifestError(f"invalid version {shown!r}: expected vMAJOR.MINOR.PATCH without leading zeros, "
                                f"each number at most {VERSION_NUMBER_DIGITS} digits long")
        raise ManifestError(f"invalid version {shown!r}: expected vMAJOR.MINOR.PATCH")
    major, minor, patch, pre, pre_num = match.groups()
    if pre is None:
        return int(major), int(minor), int(patch), 2, 0
    return int(major), int(minor), int(patch), _PRERELEASE_ORDER[pre], int(pre_num)


def is_version(value: Optional[str]) -> bool:
    return isinstance(value, str) and _VERSION_RE.fullmatch(value) is not None


def is_prerelease(value: str) -> bool:
    return parse_version(value)[3] != 2


# ------------------------------------------------------------ image refs --

@dataclass(frozen=True)
class ImageRef:
    registry: str
    repository: str
    tag: Optional[str]
    digest: Optional[str]

    @property
    def name(self) -> str:
        """Registry plus repository, e.g. docker.io/futureagi/standalone."""

        return f"{self.registry}/{self.repository}"

    def __str__(self) -> str:
        text = self.name
        if self.tag:
            text += f":{self.tag}"
        if self.digest:
            text += f"@{self.digest}"
        return text


def parse_image_ref(value: str) -> ImageRef:
    """Parse an image reference the way Docker resolves it.

    `futureagi/standalone:v1`, `docker.io/futureagi/standalone:v1` and
    `index.docker.io/futureagi/standalone:v1` all resolve to the same name.
    """

    if not isinstance(value, str) or not value or value != value.strip():
        raise ManifestError(f"invalid image reference: {value!r}")
    rest, digest = value, None
    if "@" in rest:
        rest, digest = rest.split("@", 1)
        if not _DIGEST_RE.fullmatch(digest):
            raise ManifestError(f"invalid image digest in {value!r}")
    tag = None
    last = rest.rsplit("/", 1)[-1]
    if ":" in last:
        rest, tag = rest.rsplit(":", 1)
        if not _TAG_RE.fullmatch(tag):
            raise ManifestError(f"invalid image tag in {value!r}")
    parts = rest.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        registry, path = parts[0], parts[1:]
        if not _REGISTRY_RE.fullmatch(registry):
            raise ManifestError(f"invalid registry in {value!r}")
    else:
        registry, path = "docker.io", parts
    registry = registry.lower()
    if registry in ("index.docker.io", "registry-1.docker.io"):
        registry = "docker.io"
    if registry == "docker.io" and len(path) == 1:
        path = ["library", *path]
    if not path or any(not _PATH_COMPONENT_RE.fullmatch(part) for part in path):
        raise ManifestError(f"invalid repository in {value!r}")
    return ImageRef(registry, "/".join(path), tag, digest)


# ------------------------------------------------------------- manifests --

def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def manifest_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _exact_keys(value: Any, context: str, required: Iterable[str], optional: Iterable[str] = ()) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ManifestError(f"{context} must be an object")
    required, optional = set(required), set(optional)
    missing = sorted(required - set(value))
    if missing:
        raise ManifestError(f"{context} is missing {', '.join(missing)}")
    unknown = sorted(set(value) - required - optional)
    if unknown:
        raise ManifestError(f"{context} has unknown field(s) {', '.join(unknown)}")
    return value


def _string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ManifestError(f"{context} must be a non-empty string without surrounding spaces")
    return value


def _matches(pattern: re.Pattern[str], value: Any) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _relative_path(value: Any, context: str) -> str:
    """A normalized relative path: no backslash, no leading /, no empty, . or .. segment."""

    text = _string(value, context)
    if "\\" in text or any(part in ("", ".", "..") for part in text.split("/")):
        raise ManifestError(f"{context} must be a plain relative path inside the checkout: {text!r}")
    return text


def validate_manifest(value: Any) -> Dict[str, Any]:
    """Validate a manifest and return a detached copy.

    Every rule here is also in release-manifest.schema.json where JSON Schema
    can state it. Cross-field rules it cannot (each image tag equals the
    version, minimum_version <= version, helm.version equals the version
    without its v) live only here, which is why the generator calls this
    function too.
    """

    manifest = json.loads(json.dumps(value)) if isinstance(value, Mapping) else value
    schema_version = manifest.get("schema_version") if isinstance(manifest, Mapping) else None
    if isinstance(manifest, Mapping) and "schema_version" in manifest and (
        type(schema_version) is not int or schema_version not in SUPPORTED_SCHEMA_VERSIONS
    ):
        raise ManifestError(f"unsupported manifest schema_version {schema_version!r}; update bin/update")
    _exact_keys(
        manifest,
        "manifest",
        ("schema_version", "product", "channel", "version", "source_sha", "published_at",
         "minimum_version", "signature", "components", "helm", "deployment", "migration")
        + (("compatibility",) if schema_version == 2 else ()),
        ("release_notes",),
    )
    if manifest["product"] != PRODUCT:
        raise ManifestError(f"manifest is for {manifest['product']!r}, not {PRODUCT!r}")
    channel = manifest["channel"]
    if channel not in CHANNELS:
        raise ManifestError(f"unknown manifest channel {channel!r}")
    version = _string(manifest["version"], "manifest.version")
    parse_version(version)
    if channel == "stable" and is_prerelease(version):
        raise ManifestError(f"stable manifest carries a prerelease version {version}")
    if not _matches(_SHA1_RE, manifest["source_sha"]):
        raise ManifestError("manifest.source_sha must be a 40-character commit SHA")
    if not _matches(_TIMESTAMP_RE, manifest["published_at"]):
        raise ManifestError("manifest.published_at must be a UTC timestamp like 2026-10-19T00:00:00Z")
    minimum = _string(manifest["minimum_version"], "manifest.minimum_version")
    if parse_version(minimum) > parse_version(version):
        raise ManifestError(f"manifest.minimum_version {minimum} is newer than its version {version}")
    if "release_notes" in manifest:
        notes = _string(manifest["release_notes"], "manifest.release_notes")
        if not _HTTPS_URL_RE.fullmatch(notes):
            raise ManifestError("manifest.release_notes must be an https URL")

    signature = _exact_keys(manifest["signature"], "manifest.signature", ("bundle",), ("identity", "issuer"))
    bundle = _string(signature["bundle"], "manifest.signature.bundle")
    if not _BUNDLE_RE.fullmatch(bundle):
        raise ManifestError("manifest.signature.bundle must be a file name, not a path")
    for key in ("identity", "issuer"):
        if key in signature:
            _string(signature[key], f"manifest.signature.{key}")

    components = _exact_keys(manifest["components"], "manifest.components", COMPONENT_REPOSITORIES)
    for name, component in components.items():
        context = f"manifest.components.{name}"
        _exact_keys(component, context, ("image", "digest"), ("platforms",))
        ref = parse_image_ref(_string(component["image"], f"{context}.image"))
        if ref.digest:
            raise ManifestError(f"{context}.image must not embed a digest; use {context}.digest")
        expected_tag = version + COMPONENT_TAG_SUFFIX.get(name, "")
        if ref.tag != expected_tag:
            raise ManifestError(f"{context}.image must be tagged {expected_tag}, not {ref.tag!r}")
        if ref.repository != COMPONENT_REPOSITORIES[name]:
            raise ManifestError(f"{context}.image must be a {COMPONENT_REPOSITORIES[name]} image, not {ref.repository}")
        if not _matches(_DIGEST_RE, component["digest"]):
            raise ManifestError(f"{context}.digest must be sha256:<64 hex>")
        if "platforms" in component:
            platforms = component["platforms"]
            if not isinstance(platforms, list) or not platforms or any(
                p not in ("linux/amd64", "linux/arm64") for p in platforms
            ) or len(set(platforms)) != len(platforms):
                raise ManifestError(f"{context}.platforms must list linux/amd64 and/or linux/arm64")

    helm = _exact_keys(manifest["helm"], "manifest.helm", ("chart", "version", "digest"))
    chart = _string(helm["chart"], "manifest.helm.chart")
    if not _CHART_RE.fullmatch(chart):
        raise ManifestError("manifest.helm.chart must be an untagged oci:// chart reference")
    if helm["version"] != version[1:]:
        raise ManifestError(f"manifest.helm.version must be {version[1:]}")
    if not _matches(_DIGEST_RE, helm["digest"]):
        raise ManifestError("manifest.helm.digest must be sha256:<64 hex>")

    deployment = _exact_keys(manifest["deployment"], "manifest.deployment", ("files",))
    files = deployment["files"]
    if not isinstance(files, Mapping) or not files:
        raise ManifestError("manifest.deployment.files must list the release's deployment files")
    for path, digest in files.items():
        _relative_path(path, "manifest.deployment.files key")
        if not _matches(_HEX_DIGEST_RE, digest):
            raise ManifestError(f"manifest.deployment.files[{path!r}] must be a sha256 hex digest")

    # Frozen manual metadata: automatic migration admission is row-local v2
    # standalone_bootstrap evidence, NOT requires_bootstrap=False. Never rewrite
    # these signed legacy fields to make a migration release appear stateless.
    migration = _exact_keys(manifest["migration"], "manifest.migration", ("requires_bootstrap", "rollback"))
    if not isinstance(migration["requires_bootstrap"], bool):
        raise ManifestError("manifest.migration.requires_bootstrap must be true or false")
    if migration["rollback"] not in ("manual", "unsupported"):
        raise ManifestError("manifest.migration.rollback must be manual or unsupported")
    if schema_version == 2:
        # Deferred import avoids a contracts -> manifest -> contracts cycle.
        from .compatibility import validate_compatibility
        validate_compatibility(manifest)
    return manifest


def component_tag(manifest: Mapping[str, Any], component: str) -> str:
    return manifest["version"] + COMPONENT_TAG_SUFFIX.get(component, "")


# ------------------------------------------------------ deployment files --

def read_deployment_file_list(root: Path) -> List[str]:
    """The checked-in list of files a release's compose setup depends on."""

    path = root / DEPLOYMENT_FILE_LIST
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ManifestError(f"cannot read {DEPLOYMENT_FILE_LIST}: {exc}") from exc
    entries = []
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if line:
            entries.append(_relative_path(line, DEPLOYMENT_FILE_LIST))
    if not entries:
        raise ManifestError(f"{DEPLOYMENT_FILE_LIST} lists no files")
    return entries


# Names operating systems leave in a directory a file manager opened (Windows
# names them in either case).
_OS_LITTER_NAMES = frozenset({"thumbs.db", "desktop.ini"})


def _is_litter_name(name: str) -> bool:
    """Whether one name is a hidden name or editor/OS litter git commonly ignores.

    Hidden names ('.DS_Store', '._x', '.x.swp', '.idea'), editor backups
    ('name~', what git's `*~` ignores), Emacs autosaves ('#name#', git's
    `\\#*\\#`), and Windows' Thumbs.db and desktop.ini.
    """

    if name.startswith(".") or name.endswith("~"):
        return True
    if len(name) >= 2 and name.startswith("#") and name.endswith("#"):
        return True
    return name.lower() in _OS_LITTER_NAMES


def _skipped_in_directory_hash(parts: Tuple[str, ...]) -> bool:
    """Whether a path below a hashed directory (its relative parts) is left out of the digest."""

    if "__pycache__" in parts[:-1] or parts[-1].endswith((".pyc", ".pyo")):
        return True
    return any(_is_litter_name(part) for part in parts)


def hash_path(root: Path, relative: str) -> str:
    """The digest a release records for one deployment-file entry.

    A file: the sha256 of its bytes, what `sha256sum FILE` prints.
    A directory: the sha256 of _DIRECTORY_TAG followed, for each file below
    it in byte order of its relative path, by that path, a NUL byte, the
    file's sha256 in hex and a newline. Files under a __pycache__ directory
    and .pyc/.pyo files are skipped, and so is every file or directory at any
    depth below the hashed directory whose name starts with '.' (Finder's
    .DS_Store, editor swap files), ends with '~' (editor backups), starts and
    ends with '#' (Emacs autosaves), or is Thumbs.db or desktop.ini (in any
    case): they are not part of a release, git commonly ignores them, and
    `git checkout` would neither show nor remove them.
    Empty directories add nothing, since git cannot record them. The release
    generator hashes with this same function, so both sides skip the same
    files, and it refuses a release in which git tracks a file these rules
    would skip (left_out_of_digests).
    A missing path: the sha256 of _MISSING_TAG followed by the path, so a
    release can record that a file must not exist and a checkout that lacks
    a file reads as drift.
    The tags keep an empty directory and a missing path from hashing like a
    file's content (an empty file, say).
    """

    target = root / relative
    if target.is_file():
        return hashlib.sha256(target.read_bytes()).hexdigest()
    if target.is_dir():
        entries = []
        for path in target.rglob("*"):
            parts = path.relative_to(target).parts
            if _skipped_in_directory_hash(parts) or not path.is_file():
                continue
            entries.append((os.fsencode("/".join(parts)), path))
        digest = hashlib.sha256(_DIRECTORY_TAG)
        for name, path in sorted(entries, key=lambda entry: entry[0]):
            digest.update(name + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii") + b"\n")
        return digest.hexdigest()
    return hashlib.sha256(_MISSING_TAG + relative.encode("utf-8", "surrogatepass")).hexdigest()


def deployment_hashes(root: Path, paths: Iterable[str]) -> Dict[str, str]:
    return {path: hash_path(root, path) for path in paths}


def left_out_of_digests(root: Path, entries: Iterable[str], files: Iterable[str]) -> List[str]:
    """Which of `files` lie below a listed directory yet are part of no entry's digest, sorted.

    `files` are '/'-separated paths relative to `root`; the release generator
    passes every file git tracks below the listed directories. A file the
    directory digest skips (see hash_path) is still covered when the list
    names it as an entry of its own, or names a directory it lies below whose
    digest keeps it. One that is not would be bind-mounted into containers
    without being part of the signed digest, so a host would never report a
    local edit to it as drift.
    """

    entries = list(entries)
    directories = [tuple(entry.split("/")) for entry in entries if (root / entry).is_dir()]
    listed_files = {entry for entry in entries if (root / entry).is_file()}
    left_out = set()
    for path in files:
        parts = tuple(path.split("/"))
        depths = [len(directory) for directory in directories
                  if len(parts) > len(directory) and parts[:len(directory)] == directory]
        if depths and path not in listed_files and all(_skipped_in_directory_hash(parts[depth:]) for depth in depths):
            left_out.add(path)
    return sorted(left_out)


def read_upgrade_floor(root: Path) -> str:
    path = root / UPGRADE_FLOOR_FILE
    try:
        lines = [line.split("#", 1)[0].strip() for line in path.read_text(encoding="utf-8").splitlines()]
    except OSError as exc:
        raise ManifestError(f"cannot read {UPGRADE_FLOOR_FILE}: {exc}") from exc
    values = [line for line in lines if line]
    if len(values) != 1:
        raise ManifestError(f"{UPGRADE_FLOOR_FILE} must contain exactly one version")
    parse_version(values[0])
    return values[0]
