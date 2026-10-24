"""Pure, fail-closed release certification and automatic eligibility.

This module does not verify signatures, inspect a daemon, run state probes or
reserve a transaction. Adapters must supply fresh observations under update.lock
at EVERY guard boundary; an Eligibility is not a reusable authorization token.
Private evidence must never be serialized as public status. Only public_summary
is a closed diagnostic projection. Release proof acquisition belongs to S11.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

from .contracts import AutomationPolicy, PreparedUpdate, ReasonCode, VerificationSnapshot
from .manifest import ManifestError, canonical_json, manifest_hash, parse_image_ref, parse_version, validate_manifest
from .settings import DEFAULT_TRUSTED_IDENTITY, DEFAULT_TRUSTED_ISSUER

PROOF_ASSET = "future-agi-update-compatibility.json"
MAX_PROOF_BYTES = 256 * 1024
MAX_BACKUP_BYTES = 16 * 1024
MAX_DEPTH = 24
SUITES = ("no_migrations", "bidirectional_read_write", "interruption_fallback",
          "readiness_failure_fallback", "backup_restore")


class CompatibilityError(ManifestError):
    """Only a fixed reason code, never an untrusted value or parser diagnostic."""

    def __init__(self, reason: ReasonCode = ReasonCode.UNCERTIFIED_RELEASE) -> None:
        self.reason_code = reason
        super().__init__(reason.value)


def require(condition: bool, reason: ReasonCode = ReasonCode.UNCERTIFIED_RELEASE) -> None:
    if not condition:
        raise CompatibilityError(reason)


def plain(value: Any) -> Any:
    """Thaw the frozen S01 values; never return a caller-owned collection."""
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    return value


def _depth(value: Any, level: int = 0) -> None:
    require(level <= MAX_DEPTH)
    if isinstance(value, dict):
        require(all(type(key) is str for key in value))
        for item in value.values():
            _depth(item, level + 1)
    elif isinstance(value, list):
        for item in value:
            _depth(item, level + 1)


def decode_document(raw: bytes, limit: int = MAX_PROOF_BYTES) -> dict:
    """Bounded UTF-8 JSON; duplicates/nonfinite numbers are never evidence."""
    require(type(raw) is bytes and len(raw) <= limit)

    def pairs(items):
        result = {}
        for key, item in items:
            require(key not in result)
            result[key] = item
        return result

    def constant(_value):
        raise CompatibilityError()

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
        require(type(value) is dict)
        _depth(value)
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise CompatibilityError() from None


@lru_cache(maxsize=3)
def schema(name: str) -> dict:
    require(name in ("release-manifest.schema.json", "compatibility-proof.schema.json", "backup-proof.schema.json"))
    try:
        return json.loads(Path(__file__).with_name(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CompatibilityError(ReasonCode.UNSUPPORTED_CONTRACT) from None


def timestamp(value: str) -> datetime:
    try:
        require(type(value) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value) is not None)
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError):
        raise CompatibilityError() from None


def aware(value: datetime) -> datetime:
    require(isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None,
            ReasonCode.CLOCK_SKEW)
    return value.astimezone(timezone.utc)


def shape(value: Any, spec: Mapping[str, Any]) -> None:
    """Validate just the closed subset used by S02 proof/extension schemas.

    No external refs or optional jsonschema dependency. Semantic binding checks
    are separate; tests compare this subset to Draft202012Validator.
    """
    if "anyOf" in spec:
        for option in spec["anyOf"]:
            try:
                shape(value, option)
                return
            except CompatibilityError:
                pass
        raise CompatibilityError()
    if "type" in spec:
        require(type(value) is {"object": dict, "array": list, "string": str, "integer": int,
                                "boolean": bool, "null": type(None)}[spec["type"]])
    if "const" in spec:
        require(type(value) is type(spec["const"]) and value == spec["const"])
    if "enum" in spec:
        require(value in spec["enum"])
    if isinstance(value, dict):
        require(set(spec.get("required", ())) <= set(value))
        require(spec.get("minProperties", 0) <= len(value) <= spec.get("maxProperties", 4096))
        properties = spec.get("properties", {})
        for key, item in value.items():
            if "propertyNames" in spec:
                shape(key, spec["propertyNames"])
            if key in properties:
                shape(item, properties[key])
            else:
                additional = spec.get("additionalProperties", False)
                require(isinstance(additional, dict))
                shape(item, additional)
    elif isinstance(value, list):
        require(spec.get("minItems", 0) <= len(value) <= spec.get("maxItems", 4096))
        if spec.get("uniqueItems"):
            require(len({canonical_json(item) for item in value}) == len(value))
        for item in value:
            shape(item, spec["items"])
    elif isinstance(value, str):
        require(spec.get("minLength", 0) <= len(value) <= spec.get("maxLength", MAX_PROOF_BYTES))
        if "pattern" in spec:
            require(re.fullmatch(spec["pattern"], value) is not None)
        if spec.get("format") == "date-time":
            timestamp(value)
    elif type(value) is int:
        require(spec.get("minimum", value) <= value <= spec.get("maximum", value))


@dataclass(frozen=True)
class TopologyProfile:
    """Exhaustive builtin local persistence configuration, NOT host discovery.

    Adapters must positively match this layout/configuration (including bundled
    services) before setting topology_qualified. External stores and custom
    compose services are unsupported, not inferred to be stateless.
    """

    products: Tuple[Tuple[str, str], ...]
    dependencies: Tuple[str, ...]
    persistence: Tuple[Tuple[str, bool], ...]

    @property
    def domains(self) -> Tuple[str, ...]:
        return tuple(sorted(name for name, _ in self.persistence)) + ("component_protocols",)

    @property
    def backup_domains(self) -> Tuple[str, ...]:
        return tuple(sorted(name for name, persistent in self.persistence if persistent))

    @property
    def active_components(self) -> Tuple[str, ...]:
        return tuple(sorted({component for _, component in self.products}))


def topology_profile(mode: str, profiles: Any) -> TopologyProfile:
    """Checked-in compose layouts only; reject unknown/ambiguous profiles.

    Standalone redis explicitly has save/appendonly disabled (bin/start), while
    distributed redis, Kafka, MinIO, PeerDB and shared volumes are persistent.
    Even nonpersistent Redis needs read/write protocol compatibility evidence.
    A different persistence configuration requires a future qualified mapping.
    """
    require(isinstance(profiles, (list, tuple)) and all(type(p) is str for p in profiles),
            ReasonCode.UNSUPPORTED_INSTALLATION)
    selected = set(profiles)
    require(len(selected) == len(profiles), ReasonCode.UNSUPPORTED_INSTALLATION)
    if mode == "standalone":
        require(selected <= {"ml", "sandbox"}, ReasonCode.UNSUPPORTED_INSTALLATION)
        products = [("app", "standalone")]
        if "ml" in selected:
            products.append(("serving", "serving"))
        if "sandbox" in selected:
            products.append(("code-executor", "code_executor"))
        return TopologyProfile(tuple(sorted(products)), ("clickhouse", "postgres"), (
            ("postgres_app", True), ("clickhouse", True), ("object_store", True),
            ("temporal", True), ("redis", False), ("shared_files", True)))
    require(mode == "distributed" and selected <= {"workers", "observability", "peerdb", "all", "full"},
            ReasonCode.UNSUPPORTED_INSTALLATION)
    products = [("frontend", "frontend"), ("backend", "backend"), ("worker", "backend"),
                ("worker-exact-aggregation", "backend"),
                ("agentcc-gateway", "agentcc_gateway"), ("serving", "serving"),
                ("code-executor", "code_executor"), ("fi-collector", "fi_collector"),
                ("fi-property-catalog-consumer", "fi_collector"),
                ("postgres-schema-bootstrap", "backend"), ("clickhouse-native-bootstrap", "backend"),
                ("clickhouse-cdc-bootstrap", "backend"), ("peerdb-temporal-init", "backend"),
                ("peerdb-init", "backend")]
    if selected & {"workers", "all", "full"}:
        # Inherits queue-worker's profiles, unlike worker-exact-aggregation.
        products.append(("worker-simulation-runner", "simulation_runner"))
        products.extend((service, "backend") for service in (
            "worker-default", "worker-tasks-s", "worker-tasks-l", "worker-tasks-xl",
            "worker-trace-ingestion", "worker-agent-compass"))
    dependencies = ["postgres", "clickhouse", "property-catalog-clickhouse-bootstrap",
                    "property-catalog-kafka-volume-init", "property-catalog-runtime-volume-init",
                    "property-catalog-kafka", "property-catalog-topic-init", "redis", "minio", "temporal",
                    "peerdb-catalog", "peerdb-temporal", "peerdb-minio", "peerdb-flow-api",
                    "peerdb-flow-worker", "peerdb-flow-snapshot-worker", "peerdb-server"]
    if selected & {"observability", "all", "full"}:
        dependencies.extend(("temporal-ui", "temporal-admin-tools"))
    if selected & {"peerdb", "all", "full"}:
        dependencies.append("peerdb-ui")
    return TopologyProfile(tuple(sorted(products)), tuple(sorted(dependencies)), tuple(
        (domain, True) for domain in ("postgres_app", "clickhouse", "object_store", "temporal",
                                    "peerdb_catalog", "peerdb_temporal", "peerdb_objects", "redis",
                                    "kafka_catalog", "shared_files")))


def inputs_hash(manifest: Mapping[str, Any]) -> str:
    """No hash cycle: omit ONLY compatibility.validation from the agreed inputs."""
    source = plain(manifest)
    compatibility = dict(source["compatibility"])
    compatibility.pop("validation", None)
    value = {key: source[key] for key in ("version", "source_sha", "components", "helm", "deployment")}
    value["compatibility"] = compatibility
    return manifest_hash(value)


def _row_key(row: Mapping[str, Any]) -> tuple:
    return row["version"], row["mode"], row["platform"], tuple(sorted(row["profiles"]))


def validate_compatibility(manifest: Mapping[str, Any]) -> None:
    """Validate v2 metadata, not proof availability or signature authenticity."""
    compatibility = plain(manifest["compatibility"])
    shape(compatibility, schema("release-manifest.schema.json")["$defs"]["compatibility"])
    domains, rows, env = (compatibility[key] for key in ("state_domains", "predecessors", "required_env"))
    require(len({row["domain"] for row in domains}) == len(domains))
    require(len({_row_key(row) for row in rows}) == len(rows))
    require(len({row["name"] for row in env}) == len(env))
    affirmative = compatibility["automatic_apply"] or compatibility["automatic_rollback"]["strategy"] == "images_only"
    if affirmative:
        require(compatibility["automatic_apply"] and not compatibility["requires_operator_initialization"]
                and manifest["migration"]["requires_bootstrap"] is False)
        require(bool(domains) and bool(rows) and compatibility["validation"] is not None)
        required_domains = set()
        for row in rows:
            layout = topology_profile(row["mode"], row["profiles"])
            require(set(row["active_components"]) == set(layout.active_components))
            require(parse_version(manifest["minimum_version"]) <= parse_version(row["version"])
                    < parse_version(manifest["version"]))
            require(all(row["platform"] in manifest["components"][name].get("platforms", ())
                        for name in row["active_components"]))
            required_domains.update(layout.domains)
        require({row["domain"] for row in domains} == required_domains)
        require(all(row["from_epoch"] == row["to_epoch"] and row["backward_read"] and row["backward_write"]
                    for row in domains))
        if compatibility["automatic_rollback"]["strategy"] == "images_only":
            require(any(row["rollback_certified"] for row in rows))
    else:
        require(not any(row["rollback_certified"] for row in rows))
    if compatibility["validation"] is not None:
        require(compatibility["validation"]["inputs_hash"] == inputs_hash(manifest))


def validate_release_proof(raw: bytes, manifest: Mapping[str, Any], *, repository: str) -> dict:
    """Validate exact pinned bytes and all rows. Caller verifies the manifest first.

    A same-repo run number and a passing JSON claim alone prove nothing. S11
    obtains real evidence before signing; the signature authenticates this hash.
    No arbitrary URL, executable, stdout or exit-status substitute is accepted.
    """
    value = decode_document(raw)
    shape(value, schema("compatibility-proof.schema.json"))
    target = plain(manifest)
    validate_compatibility(target)
    compatibility = target["compatibility"]
    validation = compatibility["validation"]
    require(validation is not None and hashlib.sha256(raw).hexdigest() == validation["sha256"])
    require(value["inputs_hash"] == inputs_hash(target) == validation["inputs_hash"])
    require(value["run_id"] == validation["run_id"] and value["repository"] == repository)
    require(value["tag"] == target["version"] and value["source_sha"] == target["source_sha"])
    require(value["components"] == target["components"] and value["deployment"] == target["deployment"])
    require(value["state_domains"] == compatibility["state_domains"])
    rows = value["predecessors"]
    require(len(rows) == len(compatibility["predecessors"]))
    require({canonical_json(row["predecessor"]) for row in rows}
            == {canonical_json(row) for row in compatibility["predecessors"]})
    for row in rows:
        previous = row["predecessor"]
        layout = topology_profile(previous["mode"], previous["profiles"])
        require(row["persistence"] == dict(layout.persistence))
        require(set(row["suites"]) == set(SUITES) and all(result == "passed" for result in row["suites"].values()))
        dependencies = row["dependencies"]
        require(len(dependencies) == len(layout.dependencies)
                and {item["service"] for item in dependencies} == set(layout.dependencies))
        for item in dependencies:
            ref = parse_image_ref(item["image"])
            require(str(ref) == item["image"] and ref.tag is not None and ref.digest is None)
        # No deployment replacement is supported by the bounded images-only path.
        require(row["predecessor_deployment"] == target["deployment"])
        for name, component in row["predecessor_components"].items():
            old = parse_image_ref(component["image"])
            new = parse_image_ref(target["components"][name]["image"])
            require(old.name == new.name and old.tag == previous["version"] + ("-gpu" if name == "serving_gpu" else ""))
            if name in previous["active_components"]:
                require(previous["platform"] in component.get("platforms", ()))
    return value


@dataclass(frozen=True)
class Eligibility:
    allowed: bool
    reason_codes: Tuple[ReasonCode, ...] = ()
    backup_pending: bool = False
    fingerprint: Optional[str] = field(default=None, repr=False)

    def public_summary(self) -> dict:
        return {"eligible": self.allowed, "reason_codes": [code.value for code in self.reason_codes]}


@dataclass(frozen=True)
class Controls:
    """Effective, durable control snapshot, not just update.conf defaults."""

    policy: AutomationPolicy = field(default_factory=AutomationPolicy)
    enabled: bool = False
    paused: bool = True
    registered: bool = False
    window_open: bool = False
    start_budget_available: bool = False
    trusted_identity: str = DEFAULT_TRUSTED_IDENTITY
    trusted_issuer: str = DEFAULT_TRUSTED_ISSUER


@dataclass(frozen=True)
class RuntimeImage:
    image: str
    digest: str
    image_id: str


@dataclass(frozen=True, repr=False)
class Observations:
    """Private in-lock adapter evidence. Missing/unknown facts default to refusal.

    Image maps cover ALL services (including one-shot jobs) and ALL replicas.
    Dependency pins come from the signed proof and matched deployment files,
    never a host's mutable tag resolution alone. env contains effective selected
    key values and must never reach public diagnostics. No executable probes.
    """

    now: datetime
    predecessor_raw: bytes = b""
    predecessor_verification: Optional[VerificationSnapshot] = None
    release_proof: bytes = b""
    platform: str = ""
    product_images: Mapping[str, Tuple[RuntimeImage, ...]] = field(default_factory=dict)
    dependency_images: Mapping[str, Tuple[RuntimeImage, ...]] = field(default_factory=dict)
    state_epochs: Mapping[str, int] = field(default_factory=dict)
    persistence: Mapping[str, bool] = field(default_factory=dict)
    deployment_files: Mapping[str, str] = field(default_factory=dict)
    env: Mapping[str, str] = field(default_factory=dict)
    installation_fingerprint: str = ""
    config_fingerprint: str = ""
    profile_fingerprint: str = ""
    trust_fingerprint: str = ""
    attempt_id: str = ""
    reserved_at: Optional[datetime] = None
    boundary: str = "before_env_commit"
    online_verified: bool = False
    owner_confirmed: bool = False
    topology_qualified: bool = False
    runtime_complete: bool = False
    baseline_attested: bool = False
    state_unchanged: bool = False
    graph_unchanged: bool = False
    no_pending_transaction: bool = False
    no_failed_target: bool = False
    env_unchanged: bool = False
    no_source_build: bool = False
    no_pending_migrations: bool = False


@dataclass(frozen=True, repr=False)
class Attempt:
    """The SAME live scheduled attempt, not an arbitrary historical receipt.

    Runner must CAS-reserve fallback with Eligibility.fingerprint and recheck
    under update.lock. This pure evaluator cannot enforce a reservation itself.
    """

    prepared: PreparedUpdate
    attempt_id: str
    reserved_at: datetime
    backup_raw: bytes
    first_mutation_at: datetime
    rollback_enabled_before_start: bool = False
    automatic: bool = False
    live: bool = False
    latest_attempt_id: str = ""
    failure_kind: str = "unknown"
    child_state_known: bool = False
    fallback_count: int = 0
    prior_images: Mapping[str, Tuple[RuntimeImage, ...]] = field(default_factory=dict)
    prior_images_available: bool = False
    receipt_matches: bool = False


def _verified(verification: Optional[VerificationSnapshot], version: str, controls: Controls,
              now: datetime, *, fresh: bool) -> None:
    require(isinstance(verification, VerificationSnapshot) and verification.verified is True,
            ReasonCode.SIGNATURE_REJECTED)
    # Verifier owns certificate repository/ref/issuer checks. Never accept a
    # self-declared manifest.signature or a public status verification string.
    require(verification.method == "keyless" and verification.signer == controls.trusted_identity.format(version=version)
            and verification.issuer == controls.trusted_issuer and bool(verification.tool), ReasonCode.SIGNATURE_REJECTED)
    age = (aware(now) - aware(verification.checked_at)).total_seconds()
    require(age >= 0, ReasonCode.CLOCK_SKEW)
    require(not fresh or age <= 300, ReasonCode.SIGNATURE_REJECTED)


def _repository(controls: Controls, version: str) -> str:
    match = re.fullmatch(r"https://github.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/\.github/workflows/[^/]+@refs/tags/"
                         + re.escape(version), controls.trusted_identity.format(version=version))
    require(match is not None, ReasonCode.SIGNATURE_REJECTED)
    return match.group(1)


def _images(observed: Mapping[str, Tuple[RuntimeImage, ...]], expected: Mapping[str, Mapping[str, str]]) -> None:
    require(set(observed) == set(expected), ReasonCode.RUNNING_VERSIONS_UNKNOWN)
    for service, pin in expected.items():
        images = observed[service]
        require(isinstance(images, (tuple, list)) and bool(images), ReasonCode.RUNNING_VERSIONS_UNKNOWN)
        for image in images:
            require(isinstance(image, RuntimeImage), ReasonCode.RUNNING_VERSIONS_UNKNOWN)
            ref, wanted = parse_image_ref(image.image), parse_image_ref(pin["image"])
            require(ref.name == wanted.name and ref.tag == wanted.tag
                    and ref.digest in (None, pin["digest"]) and image.digest == pin["digest"]
                    and re.fullmatch(r"sha256:[0-9a-f]{64}", image.image_id) is not None,
                    ReasonCode.IMAGE_DIGEST_MISMATCH)


def _common(prepared: PreparedUpdate, obs: Observations, controls: Controls, *, rollback: bool = False):
    require(isinstance(prepared, PreparedUpdate) and isinstance(obs, Observations) and isinstance(controls, Controls))
    target = validate_manifest(decode_document(prepared.raw_manifest, 1024 * 1024))
    require(target == plain(prepared.manifest), ReasonCode.IMMUTABLE_RELEASE_CHANGED)
    require(hashlib.sha256(prepared.raw_manifest).hexdigest() == prepared.manifest_sha256
            and hashlib.sha256(prepared.raw_bundle).hexdigest() == prepared.bundle_sha256, ReasonCode.IMMUTABLE_RELEASE_CHANGED)
    require(target["schema_version"] == 2, ReasonCode.UNCERTIFIED_RELEASE)
    _verified(prepared.verification, target["version"], controls, obs.now, fresh=not rollback)
    require(obs.online_verified is True or rollback, ReasonCode.SIGNATURE_REJECTED)
    previous = validate_manifest(decode_document(obs.predecessor_raw, 1024 * 1024))
    _verified(obs.predecessor_verification, previous["version"], controls, obs.now, fresh=False)
    require(obs.owner_confirmed is True and controls.registered is True, ReasonCode.OWNERSHIP_UNCONFIRMED)
    require(not prepared.binding.production, ReasonCode.INITIALIZATION_REQUIRED)
    require(obs.topology_qualified is True and obs.no_source_build is True, ReasonCode.UNSUPPORTED_INSTALLATION)
    require(obs.no_pending_transaction is True, ReasonCode.RECOVERY_REQUIRED)
    require(obs.baseline_attested is True and obs.state_unchanged is True and obs.no_pending_migrations is True,
            ReasonCode.INITIALIZATION_REQUIRED)
    require(obs.graph_unchanged is True and obs.env_unchanged is True, ReasonCode.CONFIGURATION_CHANGED)
    for observed, expected in ((obs.installation_fingerprint, prepared.binding.installation_fingerprint),
                               (obs.config_fingerprint, prepared.config_fingerprint),
                               (obs.profile_fingerprint, prepared.profile_fingerprint),
                               (obs.trust_fingerprint, prepared.trust_fingerprint)):
        require(bool(observed) and observed == expected, ReasonCode.CONFIGURATION_CHANGED)
    compatibility = target["compatibility"]
    require(compatibility["automatic_apply"] is True, ReasonCode.UNCERTIFIED_RELEASE)
    require(not compatibility["requires_operator_initialization"] and not target["migration"]["requires_bootstrap"],
            ReasonCode.INITIALIZATION_REQUIRED)
    layout = topology_profile(prepared.binding.topology, prepared.binding.profiles)
    require(obs.runtime_complete is True, ReasonCode.RUNNING_VERSIONS_UNKNOWN)
    proof = validate_release_proof(obs.release_proof, target, repository=_repository(controls, target["version"]))
    matches = [row for row in proof["predecessors"] if (
        row["predecessor"]["version"] == previous["version"]
        and row["predecessor"]["mode"] == prepared.binding.topology
        and row["predecessor"]["platform"] == obs.platform
        and set(row["predecessor"]["profiles"]) == set(prepared.binding.profiles))]
    require(len(matches) == 1)
    row = matches[0]
    predecessor = row["predecessor"]
    require(predecessor["manifest_hash"] == manifest_hash(previous)
            and predecessor["raw_manifest_sha256"] == hashlib.sha256(obs.predecessor_raw).hexdigest()
            and predecessor["source_sha"] == previous["source_sha"], ReasonCode.IMMUTABLE_RELEASE_CHANGED)
    require(row["predecessor_components"] == previous["components"], ReasonCode.IMAGE_DIGEST_MISMATCH)
    require(row["predecessor_deployment"] == previous["deployment"] == target["deployment"]
            and dict(obs.deployment_files) == target["deployment"]["files"], ReasonCode.DEPLOYMENT_FILES_MISMATCH)
    require(dict(obs.persistence) == row["persistence"], ReasonCode.UNSUPPORTED_INSTALLATION)
    expected_epochs = {item["domain"]: item["from_epoch"] for item in compatibility["state_domains"]
                       if item["domain"] in layout.domains}
    require(all(type(v) is int for v in obs.state_epochs.values()) and dict(obs.state_epochs) == expected_epochs,
            ReasonCode.INITIALIZATION_REQUIRED)
    deps = {item["service"]: item for item in row["dependencies"]}
    _images(obs.dependency_images, deps)
    _images(obs.product_images, {service: (target if rollback else previous)["components"][component]
                                for service, component in layout.products})
    for env in compatibility["required_env"]:
        if set(env["components"]) & set(layout.active_components):
            require(env["name"] in obs.env and type(obs.env[env["name"]]) is str
                    and (env["rule"] == "present" or bool(obs.env[env["name"]].strip())), ReasonCode.REQUIRED_ENV_MISSING)
    return target, previous, layout, row


def _fingerprint(prepared: PreparedUpdate, obs: Observations, backup: Optional[bytes], *, prior_images=None) -> str:
    def images(values):
        # Inspection order is not identity. Explicit fields also prevent a
        # future private RuntimeImage field from silently entering this binding.
        return {service: [{"image": image.image, "digest": image.digest, "image_id": image.image_id}
                          for image in sorted(replicas, key=lambda item: (item.image, item.digest, item.image_id))]
                for service, replicas in values.items()}

    return manifest_hash({
        "attempt": obs.attempt_id, "target": prepared.manifest_sha256, "bundle": prepared.bundle_sha256,
        "previous": hashlib.sha256(obs.predecessor_raw).hexdigest(),
        "release_proof": hashlib.sha256(obs.release_proof).hexdigest(),
        "backup": None if backup is None else hashlib.sha256(backup).hexdigest(),
        "installation": obs.installation_fingerprint, "config": obs.config_fingerprint,
        "profile": obs.profile_fingerprint, "trust": obs.trust_fingerprint,
        "epochs": dict(obs.state_epochs), "persistence": dict(obs.persistence),
        "products": images(obs.product_images), "dependencies": images(obs.dependency_images),
        "prior_images": None if prior_images is None else images(prior_images),
    })


def _result_error(exc: Exception) -> Eligibility:
    reason = exc.reason_code if isinstance(exc, CompatibilityError) else ReasonCode.UNCERTIFIED_RELEASE
    return Eligibility(False, (reason,))


def evaluate_automatic_apply(prepared: PreparedUpdate, observations: Observations, controls: Controls,
                             proof: Any) -> Eligibility:
    """All-of assessment; proof='pending' ONLY before_backup, bytes otherwise.

    Pending means eligible to take a backup, NEVER eligible to mutate env/images.
    This does not replace the ordinary core plan, manual backup requirements or
    guarded filesystem qualification. No raw exception text escapes.
    """
    try:
        require(controls.policy.mode == "apply" and controls.enabled is True and controls.paused is False,
                ReasonCode.CONTROL_INVALID)
        require(controls.window_open is True, ReasonCode.OUTSIDE_WINDOW)
        require(controls.start_budget_available is True, ReasonCode.INSUFFICIENT_WINDOW)
        require(observations.no_failed_target is True, ReasonCode.FAILED_TARGET_LATCHED)
        target, previous, layout, _ = _common(prepared, observations, controls)
        plan = prepared.plan
        require(plan.get("decision") == "update_available" and plan.get("resume") is False
                and plan.get("target_version") == target["version"] and plan.get("manifest_hash") == manifest_hash(target)
                and not plan.get("reasons") and not plan.get("deployment_drift") and not plan.get("missing_env_keys")
                and not plan.get("staged_version") and not plan.get("stopped_version"), ReasonCode.CONFIGURATION_CHANGED)
        require(target["channel"] == controls.policy.channel, ReasonCode.CHANNEL_MISMATCH)
        old, new = parse_version(previous["version"]), parse_version(target["version"])
        require(old < new and old >= parse_version(target["minimum_version"]), ReasonCode.VERSION_POLICY_BLOCKED)
        require(controls.policy.version_policy == "major" or old[0] == new[0], ReasonCode.VERSION_POLICY_BLOCKED)
        require(controls.policy.version_policy != "patch" or old[:2] == new[:2], ReasonCode.VERSION_POLICY_BLOCKED)
        pending = type(proof) is str and proof == "pending"
        if pending:
            require(observations.boundary == "before_backup", ReasonCode.BACKUP_REQUIRED)
        else:
            from .backup_proof import BackupExpectation, validate_backup_proof
            expected = BackupExpectation(observations.attempt_id, observations.installation_fingerprint,
                                         manifest_hash(target), previous["version"], observations.reserved_at,
                                         layout.backup_domains)
            validate_backup_proof(proof, expected, now=observations.now,
                                  max_age_seconds=controls.policy.backup_max_age_seconds)
        require(observations.boundary in ("before_backup", "after_backup", "before_env_commit", "before_rollout"))
        return Eligibility(True, backup_pending=pending,
                           fingerprint=_fingerprint(prepared, observations, None if pending else proof))
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        return _result_error(exc)


def evaluate_automatic_rollback(attempt: Attempt, observations: Observations, controls: Controls) -> Eligibility:
    """One exact completed forward failure, images only; NEVER database restore.

    Pause/window closure after rollout does not cancel the already-authorized
    safety action. Unknown child state/interruption/timeouts never qualify.
    Original proof is checked at first mutation, not relabeled after migration.
    """
    try:
        require(isinstance(attempt, Attempt) and observations.boundary == "before_fallback", ReasonCode.ROLLBACK_INELIGIBLE)
        require(attempt.automatic is True and attempt.live is True and attempt.rollback_enabled_before_start is True
                and controls.policy.automatic_rollback is True, ReasonCode.ROLLBACK_INELIGIBLE)
        require(attempt.attempt_id == attempt.latest_attempt_id == observations.attempt_id
                and attempt.reserved_at == observations.reserved_at
                and attempt.receipt_matches is True and attempt.prior_images_available is True,
                ReasonCode.ROLLBACK_INELIGIBLE)
        require(type(attempt.fallback_count) is int and attempt.fallback_count == 0
                and attempt.failure_kind in ("rollout_failed", "readiness_failed") and attempt.child_state_known is True,
                ReasonCode.ROLLBACK_INELIGIBLE)
        target, previous, layout, row = _common(attempt.prepared, observations, controls, rollback=True)
        require(target["compatibility"]["automatic_rollback"]["strategy"] == "images_only"
                and row["predecessor"]["rollback_certified"] is True, ReasonCode.ROLLBACK_INELIGIBLE)
        _images(attempt.prior_images, {service: previous["components"][component] for service, component in layout.products})
        require(aware(attempt.reserved_at) <= aware(attempt.first_mutation_at) <= aware(observations.now), ReasonCode.CLOCK_SKEW)
        from .backup_proof import BackupExpectation, validate_backup_proof
        expected = BackupExpectation(attempt.attempt_id, observations.installation_fingerprint, manifest_hash(target),
                                     previous["version"], attempt.reserved_at, layout.backup_domains)
        validate_backup_proof(attempt.backup_raw, expected, now=attempt.first_mutation_at,
                              max_age_seconds=controls.policy.backup_max_age_seconds)
        return Eligibility(True, fingerprint=_fingerprint(attempt.prepared, observations, attempt.backup_raw,
                                                         prior_images=attempt.prior_images))
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        return _result_error(exc)
