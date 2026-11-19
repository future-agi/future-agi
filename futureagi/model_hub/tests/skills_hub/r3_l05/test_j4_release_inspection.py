"""Synthetic exact-review inspection; no native or competitive claims."""

from uuid import uuid4

import pytest

from model_hub.models.skills_hub_v12 import HubSkillContextBinding
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3.human_review import append_annotation
from model_hub.services.skills_hub.r3.release_inspection import inspect_release
from model_hub.services.skills_hub.v12.credentials import canonical_digest
from model_hub.tests.skills_hub import test_i2_integration as http
from model_hub.tests.skills_hub.r3_l04 import conftest as native
from model_hub.tests.skills_hub.r3_l04 import test_native_privacy as privacy
from model_hub.tests.skills_hub.r3_l05 import test_release_requests as fixtures

native_configuration = native.native_configuration
evidence = privacy.evidence

admin = http.admin
actors = fixtures.actors
candidate = fixtures.candidate
pytestmark = pytest.mark.django_db(transaction=True)
BASE = "/model-hub/skills/release-requests/"


def selection(skill, candidate, evidence=None):
    return {
        "resource_id": str(skill.pk),
        "kind": skill.kind,
        "candidate_version_id": str(candidate.pk),
        "label": "production",
        "target": None,
        "evidence": evidence or [],
        "external_pipeline": None,
    }


def test_inspection_http_binds_exact_capabilities(admin, hub_skill, candidate):
    body = selection(hub_skill, candidate[1])
    preview = admin.post(BASE + "preview/", body, format="json")
    assert preview.status_code == 200, preview.data
    body["expected_fingerprint"] = preview.data["result"]["fingerprint"]
    inspected = admin.post(BASE + "inspection/", body, format="json")
    assert inspected.status_code == 200, inspected.data
    value = inspected.data["result"]
    assert value["fingerprint"] == body["expected_fingerprint"]
    assert value["capabilities"]["before_digest"] == candidate[0].digest
    assert value["capabilities"]["after_digest"] == candidate[1].digest
    assert value["capabilities"]["changes"] == []
    assert value["summaries"] == []
    assert value["target_configuration"] is None
    body["expected_fingerprint"] = "sha256:" + "a" * 64
    assert admin.post(BASE + "inspection/", body, format="json").status_code == 409
    body["resource_id"] = str(uuid4())
    assert admin.post(BASE + "inspection/", body, format="json").status_code == 404


def test_feedback_and_unknown_results_are_not_erased(
    admin, actors, hub_skill, candidate
):
    author, _ = actors
    run = fixtures.text_run(author, hub_skill, candidate[1])
    append_annotation(
        principal=author,
        source="skills_text",
        source_id=run.pk,
        annotation_revision=0,
        verdict="disagree",
        attempt_id=None,
        check_id=None,
        cited_text="",
        comment="Regression not yet resolved.",
    )
    append_annotation(
        principal=author,
        source="skills_text",
        source_id=run.pk,
        annotation_revision=1,
        verdict="agree",
        attempt_id=None,
        check_id=None,
        cited_text="",
        comment="Another independent opinion.",
    )
    refs = [
        {"source": "skills_text", "source_id": str(run.pk), "annotation_revision": 2}
    ]
    body = selection(hub_skill, candidate[1], refs)
    preview = admin.post(BASE + "preview/", body, format="json").data["result"]
    inspected = admin.post(
        BASE + "inspection/",
        {**body, "expected_fingerprint": preview["fingerprint"]},
        format="json",
    )
    assert inspected.status_code == 200, inspected.data
    summary = inspected.data["result"]["summaries"][0]
    assert summary["disagreements"] == 1
    assert summary["availability"] == "unavailable"
    assert summary["graded"] is summary["regressions"] is None
    run.refresh_from_db()
    assert run.summary == {}  # Inspection never changes machine grades.
    run.visibility = "private"
    run.save(update_fields=["visibility"])
    assert (
        admin.post(
            BASE + "inspection/",
            {**body, "expected_fingerprint": preview["fingerprint"]},
            format="json",
        ).status_code
        == 403
    )


def test_native_completion_remains_unknown_without_verified_result_reader(
    admin, hub_skill, candidate, evidence, native_configuration, organization, workspace
):
    # Stored as production stores native evidence: {schema, digest, native}.
    evidence.configuration = {
        "schema": 1,
        "digest": canonical_digest(native_configuration),
        "native": native_configuration,
    }
    evidence.candidate_digest = candidate[1].digest
    evidence.visibility = "workspace"
    evidence.save(update_fields=["configuration", "candidate_digest", "visibility"])
    binding = HubSkillContextBinding.objects.create(
        organization=organization,
        workspace=workspace,
        context=hub_skill,
        name="Synthetic review target",
        kind="runtime",
        selector={"label": "production"},
        update_mode="new_thread",
        profile="synthetic",
        profile_version="1",
        configuration=native_configuration,
        configuration_digest=canonical_digest(native_configuration),
        expected_report_interval_seconds=60,
    )
    body = selection(
        hub_skill,
        candidate[1],
        [
            {
                "source": "simulation_native",
                "source_id": str(evidence.pk),
                "annotation_revision": 0,
            }
        ],
    )
    body["target"] = {
        "binding_id": str(binding.pk),
        "environment_id": None,
        "revision": 1,
        "configuration_digest": binding.configuration_digest,
    }
    preview = admin.post(BASE + "preview/", body, format="json")
    assert preview.status_code == 200, preview.data
    inspected = admin.post(
        BASE + "inspection/",
        {**body, "expected_fingerprint": preview.data["result"]["fingerprint"]},
        format="json",
    )
    assert inspected.status_code == 200, inspected.data
    value = inspected.data["result"]
    assert (
        canonical_digest(value["target_configuration"]) == binding.configuration_digest
    )
    summary = value["summaries"][0]
    assert summary["planned"] == len(evidence.planned_matrix)
    assert summary["availability"] == "unavailable"
    assert all(
        summary[family] == "Not observed"
        for family in ["answer", "selection", "delivery", "tools"]
    )
    assert summary["regressions"] is summary["graded"] is None


def test_annotation_race_during_inspection_refuses_old_fingerprint(
    actors, hub_skill, candidate, mocker
):
    author, _ = actors
    run = fixtures.text_run(author, hub_skill, candidate[1])
    refs = [
        {"source": "skills_text", "source_id": str(run.pk), "annotation_revision": 0}
    ]
    packet = fixtures.preview(author, hub_skill, candidate[1], evidence=refs)
    from model_hub.services.skills_hub.r3 import release_inspection as inspection

    original = inspection._summary

    def racing_summary(principal, ref):
        value = original(principal, ref)
        append_annotation(
            principal=author,
            source="skills_text",
            source_id=run.pk,
            annotation_revision=0,
            verdict="disagree",
            attempt_id=None,
            check_id=None,
            cited_text="",
            comment="Late disagreement.",
        )
        return value

    mocker.patch.object(inspection, "_summary", side_effect=racing_summary)
    with pytest.raises(SkillsHubError) as refused:
        inspect_release(
            principal=author,
            expected_fingerprint=packet["fingerprint"],
            **selection(hub_skill, candidate[1], refs),
        )
    assert refused.value.status == 409
