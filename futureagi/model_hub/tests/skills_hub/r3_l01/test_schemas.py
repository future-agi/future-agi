"""Strict cross-lane wire contracts reject ambiguous or uncontrolled state."""

from uuid import uuid4

import pytest
from rest_framework.serializers import ChoiceField

from model_hub.serializers import skills_hub_r3 as r3
from model_hub.serializers import skills_hub_v12 as v12

DIGEST = "sha256:" + "a" * 64


def test_context_links_are_bounded_and_identity_unique():
    links = [
        {"skill_id": str(uuid4()), "selector": {"label": "production"}}
        for _ in range(20)
    ]
    base = {
        "schema": 1,
        "description": "Reviewed standing instructions",
        "links": links,
    }
    assert v12.ContextSpec(data=base).is_valid()
    assert not v12.ContextSpec(data={**base, "links": links + [links[0]]}).is_valid()
    assert not v12.ContextSpec(data={**base, "links": [links[0], links[0]]}).is_valid()
    assert not v12.ContextSpec(data={**base, "schema": True}).is_valid()


@pytest.mark.parametrize(
    "field,value",
    [
        ("force", True),
        ("labels", ["production"]),
        ("resolved_lock", {}),
        ("source", {}),
    ],
)
def test_context_publication_has_no_legacy_force_or_implicit_promotion(field, value):
    body = {
        "revision": 2,
        "base_version": 1,
        "expected_digest": DIGEST,
        "expected_head_version": 3,
        "review_fingerprint": DIGEST,
        "note": "",
        "acknowledge_warnings": True,
    }
    assert v12.ContextPublish(data=body).is_valid()
    assert not v12.ContextPublish(data={**body, field: value}).is_valid()


def test_ordinary_save_cannot_inject_server_owned_state():
    body = {
        "revision": 2,
        "base_version": 1,
        "files": [{"path": "AGENTS.md", "content": "Policy"}],
        "description": "Purpose",
        "note": "",
    }
    assert v12.DraftSave(data=body).is_valid()
    for field in ["links", "resolved_lock", "source", "expected_head_version"]:
        assert not v12.DraftSave(data={**body, field: {}}).is_valid()


def test_evidence_kind_and_configuration_must_agree():
    identity = {
        "model": {"model_id": "synthetic", "temperature": 0, "max_output_tokens": 128},
        "judge": None,
        "harness_version": "v1",
        "adapter_version": "v1",
        "evaluator_version": "v1",
    }
    body = {
        "schema": 1,
        "source": "skills_text",
        "source_id": str(uuid4()),
        "subject_kind": "agent_context",
        "subject_id": str(uuid4()),
        "candidate_digest": DIGEST,
        "execution_fingerprint": DIGEST,
        "status": "completed",
        "visibility": "workspace",
        "payload_expires_at": "2030-01-01T00:00:00Z",
        "configuration": {"schema": 1, "digest": DIGEST, "text": identity},
        "annotation_revision": 0,
    }
    serializer = r3.EvidenceRef(data=body)
    assert serializer.is_valid(), serializer.errors
    assert not r3.EvidenceRef(data={**body, "source": "simulation_native"}).is_valid()
    assert not r3.EvidenceRef(data={**body, "status": "running"}).is_valid()
    assert not r3.EvidenceRef(data={**body, "annotation_revision": -1}).is_valid()


def test_target_configuration_requires_one_identity():
    common = {"revision": 1, "configuration_digest": DIGEST}
    assert r3.TargetConfiguration(
        data={**common, "binding_id": str(uuid4()), "environment_id": None}
    ).is_valid()
    assert not r3.TargetConfiguration(
        data={**common, "binding_id": None, "environment_id": None}
    ).is_valid()
    assert not r3.TargetConfiguration(
        data={**common, "binding_id": str(uuid4()), "environment_id": "environment"}
    ).is_valid()


def test_child_selection_is_not_a_root_activation_expectation():
    child = {"skill_id": str(uuid4()), "expectation": "required"}
    body = {
        "child_selection": [child],
        "catalog_digest": DIGEST,
        "invocation_mode": "natural",
    }
    assert r3.NativeSelection(data=body).is_valid()
    assert not r3.NativeSelection(
        data={**body, "child_selection": [child, child]}
    ).is_valid()
    assert not r3.NativeSelection(data={**body, "expected_trigger": True}).is_valid()


def test_report_credentials_cannot_request_content():
    grant = {
        "kind": "skill",
        "resource_id": str(uuid4()),
        "labels": ["production"],
        "version_ids": [],
    }
    body = {
        "purpose": "report",
        "grants": [],
        "binding_ids": [str(uuid4())],
        "expires_at": "2030-01-01T00:00:00Z",
    }
    assert v12.CredentialInput(data=body).is_valid()
    assert not v12.CredentialInput(data={**body, "grants": [grant]}).is_valid()
    assert not v12.CredentialInput(data={**body, "purpose": "rollout"}).is_valid()


def test_metadata_omission_preserves_explicit_empty_distinction():
    body = {
        "plan_id": str(uuid4()),
        "expected_fingerprint": DIGEST,
        "item_key": "root",
        "mode": "snapshot",
        "expected_head_version": None,
        "expected_head_digest": None,
        "rights_confirmed": True,
    }
    absent = r3.RetainedPlanAcceptance(data=body)
    empty = r3.RetainedPlanAcceptance(data={**body, "tags": []})
    assert absent.is_valid() and empty.is_valid()
    assert "tags" not in absent.validated_data
    assert empty.validated_data["tags"] == []


def context_run_config():
    return {
        "candidate": {"kind": "version", "version": 4, "digest": DIGEST},
        "cases": {
            "kind": "ad_hoc",
            "cases": [
                {
                    "id": str(uuid4()),
                    "title": "Synthetic context case",
                    "source_group_id": "synthetic",
                    "prompt": "Return the approved amount.",
                    "fixtures": [],
                    "expected_trigger": None,
                    "reviewed": True,
                    "checks": [
                        {
                            "id": str(uuid4()),
                            "kind": "contains",
                            "critical": True,
                            "config": {"text": "20", "case_sensitive": True},
                        }
                    ],
                }
            ],
        },
        "baselines": [{"kind": "version", "version": 2}],
        "competitors": [],
        "model": {"model_id": "synthetic", "temperature": 0, "max_output_tokens": 100},
        "judge": None,
        "profile": "quick",
        "repetitions": 1,
        "limits": {"max_total_tokens": 150000, "max_estimated_cost_usd": "1.00000000"},
        "purpose": "regression",
        "parent_run_id": None,
        "harness": "contexts-text-v1.2",
    }


def test_context_reference_contract_keeps_historical_identity():
    body = context_run_config()
    serializer = v12.ContextRunConfig(data=body)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["baselines"] == [{"kind": "version", "version": 2}]
    body.update(
        profile="repeated",
        repetitions=3,
        baselines=[{"kind": "production"}, {"kind": "version", "version": 2}],
    )
    serializer = v12.ContextRunConfig(data=body)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["harness"] == "contexts-text-v1.2"


def test_context_contract_rejects_root_activation_competitors_and_ambiguous_arms():
    for change in (
        {"baselines": [{"kind": "version"}]},
        {"baselines": [{"kind": "production", "version": 1}]},
        {
            "baselines": [
                {"kind": "version", "version": 1},
                {"kind": "version", "version": 2},
            ]
        },
        {"competitors": [{"skill_id": str(uuid4()), "version": 1}]},
        {"harness": "skills-text-v1.1"},
    ):
        assert not v12.ContextRunConfig(
            data={**context_run_config(), **change}
        ).is_valid()
    body = context_run_config()
    for trigger in (True, False, "null"):
        body["cases"]["cases"][0]["expected_trigger"] = trigger
        assert not v12.ContextRunConfig(data=body).is_valid()
    del body["cases"]["cases"][0]["expected_trigger"]
    assert not v12.ContextRunConfig(data=body).is_valid()


def test_lifecycle_outputs_have_explicit_identities_and_no_credentials():
    schemas = [
        v12.ContextSummary,
        v12.ContextDraftOut,
        v12.HeadReview,
        v12.ContextReleaseState,
        v12.ContextSafetyOut,
        v12.SessionOut,
        v12.ReceiptAccepted,
    ]
    for schema in schemas:
        assert "secret" not in schema().fields
        assert not schema(data={"unexpected": True}).is_valid()
    assert {
        "resolution_id",
        "resolution_expires_at",
        "history_mode",
        "binding_revision",
    } <= set(v12.SessionOut().fields)
    assert {
        "base_version",
        "expected_head_version",
        "candidate_digest",
        "review_fingerprint",
    } <= set(v12.HeadReview().fields)


def test_reference_arm_outputs_are_distinct_from_legacy_root_routing():
    from model_hub.serializers import skills_hub_v11 as legacy

    leaf_variant = legacy.VariantResult().fields["variant"]
    context_variant = v12.ContextVariantResult().fields["variant"]
    snapshot_variant = v12.ContextSnapshotFileQuery().fields["variant"]
    routing = v12.ContextAttemptDetail().fields["routing_verdict"]
    assert isinstance(leaf_variant, ChoiceField)
    assert isinstance(context_variant, ChoiceField)
    assert isinstance(snapshot_variant, ChoiceField)
    assert isinstance(routing, ChoiceField)
    assert "reference" not in leaf_variant.choices
    assert "reference" in context_variant.choices
    assert "reference" in snapshot_variant.choices
    assert set(routing.choices) == {"not_applicable"}
    for schema in (
        v12.ContextRunSummary,
        v12.ContextCaseResult,
        v12.ContextAttemptDetail,
    ):
        assert schema().fields["reference"].required
    assert "source_version_id" in v12.ContextDeliveryReceipt().fields
