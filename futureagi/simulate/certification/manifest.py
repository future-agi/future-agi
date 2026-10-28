"""Production certification suite manifest: schema, validation, expansion.

Implements the manifest contract from the production-agent-certification plan:

- A frozen suite YAML (``version: 1``, ``suite_key``, ``defaults``, ``cases``)
  carries one entry per certification case. Each entry pins an immutable agent
  source (repository + 40-hex commit SHA), declares its target kind
  (``local_test`` or ``hosted_service``), and lists its interactions with
  repo-scoped scenario/config refs, fixed seeds, and platform-vault secret
  references.
- The validator rejects raw secret material, mutable source refs (branches,
  tags, missing SHAs), duplicate case/interaction keys, external targets
  without an explicit ``authorization`` record, and disabled entries that are
  not clearly marked with ``pending`` reasons.
- ``expand_enabled_cases`` produces the normalized case descriptors a campaign
  turns into HostedHarnessJob payloads. Disabled entries are never expanded:
  they are placeholders that production admission must reject, which is why
  ``require_admission_ready`` fails when a suite has no enabled cases.

No Django imports: this module is safe to load from Temporal workflow code.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

SUITE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
CASE_KEY_RE = re.compile(r"^[a-z][a-z0-9]*(?:_+[a-z0-9]+)*$")
COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SCENARIO_REF_RE = re.compile(r"^scenarios:[A-Za-z0-9_.\-/]+$")

SUITE_DIR = Path(__file__).resolve().parent / "suites"
REPO_ROOT = SUITE_DIR.parents[2]

TARGET_KINDS = frozenset({"local_test", "hosted_service"})
AGENT_CONNECTORS = frozenset(
    {"livekit", "vapi", "retell", "retell_chat", "phone", "auto"}
)
RUN_TEST_IDS = frozenset({"l2_audio_room_speak_l2", "l1_chat_top_voice"})
SECRET_REF_MANAGERS = frozenset({"platform-vault"})
APPROVED_CALL_STATES = frozenset({"approved"})
PENDING_CALL_STATES = frozenset({"pending", "rejected"})
TARGET_OWNERSHIP = frozenset({"futureagi", "provider_test", "external"})

# Free-text patterns that indicate raw credential material anywhere it does not
# belong (target refs, notes, scenario refs, tool specs, ...).
_SECRET_MATERIAL_RE = re.compile(
    r"(-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|bearer\s+[a-zA-Z0-9._\-]{8,}"
    r"|api[_-]?key\s*[:=]"
    r"|secret[_-]?key\s*[:=]"
    r"|password\s*[:=]"
    r"|://[^/\s:]+:[^/\s@]+@"
    r"|\bsk-[a-zA-Z0-9_\-]{10,}"
    r"|eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{5,})",
    re.IGNORECASE,
)


class CertificationManifestError(Exception):
    """Raised when a suite manifest fails schema or admission validation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


# ---------------------------------------------------------------------------
# canonicalization / digest
# ---------------------------------------------------------------------------


def canonical_json_bytes(value: Any) -> bytes:
    """RFC-8785-ish canonical JSON used for stable digests."""

    def _norm(v: Any) -> Any:
        if isinstance(v, Mapping):
            return {str(k): _norm(v[k]) for k in sorted(v)}
        if isinstance(v, (list, tuple)):
            return [_norm(i) for i in v]
        return v

    return json.dumps(
        _norm(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def canonical_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def suite_digest(normalized_suite: Mapping[str, Any]) -> str:
    """Digest over the normalized suite — the campaign's suite identity."""

    return canonical_digest(normalized_suite)


def campaign_idempotency_key(deployment_id: str, digest: str) -> str:
    return f"certification:{deployment_id}:{digest}"


def case_idempotency_key(deployment_id: str, digest: str, case_key: str) -> str:
    return f"{campaign_idempotency_key(deployment_id, digest)}:{case_key}"


def notification_idempotency_key(
    campaign_id: int, case_key: str, event_kind: str, payload_digest: str
) -> str:
    return f"certification:{campaign_id}:{case_key}:{event_kind}:{payload_digest}"


# ---------------------------------------------------------------------------
# loading (with duplicate-mapping-key detection)
# ---------------------------------------------------------------------------


class _DupCheckLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _DupCheckLoader, node: yaml.nodes.MappingNode):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in mapping:
            raise CertificationManifestError(
                "duplicate_key", f"duplicate mapping key {key!r} in manifest"
            )
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


_DupCheckLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def load_manifest_text(text: str) -> dict:
    try:
        doc = yaml.load(text, Loader=_DupCheckLoader)
    except CertificationManifestError:
        raise
    except yaml.YAMLError as exc:
        raise CertificationManifestError("invalid_yaml", str(exc)) from exc
    if not isinstance(doc, Mapping):
        raise CertificationManifestError(
            "invalid_schema", "suite manifest must be a YAML mapping"
        )
    return dict(doc)


def suite_manifest_path(suite_key: str) -> Path:
    if not SUITE_KEY_RE.match(suite_key or ""):
        raise CertificationManifestError(
            "invalid_suite_key", f"invalid suite key {suite_key!r}"
        )
    path = SUITE_DIR / f"{suite_key}.yaml"
    if not path.is_file():
        raise CertificationManifestError(
            "unknown_suite", f"no certification suite named {suite_key!r}"
        )
    return path


def load_suite(suite_key: str) -> dict:
    return load_manifest_text(suite_manifest_path(suite_key).read_text())


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _fail(code: str, where: str, message: str) -> None:
    raise CertificationManifestError(code, f"{where}: {message}")


def _iter_strings(value: Any, path: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, Mapping):
        for k, v in value.items():
            yield from _iter_strings(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from _iter_strings(v, f"{path}[{i}]")


def _assert_no_secret_material(value: Any, where: str) -> None:
    for path, s in _iter_strings(value, where):
        m = _SECRET_MATERIAL_RE.search(s)
        if m:
            _fail(
                "raw_secret_material",
                path,
                f"string contains credential-like material ({m.group(0)[:16]}...); "
                "use platform-vault secret_ref_set entries",
            )


def _canonical_case_key(raw: Any, where: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        _fail("invalid_case_key", where, "case_key must be a non-empty string")
    key = re.sub(r"[\s:\-]+", "_", raw.strip().lower())
    if not CASE_KEY_RE.match(key):
        _fail(
            "invalid_case_key",
            where,
            f"case_key {raw!r} normalizes to {key!r}, which does not match "
            f"{CASE_KEY_RE.pattern}",
        )
    return key


def _req(mapping: Mapping, field: str, where: str) -> Any:
    v = mapping.get(field)
    if v is None or (isinstance(v, str) and not v.strip()):
        _fail("missing_field", where, f"missing required field {field!r}")
    return v


def _validate_secret_ref_set(value: Any, where: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        _fail("invalid_secret_refs", where, "secret_ref_set must be a list")
    refs = []
    for i, ref in enumerate(value):
        w = f"{where}[{i}]"
        if not isinstance(ref, Mapping):
            _fail("invalid_secret_refs", w, "secret ref must be a mapping")
        manager = ref.get("manager")
        if manager not in SECRET_REF_MANAGERS:
            _fail(
                "secret_manager_unsupported",
                w,
                f"manager {manager!r} not allowed; only 'platform-vault' refs "
                "may be materialized for production certification",
            )
        key = ref.get("key")
        if not isinstance(key, str) or not key.strip():
            _fail("invalid_secret_refs", w, "secret ref requires a non-empty 'key'")
        purpose = ref.get("purpose", "target_provider")
        if purpose != "target_provider":
            _fail(
                "invalid_secret_refs",
                w,
                f"purpose {purpose!r} unsupported; certification only grants "
                "target_provider secrets",
            )
        _assert_no_secret_material(key, f"{w}.key")
        refs.append({"manager": manager, "key": key.strip(), "purpose": purpose})
    return refs


def _validate_authorization(value: Any, where: str, target_kind: str) -> dict:
    if not isinstance(value, Mapping):
        _fail(
            "authorization_missing",
            where,
            "enabled cases require an authorization block",
        )
    owner = value.get("owner")
    calls = value.get("automated_production_calls")
    approved_by = value.get("approved_by")
    approved_at = value.get("approved_at")
    ownership = value.get("target_ownership")
    expires_at = value.get("expires_at")
    if not isinstance(owner, str) or not owner.strip():
        _fail(
            "authorization_missing",
            where,
            "authorization.owner must name the owning team/alias",
        )
    if calls not in APPROVED_CALL_STATES:
        _fail(
            "authorization_unverified",
            where,
            f"authorization.automated_production_calls is {calls!r}; "
            "production admission requires 'approved'",
        )
    if not isinstance(approved_by, str) or not approved_by.strip():
        _fail(
            "authorization_unverified",
            where,
            "authorization.approved_by must name the approver",
        )
    if approved_at is None:
        _fail(
            "authorization_unverified",
            where,
            "authorization.approved_at must record the approval timestamp",
        )
    if ownership not in TARGET_OWNERSHIP:
        _fail(
            "authorization_unverified",
            where,
            f"authorization.target_ownership must be one of {sorted(TARGET_OWNERSHIP)}",
        )
    if ownership == "external" and expires_at is None:
        _fail(
            "authorization_unverified",
            where,
            "external targets require authorization.expires_at",
        )
    if target_kind == "hosted_service" and not value.get("target_endpoint_reviewed"):
        _fail(
            "authorization_unverified",
            where,
            "hosted_service targets require "
            "authorization.target_endpoint_reviewed: true",
        )
    return {
        "owner": owner.strip(),
        "automated_production_calls": calls,
        "approved_by": approved_by.strip(),
        "approved_at": str(approved_at),
        "target_ownership": ownership,
        **({"expires_at": str(expires_at)} if expires_at is not None else {}),
        **(
            {"target_endpoint_reviewed": bool(value["target_endpoint_reviewed"])}
            if "target_endpoint_reviewed" in value
            else {}
        ),
    }


def _load_agent_config(path: Path, where: str) -> dict:
    try:
        value = yaml.safe_load(path.read_text())
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        _fail("config_unmaterialized", where, f"cannot parse config_ref: {exc}")
    if not isinstance(value, Mapping):
        _fail("invalid_config_ref", where, "config_ref must contain a YAML mapping")
    value = dict(value)
    if set(value) - {"mode", "call_direction", "config"}:
        _fail(
            "invalid_config_ref",
            where,
            "config_ref supports only mode, call_direction, and config",
        )
    if value.get("mode") not in {
        None,
        "connect_only",
        "environment_backed",
        "provider_import",
    }:
        _fail("invalid_config_ref", where, "config_ref contains an invalid mode")
    if value.get("call_direction") not in {None, "inbound", "outbound"}:
        _fail(
            "invalid_config_ref",
            where,
            "config_ref contains an invalid call_direction",
        )
    config = value.get("config", {})
    if not isinstance(config, Mapping):
        _fail("invalid_config_ref", where, "config_ref.config must be a mapping")
    config = dict(config)
    _assert_no_secret_material(config, f"{where}.config")
    secret_names = ("token", "secret", "password", "api_key", "private_key")
    secret_keys = [
        str(key)
        for key in config
        if any(marker in str(key).lower() for marker in secret_names)
    ]
    if secret_keys:
        _fail(
            "raw_secret_material",
            where,
            "config_ref.config contains secret-bearing keys: "
            + ", ".join(sorted(secret_keys)),
        )
    invalid = [
        str(key)
        for key, item in config.items()
        if not isinstance(item, (str, int, float, bool))
        and not (
            key == "dynamic_variables"
            and isinstance(item, Mapping)
            and all(
                isinstance(name, str) and isinstance(entry, (str, int, float, bool))
                for name, entry in item.items()
            )
        )
    ]
    if invalid:
        _fail(
            "invalid_config_ref",
            where,
            "config_ref.config values must be scalar; invalid keys: "
            + ", ".join(sorted(invalid)),
        )
    return {
        **({"mode": value["mode"]} if value.get("mode") else {}),
        **(
            {"call_direction": value["call_direction"]}
            if value.get("call_direction")
            else {}
        ),
        "config": config,
    }


def _validate_source(value: Any, where: str, enabled: bool) -> dict:
    if value is None:
        if enabled:
            _fail(
                "source_unrecorded",
                where,
                "enabled cases must record agent source",
            )
        return None
    if not isinstance(value, Mapping):
        _fail("invalid_source", where, "agent.source must be a mapping")
    repo = value.get("repository")
    sha = value.get("commit_sha")
    if enabled:
        if not isinstance(repo, str) or not repo.strip():
            _fail("source_unrecorded", where, "agent.source.repository required")
        _assert_no_secret_material(repo, f"{where}.repository")
        if not isinstance(sha, str) or not COMMIT_SHA_RE.match(sha):
            _fail(
                "source_commit_unverified",
                where,
                "agent.source.commit_sha must be a pinned 40-hex commit "
                "(branches/tags are mutable and rejected)",
            )
    else:
        if repo is not None and not isinstance(repo, str):
            _fail("invalid_source", where, "repository must be a string")
        if sha is not None and (
            not isinstance(sha, str) or not COMMIT_SHA_RE.match(sha)
        ):
            _fail(
                "invalid_source",
                where,
                "commit_sha, when present, must be a pinned 40-hex commit",
            )
    path = value.get("path")
    if path is not None:
        if not isinstance(path, str) or path.startswith("/") or ".." in path:
            _fail(
                "invalid_source",
                where,
                "agent.source.path must be a repo-relative path without '..'",
            )
    out = {}
    if isinstance(repo, str) and repo.strip():
        out["repository"] = repo.strip()
    if isinstance(sha, str) and sha:
        out["commit_sha"] = sha
    if isinstance(path, str) and path:
        out["path"] = path
    return out or None


def _validate_interaction(
    raw: Any, index: int, where: str, enabled: bool, repo_root: Path | None
) -> dict:
    if not isinstance(raw, Mapping):
        _fail("invalid_schema", f"{where}[{index}]", "interaction must be a mapping")
    w = f"{where}[{index}]"
    number = raw.get("interaction", index + 1)
    if not isinstance(number, int) or number < 1:
        _fail("invalid_schema", w, "interaction must be a positive integer")
    out: dict = {"interaction": number}
    if enabled:
        scenario_ref = _req(raw, "scenario_ref", w)
        if not isinstance(scenario_ref, str) or not SCENARIO_REF_RE.match(scenario_ref):
            _fail(
                "invalid_scenario_ref",
                w,
                "scenario_ref must match 'scenarios:<repo-scoped path>'",
            )
        _assert_no_secret_material(scenario_ref, f"{w}.scenario_ref")
        config_ref = _req(raw, "config_ref", w)
        if (
            not isinstance(config_ref, str)
            or config_ref.startswith("/")
            or ".." in config_ref
        ):
            _fail(
                "invalid_config_ref",
                w,
                "config_ref must be a repo-relative path without '..'",
            )
        config_sha256 = raw.get("config_sha256")
        if not isinstance(config_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", config_sha256
        ):
            _fail(
                "invalid_config_ref",
                w,
                "config_sha256 must be a lowercase 64-hex sha256",
            )
        if repo_root is not None:
            resolved = (repo_root / config_ref).resolve()
            try:
                resolved.relative_to(repo_root.resolve())
            except ValueError:
                _fail("invalid_config_ref", w, "config_ref escapes the repository")
            if not resolved.is_file():
                _fail(
                    "config_unmaterialized",
                    w,
                    f"config_ref {config_ref!r} does not exist in the repository; "
                    "scenario material must be committed at manifest freeze",
                )
            else:
                actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
                if actual != config_sha256:
                    _fail(
                        "config_unmaterialized",
                        w,
                        f"config_ref {config_ref!r} sha256 mismatch "
                        f"(manifest {config_sha256[:12]}… != file {actual[:12]}…)",
                    )
                out["agent_config"] = _load_agent_config(resolved, w)
        out["scenario_ref"] = scenario_ref
        out["config_ref"] = config_ref
        out["config_sha256"] = config_sha256
        out["seed"] = _req(raw, "seed", w)
        if not isinstance(out["seed"], int):
            _fail("invalid_schema", w, "seed must be an integer")
        out["secret_ref_set"] = _validate_secret_ref_set(
            _req(raw, "secret_ref_set", w), f"{w}.secret_ref_set"
        )
        for field in ("expected_tools", "expected_state", "trace_reference"):
            if field in raw and raw[field] is not None:
                _assert_no_secret_material(raw[field], f"{w}.{field}")
                out[field] = raw[field]
    else:
        # Placeholder interactions keep only their declared count; scenario and
        # config material is filled in when the case is materialized.
        for field in ("scenario_ref", "config_ref"):
            if raw.get(field) is not None:
                out[field] = raw[field]
    return out


def _validate_case(raw: Any, index: int, where: str, repo_root: Path | None) -> dict:
    w = f"{where}[{index}]"
    if not isinstance(raw, Mapping):
        _fail("invalid_schema", w, "case must be a mapping")
    case_key = _canonical_case_key(raw.get("case_key"), w)
    w = f"{w}({case_key})"
    enabled = bool(raw.get("enabled", False))

    agent = raw.get("agent")
    if not isinstance(agent, Mapping):
        _fail("invalid_schema", w, "agent block required")
    name = agent.get("name")
    model = agent.get("model")
    connector = agent.get("connector", "auto")
    if not isinstance(name, str) or not name.strip():
        _fail("missing_field", w, "agent.name required")
    if enabled and (not isinstance(model, str) or not model.strip()):
        _fail("missing_field", w, "agent.model required for enabled cases")
    if connector not in AGENT_CONNECTORS:
        _fail(
            "invalid_schema",
            w,
            f"agent.connector must be one of {sorted(AGENT_CONNECTORS)}",
        )
    source = _validate_source(agent.get("source"), f"{w}.agent", enabled)

    target = raw.get("target")
    if not isinstance(target, Mapping):
        _fail("invalid_schema", w, "target block required")
    kind = target.get("kind")
    if kind not in TARGET_KINDS:
        _fail(
            "invalid_target",
            w,
            f"target.kind must be one of {sorted(TARGET_KINDS)}",
        )
    ref = target.get("ref")
    if enabled:
        if not isinstance(ref, str) or not ref.strip():
            _fail("target_endpoint_unrecorded", w, "target.ref required")
        _assert_no_secret_material(ref, f"{w}.target.ref")

    run_test_id = raw.get("run_test_id")
    if enabled:
        if run_test_id not in RUN_TEST_IDS:
            _fail(
                "invalid_run_test",
                w,
                f"run_test_id must be one of {sorted(RUN_TEST_IDS)}",
            )

    raw_interactions = raw.get("interactions")
    if not isinstance(raw_interactions, list) or not raw_interactions:
        _fail("invalid_schema", w, "interactions must be a non-empty list")
    if enabled and len(raw_interactions) < 2:
        _fail(
            "invalid_schema",
            w,
            "enabled cases require at least two multi-turn interactions",
        )
    interactions = [
        _validate_interaction(item, i, f"{w}.interactions", enabled, repo_root)
        for i, item in enumerate(raw_interactions)
    ]
    seen_i = set()
    for item in interactions:
        if item["interaction"] in seen_i:
            _fail(
                "duplicate_key",
                w,
                f"duplicate interaction number {item['interaction']}",
            )
        seen_i.add(item["interaction"])
    materialized_configs = [
        item.get("agent_config") for item in interactions if item.get("agent_config")
    ]
    if materialized_configs and any(
        item != materialized_configs[0] for item in materialized_configs[1:]
    ):
        _fail(
            "conflicting_agent_config",
            w,
            "all interactions in one case must use the same executable agent config",
        )

    if enabled:
        pending = raw.get("pending")
        if pending:
            _fail(
                "invalid_schema",
                w,
                "enabled cases must not carry pending markers",
            )
        authorization = _validate_authorization(raw.get("authorization"), w, kind)
    else:
        pending = raw.get("pending")
        if (
            not isinstance(pending, list)
            or not pending
            or not all(isinstance(p, str) and p.strip() for p in pending)
        ):
            _fail(
                "unmarked_placeholder",
                w,
                "disabled cases must be clearly marked with a non-empty "
                "'pending' reason list so production admission can reject them",
            )
        authorization = None
        if isinstance(raw.get("authorization"), Mapping):
            authorization = raw["authorization"]

    _assert_no_secret_material(
        {k: v for k, v in raw.items() if k != "secret_ref_set"}, w
    )
    out = {
        "case_key": case_key,
        "enabled": enabled,
        "agent": {
            "name": name.strip(),
            "connector": connector,
            **({"model": model.strip()} if isinstance(model, str) and model else {}),
            **({"source": source} if source else {}),
        },
        "target": {
            "kind": kind,
            **({"ref": ref.strip()} if isinstance(ref, str) and ref else {}),
        },
        **({"run_test_id": run_test_id} if run_test_id else {}),
        "interactions": interactions,
    }
    if authorization is not None:
        out["authorization"] = authorization
    if not enabled:
        out["pending"] = [p.strip() for p in raw["pending"]]
    if raw.get("notes"):
        _assert_no_secret_material(raw["notes"], f"{w}.notes")
        out["notes"] = raw["notes"]
    return out


def validate_manifest(doc: Mapping[str, Any], *, repo_root: Path | None = None) -> dict:
    """Validate + normalize a loaded suite manifest.

    ``repo_root`` enables config_ref existence/sha checks for enabled cases.
    Returns the normalized suite dict; raises CertificationManifestError.
    """

    where = "suite"
    version = doc.get("version")
    if version != 1:
        _fail("invalid_schema", where, "version must be 1")
    suite_key = doc.get("suite_key")
    if not isinstance(suite_key, str) or not SUITE_KEY_RE.match(suite_key):
        _fail("invalid_schema", where, "suite_key must match " + SUITE_KEY_RE.pattern)

    defaults = doc.get("defaults", {})
    if not isinstance(defaults, Mapping):
        _fail("invalid_schema", where, "defaults must be a mapping")
    defaults = dict(defaults)
    if "run_test_id" in defaults and defaults["run_test_id"] not in RUN_TEST_IDS:
        _fail(
            "invalid_run_test",
            f"{where}.defaults",
            f"run_test_id must be one of {sorted(RUN_TEST_IDS)}",
        )
    for field in ("interaction_count", "max_duration_seconds"):
        if field in defaults and (
            not isinstance(defaults[field], int) or defaults[field] < 1
        ):
            _fail(
                "invalid_schema",
                f"{where}.defaults",
                f"{field} must be a positive integer",
            )
    _assert_no_secret_material(defaults, f"{where}.defaults")

    cases = doc.get("cases")
    if not isinstance(cases, list) or not cases:
        _fail("invalid_schema", where, "cases must be a non-empty list")
    seen = set()
    normalized_cases = []
    for i, raw in enumerate(cases):
        case = _validate_case(raw, i, f"{where}.cases", repo_root)
        key = case["case_key"]
        if key in seen:
            _fail(
                "duplicate_key",
                f"{where}.cases[{i}]",
                f"duplicate case_key {key!r} (after canonicalization)",
            )
        seen.add(key)
        normalized_cases.append(case)

    return {
        "version": 1,
        "suite_key": suite_key,
        "defaults": defaults,
        "cases": normalized_cases,
    }


# ---------------------------------------------------------------------------
# expansion / admission
# ---------------------------------------------------------------------------


def expand_enabled_cases(normalized_suite: Mapping[str, Any]) -> list[dict]:
    """Produce normalized case specs for enabled entries only.

    Disabled placeholders are skipped by construction — they can never reach a
    campaign. Each spec is the durable CertificationCase payload source.
    """

    specs = []
    for case in normalized_suite["cases"]:
        if not case["enabled"]:
            continue
        specs.append(
            {
                "case_key": case["case_key"],
                "agent": case["agent"],
                "target": case["target"],
                "run_test_id": case.get("run_test_id")
                or normalized_suite["defaults"].get("run_test_id"),
                "authorization": case["authorization"],
                "interactions": [
                    {
                        "scenario_key": f"{case['case_key']}__interaction_{i['interaction']:02d}",
                        **i,
                    }
                    for i in case["interactions"]
                ],
                **({"notes": case["notes"]} if case.get("notes") else {}),
            }
        )
    return specs


def _parse_timestamp(value: str, where: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail("authorization_unverified", where, f"invalid timestamp {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def require_admission_ready(
    normalized_suite: Mapping[str, Any], *, now: datetime | None = None
) -> None:
    """Production admission gate: a suite must expose enabled cases.

    Disabled placeholders exist only to record the audited 25-agent set and its
    pending reasons; they must never be silently certified.
    """

    enabled = sum(1 for c in normalized_suite["cases"] if c["enabled"])
    if enabled != len(normalized_suite["cases"]):
        pending: dict[str, list[str]] = {}
        for c in normalized_suite["cases"]:
            for p in c.get("pending", []):
                pending.setdefault(p, []).append(c["case_key"])
        reasons = "; ".join(
            f"{reason}: {len(keys)} case(s)" for reason, keys in sorted(pending.items())
        )
        raise CertificationManifestError(
            "suite_not_admission_ready",
            f"suite {normalized_suite['suite_key']!r} has {enabled}/"
            f"{len(normalized_suite['cases'])} enabled cases; "
            f"pending reasons — {reasons}",
        )
    now = (now or datetime.now(UTC)).astimezone(UTC)
    for case in normalized_suite["cases"]:
        authorization = case["authorization"]
        _parse_timestamp(
            authorization["approved_at"],
            f"case {case['case_key']}.authorization.approved_at",
        )
        if authorization.get("target_ownership") == "external":
            expires_at = _parse_timestamp(
                authorization["expires_at"],
                f"case {case['case_key']}.authorization.expires_at",
            )
            if expires_at <= now:
                _fail(
                    "authorization_expired",
                    f"case {case['case_key']}",
                    f"production-call approval expired at {expires_at.isoformat()}",
                )
        if any("agent_config" not in item for item in case["interactions"]):
            _fail(
                "config_unmaterialized",
                f"case {case['case_key']}",
                "every enabled interaction must have materialized executable config",
            )


def pending_summary(normalized_suite: Mapping[str, Any]) -> dict:
    """Machine-readable placeholder report for APIs and the campaign record."""

    enabled, disabled = [], {}
    for c in normalized_suite["cases"]:
        if c["enabled"]:
            enabled.append(c["case_key"])
        else:
            disabled[c["case_key"]] = list(c.get("pending", []))
    return {"enabled": enabled, "disabled": disabled}
