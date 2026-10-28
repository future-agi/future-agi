"""Contract tests for the production certification suite manifest machinery."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from simulate.certification.manifest import (
    CertificationManifestError,
    campaign_idempotency_key,
    canonical_digest,
    case_idempotency_key,
    expand_enabled_cases,
    load_manifest_text,
    load_suite,
    pending_summary,
    require_admission_ready,
    suite_manifest_path,
    validate_manifest,
)

SHA = "a" * 40
SHA2 = "b" * 40


def _enabled_case(**overrides):
    case = {
        "case_key": "ride_booking__veh002_single",
        "enabled": True,
        "agent": {
            "name": "Ride Booking",
            "model": "gpt-x",
            "source": {
                "repository": "https://github.com/future-agi/ride-voice-agent",
                "commit_sha": SHA,
            },
        },
        "target": {"kind": "local_test", "ref": "local-ride-agent"},
        "run_test_id": "l1_chat_top_voice",
        "interactions": [
            {
                "interaction": 1,
                "scenario_ref": "scenarios:samples/agents.yaml",
                "config_ref": "futureagi/simulate/certification/fixtures/ride.yaml",
                "seed": 11,
                "secret_ref_set": [
                    {
                        "manager": "platform-vault",
                        "key": "RIDE_TARGET_CRED",
                        "purpose": "target_provider",
                    }
                ],
            },
            {
                "interaction": 2,
                "scenario_ref": "scenarios:samples/agents.yaml",
                "config_ref": "futureagi/simulate/certification/fixtures/ride.yaml",
                "seed": 22,
                "secret_ref_set": [],
            },
        ],
        "authorization": {
            "owner": "simulations",
            "automated_production_calls": "approved",
            "approved_by": "qa-oncall",
            "approved_at": "2026-10-01",
        },
    }
    case.update(overrides)
    return case


def _doc(cases):
    return {"version": 1, "suite_key": "test_suite", "defaults": {}, "cases": cases}


def _err(fn, code):
    with pytest.raises(CertificationManifestError) as ei:
        fn()
    assert ei.value.code == code
    return ei.value


# ---------------------------------------------------------------------------
# shipped suite
# ---------------------------------------------------------------------------


def test_production_suite_validates_and_is_all_placeholders():
    doc = load_suite("production_agents_v1")
    normalized = validate_manifest(doc)
    summary = pending_summary(normalized)
    assert summary["enabled"] == []
    assert len(summary["disabled"]) == 26
    for case in normalized["cases"]:
        assert case["enabled"] is False
        assert case["pending"], f"{case['case_key']} missing pending reasons"


def test_production_suite_is_not_admission_ready():
    normalized = validate_manifest(load_suite("production_agents_v1"))
    err = _err(lambda: require_admission_ready(normalized), "suite_not_admission_ready")
    assert "authorization_unverified" in err.message


def test_unknown_suite_key_rejected():
    _err(lambda: suite_manifest_path("no_such_suite"), "unknown_suite")
    _err(lambda: suite_manifest_path("../etc"), "invalid_suite_key")


# ---------------------------------------------------------------------------
# happy path for enabled entries
# ---------------------------------------------------------------------------


def test_enabled_case_validates_and_expands():
    normalized = validate_manifest(_doc([_enabled_case()]))
    specs = expand_enabled_cases(normalized)
    assert len(specs) == 1
    spec = specs[0]
    assert spec["case_key"] == "ride_booking__veh002_single"
    assert spec["authorization"]["automated_production_calls"] == "approved"
    assert [i["scenario_key"] for i in spec["interactions"]] == [
        "ride_booking__veh002_single__interaction_01",
        "ride_booking__veh002_single__interaction_02",
    ]
    require_admission_ready(normalized)


def test_case_key_canonicalization():
    normalized = validate_manifest(
        _doc([_enabled_case(case_key="Ride-Booking  VEH002")])
    )
    assert normalized["cases"][0]["case_key"] == "ride_booking_veh002"


# ---------------------------------------------------------------------------
# rejection matrix
# ---------------------------------------------------------------------------


def test_rejects_mutable_source_ref():
    case = _enabled_case()
    case["agent"]["source"]["commit_sha"] = "main"
    _err(lambda: validate_manifest(_doc([case])), "source_commit_unverified")


def test_rejects_missing_source():
    case = _enabled_case()
    del case["agent"]["source"]
    _err(lambda: validate_manifest(_doc([case])), "source_unrecorded")


def test_rejects_raw_secret_in_target_ref():
    case = _enabled_case()
    case["target"]["ref"] = "https://user:hunter2pass@agents.example.com/x"
    _err(lambda: validate_manifest(_doc([case])), "raw_secret_material")


def test_rejects_raw_secret_in_free_text():
    case = _enabled_case()
    case["notes"] = "debug token sk-abcdef1234567890 do not ship"
    _err(lambda: validate_manifest(_doc([case])), "raw_secret_material")


def test_rejects_api_key_in_secret_ref_key():
    case = _enabled_case()
    case["interactions"][0]["secret_ref_set"] = [
        {
            "manager": "platform-vault",
            "key": "api_key=abc123",
            "purpose": "target_provider",
        }
    ]
    _err(lambda: validate_manifest(_doc([case])), "raw_secret_material")


def test_rejects_non_vault_secret_manager():
    case = _enabled_case()
    case["interactions"][0]["secret_ref_set"] = [
        {"manager": "env", "key": "X", "purpose": "target_provider"}
    ]
    _err(lambda: validate_manifest(_doc([case])), "secret_manager_unsupported")


def test_rejects_duplicate_case_key_after_canonicalization():
    _err(
        lambda: validate_manifest(
            _doc(
                [
                    _enabled_case(case_key="ride-booking"),
                    _enabled_case(case_key="ride_booking"),
                ]
            )
        ),
        "duplicate_key",
    )


def test_rejects_duplicate_yaml_keys():
    text = "version: 1\nsuite_key: a\ncases:\n  - case_key: x\n    case_key: y\n"
    _err(lambda: load_manifest_text(text), "duplicate_key")


def test_rejects_enabled_with_pending_markers():
    case = _enabled_case(pending=["authorization_unverified"])
    _err(lambda: validate_manifest(_doc([case])), "invalid_schema")


def test_rejects_disabled_without_pending_markers():
    case = _enabled_case(enabled=False)
    _err(lambda: validate_manifest(_doc([case])), "unmarked_placeholder")


def test_rejects_enabled_without_authorization():
    case = _enabled_case()
    del case["authorization"]
    _err(lambda: validate_manifest(_doc([case])), "authorization_missing")


def test_rejects_unapproved_authorization():
    case = _enabled_case()
    case["authorization"]["automated_production_calls"] = "pending"
    _err(lambda: validate_manifest(_doc([case])), "authorization_unverified")


def test_hosted_service_requires_endpoint_review():
    case = _enabled_case()
    case["target"] = {"kind": "hosted_service", "ref": "agents.example.com"}
    _err(lambda: validate_manifest(_doc([case])), "authorization_unverified")
    case["authorization"]["target_endpoint_reviewed"] = True
    normalized = validate_manifest(_doc([case]))
    assert normalized["cases"][0]["target"]["kind"] == "hosted_service"


def test_rejects_single_interaction():
    case = _enabled_case()
    case["interactions"] = case["interactions"][:1]
    _err(lambda: validate_manifest(_doc([case])), "invalid_schema")


def test_rejects_bad_scenario_ref():
    case = _enabled_case()
    case["interactions"][0]["scenario_ref"] = "https://evil.example/x.yaml"
    _err(lambda: validate_manifest(_doc([case])), "invalid_scenario_ref")


def test_rejects_config_ref_escape():
    case = _enabled_case()
    case["interactions"][0]["config_ref"] = "../../secrets.env"
    _err(lambda: validate_manifest(_doc([case])), "invalid_config_ref")


# ---------------------------------------------------------------------------
# config materialization (repo_root checks)
# ---------------------------------------------------------------------------


def test_config_ref_materialized_from_repo(tmp_path: Path):
    fixture = tmp_path / "conf" / "ride.yaml"
    fixture.parent.mkdir(parents=True)
    fixture.write_bytes(b"scenario: ride\n")
    case = _enabled_case()
    for i in case["interactions"]:
        i["config_ref"] = "conf/ride.yaml"
        i["config_sha256"] = hashlib.sha256(b"scenario: ride\n").hexdigest()
    normalized = validate_manifest(_doc([case]), repo_root=tmp_path)
    assert normalized["cases"][0]["interactions"][0]["config_ref"] == "conf/ride.yaml"


def test_config_sha_mismatch_rejected(tmp_path: Path):
    (tmp_path / "ride.yaml").write_bytes(b"actual\n")
    case = _enabled_case()
    case["interactions"][0]["config_ref"] = "ride.yaml"
    case["interactions"][0]["config_sha256"] = "0" * 64
    _err(
        lambda: validate_manifest(_doc([case]), repo_root=tmp_path),
        "config_unmaterialized",
    )


def test_missing_config_file_rejected(tmp_path: Path):
    case = _enabled_case()
    case["interactions"][0]["config_ref"] = "nope.yaml"
    _err(
        lambda: validate_manifest(_doc([case]), repo_root=tmp_path),
        "config_unmaterialized",
    )


# ---------------------------------------------------------------------------
# digests and idempotency keys
# ---------------------------------------------------------------------------


def test_digest_is_order_stable():
    a = {"b": 1, "a": [1, {"y": 2, "x": 1}]}
    b = {"a": [1, {"x": 1, "y": 2}], "b": 1}
    assert canonical_digest(a) == canonical_digest(b)
    assert canonical_digest(a).startswith("sha256:")


def test_idempotency_key_formats():
    assert (
        campaign_idempotency_key("dep-1", "sha256:abc")
        == "certification:dep-1:sha256:abc"
    )
    assert case_idempotency_key("dep-1", "sha256:abc", "case_x") == (
        "certification:dep-1:sha256:abc:case_x"
    )


def test_yaml_roundtrip_loads_shipped_suite():
    text = suite_manifest_path("production_agents_v1").read_text()
    doc = load_manifest_text(text)
    assert doc["suite_key"] == "production_agents_v1"
    # sanity: file stays plain YAML-serializable
    yaml.safe_load(text)
