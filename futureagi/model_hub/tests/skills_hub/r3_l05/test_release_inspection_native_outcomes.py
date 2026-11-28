"""Review packets carry a completed native run's own reduced outcomes, never more."""

from uuid import uuid4

import pytest
from django.utils import timezone

from model_hub.models.skills_hub_v12 import HubSkillContextBinding
from model_hub.services.skills_hub.r3.native_reducer import (
    NativeCheck,
    NativeTrial,
    planned_matrix,
    reduce_native_results,
)
from model_hub.services.skills_hub.r3.release_inspection import (
    native_outcomes,
    summarize_native,
)
from model_hub.services.skills_hub.v12.credentials import canonical_digest
from model_hub.tests.skills_hub.r3_l05 import test_j4_release_inspection as base

native_configuration = base.native_configuration
evidence = base.evidence
admin = base.admin
actors = base.actors
candidate = base.candidate
pytestmark = pytest.mark.django_db(transaction=True)
FAMILIES = ("answer", "selection", "delivery", "tools")


def _checks(case_id):
    return {
        case_id: [
            {
                "check_id": family,
                "family": family,
                "variants": ["candidate", "reference"],
            }
            for family in FAMILIES
        ]
    }


def _trial(case_id, variant, verdicts, status="completed"):
    return NativeTrial(
        case_id,
        variant,
        0,
        status,
        tuple(
            NativeCheck(family, family, verdicts.get(family, "pass"))
            for family in FAMILIES
        ),
    )


def _repair(candidate_verdicts=None, candidate_status="completed"):
    case_id = str(uuid4())
    reduced = reduce_native_results(
        matrix=planned_matrix(
            case_ids=[case_id], baselines=["reference"], repetitions=1
        ),
        required_checks=_checks(case_id),
        trials=[
            _trial(case_id, "candidate", candidate_verdicts or {}, candidate_status),
            _trial(case_id, "reference", {"tools": "fail"}),
        ],
        reproduction_checks={case_id: "tools"},
    )
    arms = {"candidate": {"version": 2}, "reference": {"version": 1}}
    return case_id, summarize_native(reduced, arms, {case_id: "Blender refund"})


def test_verified_repair_carries_per_case_outcomes():
    case_id, summary = _repair()
    assert summary["availability"] == "available"
    assert (summary["planned"], summary["graded"], summary["incomplete"]) == (2, 2, 0)
    assert summary["result"] == {
        "state": "works_on_examples",
        "reproduction": "reproduced",
        "repair_verified": True,
    }
    assert all(summary[family] == "pass" for family in FAMILIES)
    rows = {row["variant"]: row for row in summary["cases"]}
    assert rows["candidate"]["version"] == 2 and rows["candidate"]["tools"] == "pass"
    assert rows["reference"]["version"] == 1 and rows["reference"]["tools"] == "fail"
    assert {row["case_id"] for row in summary["cases"]} == {case_id}
    assert {row["title"] for row in summary["cases"]} == {"Blender refund"}


def test_candidate_failure_is_carried_not_hidden():
    _, summary = _repair({"tools": "fail"})
    assert summary["tools"] == "fail"
    assert summary["result"]["repair_verified"] is False
    assert summary["result"]["state"] == "needs_changes"


@pytest.mark.parametrize("verdict", ["unverifiable", "not_graded"])
def test_ungraded_candidate_work_never_reads_as_a_pass(verdict):
    _, summary = _repair({"answer": verdict})
    assert summary["answer"] == "Not observed"
    assert summary["graded"] == 1 and summary["incomplete"] == 1
    assert summary["result"]["repair_verified"] is False


def test_unfinished_candidate_is_incomplete():
    _, summary = _repair(candidate_status="error")
    assert summary["graded"] == 1
    assert summary["incomplete"] == 1
    assert summary["result"]["repair_verified"] is False


def test_job_without_skills_result_or_with_purged_payload_has_no_outcomes(
    evidence, mocker
):
    reduce = mocker.patch("model_hub.services.skills_hub.r3.native_runs._reduce")
    assert native_outcomes(evidence) == {}  # completed job, but no skills-native result
    job = evidence.simulation_job
    job.payload = {"skills_context": {"id": "synthetic"}, "skills_native_control": {}}
    job.save(update_fields=["payload"])
    evidence.payload_purged_at = timezone.now()
    evidence.save(update_fields=["payload_purged_at"])
    assert native_outcomes(evidence) == {}
    job.state = "running"
    job.save(update_fields=["state"])
    evidence.payload_purged_at = None
    evidence.save(update_fields=["payload_purged_at"])
    assert native_outcomes(evidence) == {}
    reduce.assert_not_called()


def test_inspection_http_returns_the_reduced_native_outcomes(
    admin,
    hub_skill,
    candidate,
    evidence,
    native_configuration,
    organization,
    workspace,
    mocker,
):
    case_id, expected = _repair()
    job = evidence.simulation_job
    job.payload = {
        "skills_context": {"id": "synthetic"},
        "skills_native_control": {
            "arms": {"candidate": {"version": 2}, "reference": {"version": 1}},
            "cases": [{"id": case_id, "title": "Blender refund"}],
        },
    }
    job.save(update_fields=["payload"])
    reduced = reduce_native_results(
        matrix=planned_matrix(
            case_ids=[case_id], baselines=["reference"], repetitions=1
        ),
        required_checks=_checks(case_id),
        trials=[
            _trial(case_id, "candidate", {}),
            _trial(case_id, "reference", {"tools": "fail"}),
        ],
        reproduction_checks={case_id: "tools"},
    )
    mocker.patch(
        "model_hub.services.skills_hub.r3.native_runs._reduce",
        return_value=(reduced, {}),
    )
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
    body = base.selection(
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
    preview = admin.post(base.BASE + "preview/", body, format="json")
    assert preview.status_code == 200, preview.data
    inspected = admin.post(
        base.BASE + "inspection/",
        {**body, "expected_fingerprint": preview.data["result"]["fingerprint"]},
        format="json",
    )
    assert inspected.status_code == 200, inspected.data
    summary = inspected.data["result"]["summaries"][0]
    assert summary["availability"] == "available"
    assert (summary["graded"], summary["planned"]) == (2, 2)
    assert summary["result"] == expected["result"]
    assert [dict(row) for row in summary["cases"]] == expected["cases"]


def test_text_and_unavailable_summaries_declare_no_native_outcomes(
    admin, hub_skill, candidate, actors
):
    author, _ = actors
    run = base.fixtures.text_run(author, hub_skill, candidate[1])
    refs = [
        {"source": "skills_text", "source_id": str(run.pk), "annotation_revision": 0}
    ]
    body = base.selection(hub_skill, candidate[1], refs)
    preview = admin.post(base.BASE + "preview/", body, format="json").data["result"]
    inspected = admin.post(
        base.BASE + "inspection/",
        {**body, "expected_fingerprint": preview["fingerprint"]},
        format="json",
    )
    assert inspected.status_code == 200, inspected.data
    summary = inspected.data["result"]["summaries"][0]
    assert summary["result"] is None and summary["cases"] == []


def test_composer_lists_a_completed_run_with_its_own_outcome(
    actors, hub_skill, candidate, evidence, native_configuration, mocker
):
    from model_hub.services.skills_hub.r3.release_options import release_options

    author, _ = actors
    case_id, _ = _repair()
    job = evidence.simulation_job
    job.payload = {
        "skills_context": {"id": "synthetic"},
        "skills_native_control": {
            "arms": {"candidate": {"version": 2}, "reference": {"version": 1}},
            "cases": [{"id": case_id, "title": "Blender refund"}],
        },
    }
    job.save(update_fields=["payload"])
    reduced = reduce_native_results(
        matrix=planned_matrix(
            case_ids=[case_id], baselines=["reference"], repetitions=1
        ),
        required_checks=_checks(case_id),
        trials=[
            _trial(case_id, "candidate", {}),
            _trial(case_id, "reference", {"tools": "fail"}),
        ],
        reproduction_checks={case_id: "tools"},
    )
    mocker.patch(
        "model_hub.services.skills_hub.r3.native_runs._reduce",
        return_value=(reduced, {}),
    )
    evidence.configuration = {
        "schema": 1,
        "digest": canonical_digest(native_configuration),
        "native": native_configuration,
    }
    evidence.candidate_digest = candidate[1].digest
    evidence.visibility = "workspace"
    evidence.save(update_fields=["configuration", "candidate_digest", "visibility"])
    text = base.fixtures.text_run(author, hub_skill, candidate[1])
    value = release_options(
        principal=author,
        resource_id=hub_skill.pk,
        kind=hub_skill.kind,
        candidate_version_id=candidate[1].pk,
    )
    rows = {ref["source"]: ref for ref in value["evidence"]}
    assert rows["simulation_native"]["outcome"] == "works_on_examples"
    assert rows["simulation_native"]["counts"] == {
        "graded": 2,
        "planned": 2,
        "regressed": 0,
        "disagreements": 0,
    }
    assert rows["skills_text"]["source_id"] == str(text.pk)
    assert rows["skills_text"]["outcome"] is rows["skills_text"]["counts"] is None
