"""Bounded public-file consumer; deliberately independent of the host updater.

The compact schema below is pinned against the frozen S01 JSON schemas in tests.
Keeping it in the application package avoids a runtime dependency on the host
checkout (which is not present in application images). Only accepted scalars are
copied into responses. Public data is never evidence for a host action.
"""

import errno
import json
import logging
import os
import re
import stat
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Keep the standalone stdlib reader testable on the host's Python 3.9 floor.
UTC = timezone.utc  # noqa: UP017
logger = logging.getLogger(__name__)

STATUS_FILE = "/run/futureagi-host-update/status.json"
MAX_BYTES = 32768
MAX_DEPTH = 12
MAX_INTEGER = 9007199254740991
ACTIVE_STATES = frozenset(
    {
        "checking",
        "downloading",
        "backing_up",
        "applying",
        "verifying",
        "rolling_back",
        "restoring",
    }
)
COMPONENTS = (
    "standalone",
    "backend",
    "frontend",
    "fi_collector",
    "agentcc_gateway",
    "serving",
    "serving_gpu",
    "code_executor",
    "simulation_runner",
)
REASONS = (
    "manifest_unavailable",
    "signature_rejected",
    "channel_mismatch",
    "version_policy_blocked",
    "release_incomplete",
    "discovery_incomplete",
    "immutable_release_changed",
    "network_unavailable",
    "rate_limited",
    "cache_invalid",
    "ownership_unconfirmed",
    "deployment_files_mismatch",
    "required_env_missing",
    "unsupported_contract",
    "uncertified_release",
    "configuration_changed",
    "backup_required",
    "backup_failed",
    "backup_proof_invalid",
    "backup_expired",
    "stale_stage",
    "env_conflict",
    "initialization_required",
    "manual_stage_present",
    "recovery_required",
    "readiness_failed",
    "operation_interrupted",
    "operation_timeout",
    "rollback_ineligible",
    "rollback_failed",
    "failed_target_latched",
    "running_versions_unknown",
    "mixed_running_versions",
    "outside_window",
    "insufficient_window",
    "clock_skew",
    "timezone_unavailable",
    "host_verifier_required",
    "image_digest_mismatch",
    "disk_space_insufficient",
    "status_reporting_unavailable",
    "scheduler_unregistered",
    "unsupported_installation",
    "control_invalid",
    "process_failed",
    "restore_not_certified",
    "transition_not_rehearsed",
    "platform_not_rehearsed",
    "restore_procedure_unsupported",
    "migration_inventory_unsupported",
    "migration_inventory_mismatch",
    "backup_inventory_mismatch",
    "restore_capacity_unknown",
    "restore_capacity_insufficient",
    "restore_child_ambiguous",
    "restore_verification_failed",
    "automatic_recovery_pending",
    "restored_after_update_failure",
    "migration_exceeds_window",
    "insufficient_disk_for_migration",
    "backup_exceeds_window",
    "insufficient_disk_for_backup",
    "migration_stalled",
    "disk_watermark_abort",
)
API_REASONS = (
    "not_configured",
    "not_reported",
    "unreadable",
    "malformed",
    "unsupported_schema",
    "untrusted_file",
    "clock_skew",
    "installation_mismatch",
)


def _object(**properties):
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def _enum(*values):
    return {"type": "string", "enum": list(values)}


def _nullable(value):
    return {"anyOf": [value, {"type": "null"}]}


def _array(items, maximum, *, minimum=0, unique=False):
    result = {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}
    if unique:
        result["uniqueItems"] = True
    return result


def _text(maximum, *, minimum=1, pattern=None):
    result = {"type": "string", "minLength": minimum, "maxLength": maximum}
    if pattern:
        result["pattern"] = pattern
    return result


BOOLEAN = {"type": "boolean"}
INTEGER = {"type": "integer", "minimum": 0, "maximum": MAX_INTEGER}
VERSION = _text(
    128,
    pattern=(
        r"^v(0|[1-9][0-9]{0,17})\.(0|[1-9][0-9]{0,17})\.(0|[1-9][0-9]{0,17})"
        r"(-(beta|rc)\.(0|[1-9][0-9]{0,17}))?$"
    ),
)
TIME = {
    **_text(
        20,
        minimum=20,
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$",
    ),
    "format": "date-time",
}
CLOCK_TIME = _text(5, pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
REASON_LIST = _array(_enum(*REASONS), 12, unique=True)
WINDOW = {
    "oneOf": [
        _object(kind={"const": "always", "type": "string"}, timezone=_text(64)),
        _object(
            kind={"const": "interval", "type": "string"},
            timezone=_text(64),
            days_of_week=_array(
                {"type": "integer", "minimum": 1, "maximum": 7},
                7,
                minimum=1,
                unique=True,
            ),
            start=CLOCK_TIME,
            end=CLOCK_TIME,
        ),
    ]
}
PUBLIC_SCHEMA = _object(
    schema_version={"type": "integer", "const": 1},
    sequence=INTEGER,
    generated_at=TIME,
    installation=_object(
        slot_id=_text(32, pattern=r"^[0-9a-f]{32}$"),
        topology=_enum("standalone", "distributed"),
        production=BOOLEAN,
    ),
    versions=_object(
        observed_at=_nullable(TIME),
        runtime_state=_enum("consistent", "mixed", "unknown"),
        selected_release=_nullable(VERSION),
        running_release=_nullable(VERSION),
        components=_array(
            _object(
                component=_enum(*COMPONENTS),
                selected_version=_nullable(VERSION),
                observed_versions=_array(VERSION, 8, unique=True),
                observation=_enum("known", "unknown", "not_deployed"),
            ),
            9,
        ),
    ),
    policy=_object(
        channel=_enum("stable", "beta"),
        version_policy=_enum("patch", "minor", "major"),
        check_interval_seconds={"type": "integer", "minimum": 300, "maximum": 86400},
    ),
    automation=_object(
        mode=_enum("manual", "notify", "download", "apply"),
        enabled=BOOLEAN,
        state=_enum(
            "not_implemented", "disabled", "manual", "enabled", "paused", "blocked"
        ),
        paused_until=_nullable(TIME),
        pause_indefinite=BOOLEAN,
        maintenance_window=_nullable(WINDOW),
        last_tick_at=_nullable(TIME),
        next_check_at=_nullable(TIME),
        next_action_at=_nullable(TIME),
    ),
    discovery=_object(
        state=_enum("not_checked", "current", "available", "blocked", "failed"),
        last_attempt_at=_nullable(TIME),
        checked_at=_nullable(TIME),
        candidate_version=_nullable(VERSION),
        verification=_enum("not_checked", "verified", "rejected"),
        reason_codes=REASON_LIST,
    ),
    download=_object(
        state=_enum("not_downloaded", "downloading", "downloaded", "failed", "unknown"),
        target_version=_nullable(VERSION),
        completed_at=_nullable(TIME),
        verified_components=_array(_enum(*COMPONENTS), 9, unique=True),
        reason_codes=REASON_LIST,
    ),
    operation=_object(
        state=_enum(
            "idle",
            "checking",
            "downloading",
            "backing_up",
            "staged",
            "awaiting_initialization",
            "applying",
            "verifying",
            "succeeded",
            "failed",
            "interrupted",
            "rolling_back",
            "rolled_back",
            "blocked",
            "unknown",
        ),
        kind=_nullable(_enum("check", "download", "apply", "rollback")),
        stage=_nullable(
            _enum(
                "planning",
                "backup",
                "image_fetch",
                "image_verification",
                "env_selection",
                "production_initialization",
                "rollout",
                "readiness",
                "recovery",
            )
        ),
        started_at=_nullable(TIME),
        finished_at=_nullable(TIME),
        reported_at=_nullable(TIME),
        reason_codes=REASON_LIST,
    ),
    last_run=_nullable(
        _object(
            kind=_enum("apply", "rollback"),
            automatic=BOOLEAN,
            outcome=_enum(
                "succeeded",
                "staged",
                "rolled_back",
                "failed_before_rollout",
                "failed_during_rollout",
                "interrupted_before_rollout",
                "interrupted_during_rollout",
                "blocked",
            ),
            from_version=_nullable(VERSION),
            target_version=_nullable(VERSION),
            finished_at=_nullable(TIME),
            fallback_outcome=_enum("none", "succeeded", "failed", "ineligible"),
        )
    ),
    safety=_object(
        backup_required=BOOLEAN,
        backup_state=_enum(
            "not_evaluated",
            "required",
            "reported_complete",
            "failed",
            "explicitly_skipped",
        ),
        readiness=_enum("unknown", "pending", "passed", "failed"),
        automatic_rollback=_enum("unsupported", "disabled", "eligible", "ineligible"),
        operator_action=_enum(
            "none",
            "inspect_host",
            "prepare_installation",
            "initialize_then_apply",
            "review_recovery",
            "repair_status_reporting",
        ),
    ),
)


# Optional additions travel as a complete group. The first branch preserves
# legacy/manual snapshots; the outer envelope documents all possible fields in
# Swagger 2, with exact alternatives retained in x-anyOf by the serializer.
# Both readers validate the complete branches and reject partial/unknown fields.
def _extend(schema, **additions):
    properties = {**schema["properties"], **additions}
    legacy = {
        **schema,
        "properties": properties,
        "maxProperties": len(schema["required"]),
    }
    complete = _object(**properties)
    return {**complete, "required": schema["required"], "anyOf": [legacy, complete]}


AUTOMATIC_PHASES = (
    "attempt_reserved",
    "quiescing",
    "quiesced",
    "archiving",
    "backup_sealed",
    "env_commit_pending",
    "env_selected",
    "target_create_pending",
    "target_created",
    "target_starting",
    "target_verifying",
    "success_decided",
    "success_metadata_committed",
    "succeeded",
    "restore_decided",
    "restore_quiescing",
    "restore_quiesced",
    "restore_inputs_verified",
    "restoring_volumes",
    "restore_files_verified",
    "config_revert_pending",
    "config_reverted",
    "restore_data_committed",
    "previous_create_pending",
    "previous_created",
    "previous_start_armed",
    "previous_starting",
    "previous_verifying",
    "restore_verified",
    "restore_metadata_committed",
    "restored",
    "abort_quiescence",
    "failed_available",
)
RESTORE_STATES = ("not_needed", "running", "verified", "failed", "recovery_required")
ESTIMATE_FIELDS = (
    "backup_seconds",
    "migration_seconds",
    "restore_seconds",
    "extra_disk_bytes",
    "backup_required_free_bytes",
    "docker_required_free_bytes",
    "protected_restore_reserve_bytes",
    "forward_seconds",
    "recovery_seconds",
    "total_seconds",
    "migration_deadline_seconds",
    "migration_stall_timeout_seconds",
)
properties = PUBLIC_SCHEMA["properties"]
operation = properties["operation"]
operation["properties"]["state"]["enum"] += [
    "restoring",
    "restored",
    "recovery_required",
]
operation["properties"]["kind"]["anyOf"][0]["enum"].append("restore")
operation["properties"]["stage"]["anyOf"][0]["enum"] += [
    "quiescence",
    "restore",
    "stability",
]
properties["operation"] = _extend(operation, phase=_nullable(_enum(*AUTOMATIC_PHASES)))
run = properties["last_run"]["anyOf"][0]
properties["last_run"] = _nullable(
    _extend(
        run,
        forward_result=_enum(
            "not_started", "running", "succeeded", "failed", "interrupted"
        ),
        restore_result=_enum(*RESTORE_STATES),
        effective_version=_nullable(VERSION),
        failed_target_version=_nullable(VERSION),
    )
)
safety = properties["safety"]
safety["properties"]["backup_state"]["enum"] += ["pending", "quiescing", "sealed"]
properties["safety"] = _extend(
    safety,
    fallback_strategy=_enum("restore_backup", "none"),
    restore_state=_enum(*RESTORE_STATES),
    snapshot_at=_nullable(TIME),
    loss_window_start=_nullable(TIME),
    loss_window_end=_nullable(TIME),
    loss_window_qualification=_enum(
        "not_applicable", "bounded", "conservative", "unknown"
    ),
    guidance_id=_enum("none", "inspect_host", "review_recovery"),
    estimates=_nullable(_object(**dict.fromkeys(ESTIMATE_FIELDS, INTEGER))),
    failed_target_version=_nullable(VERSION),
)
FRESHNESS = _enum("fresh", "stale", "unknown")
RESPONSE_SCHEMA = _object(
    status={"type": "boolean", "const": True},
    result=_object(
        api_schema_version={"type": "integer", "const": 1},
        availability=_enum("available", "unavailable", "unsupported"),
        reason_code=_nullable(_enum(*API_REASONS)),
        freshness=FRESHNESS,
        age_seconds=_nullable(INTEGER),
        discovery_freshness=FRESHNESS,
        versions_freshness=FRESHNESS,
        progress_freshness=FRESHNESS,
        snapshot=_nullable(PUBLIC_SCHEMA),
    ),
)


class StatusUnavailable(ValueError):
    """A fixed reason only; never attach file data, paths or original exceptions."""

    def __init__(self, reason="malformed"):
        self.reason = reason if reason in API_REASONS else "malformed"
        super().__init__(self.reason)


def _require(condition, reason="malformed"):
    if not condition:
        raise StatusUnavailable(reason)


def _time(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _shape(value, schema):
    """Validate the frozen schema subset and reconstruct a closed scalar tree."""
    for union in ("anyOf", "oneOf"):
        if union in schema:
            accepted = []
            for option in schema[union]:
                try:
                    accepted.append(_shape(value, option))
                except StatusUnavailable:
                    pass
            _require(len(accepted) == 1 if union == "oneOf" else bool(accepted))
            return accepted[0]
    kind = schema["type"]
    _require(
        type(value)
        is {
            "object": dict,
            "array": list,
            "string": str,
            "integer": int,
            "boolean": bool,
            "null": type(None),
        }[kind]
    )
    if "const" in schema:
        _require(value == schema["const"])
    if "enum" in schema:
        _require(value in schema["enum"])
    if kind == "object":
        _require(set(value) == set(schema["required"]))
        return {key: _shape(value[key], schema["properties"][key]) for key in value}
    if kind == "array":
        _require(schema["minItems"] <= len(value) <= schema["maxItems"])
        if schema.get("uniqueItems"):
            _require(all(item not in value[:index] for index, item in enumerate(value)))
        return [_shape(item, schema["items"]) for item in value]
    if kind == "string":
        _require(
            schema.get("minLength", 0)
            <= len(value)
            <= schema.get("maxLength", MAX_BYTES)
        )
        if "pattern" in schema:
            _require(re.fullmatch(schema["pattern"], value) is not None)
        if schema.get("format") == "date-time":
            try:
                _time(value)
            except (ValueError, OverflowError):
                raise StatusUnavailable() from None
    if kind == "integer":
        _require(schema.get("minimum", value) <= value <= schema.get("maximum", value))
    return value


def _depth(value, depth=1):
    _require(depth <= MAX_DEPTH)
    if isinstance(value, dict):
        for child in value.values():
            _depth(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _depth(child, depth + 1)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _invalid_constant(value):
    raise StatusUnavailable()


def _expected_installation(identity):
    """Validate app-known identity, optionally including a host production fact.

    The app knows its registered slot and runtime topology, not the host's
    --production registration. ENV_TYPE is unrelated. A caller that independently
    knows production may still require an exact match; omission ignores only that
    comparison, never the required boolean in the public snapshot.
    """
    properties = PUBLIC_SCHEMA["properties"]["installation"]["properties"]
    keys = ("slot_id", "topology")
    if type(identity) is dict and "production" in identity:
        keys += ("production",)
    return _shape(identity, _object(**{key: properties[key] for key in keys}))


def _semantics(snapshot, expected_installation, now):
    installation = snapshot["installation"]
    _require(
        not installation["production"] or installation["topology"] == "distributed"
    )
    expected = _expected_installation(expected_installation)
    _require(
        all(installation[key] == value for key, value in expected.items()),
        "installation_mismatch",
    )
    generated = _time(snapshot["generated_at"])
    _require((generated - now).total_seconds() <= 300, "clock_skew")
    versions = snapshot["versions"]
    components = versions["components"]
    _require(len({item["component"] for item in components}) == len(components))
    for item in components:
        _require(
            bool(item["observed_versions"])
            if item["observation"] == "known"
            else not item["observed_versions"]
        )
    if versions["runtime_state"] == "consistent":
        active = [item for item in components if item["observation"] != "not_deployed"]
        _require(
            bool(active)
            and versions["running_release"] is not None
            and versions["observed_at"] is not None
        )
        _require(
            all(
                item["observation"] == "known"
                and item["observed_versions"] == [versions["running_release"]]
                for item in active
            )
        )
    else:
        _require(versions["running_release"] is None)
    discovery = snapshot["discovery"]
    candidate = discovery["candidate_version"]
    if candidate is not None or discovery["state"] in ("current", "available"):
        _require(
            candidate is not None
            and discovery["verification"] == "verified"
            and discovery["checked_at"] is not None
        )
    if discovery["state"] == "current":
        _require(not discovery["reason_codes"])
    if (
        discovery["state"] == "failed" or discovery["verification"] == "rejected"
    ) and discovery["checked_at"]:
        _require(
            discovery["last_attempt_at"] is not None
            and _time(discovery["checked_at"]) <= _time(discovery["last_attempt_at"])
        )
    download = snapshot["download"]
    if download["state"] == "downloaded":
        _require(
            candidate is not None
            and download["target_version"] == candidate
            and download["completed_at"] is not None
            and bool(download["verified_components"])
        )
    if download["state"] == "not_downloaded":
        _require(
            download["target_version"] is None
            and download["completed_at"] is None
            and not download["verified_components"]
        )
    automation = snapshot["automation"]
    until = automation["paused_until"]
    _require(not (automation["pause_indefinite"] and until is not None))
    paused = automation["pause_indefinite"] or (
        until is not None and _time(until) > generated
    )
    state = automation["state"]
    if state == "not_implemented":
        _require(automation["mode"] == "manual" and not automation["enabled"])
    elif state != "blocked":
        effective = (
            "disabled"
            if not automation["enabled"]
            else "paused"
            if paused
            else "manual"
            if automation["mode"] == "manual"
            else "enabled"
        )
        _require(state == effective)
    if state != "enabled":
        for key in ("next_check_at", "next_action_at"):
            _require(
                automation[key] is None
                or (
                    state == "paused" and until is not None and automation[key] == until
                )
            )
    window = automation["maintenance_window"]
    if window is not None:
        _require(
            re.fullmatch(r"[A-Za-z0-9_+\-]+(?:/[A-Za-z0-9_+\-]+)*", window["timezone"])
            is not None
        )
        try:
            if window["timezone"] != "UTC":
                ZoneInfo(window["timezone"])
        except (ZoneInfoNotFoundError, OSError, ValueError):
            raise StatusUnavailable() from None
        if window["kind"] == "interval":
            _require(window["start"] != window["end"])
    operation = snapshot["operation"]
    if operation["finished_at"] is not None:
        _require(
            operation["started_at"] is not None
            and _time(operation["finished_at"]) >= _time(operation["started_at"])
        )
    if operation["state"] in ACTIVE_STATES:
        _require(operation["reported_at"] is not None)
    safety = snapshot["safety"]
    run = snapshot["last_run"]
    if "restore_state" in safety:
        if safety["restore_state"] in (
            "running",
            "failed",
            "recovery_required",
        ) or operation["state"] in ("restoring", "recovery_required"):
            _require(
                versions["runtime_state"] == "unknown"
                and versions["running_release"] is None
            )
            _require(run is None or run.get("effective_version") is None)
        start, end = safety["loss_window_start"], safety["loss_window_end"]
        _require(end is None or start is not None and _time(start) <= _time(end))
        _require(
            safety["loss_window_qualification"] != "bounded"
            or start is not None
            and end is not None
        )
    observed = [
        versions["observed_at"],
        discovery["checked_at"],
        discovery["last_attempt_at"],
        download["completed_at"],
        automation["last_tick_at"],
        operation["started_at"],
        operation["finished_at"],
        operation["reported_at"],
    ]
    if snapshot["last_run"] is not None:
        observed.append(snapshot["last_run"]["finished_at"])
    if "restore_state" in safety:
        observed.extend(
            safety[key]
            for key in ("snapshot_at", "loss_window_start", "loss_window_end")
        )
    _require(
        all(
            value is None or (_time(value) - generated).total_seconds() <= 300
            for value in observed
        )
    )


def decode_public_status(raw, *, expected_installation, now):
    """Validate untrusted bytes. Identity and aware server time are mandatory."""
    _require(
        isinstance(now, datetime)
        and now.tzinfo is not None
        and now.utcoffset() is not None,
        "clock_skew",
    )
    _require(type(raw) is bytes and len(raw) <= MAX_BYTES)
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_invalid_constant,
        )
        _depth(value)
        _require(type(value) is dict)
        if type(value.get("schema_version")) is int and value["schema_version"] != 1:
            raise StatusUnavailable("unsupported_schema")
        snapshot = _shape(value, PUBLIC_SCHEMA)
        _semantics(snapshot, expected_installation, now.astimezone(UTC))
        return snapshot
    except (UnicodeError, ValueError, RecursionError, OverflowError) as exc:
        reason = exc.reason if isinstance(exc, StatusUnavailable) else "malformed"
        raise StatusUnavailable(reason) from None


def _read_file(path):
    """Walk absolute ancestors by descriptor; never follow a symlink or block on FIFO.

    This private primitive also permits native temporary test paths. The public
    entry point below accepts only STATUS_FILE, never a request-supplied path.
    """
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC
    descriptors = []
    try:
        parts = path.split("/")
        _require(
            parts[0] == "" and all(part not in ("", ".", "..") for part in parts[1:]),
            "untrusted_file",
        )
        directory = os.open("/", flags)
        descriptors.append(directory)
        for part in parts[1:-1]:
            directory = os.open(part, flags, dir_fd=directory)
            descriptors.append(directory)
            _require(not os.fstat(directory).st_mode & 0o022, "untrusted_file")
        file_fd = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=directory,
        )
        descriptors.append(file_fd)
        before = os.fstat(file_fd)
        _require(
            stat.S_ISREG(before.st_mode)
            and before.st_nlink == 1
            and not before.st_mode & 0o022,
            "untrusted_file",
        )
        _require(before.st_size <= MAX_BYTES)
        raw = bytearray()
        while len(raw) <= MAX_BYTES:
            chunk = os.read(file_fd, MAX_BYTES + 1 - len(raw))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(file_fd)
        _require(len(raw) <= MAX_BYTES)
        # Changes to this inode are not atomic publication. A replaced pathname
        # is fine: no path re-stat and no link-count comparison after the read.
        _require(
            (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
        )
        _require(not after.st_mode & 0o022, "untrusted_file")
        return bytes(raw)
    except FileNotFoundError:
        raise StatusUnavailable("not_reported") from None
    except OSError as exc:
        reason = (
            "untrusted_file"
            if exc.errno in (errno.ELOOP, errno.ENOTDIR)
            else "unreadable"
        )
        raise StatusUnavailable(reason) from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def unavailable_response(reason):
    _require(reason in API_REASONS)
    return {
        "status": True,
        "result": {
            "api_schema_version": 1,
            "availability": "unsupported"
            if reason == "unsupported_schema"
            else "unavailable",
            "reason_code": reason,
            "freshness": "unknown",
            "age_seconds": None,
            "discovery_freshness": "unknown",
            "versions_freshness": "unknown",
            "progress_freshness": "unknown",
            "snapshot": None,
        },
    }


def _freshness(value, now, maximum):
    if value is None:
        return "unknown"
    return "stale" if (now - _time(value)).total_seconds() > maximum else "fresh"


def available_response(snapshot, *, now):
    operation = snapshot["operation"]
    return {
        "status": True,
        "result": {
            "api_schema_version": 1,
            "availability": "available",
            "reason_code": None,
            "age_seconds": max(
                0, int((now - _time(snapshot["generated_at"])).total_seconds())
            ),
            "freshness": _freshness(snapshot["generated_at"], now, 86400),
            "discovery_freshness": _freshness(
                snapshot["discovery"]["checked_at"],
                now,
                snapshot["policy"]["check_interval_seconds"] + 300,
            ),
            "versions_freshness": _freshness(
                snapshot["versions"]["observed_at"], now, 86400
            ),
            "progress_freshness": _freshness(
                operation["reported_at"]
                if operation["state"] in ACTIVE_STATES
                else None,
                now,
                300,
            ),
            "snapshot": snapshot,
        },
    }


def read_public_status(*, configured_file, expected_installation, clock=None):
    """Fixed mount only. Failure is local to reporting, never application startup."""
    if not configured_file:
        return unavailable_response("not_configured")
    if configured_file != STATUS_FILE:
        return unavailable_response("untrusted_file")
    try:
        _expected_installation(expected_installation)
    except StatusUnavailable:
        return unavailable_response("installation_mismatch")
    try:
        now = (clock or (lambda: datetime.now(UTC)))()
        _require(
            isinstance(now, datetime)
            and now.tzinfo is not None
            and now.utcoffset() is not None,
            "clock_skew",
        )
        now = now.astimezone(UTC)
    except (OSError, ValueError, TypeError, OverflowError):
        return unavailable_response("clock_skew")
    try:
        snapshot = decode_public_status(
            _read_file(STATUS_FILE),
            expected_installation=expected_installation,
            now=now,
        )
        return _shape(available_response(snapshot, now=now), RESPONSE_SCHEMA)
    except StatusUnavailable as exc:
        logger.warning(
            "Host update status unavailable",
            extra={
                "reason_code": exc.reason,
                "correlation_id": uuid4().hex,
            },
        )
        return unavailable_response(exc.reason)
