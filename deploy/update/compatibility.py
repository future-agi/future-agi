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
import math
import re
from fractions import Fraction
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

from .contracts import (AutomationPolicy, PreparedUpdate, ReasonCode, VerificationSnapshot, AuthorisedTransition,
                        BackupSeal, ColdHold, RuntimeObservationV2, RuntimeImage, MigrationEvidence, ContractError,
                        QUALIFICATION_POLICY, PERSISTENT_DOMAINS, installation_binding_hash,
                        TransitionMeasurements, BootstrapEnvelopeMeasurement, HostSizeObservation,
                        AutomaticUpdateEstimate, AutomaticUpdateDecision)
from .manifest import ManifestError, canonical_json, manifest_hash, parse_image_ref, parse_version, validate_manifest
from .settings import DEFAULT_TRUSTED_IDENTITY, DEFAULT_TRUSTED_ISSUER

PROOF_ASSET = "future-agi-update-compatibility.json"
MAX_PROOF_BYTES = 256 * 1024
MAX_BACKUP_BYTES = 16 * 1024
MAX_DEPTH = 24
LEGACY_SUITES = ("no_migrations", "bidirectional_read_write", "interruption_fallback",
          "readiness_failure_fallback", "backup_restore")
SUITES = ("source_snapshot", "migration_forward", "target_readiness", "failure_restore",
          "interruption_restore", "backup_integrity")


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
    elif type(value) is float:
        require(math.isfinite(value))  # Includes overflowed JSON exponent literals.


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
    """Validate a document without changing legacy integer-token semantics.

    Unreleased v2 uses mathematical JSON integers. Legacy backup/manual readers
    intentionally retain their strict integer-token checks, including 1.0 refusal.
    """
    version = value.get("schema_version", value.get("contract_version")) if isinstance(value, dict) else None
    _shape(value, spec, json_integers=type(version) in (int, float) and version == 2)


def _shape(value: Any, spec: Mapping[str, Any], *, json_integers: bool) -> None:
    """Validate just the closed subset used by S02 proof/extension schemas.

    No external refs or optional jsonschema dependency. Semantic binding checks
    are separate; tests compare this subset to Draft202012Validator.
    """
    for union in ("anyOf", "oneOf"):
        if union in spec:
            matches = 0
            for option in spec[union]:
                try:
                    _shape(value, option, json_integers=json_integers)
                    matches += 1
                except CompatibilityError:
                    pass
            require(matches == 1 if union == "oneOf" else matches > 0)
    for option in spec.get("allOf", ()):
        _shape(value, option, json_integers=json_integers)
    if "not" in spec:
        try:
            _shape(value, spec["not"], json_integers=json_integers)
        except CompatibilityError:
            pass
        else:
            raise CompatibilityError()
    if "if" in spec:
        try:
            _shape(value, spec["if"], json_integers=json_integers)
            branch = "then"
        except CompatibilityError:
            branch = "else"
        _shape(value, spec.get(branch, {}), json_integers=json_integers)
    if type(value) is float:
        require(math.isfinite(value))
    if "type" in spec:
        kind = spec["type"]
        if kind == "integer":
            # JSON Schema defines integer by value, not JSON/Python spelling.
            require(type(value) is int or (json_integers and type(value) is float and value.is_integer()))
        elif kind == "number":
            require(type(value) in (int, float))
        else:
            require(type(value) is {"object": dict, "array": list, "string": str,
                                    "boolean": bool, "null": type(None)}[kind])
    if "const" in spec:
        expected = spec["const"]
        numeric = json_integers and type(value) in (int, float) and type(expected) in (int, float)
        require((numeric or type(value) is type(expected)) and value == expected)
    if "enum" in spec:
        require(any((type(value) is type(item) or (type(value) in (int, float) and type(item) in (int, float)))
                    and value == item for item in spec["enum"]))
    if isinstance(value, dict) and any(k in spec for k in ("properties", "required", "additionalProperties", "propertyNames", "minProperties", "maxProperties")):
        require(set(spec.get("required", ())) <= set(value))
        require(spec.get("minProperties", 0) <= len(value) <= spec.get("maxProperties", 4096))
        properties = spec.get("properties", {})
        for key, item in value.items():
            if "propertyNames" in spec:
                _shape(key, spec["propertyNames"], json_integers=json_integers)
            if key in properties:
                _shape(item, properties[key], json_integers=json_integers)
            else:
                additional = spec.get("additionalProperties", True)
                require(additional is not False)
                if isinstance(additional, dict):
                    _shape(item, additional, json_integers=json_integers)
    elif isinstance(value, list):
        require(spec.get("minItems", 0) <= len(value) <= spec.get("maxItems", 4096))
        if spec.get("uniqueItems"):
            require(len({canonical_json(item) for item in value}) == len(value))
        for item in value:
            _shape(item, spec.get("items", {}), json_integers=json_integers)
    elif isinstance(value, str):
        require(spec.get("minLength", 0) <= len(value) <= spec.get("maxLength", MAX_PROOF_BYTES))
        if "pattern" in spec:
            require(re.fullmatch(spec["pattern"], value) is not None)
        if spec.get("format") == "date-time":
            timestamp(value)
    elif type(value) in (int, float):
        require(spec.get("minimum", value) <= value <= spec.get("maximum", value))
        if "exclusiveMinimum" in spec:
            require(value > spec["exclusiveMinimum"])
        if "exclusiveMaximum" in spec:
            require(value < spec["exclusiveMaximum"])


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
    if compatibility["contract_version"] == 2:
        # The frozen migration object is qualification input, never rewritten to
        # hide requires_bootstrap. Legacy hashes retain their original meaning.
        value["migration"] = source["migration"]
    return manifest_hash(value)


def _row_key(row: Mapping[str, Any]) -> tuple:
    return (row["version"], row["mode"], row["platform"], tuple(sorted(row["profiles"])),
            row.get("execution_deployment_hash"))


def _validate_legacy_compatibility(manifest: Mapping[str, Any]) -> None:
    """Validate v2 metadata, not proof availability or signature authenticity."""
    compatibility = plain(manifest["compatibility"])
    shape(compatibility, schema("release-manifest.schema.json")["$defs"]["compatibility"])
    domains, rows, env = (compatibility[key] for key in ("state_domains", "predecessors", "required_env"))
    require(len({row["domain"] for row in domains}) == len(domains))
    require(len({_row_key(row) for row in rows}) == len(rows))
    require(all(("execution_deployment_hash" in row) == (compatibility["contract_version"] == 2) for row in rows))
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
    require(value["schema_version"] == compatibility["contract_version"])
    validation = compatibility["validation"]
    require(validation is not None and hashlib.sha256(raw).hexdigest() == validation["sha256"])
    require(value["inputs_hash"] == inputs_hash(target) == validation["inputs_hash"])
    require(value["run_id"] == validation["run_id"] and value["repository"] == repository)
    require(value["tag"] == target["version"] and value["source_sha"] == target["source_sha"])
    require(value["components"] == target["components"] and value["deployment"] == target["deployment"])
    if value["schema_version"] == 1:
        require(value["state_domains"] == compatibility["state_domains"])
    else:
        for key in ("qualification_policy", "restore_procedure_id", "backup_inventory_version",
                    "host_observation_contract", "runtime_activation_contract"):
            require(value[key] == compatibility[key])
    rows = value["predecessors"]
    require(len(rows) == len(compatibility["predecessors"]))
    require(len({_row_key(row["predecessor"]) for row in rows}) == len(rows))
    require({canonical_json(row["predecessor"]) for row in rows}
            == {canonical_json(row) for row in compatibility["predecessors"]})
    for row in rows:
        previous = row["predecessor"]
        layout = topology_profile(previous["mode"], previous["profiles"])
        require(row["persistence"] == dict(layout.persistence))
        require(set(row["suites"]) == set(SUITES if value["schema_version"] == 2 else LEGACY_SUITES) and all(result == "passed" for result in row["suites"].values()))
        dependencies = row["dependencies"]
        require(len(dependencies) == len(layout.dependencies)
                and {item["service"] for item in dependencies} == set(layout.dependencies))
        for item in dependencies:
            ref = parse_image_ref(item["image"])
            require(str(ref) == item["image"] and ref.tag is not None and ref.digest is None)
        # Contract 2 certifies exact retained physical bytes, not a checkout swap.
        require(("execution_deployment" in row) == (value["schema_version"] == 2))
        if value["schema_version"] == 2:
            require(manifest_hash(row["execution_deployment"]) == previous["execution_deployment_hash"],
                    ReasonCode.DEPLOYMENT_FILES_MISMATCH)
            require(previous["restore_certified"] is True, ReasonCode.RESTORE_NOT_CERTIFIED)
            require(previous["mode"] == "standalone" and previous["profiles"] == [], ReasonCode.UNSUPPORTED_INSTALLATION)
            require(set(row["persistent_coverage"]) == set(layout.backup_domains), ReasonCode.BACKUP_INVENTORY_MISMATCH)
            transition_measurements(row)  # A6: exact pending-plan coverage and finite measured rates.
        else:
            require(row["predecessor_deployment"] == target["deployment"])
        for name, component in row["predecessor_components"].items():
            old = parse_image_ref(component["image"])
            new = parse_image_ref(target["components"][name]["image"])
            require(old.name == new.name and old.tag == previous["version"] + ("-gpu" if name == "serving_gpu" else ""))
            if name in previous["active_components"]:
                require(previous["platform"] in component.get("platforms", ()))
    return value


@dataclass(frozen=True, repr=False)
class DeploymentAuthorization:
    """Exact signed execution row for later planner integration, never skip-drift."""
    target_manifest_sha256: str
    predecessor_raw_sha256: str
    execution_deployment: Mapping[str, Any]
    row: Mapping[str, Any]

    def __post_init__(self):
        from .contracts import _freeze
        object.__setattr__(self, "execution_deployment", _freeze(self.execution_deployment))
        object.__setattr__(self, "row", _freeze(self.row))


def authorize_execution(target_raw: bytes, predecessor_raw: bytes, proof_raw: bytes, *, repository: str,
                        mode: str, profiles: tuple, platform: str,
                        deployment_files: Mapping[str, str]) -> DeploymentAuthorization:
    """Caller authenticates both manifests. Select one exact, fully bound row."""
    target = validate_manifest(decode_document(target_raw, 1024 * 1024))
    previous = validate_manifest(decode_document(predecessor_raw, 1024 * 1024))
    proof = validate_release_proof(proof_raw, target, repository=repository)
    execution = {"files": dict(deployment_files)}
    row = _select_row(proof, previous, mode=mode, profiles=profiles, platform=platform, deployment_files=deployment_files)
    predecessor = row["predecessor"]
    require(predecessor["manifest_hash"] == manifest_hash(previous)
            and predecessor["raw_manifest_sha256"] == hashlib.sha256(predecessor_raw).hexdigest()
            and predecessor["source_sha"] == previous["source_sha"], ReasonCode.IMMUTABLE_RELEASE_CHANGED)
    require(row["predecessor_components"] == previous["components"], ReasonCode.IMAGE_DIGEST_MISMATCH)
    require(row["predecessor_deployment"] == previous["deployment"], ReasonCode.DEPLOYMENT_FILES_MISMATCH)
    return DeploymentAuthorization(hashlib.sha256(target_raw).hexdigest(), hashlib.sha256(predecessor_raw).hexdigest(),
                                   execution, row)


@dataclass(frozen=True)
class Eligibility:
    allowed: bool
    reason_codes: Tuple[ReasonCode, ...] = ()
    backup_pending: bool = False
    fingerprint: Optional[str] = field(default=None, repr=False)
    transition: Optional[AuthorisedTransition] = field(default=None, repr=False)

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
    public_key_sha256: str = ""
    repository: str = "future-agi/future-agi"


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
    no_pending_migrations: bool = False  # Legacy observation field, not a v2 admission predicate.
    runtime_observation: Optional[RuntimeObservationV2] = None
    cold_hold: Optional[ColdHold] = None
    backup_seal: Optional[BackupSeal] = None
    executor_sha256: str = ""
    binding_sha256: str = ""
    operation_token: str = ""
    run_id: str = ""


@dataclass(frozen=True, repr=False)
class Attempt:
    """Deprecated inert import compatibility for the old orchestrator only.

    No instance authorizes automatic image rollback. Remove with its consumers.
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
    from .trust import matches_trust
    require(matches_trust(verification, version, identity=controls.trusted_identity, issuer=controls.trusted_issuer,
                          public_key_sha256=controls.public_key_sha256), ReasonCode.SIGNATURE_REJECTED)
    age = (aware(now) - aware(verification.checked_at)).total_seconds()
    require(age >= 0, ReasonCode.CLOCK_SKEW)
    require(not fresh or age <= 300, ReasonCode.SIGNATURE_REJECTED)


def _repository(controls: Controls, version: str) -> str:
    if controls.public_key_sha256:
        require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", controls.repository) is not None,
                ReasonCode.SIGNATURE_REJECTED)
        return controls.repository
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


def validate_compatibility(manifest: Mapping[str, Any]) -> None:
    """Readable v1; closed restore-backed v2. Metadata alone is not admission."""
    compatibility = plain(manifest["compatibility"])
    shape(compatibility, schema("release-manifest.schema.json")["$defs"]["compatibility"])
    if compatibility["contract_version"] == 1:
        return _validate_legacy_compatibility(manifest)
    rows, env = compatibility["predecessors"], compatibility["required_env"]
    require(len({_row_key(row) for row in rows}) == len(rows))
    require(len({row["name"] for row in env}) == len(env))
    affirmative = compatibility["automatic_apply"]
    require(compatibility["automatic_rollback"]["strategy"] == ("restore_backup" if affirmative else "never"),
            ReasonCode.RESTORE_NOT_CERTIFIED)
    if affirmative:
        require(not compatibility["requires_operator_initialization"], ReasonCode.INITIALIZATION_REQUIRED)
        require(bool(rows) and compatibility["validation"] is not None, ReasonCode.RESTORE_NOT_CERTIFIED)
    for row in rows:
        require(row["restore_certified"] is affirmative, ReasonCode.RESTORE_NOT_CERTIFIED)
        require(row["mode"] == "standalone" and row["profiles"] == []
                and row["active_components"] == ["standalone"], ReasonCode.UNSUPPORTED_INSTALLATION)
        require(parse_version(manifest["minimum_version"]) <= parse_version(row["version"])
                < parse_version(manifest["version"]))
        require(row["platform"] in manifest["components"]["standalone"].get("platforms", ()),
                ReasonCode.PLATFORM_NOT_REHEARSED)
    if compatibility["validation"] is not None:
        require(compatibility["validation"]["inputs_hash"] == inputs_hash(manifest))


def _migration_evidence(row: Mapping[str, Any]) -> MigrationEvidence:
    return MigrationEvidence(**row["migration_evidence"])


def transition_measurements(row: Mapping[str, Any]) -> TransitionMeasurements:
    """Extract a row's A6 data; callers must authenticate/validate the proof first.

    This constructor alone is NOT release authentication or host admission.
    """
    try:
        return TransitionMeasurements(**{key: row[key] for key in (
            "migration_evidence", "migration_measurements", "backup_measurement", "restore_measurement")})
    except (ValueError, TypeError, KeyError):
        raise CompatibilityError() from None


def estimate_automatic_update(measurements: TransitionMeasurements, host_sizes: HostSizeObservation,
                              policy: AutomationPolicy, *, window_remaining_seconds: float,
                              tick_remaining_seconds: float) -> AutomaticUpdateDecision:
    """A6 pure preflight, before stopping A. No I/O, clocks, SQL or mutation.

    Rates must come from the authenticated selected row, sizes from fixed host
    probes, and remaining budgets from the runner's real clocks. Missing facts
    refuse; this result neither reserves capacity nor authorizes execution.
    Fraction arithmetic avoids overflow and rounds all estimates UP. The safety
    factor is applied once to every measured duration/growth and transfer size.
    Migration measurements certify a reference workload, not a scaling law:
    every touched table's host rows AND bytes must fit its reference inputs.
    """
    try:
        require(isinstance(policy, AutomationPolicy) and policy.qualification_policy == QUALIFICATION_POLICY,
                ReasonCode.RESTORE_NOT_CERTIFIED)
        require(isinstance(measurements, TransitionMeasurements))
        require(isinstance(host_sizes, HostSizeObservation), ReasonCode.RESTORE_CAPACITY_UNKNOWN)
        for budget in (window_remaining_seconds, tick_remaining_seconds):
            require(type(budget) in (int, float) and budget >= 0
                    and (type(budget) is int or math.isfinite(budget)), ReasonCode.INSUFFICIENT_WINDOW)
        f = policy.estimate_safety_factor
        sizes = {(item.engine, item.table): item for item in host_sizes.table_sizes}
        volume_bytes = sum(host_sizes.volume_bytes.values())
        # Time scales on the signed sealed-tree basis, payload AND pathnames. The
        # restore workload (bytes + per-pathname allowance) is not a time bound
        # for pathname growth, so a side without that basis cannot be estimated.
        # Space below always keeps volume_bytes.
        for transfer in (measurements.backup_measurement, measurements.restore_measurement):
            require(None not in (host_sizes.transfer_bytes, host_sizes.transfer_members,
                                 transfer.reference_volume_members), ReasonCode.RESTORE_CAPACITY_UNKNOWN)
            require(all(not size or transfer.reference_volume_bytes[volume] > 0
                        for volume, size in host_sizes.transfer_bytes.items()), ReasonCode.RESTORE_CAPACITY_UNKNOWN)

        def ceiling(value):
            return -(-value.numerator // value.denominator)

        def transfer_seconds(transfer):
            # Aggregate throughput hides differences between volume roles. Scale
            # the whole reference duration by the largest role ratio, so every
            # role's contribution is covered even when the data mix changes.
            # Archive, extract, fsync and inventory run per pathname; payload is
            # read once per hardlink group. Bound both by the largest role ratio.
            scale = max([Fraction(1)] + [Fraction(host_sizes.transfer_bytes[role], reference)
                        for role, reference in transfer.reference_volume_bytes.items() if reference]
                        + [Fraction(host_sizes.transfer_members[role], reference)
                           for role, reference in transfer.reference_volume_members.items()])
            reference_duration = (Fraction(sum(transfer.reference_volume_bytes.values()))
                                  / Fraction(transfer.throughput_bytes_per_second))
            return ceiling(f * reference_duration * scale)

        migration_seconds, extra_disk = Fraction(0), Fraction(0)
        migration_data = measurements.migration_measurements
        if isinstance(migration_data, BootstrapEnvelopeMeasurement):
            # A's sizes cannot bound a later migration's input after an earlier
            # migration changed the same registered reference table.
            touched_tables = set()
            for scope in migration_data.migration_tables:
                for table in scope["tables"]:
                    key = (table["engine"], table["table"])
                    require(key not in touched_tables, ReasonCode.RESTORE_CAPACITY_UNKNOWN)
                    touched_tables.add(key)
            for table in migration_data.tables:
                actual = sizes.get((table.engine, table.table))
                require(actual is not None, ReasonCode.RESTORE_CAPACITY_UNKNOWN)
                require(actual.rows <= table.reference_rows and actual.bytes <= table.reference_bytes,
                        ReasonCode.RESTORE_CAPACITY_UNKNOWN)
            migration_seconds = f * Fraction(migration_data.duration_seconds)
            # Disk already includes physical WAL/temp; retaining their observed
            # peaks again is conservative and does not call them cumulative writes.
            extra_disk = f * (migration_data.peak_disk_growth_bytes + migration_data.wal_bytes
                              + migration_data.temp_bytes)
        else:
            touched_tables = set()
            for migration in migration_data:
                actual_work, reference_bytes = 0, 0
                for table in migration.tables:
                    key = (migration.engine, table.table)
                    # Host sizes describe A, not intermediate migration outputs.
                    # No retained output-row bound makes repeated input unsafe.
                    require(key not in touched_tables, ReasonCode.RESTORE_CAPACITY_UNKNOWN)
                    touched_tables.add(key)
                    actual = sizes.get(key)
                    require(actual is not None, ReasonCode.RESTORE_CAPACITY_UNKNOWN)
                    # Neither a measured rate nor a safety factor bounds scaling.
                    for count, reference in ((actual.rows, table.reference_rows), (actual.bytes, table.reference_bytes)):
                        require(count <= reference, ReasonCode.RESTORE_CAPACITY_UNKNOWN)
                    actual_work += actual.rows if migration.throughput.unit == "rows_per_second" else actual.bytes
                    reference_bytes += table.reference_bytes
                migration_seconds += f * max(Fraction(migration.duration_seconds),
                                               Fraction(actual_work) / Fraction(migration.throughput.value))
                # Retain every reference peak without assuming reclamation.
                extra_disk += f * ((Fraction(migration.peak_disk_amplification_factor) - 1) * reference_bytes
                                   + migration.wal_bytes + migration.temp_bytes)
        backup, restore = (transfer_seconds(t) for t in (measurements.backup_measurement, measurements.restore_measurement))
        migration, growth = ceiling(migration_seconds), ceiling(extra_disk)
        reserve = volume_bytes * f + policy.disk_watermark_reserve_bytes
        backup_required = volume_bytes * f + policy.disk_watermark_reserve_bytes
        docker_base = reserve + (backup_required if host_sizes.backup_shares_docker_filesystem else 0)
        overhead = policy.stability_period_seconds + policy.quiescence_reserve_seconds + policy.metadata_reserve_seconds
        forward = backup + migration + policy.wait_timeout_seconds + overhead
        recovery = restore + policy.recovery_startup_timeout_seconds + overhead
        estimate = AutomaticUpdateEstimate(backup, migration, restore, growth, backup_required, docker_base + growth,
            reserve, forward, recovery, forward + recovery, min(migration, policy.migration_timeout_seconds),
            policy.migration_stall_timeout_seconds)
        reasons = []
        budget = min(Fraction(window_remaining_seconds), Fraction(tick_remaining_seconds))
        if (backup > policy.backup_timeout_seconds or restore > policy.restore_timeout_seconds
                or forward + recovery - migration > budget):
            reasons.append(ReasonCode.BACKUP_EXCEEDS_WINDOW)
        if migration > policy.migration_timeout_seconds or (migration and forward + recovery > budget):
            reasons.append(ReasonCode.MIGRATION_EXCEEDS_WINDOW)
        if host_sizes.backup_free_bytes < backup_required or host_sizes.docker_free_bytes < docker_base:
            reasons.append(ReasonCode.INSUFFICIENT_DISK_FOR_BACKUP)
        if growth and host_sizes.docker_free_bytes < docker_base + growth:
            reasons.append(ReasonCode.INSUFFICIENT_DISK_FOR_MIGRATION)
        return AutomaticUpdateDecision(not reasons, tuple(reasons), estimate)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        result = _result_error(exc)
        return AutomaticUpdateDecision(False, result.reason_codes)


def _select_row(proof: Mapping[str, Any], previous: Mapping[str, Any], *, mode: str,
                profiles: tuple, platform: str, deployment_files: Mapping[str, str]) -> dict:
    rows = [r for r in proof["predecessors"] if r["predecessor"]["version"] == previous["version"]
            and r["predecessor"]["mode"] == mode and set(r["predecessor"]["profiles"]) == set(profiles)]
    require(bool(rows), ReasonCode.TRANSITION_NOT_REHEARSED)
    rows = [r for r in rows if r["predecessor"]["platform"] == platform]
    require(bool(rows), ReasonCode.PLATFORM_NOT_REHEARSED)
    execution = {"files": dict(deployment_files)}
    rows = [r for r in rows if r.get("execution_deployment", proof["deployment"]) == execution]
    require(len(rows) == 1, ReasonCode.DEPLOYMENT_FILES_MISMATCH)
    return rows[0]


def _common(prepared: PreparedUpdate, obs: Observations, controls: Controls):
    require(isinstance(prepared, PreparedUpdate) and isinstance(obs, Observations) and isinstance(controls, Controls))
    target = validate_manifest(decode_document(prepared.raw_manifest, 1024 * 1024))
    require(target == plain(prepared.manifest), ReasonCode.IMMUTABLE_RELEASE_CHANGED)
    require(hashlib.sha256(prepared.raw_manifest).hexdigest() == prepared.manifest_sha256
            and hashlib.sha256(prepared.raw_bundle).hexdigest() == prepared.bundle_sha256, ReasonCode.IMMUTABLE_RELEASE_CHANGED)
    require(target["schema_version"] == 2 and target["compatibility"]["contract_version"] == 2,
            ReasonCode.RESTORE_NOT_CERTIFIED)
    _verified(prepared.verification, target["version"], controls, obs.now, fresh=True)
    require(obs.online_verified is True, ReasonCode.SIGNATURE_REJECTED)
    previous = validate_manifest(decode_document(obs.predecessor_raw, 1024 * 1024))
    _verified(obs.predecessor_verification, previous["version"], controls, obs.now, fresh=False)
    require(obs.owner_confirmed is True and controls.registered is True, ReasonCode.OWNERSHIP_UNCONFIRMED)
    require(prepared.binding.topology == "standalone" and not prepared.binding.production
            and not prepared.binding.profiles and obs.topology_qualified is True and obs.no_source_build is True,
            ReasonCode.UNSUPPORTED_INSTALLATION)
    require(obs.no_pending_transaction is True, ReasonCode.AUTOMATIC_RECOVERY_PENDING)
    require(obs.baseline_attested is True, ReasonCode.OWNERSHIP_UNCONFIRMED)
    require(obs.graph_unchanged is True and obs.env_unchanged is True, ReasonCode.CONFIGURATION_CHANGED)
    for observed, expected in ((obs.installation_fingerprint, prepared.binding.installation_fingerprint),
                               (obs.config_fingerprint, prepared.config_fingerprint),
                               (obs.profile_fingerprint, prepared.profile_fingerprint),
                               (obs.trust_fingerprint, prepared.trust_fingerprint)):
        require(bool(observed) and observed == expected, ReasonCode.CONFIGURATION_CHANGED)
    compatibility = target["compatibility"]
    require(compatibility["automatic_apply"] is True
            and compatibility["automatic_rollback"]["strategy"] == "restore_backup", ReasonCode.RESTORE_NOT_CERTIFIED)
    require(not compatibility["requires_operator_initialization"], ReasonCode.INITIALIZATION_REQUIRED)
    layout = topology_profile("standalone", ())
    require(obs.runtime_complete is True, ReasonCode.RUNNING_VERSIONS_UNKNOWN)
    proof = validate_release_proof(obs.release_proof, target, repository=_repository(controls, target["version"]))
    row = _select_row(proof, previous, mode="standalone", profiles=(), platform=obs.platform,
                      deployment_files=obs.deployment_files)
    predecessor = row["predecessor"]
    require(predecessor["restore_certified"] is True, ReasonCode.RESTORE_NOT_CERTIFIED)
    require(predecessor["manifest_hash"] == manifest_hash(previous)
            and predecessor["raw_manifest_sha256"] == hashlib.sha256(obs.predecessor_raw).hexdigest()
            and predecessor["source_sha"] == previous["source_sha"], ReasonCode.IMMUTABLE_RELEASE_CHANGED)
    require(row["predecessor_components"] == previous["components"], ReasonCode.IMAGE_DIGEST_MISMATCH)
    require(row["predecessor_deployment"] == previous["deployment"], ReasonCode.DEPLOYMENT_FILES_MISMATCH)
    require(dict(obs.persistence) == row["persistence"], ReasonCode.UNSUPPORTED_INSTALLATION)
    require(bool(obs.executor_sha256) and obs.executor_sha256 == row["executor_sha256"],
            ReasonCode.TRANSITION_NOT_REHEARSED)
    require(obs.binding_sha256 == installation_binding_hash(prepared.binding), ReasonCode.CONFIGURATION_CHANGED)
    source = obs.runtime_observation
    require(isinstance(source, RuntimeObservationV2), ReasonCode.MIGRATION_INVENTORY_UNSUPPORTED)
    require(source.release_version == previous["version"]
            and source.manifest_sha256 == hashlib.sha256(obs.predecessor_raw).hexdigest()
            and source.platform == obs.platform and source.binding_sha256 == obs.binding_sha256,
            ReasonCode.IMMUTABLE_RELEASE_CHANGED)
    require(source.ready is True and source.stable is True, ReasonCode.READINESS_FAILED)
    require(source.deployment_sha256 == manifest_hash(row["execution_deployment"]), ReasonCode.DEPLOYMENT_FILES_MISMATCH)
    migration = _migration_evidence(row)
    require(source.migration_inventory_sha256 == migration.source_inventory_sha256
            and source.applied_migrations_sha256 == migration.source_applied_set_sha256
            and source.active_app_mapping_sha256 == migration.active_app_mapping_sha256,
            ReasonCode.MIGRATION_INVENTORY_MISMATCH)
    require(aware(source.observed_at) <= aware(obs.now), ReasonCode.CLOCK_SKEW)
    # A stopped installation cannot yield a fresh healthy-A observation. The
    # retained pre-quiescence observation is bound to a freshly verified hold.
    if obs.boundary == "before_backup":
        require((aware(obs.now) - aware(source.observed_at)).total_seconds() <= 300, ReasonCode.READINESS_FAILED)
    else:
        hold = obs.cold_hold
        require(isinstance(hold, ColdHold), ReasonCode.BACKUP_PROOF_INVALID)
        require(hold.attempt_id == obs.attempt_id and hold.operation_token == obs.operation_token
                and hold.run_id == obs.run_id and hold.binding_sha256 == obs.binding_sha256
                and hold.source_observation_sha256 == source.sha256 and hold.storage_sha256 == source.storage_sha256
                and hold.daemon_identity == prepared.binding.daemon_identity,
                ReasonCode.BACKUP_INVENTORY_MISMATCH)
        require(aware(source.observed_at) <= aware(hold.snapshot_at) <= aware(hold.verified_at) <= aware(obs.now)
                and (aware(obs.now) - aware(hold.verified_at)).total_seconds() <= 300, ReasonCode.BACKUP_PROOF_INVALID)
        require(dict(hold.restart_counts) == dict(source.restart_counts)
                and dict(hold.restart_policies) == dict(source.restart_policies), ReasonCode.UNSUPPORTED_INSTALLATION)
    _images(source.dependency_images, {item["service"]: item for item in row["dependencies"]})
    _images(source.product_images, {service: previous["components"][component] for service, component in layout.products})
    for env in compatibility["required_env"]:
        if set(env["components"]) & set(layout.active_components):
            require(env["name"] in obs.env and type(obs.env[env["name"]]) is str
                    and (env["rule"] == "present" or bool(obs.env[env["name"]].strip())), ReasonCode.REQUIRED_ENV_MISSING)
    return target, previous, layout, row


def _fingerprint(prepared: PreparedUpdate, obs: Observations, backup: Optional[bytes]) -> str:
    source = obs.runtime_observation
    return manifest_hash({
        "attempt": obs.attempt_id, "operation_token": obs.operation_token, "run_id": obs.run_id,
        "target": prepared.manifest_sha256, "bundle": prepared.bundle_sha256,
        "previous": hashlib.sha256(obs.predecessor_raw).hexdigest(),
        "release_proof": hashlib.sha256(obs.release_proof).hexdigest(),
        "backup": None if backup is None else hashlib.sha256(backup).hexdigest(),
        "installation": obs.installation_fingerprint, "binding": obs.binding_sha256,
        "config": obs.config_fingerprint, "profile": obs.profile_fingerprint, "trust": obs.trust_fingerprint,
        "source_observation": None if source is None else source.sha256, "executor": obs.executor_sha256,
        "persistence": dict(obs.persistence), "deployment_files": dict(obs.deployment_files),
    })


def _validate_automatic_backup(raw: bytes, prepared: PreparedUpdate, obs: Observations,
                               controls: Controls, previous: Mapping[str, Any], layout: TopologyProfile) -> None:
    from .backup_proof import BackupExpectation, validate_backup_proof
    require(obs.boundary != "before_backup", ReasonCode.BACKUP_REQUIRED)
    expected = BackupExpectation(obs.attempt_id, obs.installation_fingerprint,
                                 manifest_hash(plain(prepared.manifest)), previous["version"], obs.reserved_at,
                                 layout.backup_domains)
    validate_backup_proof(raw, expected, now=obs.now, max_age_seconds=controls.policy.backup_max_age_seconds)
    value = decode_document(raw, MAX_BACKUP_BYTES)
    require(value["schema_version"] == 2, ReasonCode.BACKUP_PROOF_INVALID)
    shape(value, schema("backup-proof.schema.json"))
    seal, hold, source = obs.backup_seal, obs.cold_hold, obs.runtime_observation
    require(isinstance(seal, BackupSeal) and isinstance(hold, ColdHold) and isinstance(source, RuntimeObservationV2),
            ReasonCode.BACKUP_PROOF_INVALID)
    require(set(value["covered_domains"]) == set(PERSISTENT_DOMAINS), ReasonCode.BACKUP_INVENTORY_MISMATCH)
    require(value["binding_sha256"] == seal.binding_sha256 == obs.binding_sha256
            and value["inventory_sha256"] == seal.inventory_sha256
            and value["source_observation_sha256"] == seal.source_observation_sha256 == source.sha256
            and value["quiescence_evidence_sha256"] == seal.quiescence_evidence_sha256 == hold.quiescence_evidence_sha256
            and hashlib.sha256(raw).hexdigest() == seal.proof_sha256
            and seal.attempt_id == obs.attempt_id and seal.operation_token == obs.operation_token
            and seal.run_id == obs.run_id and seal.target_manifest_hash == expected.target_manifest_hash
            and seal.source_version == previous["version"], ReasonCode.BACKUP_INVENTORY_MISMATCH)
    snapshot = timestamp(value["snapshot_at"])
    require(timestamp(value["started_at"]) <= snapshot <= timestamp(value["completed_at"])
            and snapshot == seal.snapshot_at == hold.snapshot_at
            and snapshot <= aware(seal.authorised_at) <= aware(obs.now), ReasonCode.BACKUP_PROOF_INVALID)
    require((aware(obs.now) - snapshot).total_seconds() <= controls.policy.backup_max_age_seconds,
            ReasonCode.BACKUP_EXPIRED)


def _result_error(exc: Exception) -> Eligibility:
    if isinstance(exc, CompatibilityError):
        reason = exc.reason_code
    elif isinstance(exc, ContractError) and exc.reason_code in {r.value for r in ReasonCode}:
        reason = ReasonCode(exc.reason_code)
    else:
        reason = ReasonCode.UNCERTIFIED_RELEASE
    return Eligibility(False, (reason,))


def evaluate_automatic_apply(prepared: PreparedUpdate, observations: Observations, controls: Controls,
                             proof: Any) -> Eligibility:
    """Restore-backed all-of assessment. Pending grants BACKUP ONLY, not rollout.

    Pure values do not reserve attempts, verify signatures, establish locks or
    reconcile Docker writers. The automatic executor must revalidate each hold
    and own durable direction/seals under update.lock; manual callers are unchanged.
    """
    try:
        require(controls.policy.mode == "apply" and controls.enabled is True and controls.paused is False,
                ReasonCode.CONTROL_INVALID)
        require(controls.policy.qualification_policy == QUALIFICATION_POLICY, ReasonCode.RESTORE_NOT_CERTIFIED)
        require(controls.window_open is True, ReasonCode.OUTSIDE_WINDOW)
        require(controls.start_budget_available is True, ReasonCode.INSUFFICIENT_WINDOW)
        require(observations.no_failed_target is True, ReasonCode.FAILED_TARGET_LATCHED)
        target, previous, layout, row = _common(prepared, observations, controls)
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
        require(observations.boundary in ("before_backup", "after_backup", "before_env_commit", "before_rollout"))
        if pending:
            require(observations.boundary == "before_backup", ReasonCode.BACKUP_REQUIRED)
        else:
            _validate_automatic_backup(proof, prepared, observations, controls, previous, layout)
        fingerprint = _fingerprint(prepared, observations, None if pending else proof)
        transition = AuthorisedTransition(prepared, observations.runtime_observation, _migration_evidence(row),
            hashlib.sha256(observations.predecessor_raw).hexdigest(), hashlib.sha256(observations.release_proof).hexdigest(),
            row["execution_deployment"], row["executor_sha256"], manifest_hash(row), observations.now, fingerprint,
            measurements=transition_measurements(row))
        return Eligibility(True, backup_pending=pending, fingerprint=fingerprint, transition=transition)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        return _result_error(exc)


def authorise_transition(prepared: PreparedUpdate, observations: Observations, controls: Controls,
                         proof: Any) -> AuthorisedTransition:
    """Raising interface to the same evaluator, NOT a second/bypass admission path."""
    result = evaluate_automatic_apply(prepared, observations, controls, proof)
    require(result.allowed and result.transition is not None,
            result.reason_codes[0] if result.reason_codes else ReasonCode.RESTORE_NOT_CERTIFIED)
    return result.transition


def evaluate_automatic_rollback(attempt: Attempt, observations: Observations, controls: Controls) -> Eligibility:
    """Deprecated import tombstone until orchestrator removes its eager imports.

    The image-only evaluator has been removed. No automatic image rollback can
    be authorized, including with readable legacy data. Manual Updater.rollback
    does not call this API and retains its behavior.
    """
    return Eligibility(False, (ReasonCode.ROLLBACK_INELIGIBLE,))
