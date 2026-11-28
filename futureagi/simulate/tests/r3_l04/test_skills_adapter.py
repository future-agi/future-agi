"""The real control-plane adapter pins code/state without importing customer code."""

from copy import deepcopy
from typing import Any
from uuid import uuid4

import pytest

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import manifest_digest
from model_hub.services.skills_hub.r3.native_reducer import planned_matrix
from model_hub.services.skills_hub.v11.inventory import fingerprint
from simulate.services.skills_context_adapter import (
    ENTRYPOINT_MODULE,
    PROTOCOL,
    build_execution_spec,
    validate_native_configuration,
    validate_native_result_receipt,
    verify_guest_health,
)


@pytest.fixture
def configuration():
    digest = "sha256:" + "a" * 64
    return {
        "code_commit": "a" * 40,
        "code_digest": digest,
        "entrypoint": "customer.application:run",
        "image_digest": digest,
        "runtime_version": "python-3.11.11",
        "packages": [{"name": "synthetic-app", "version": "1.0.0", "sha256": "b" * 64}],
        "package_lock_digest": digest,
        "model": {
            "model_id": "synthetic-model",
            "temperature": 0,
            "max_output_tokens": 1024,
        },
        "connection_id": str(uuid4()),
        "connection_revision": "1",
        "tools_digest": digest,
        "retrieval_fixture_digest": digest,
        "state_schema": "synthetic-checkpoint-v1",
        "checkpoint_digest": digest,
        "side_effect_policy": "fixture_only",
        "reset_entrypoint": "customer.application:reset",
        "context_mount": "/fi_context/",
        "observable_boundaries": [
            "model_request",
            "root_delivery",
            "checkpoint",
            "child_selection",
        ],
    }


@pytest.fixture
def spec(configuration):
    case: dict[str, Any] = {
        "id": str(uuid4()),
        "prompt": "Review this synthetic refund",
        "fixtures": [{"name": "policy", "text": "Synthetic fixture"}],
        "checks": [{"secret_expectation": "EXPECTED_MUST_NOT_BE_TASK_DATA"}],
        "expected_trigger": None,
        "child_selection": [{"expectation": "forbidden"}],
    }
    files = [{"path": "AGENTS.md", "content": "Use only supplied policy."}]
    subject_id = str(uuid4())
    snapshots: dict[str, dict[str, Any] | None] = {
        variant: {
            "skill_id": subject_id,
            "digest": manifest_digest(files),
            "files": files,
        }
        for variant in ("candidate", "reference")
    }
    return build_execution_spec(
        configuration=configuration,
        snapshots=snapshots,
        cases=[case],
        matrix=planned_matrix(
            case_ids=[case["id"]], baselines=["reference"], repetitions=1
        ),
        execution_fingerprint="sha256:" + "c" * 64,
    )


def test_build_is_local_and_does_not_leak_expectations(spec):
    payload = spec.guest_payload()
    assert payload["adapter_module"] == ENTRYPOINT_MODULE
    assert payload["configuration"]["entrypoint"] == "customer.application:run"
    assert "EXPECTED_MUST_NOT_BE_TASK_DATA" not in str(payload)
    assert "child_selection" not in str(payload["tasks"])
    assert payload["mounts"]["context"]["read_only"] is True
    assert payload["mounts"]["source"]["read_only"] is True
    assert payload["mounts"]["state"]["read_only"] is False
    assert payload["mounts"]["output"]["path"] != payload["mounts"]["context"]["path"]
    payload["snapshots"].clear()
    assert spec.snapshots


@pytest.mark.parametrize(
    "field,value",
    [
        ("entrypoint", "sh -c anything"),
        ("entrypoint", "app.run"),
        ("reset_entrypoint", ""),
        ("context_mount", "/"),
        ("code_commit", "latest"),
        ("checkpoint_digest", "unknown"),
        ("side_effect_policy", "unrestricted"),
        ("observable_boundaries", ["catalog"]),
        ("connection_id", "not-an-id"),
    ],
)
def test_incompatible_application_cannot_claim_pins(configuration, field, value):
    configuration[field] = value
    with pytest.raises(SkillsHubError) as error:
        validate_native_configuration(configuration)
    assert error.value.code == "runtime_incompatible"


def test_ambient_keys_and_duplicate_packages_are_not_configuration(configuration):
    extra = {**configuration, "api_key": "synthetic-not-a-key"}
    with pytest.raises(SkillsHubError):
        validate_native_configuration(extra)
    configuration["packages"].append(
        {**configuration["packages"][0], "name": "synthetic_app"}
    )
    with pytest.raises(SkillsHubError):
        validate_native_configuration(configuration)


def test_result_receipt_is_bound_to_attempt_generation_and_exact_check_plan(spec):
    attempt_id, check_id = uuid4(), str(uuid4())
    case_id = spec.matrix[0]["case_id"]
    checks = {
        case_id: [
            {
                "check_id": check_id,
                "family": "answer",
                "variants": ["candidate", "reference"],
            }
        ]
    }
    receipt = {
        "schema": 1,
        "protocol": PROTOCOL,
        "attempt_id": str(attempt_id),
        "claim_generation": 2,
        "execution_fingerprint": spec.fingerprint,
        "case_id": case_id,
        "variant": "candidate",
        "repeat_index": 0,
        "status": "completed",
        "checks": [{"check_id": check_id, "family": "answer", "verdict": "pass"}],
    }
    trial = validate_native_result_receipt(
        spec=spec,
        attempt_id=attempt_id,
        generation=2,
        required_checks=checks,
        receipt=receipt,
    )
    assert trial.checks[0].verdict == "pass"
    for fields in (
        {"attempt_id": str(uuid4())},
        {"claim_generation": 1},
        {"claim_generation": True},
        {"execution_fingerprint": "sha256:" + "f" * 64},
        {"case_id": str(uuid4())},
        {"repeat_index": 1},
        {"checks": [{"check_id": str(uuid4()), "family": "answer", "verdict": "pass"}]},
    ):
        with pytest.raises(SkillsHubError):
            validate_native_result_receipt(
                spec=spec,
                attempt_id=attempt_id,
                generation=2,
                required_checks=checks,
                receipt={**receipt, **fields},
            )


def health_for(spec):
    return {
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


def test_actual_guest_identity_state_and_isolation_must_match(spec):
    health = health_for(spec)
    verify_guest_health(spec=spec, health=health)
    for field in health:
        changed = deepcopy(health)
        changed[field] = False if isinstance(health[field], bool) else "different"
        with pytest.raises(SkillsHubError):
            verify_guest_health(spec=spec, health=changed)


def test_guest_isolation_claims_require_actual_booleans(spec):
    health = health_for(spec)
    health["read_only_source"] = 1
    with pytest.raises(SkillsHubError):
        verify_guest_health(spec=spec, health=health)


def test_native_historical_baseline_cannot_name_another_subject(spec):
    spec.snapshots["reference"]["skill_id"] = str(uuid4())
    with pytest.raises(SkillsHubError) as error:
        build_execution_spec(
            configuration=spec.configuration,
            snapshots=spec.snapshots,
            cases=spec.tasks,
            matrix=spec.matrix,
            execution_fingerprint=spec.fingerprint,
        )
    assert error.value.code == "evidence_mismatch"


def test_native_snapshot_hash_mismatch_blocks_before_guest(spec):
    from model_hub.services.skills_hub.packages import file_sha256

    item = spec.snapshots["candidate"]["files"][0]
    item["sha256"] = file_sha256(item["content"])
    item["content"] = "different"
    with pytest.raises(SkillsHubError) as error:
        build_execution_spec(
            configuration=spec.configuration,
            snapshots=spec.snapshots,
            cases=spec.tasks,
            matrix=spec.matrix,
            execution_fingerprint=spec.fingerprint,
        )
    assert error.value.code == "candidate_changed"
