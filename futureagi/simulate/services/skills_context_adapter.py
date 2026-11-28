"""Pinned Skills-native control-plane adapter for the existing Simulation lifecycle.

This module never imports the application or launches a second runner. The hosted
provider launches the packaged fi.context.adapters.simulation_entrypoint after
its normal admission, private-storage and spending gates have succeeded.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from uuid import UUID

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import manifest_digest
from model_hub.services.skills_hub.r3.native_reducer import (
    NativeCheck,
    NativeTrial,
    reduce_native_results,
    validate_matrix,
)
from model_hub.services.skills_hub.v11.inventory import canonical_json, fingerprint

PROTOCOL = "skills-native-v1"
ENTRYPOINT_MODULE = "fi.context.adapters.simulation_entrypoint"
CONFIGURATION_FIELDS = {
    "code_commit",
    "code_digest",
    "entrypoint",
    "image_digest",
    "runtime_version",
    "packages",
    "package_lock_digest",
    "model",
    "connection_id",
    "connection_revision",
    "tools_digest",
    "retrieval_fixture_digest",
    "state_schema",
    "checkpoint_digest",
    "side_effect_policy",
    "reset_entrypoint",
    "context_mount",
    "observable_boundaries",
}
BOUNDARIES = {
    "catalog",
    "child_selection",
    "root_delivery",
    "file_delivery",
    "model_request",
    "tool_result",
    "checkpoint",
}


@dataclass(frozen=True)
class NativeExecutionSpec:
    configuration: dict
    snapshots: dict[str, dict | None]
    tasks: list[dict]
    matrix: list[dict]
    fingerprint: str

    def guest_payload(self) -> dict:
        # A fresh JSON value prevents a caller from mutating this spec through a
        # provider request. Expectations remain in the control-plane evaluator.
        payload: dict = json.loads(
            canonical_json(
                {
                    "schema": 1,
                    "protocol": PROTOCOL,
                    "adapter_module": ENTRYPOINT_MODULE,
                    "configuration": self.configuration,
                    "configuration_digest": fingerprint(self.configuration),
                    "snapshots": self.snapshots,
                    "tasks": self.tasks,
                    "matrix": self.matrix,
                    "execution_fingerprint": self.fingerprint,
                    "mounts": {
                        "context": {"path": "/fi_context/", "read_only": True},
                        "source": {"path": "/fi_app/", "read_only": True},
                        "state": {"path": "/fi_state/", "read_only": False},
                        "output": {"path": "/fi_output/", "read_only": False},
                    },
                }
            )
        )
        return payload


def validate_native_result_receipt(
    *,
    spec: NativeExecutionSpec,
    attempt_id: UUID,
    generation: int,
    required_checks: dict[str, list[dict]],
    receipt: dict,
) -> NativeTrial:
    """Validate after hosted ingestion authenticates and locks its current attempt.

    Attempt identity and generation come from the database, not request headers.
    Persist through HostedHarnessReceipt; this does not create another result store.
    """
    fields = {
        "schema",
        "protocol",
        "attempt_id",
        "claim_generation",
        "execution_fingerprint",
        "case_id",
        "variant",
        "repeat_index",
        "status",
        "checks",
    }
    if (
        not isinstance(receipt, dict)
        or set(receipt) != fields
        or type(receipt["schema"]) is not int
        or receipt["schema"] != 1
        or receipt["protocol"] != PROTOCOL
        or receipt["attempt_id"] != str(attempt_id)
        or type(receipt["claim_generation"]) is not int
        or receipt["claim_generation"] != generation
        or receipt["execution_fingerprint"] != spec.fingerprint
        or type(receipt["repeat_index"]) is not int
        or not isinstance(receipt["checks"], list)
        or len(receipt["checks"]) > 50
    ):
        raise SkillsHubError(
            "invalid_receipt",
            "Native receipt does not match the current execution attempt.",
            status=409,
        )
    checks = []
    for check in receipt["checks"]:
        if not isinstance(check, dict) or set(check) != {
            "check_id",
            "family",
            "verdict",
        }:
            raise SkillsHubError(
                "invalid_receipt", "Use exact native check result fields.", status=400
            )
        checks.append(NativeCheck(**check))
    trial = NativeTrial(
        receipt["case_id"],
        receipt["variant"],
        receipt["repeat_index"],
        receipt["status"],
        tuple(checks),
    )
    # The same reducer validates membership, exact check identities/applicability
    # and missing assertions at ingestion and later aggregation.
    reduce_native_results(
        matrix=spec.matrix, trials=[trial], required_checks=required_checks
    )
    return trial


def validate_native_configuration(configuration: dict) -> dict:
    """Domain validation of r3.2 pins; validation is not runtime qualification."""

    def invalid() -> None:
        raise SkillsHubError(
            "runtime_incompatible",
            "The application needs a complete pinned native adapter configuration.",
            status=422,
        )

    if (
        not isinstance(configuration, dict)
        or set(configuration) != CONFIGURATION_FIELDS
    ):
        invalid()
    try:
        value: dict = json.loads(canonical_json(configuration))
    except (ValueError, TypeError, RecursionError) as exc:
        raise SkillsHubError(
            "runtime_incompatible",
            "Configuration must contain finite JSON values.",
            status=422,
        ) from exc
    if len(canonical_json(value).encode()) > 65536:
        invalid()
    for field in (
        "code_digest",
        "image_digest",
        "package_lock_digest",
        "tools_digest",
        "retrieval_fixture_digest",
        "checkpoint_digest",
    ):
        if (
            not isinstance(value[field], str)
            or re.fullmatch(r"sha256:[a-f0-9]{64}", value[field]) is None
        ):
            invalid()
    if (
        not isinstance(value["code_commit"], str)
        or re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", value["code_commit"]) is None
    ):
        invalid()
    for field in ("entrypoint", "reset_entrypoint"):
        if (
            not isinstance(value[field], str)
            or len(value[field]) > 255
            or re.fullmatch(
                r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", value[field]
            )
            is None
        ):
            invalid()
    for field in ("runtime_version", "state_schema", "connection_revision"):
        if (
            not isinstance(value[field], str)
            or not value[field].strip()
            or len(value[field]) > 100
        ):
            invalid()
    try:
        value["connection_id"] = str(UUID(str(value["connection_id"])))
    except (ValueError, TypeError, AttributeError):
        invalid()
    model = value["model"]
    if (
        not isinstance(model, dict)
        or set(model) != {"model_id", "temperature", "max_output_tokens"}
        or not isinstance(model["model_id"], str)
        or not model["model_id"]
        or type(model["temperature"]) not in {int, float}
        or not 0 <= model["temperature"] <= 2
        or type(model["max_output_tokens"]) is not int
        or not 1 <= model["max_output_tokens"] <= 2048
    ):
        invalid()
    if (
        value["side_effect_policy"]
        not in {"read_only", "fixture_only", "idempotent_approved"}
        or value["context_mount"] != "/fi_context/"
    ):
        invalid()
    boundaries = value["observable_boundaries"]
    if (
        not isinstance(boundaries, list)
        or any(not isinstance(item, str) for item in boundaries)
        or len(set(boundaries)) != len(boundaries)
        or not set(boundaries) <= BOUNDARIES
        or not {"model_request", "root_delivery", "checkpoint"} <= set(boundaries)
    ):
        invalid()
    packages = value["packages"]
    if not isinstance(packages, list) or not 1 <= len(packages) <= 200:
        invalid()
    names = set()
    for package in packages:
        if not isinstance(package, dict) or set(package) != {
            "name",
            "version",
            "sha256",
        }:
            invalid()
        name, version, digest = package["name"], package["version"], package["sha256"]
        if (
            not all(isinstance(item, str) for item in (name, version, digest))
            or not 1 <= len(name) <= 200
            or not 1 <= len(version) <= 100
            or re.fullmatch(r"[a-f0-9]{64}", digest) is None
        ):
            invalid()
        key = re.sub(r"[-_.]+", "-", name).lower()
        if key in names:
            invalid()
        names.add(key)
    value["packages"] = sorted(packages, key=lambda item: item["name"])
    value["observable_boundaries"] = sorted(boundaries)
    return value


def build_execution_spec(
    *,
    configuration: dict,
    snapshots: dict[str, dict | None],
    cases: list[dict],
    matrix: list[dict],
    execution_fingerprint: str,
) -> NativeExecutionSpec:
    config = validate_native_configuration(configuration)
    planned = validate_matrix(matrix)
    variants = {cell["variant"] for cell in planned}
    if (
        set(snapshots) != variants
        or len({case["id"] for case in cases}) != len(cases)
        or {case["id"] for case in cases} != {cell["case_id"] for cell in planned}
    ):
        raise SkillsHubError(
            "invalid_test_config",
            "Tasks and immutable arms must exactly match the plan.",
            status=400,
        )
    candidate = snapshots["candidate"]
    if candidate is None or not isinstance(candidate.get("skill_id"), str):
        raise SkillsHubError(
            "invalid_test_config",
            "Native execution requires an identified candidate.",
            status=400,
        )
    for variant, snapshot in snapshots.items():
        if snapshot is not None and snapshot.get("skill_id") != candidate["skill_id"]:
            raise SkillsHubError(
                "evidence_mismatch",
                "Matched native arms must belong to the same subject.",
                status=409,
            )
        if variant == "without_target":
            if snapshot is not None:
                raise SkillsHubError(
                    "invalid_test_config",
                    "Without-context cannot retain a context tree.",
                    status=400,
                )
        elif snapshot is None or snapshot.get("digest") != manifest_digest(
            [
                {"path": item["path"], "content": item["content"]}
                for item in snapshot["files"]
            ]
        ):
            raise SkillsHubError(
                "candidate_changed",
                "A native arm does not match its complete manifest.",
                status=409,
            )
    if re.fullmatch(r"sha256:[a-f0-9]{64}", execution_fingerprint) is None:
        raise SkillsHubError(
            "invalid_test_config", "Bind execution to its accepted review.", status=400
        )
    tasks = [
        {
            "id": case["id"],
            "prompt": case["prompt"],
            "fixtures": [
                {"name": fixture["name"], "text": fixture["text"]}
                for fixture in case["fixtures"]
            ],
        }
        for case in cases
    ]
    payload = {
        "configuration": config,
        "snapshots": snapshots,
        "tasks": tasks,
        "matrix": planned,
    }
    if len(canonical_json(payload).encode()) > 16 * 1024 * 1024:
        raise SkillsHubError(
            "payload_too_large",
            "Native snapshot exceeds 16 MiB; no input was omitted.",
            status=413,
        )
    return NativeExecutionSpec(
        config,
        json.loads(canonical_json(snapshots)),
        tasks,
        planned,
        execution_fingerprint,
    )


def verify_guest_health(*, spec: NativeExecutionSpec, health: dict) -> None:
    """Call only after authenticating the existing attempt-bound guest channel."""
    expected = {
        "protocol": PROTOCOL,
        "adapter_module": ENTRYPOINT_MODULE,
        "configuration_digest": fingerprint(spec.configuration),
        "execution_fingerprint": spec.fingerprint,
        "code_digest": spec.configuration["code_digest"],
        "image_digest": spec.configuration["image_digest"],
        "packages": spec.configuration["packages"],
        "checkpoint_digest": spec.configuration["checkpoint_digest"],
        "read_only_source": True,
        "read_only_context": True,
        "separate_writable_state": True,
        "controlled_paid_egress": True,
    }
    if (
        not isinstance(health, dict)
        or health != expected
        or any(
            type(health.get(key)) is not bool
            for key in (
                "read_only_source",
                "read_only_context",
                "separate_writable_state",
                "controlled_paid_egress",
            )
        )
    ):
        raise SkillsHubError(
            "runtime_incompatible",
            "Guest identity, restored state or isolation did not match the reviewed adapter.",
            status=422,
        )
