"""Judge jobs retain pinned, tenant-scoped evidence independently of reviews."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.dataset_versions import DatasetVersionItem
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion
from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.models.pairwise_judges import PairwiseJudgeItem, PairwiseJudgeJob
from model_hub.models.pairwise_preferences import PairwisePreference
from model_hub.services import pairwise_judges as service
from tfc.constants.levels import Level

pytest_plugins = ["model_hub.tests.pairwise_judge_support"]

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
    # Shared through pytest_plugins, so modules opt in instead of autouse.
    pytest.mark.usefixtures("dispatch"),
]


def url(prepared, suffix=""):
    return f"/model-hub/experiments/v2/{prepared[0].pk}/pairwise/judges/" + suffix


def submit(client, prepared, **changes):
    return client.post(
        url(prepared),
        {
            "run_a": str(prepared[1][0].pk),
            "run_b": str(prepared[1][1].pk),
            "judge_version": str(prepared[3].pk),
            **changes,
        },
        format="json",
    )


def saved_jobs(client, prepared, **changes):
    return client.get(
        url(prepared),
        {
            "run_a": str(prepared[1][0].pk),
            "run_b": str(prepared[1][1].pk),
            **changes,
        },
    )


def start(client, prepared):
    response = submit(client, prepared)
    assert response.status_code == 202, response.data
    return PairwiseJudgeJob.objects.get(pk=response.data["id"])


def summary(client, prepared, job, reverse=False):
    runs = list(reversed(prepared[1])) if reverse else prepared[1]
    return client.get(
        f"/model-hub/experiments/v2/{prepared[0].pk}/pairwise/summary/",
        {"run_a": runs[0].pk, "run_b": runs[1].pk, "judge_job": job.pk},
    )


def test_reversed_submissions_reuse_one_job_and_snapshot(
    auth_client, prepared, dispatch, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        job = start(auth_client, prepared)
    assert dispatch.call_count == 4
    assert job.items.count() == 4
    assert job.status == "queued"
    response = submit(
        auth_client,
        prepared,
        run_a=str(prepared[1][1].pk),
        run_b=str(prepared[1][0].pk),
    )
    assert response.status_code == 200
    assert response.data["id"] == str(job.pk)
    assert response.data["source"] == "judge"
    assert PairwiseJudgeJob.objects.count() == 1
    item = job.items.first()
    assert item.snapshot == {
        "input": {"question": "Explain"},
        "output_a": "Answer 0",
        "output_b": "Answer 1",
    }
    assert item.presentation_order in ("ab", "ba")
    ExperimentRunItem.no_workspace_objects.filter(run=prepared[1][0]).update(
        output="Changed after admission"
    )
    item.refresh_from_db()
    assert item.snapshot["output_a"] == "Answer 0"


@pytest.mark.parametrize(
    "mutation",
    ["version", "pending", "missing", "input", "failed", "not_member", "trial"],
)
def test_incomparable_runs_are_refused(auth_client, prepared, mutation):
    run = prepared[1][1]
    item = ExperimentRunItem.no_workspace_objects.filter(run=run).first()
    if mutation == "version":
        other = (
            prepared[0]
            .dataset.dataset_versions.exclude(pk=run.dataset_version_id)
            .first()
        )
        ExperimentRun.no_workspace_objects.filter(pk=run.pk).update(
            dataset_version=other
        )
    elif mutation == "pending":
        ExperimentRun.no_workspace_objects.filter(pk=run.pk).update(status="running")
    elif mutation == "not_member":
        DatasetVersionItem.no_workspace_objects.filter(
            dataset_version=run.dataset_version, example_id=item.example_id
        ).update(deleted=True)
    else:
        change = {
            "missing": {"deleted": True},
            "input": {"input": {"question": "Other"}},
            "failed": {"status": "failed"},
            "trial": {"trial_number": 2},
        }[mutation]
        ExperimentRunItem.no_workspace_objects.filter(pk=item.pk).update(**change)
    response = submit(auth_client, prepared)
    assert response.status_code == 400
    assert PairwiseJudgeJob.objects.count() == 0


@pytest.mark.parametrize("kind", ["organization", "workspace", "deleted"])
def test_foreign_job_reads_and_writes_are_not_found(auth_client, prepared, kind, user):
    job = start(auth_client, prepared)
    dataset = prepared[0].dataset
    if kind == "organization":
        dataset.organization = Organization.objects.create(name="Other tenant")
    elif kind == "workspace":
        dataset.workspace = Workspace.no_workspace_objects.create(
            name="Other workspace",
            organization=dataset.organization,
            created_by=user,
        )
    else:
        dataset.deleted = True
    dataset.save()
    assert submit(auth_client, prepared).status_code == 404
    assert auth_client.get(url(prepared, f"{job.pk}/")).status_code == 404
    assert auth_client.post(url(prepared, f"{job.pk}/cancel/")).status_code == 404
    assert summary(auth_client, prepared, job).status_code == 404
    assert saved_jobs(auth_client, prepared).status_code == 404


@pytest.mark.parametrize("field", ["organization", "workspace", "deleted"])
def test_inaccessible_judge_version_is_not_found(auth_client, prepared, field, user):
    template = prepared[3].eval_template
    if field == "organization":
        template.organization = Organization.objects.create(name="Other tenant")
    elif field == "workspace":
        template.workspace = Workspace.no_workspace_objects.create(
            name="Other workspace",
            organization=template.organization,
            created_by=user,
        )
    else:
        template.deleted = True
    template.save()
    assert submit(auth_client, prepared).status_code == 404
    assert PairwiseJudgeJob.objects.count() == 0


@pytest.mark.parametrize("invalid", ["type", "choices", "inputs", "multiple"])
def test_judge_requires_a_pinned_pairwise_contract(auth_client, prepared, invalid):
    pin = prepared[3]
    key, value = {
        "type": ("eval_type_id", "CustomCodeEval"),
        "choices": ("choices", ["yes", "no"]),
        "inputs": ("required_keys", ["input"]),
        "multiple": ("multi_choice", True),
    }[invalid]
    pin.config_snapshot[key] = value
    pin.save()
    assert submit(auth_client, prepared).status_code == 400
    assert PairwiseJudgeJob.objects.count() == 0


def test_viewer_can_inspect_but_not_start_or_cancel(
    auth_client, prepared, user, organization, workspace
):
    job = start(auth_client, prepared)
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(level=Level.VIEWER, role="Viewer")
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(level=Level.WORKSPACE_VIEWER, role="workspace_viewer")
    with patch("model_hub.services.pairwise_judges.enqueue_job") as enqueue:
        response = saved_jobs(auth_client, prepared)
        assert response.status_code == 200
        assert response.data == {
            "jobs": [service.job_data(job)],
            "next_offset": None,
        }
        assert auth_client.get(url(prepared, f"{job.pk}/")).status_code == 200
    assert enqueue.call_count == 0
    assert PairwiseJudgeJob.objects.count() == 1
    assert submit(auth_client, prepared).status_code == 403
    assert auth_client.post(url(prepared, f"{job.pk}/cancel/")).status_code == 403


@pytest.mark.parametrize("order,expected", [("ab", "a"), ("ba", "b")])
def test_worker_preserves_pin_usage_identity_and_recorded_order(
    auth_client, prepared, order, expected
):
    job = start(auth_client, prepared)
    item = job.items.first()
    item.presentation_order = order
    item.save()
    EvalTemplate.no_workspace_objects.filter(pk=prepared[3].eval_template_id).update(
        config={"eval_type_id": "CustomPromptEvaluator", "choices": ["Other"]},
        model="turing_large",
    )
    with patch(
        "model_hub.services.pairwise_judge_runner.PairwiseJudgeRunner.evaluate",
        autospec=True,
        return_value={"output": {"choice": "A"}, "reason": "Clearer", "log_id": "log"},
    ) as evaluate:
        service.execute_item(item.pk)
        service.execute_item(item.pk)
    assert evaluate.call_count == 1
    runner = evaluate.call_args.args[0]
    assert runner._inputs == {
        "input": {"question": "Explain"},
        "output_a": "Answer 0" if order == "ab" else "Answer 1",
        "output_b": "Answer 1" if order == "ab" else "Answer 0",
    }
    assert runner._resolved_version.pk == prepared[3].pk
    assert runner.version_number == prepared[3].version_number
    assert runner.user_eval_metric.model == "turing_flash"
    assert runner.eval_template.config == prepared[3].config_snapshot
    assert runner.eval_template.model == "turing_flash"
    assert runner.workspace_id == prepared[0].dataset.workspace_id
    assert runner.organization_id == prepared[0].dataset.organization_id
    # Shared runner identity now uses the immutable item UUID, which binds the
    # example/trial/order, rather than the playground's custom UsageStep tuple.
    assert runner._item.pk == item.pk
    assert runner._item.presentation_order == order
    assert runner._usage_run == str(job.pk)
    assert runner.user_eval_metric.pk == prepared[3].pk
    assert runner.source_configs == {
        "run_id": str(job.pk),
        "version_id": str(prepared[3].pk),
        "item_id": str(item.pk),
        "presentation_order": order,
    }
    item.refresh_from_db()
    assert (item.status, item.winner, item.reason, item.log_id) == (
        "completed",
        expected,
        "Clearer",
        "log",
    )
    assert PairwisePreference.objects.count() == 0


@pytest.mark.parametrize(
    "result",
    [
        {"output": "unknown", "reason": "Uncertain"},
        {"output": "A", "reason": ""},
        {"output": {"choices": ["A", "B"]}, "reason": "Both"},
        {"output": None, "reason": "Failed"},
    ],
)
def test_invalid_judge_answers_are_failures_not_ties(auth_client, prepared, result):
    job = start(auth_client, prepared)
    with patch(
        "model_hub.services.pairwise_judge_runner.PairwiseJudgeRunner.evaluate",
        return_value=result,
    ):
        for item in job.items.all():
            service.execute_item(item.pk)
    job.refresh_from_db()
    assert job.status == "failed"
    assert list(job.items.values_list("winner", flat=True)) == ["", "", "", ""]
    assert set(job.items.values_list("error", flat=True)) == {"invalid_judge_output"}


def test_provider_exception_is_terminal_and_redacted(auth_client, prepared):
    job = start(auth_client, prepared)
    item = job.items.first()
    with patch(
        "model_hub.services.pairwise_judge_runner.PairwiseJudgeRunner.evaluate",
        side_effect=ValueError("private"),
    ) as evaluate:
        service.execute_item(item.pk)
        service.execute_item(item.pk)
    assert evaluate.call_count == 1
    item.refresh_from_db()
    assert (item.status, item.error, item.reason) == ("failed", "evaluation_failed", "")


def test_cancel_keeps_admitted_result_and_prevents_later_calls(auth_client, prepared):
    job = start(auth_client, prepared)
    item = job.items.first()

    def finish(*args, **kwargs):
        response = auth_client.post(url(prepared, f"{job.pk}/cancel/"))
        assert response.status_code == 200
        return {"output": "tie", "reason": "Equivalent"}

    with patch(
        "model_hub.services.pairwise_judge_runner.PairwiseJudgeRunner.evaluate",
        side_effect=finish,
    ) as run:
        service.execute_item(item.pk)
        for remaining in job.items.exclude(pk=item.pk):
            service.execute_item(remaining.pk)
    assert run.call_count == 1
    job.refresh_from_db()
    assert job.status == "cancelled"
    assert job.items.filter(status="completed", winner="tie").count() == 1
    assert job.items.filter(status="cancelled").count() == 3


def test_expired_attempt_is_not_replayed(auth_client, prepared):
    job = start(auth_client, prepared)
    item = job.items.first()
    job.items.update(status="completed", winner="tie", reason="Same")
    PairwiseJudgeItem.objects.filter(pk=item.pk).update(
        status="running", lease_until=timezone.now() - timedelta(seconds=1), winner=""
    )
    with patch(
        "model_hub.services.pairwise_judge_runner.PairwiseJudgeRunner.evaluate"
    ) as evaluate:
        response = auth_client.get(url(prepared, f"{job.pk}/"))
        service.execute_item(item.pk)
    assert response.status_code == 200
    assert response.data["status"] == "failed"
    item.refresh_from_db()
    assert (item.status, item.error) == ("failed", "attempt_interrupted")
    assert evaluate.call_count == 0


def test_summary_separates_sources_and_orients_agreement(auth_client, prepared, user):
    job = start(auth_client, prepared)
    for item, winner in zip(
        job.items.order_by("example_id"), ["a", "b", "tie", "a"], strict=True
    ):
        item.status, item.winner, item.reason = "completed", winner, "Compared"
        item.save()
        PairwisePreference.objects.create(
            experiment=prepared[0],
            run_a=prepared[1][0],
            run_b=prepared[1][1],
            row_id=item.example_id,
            created_by=user,
            winner="a",
            source="human",
        )
    result = summary(auth_client, prepared, job)
    assert result.status_code == 200
    assert result.data["human"] == {
        "a_wins": 4,
        "b_wins": 0,
        "ties": 0,
        "rows_reviewed": 4,
    }
    assert result.data["judge"] == {
        "a_wins": 2,
        "b_wins": 1,
        "ties": 1,
        "rows_reviewed": 4,
    }
    assert result.data["agreement"] == {"matches": 2, "compared": 4, "rate": 0.5}
    reverse = summary(auth_client, prepared, job, reverse=True)
    assert reverse.data["judge"] == {
        "a_wins": 1,
        "b_wins": 2,
        "ties": 1,
        "rows_reviewed": 4,
    }
    assert reverse.data["human"]["b_wins"] == 4
    assert reverse.data["agreement"] == result.data["agreement"]
    assert PairwisePreference.objects.count() == 4


def test_missing_human_evidence_is_not_disagreement(auth_client, prepared):
    job = start(auth_client, prepared)
    assert summary(auth_client, prepared, job).data["agreement"] == {
        "matches": 0,
        "compared": 0,
        "rate": None,
    }


def test_database_rejects_duplicate_item_identity(auth_client, prepared):
    job = start(auth_client, prepared)
    item = job.items.first()
    with pytest.raises(IntegrityError), transaction.atomic():
        PairwiseJudgeItem.objects.create(
            job=job,
            example_id=item.example_id,
            trial_number=item.trial_number,
            presentation_order="ab",
            snapshot=item.snapshot,
        )
    assert job.items.count() == 4


def test_detail_paginates_and_rejects_server_owned_fields(auth_client, prepared):
    assert submit(auth_client, prepared, status="completed").status_code == 400
    job = start(auth_client, prepared)
    response = auth_client.get(url(prepared, f"{job.pk}/"), {"offset": 2, "limit": 1})
    assert response.status_code == 200
    assert response.data["total"] == 4
    assert len(response.data["items"]) == 1
    assert response.data["next_offset"] == 3
    invalid = auth_client.get(url(prepared, f"{job.pk}/"), {"limit": 101})
    assert invalid.status_code == 400


@pytest.mark.parametrize("revoked", ["template", "dataset", "workspace", "membership"])
def test_worker_rechecks_scope_before_admission(
    auth_client, prepared, user, workspace, organization, revoked
):
    job = start(auth_client, prepared)
    item = job.items.first()
    if revoked == "workspace":
        Workspace.no_workspace_objects.filter(pk=workspace.pk).update(is_active=False)
    elif revoked == "template":
        EvalTemplate.no_workspace_objects.filter(
            pk=prepared[3].eval_template_id
        ).update(deleted=True)
    elif revoked == "dataset":
        type(prepared[0].dataset).no_workspace_objects.filter(
            pk=prepared[0].dataset_id
        ).update(deleted=True)
    else:
        OrganizationMembership.no_workspace_objects.filter(
            user=user, organization=organization
        ).update(level=Level.VIEWER, role="Viewer")
        WorkspaceMembership.no_workspace_objects.filter(
            user=user, workspace=workspace
        ).update(level=Level.WORKSPACE_VIEWER, role="workspace_viewer")
    with patch(
        "model_hub.services.pairwise_judge_runner.PairwiseJudgeRunner.evaluate"
    ) as evaluate:
        service.execute_item(item.pk)
    item.refresh_from_db()
    assert (item.status, item.error) == ("failed", "scope_unavailable")
    assert evaluate.call_count == 0


def test_dispatch_failure_is_visible_without_provider_calls(
    auth_client, prepared, django_capture_on_commit_callbacks
):
    with (
        patch(
            "model_hub.tasks.pairwise_judges.dispatch_pairwise_job.apply_async",
            side_effect=RuntimeError("private transport details"),
        ),
        django_capture_on_commit_callbacks(execute=True),
    ):
        job = start(auth_client, prepared)
    job.refresh_from_db()
    assert job.status == "failed"
    assert job.items.filter(status="failed", error="dispatch_failed").count() == 4


def test_repeated_trials_are_not_compared_with_unscoped_human_votes(
    auth_client, prepared, user
):
    for run in prepared[1]:
        ExperimentRunItem.no_workspace_objects.create(
            run=run,
            example_id=prepared[2][0],
            trial_number=2,
            status="completed",
            input={"question": "Explain"},
            output="Again",
        )
    job = start(auth_client, prepared)
    job.items.update(status="completed", winner="a", reason="Compared")
    PairwisePreference.objects.create(
        experiment=prepared[0],
        run_a=prepared[1][0],
        run_b=prepared[1][1],
        row_id=prepared[2][0],
        created_by=user,
        winner="a",
    )
    result = summary(auth_client, prepared, job)
    assert result.data["judge"] == {
        "a_wins": 5,
        "b_wins": 0,
        "ties": 0,
        "rows_reviewed": 4,
    }
    assert result.data["agreement"] == {"matches": 0, "compared": 0, "rate": None}


def test_human_upsert_cannot_replace_a_judge_verdict(auth_client, prepared):
    job = start(auth_client, prepared)
    item = job.items.first()
    item.status, item.winner, item.reason = "completed", "b", "More complete"
    item.save()
    response = auth_client.post(
        f"/model-hub/experiments/v2/{prepared[0].pk}/pairwise/",
        {
            "run_a": str(job.run_a_id),
            "run_b": str(job.run_b_id),
            "row_id": str(item.example_id),
            "winner": "a",
        },
        format="json",
    )
    assert response.status_code == 204
    item.refresh_from_db()
    assert (item.winner, item.reason) == ("b", "More complete")
    assert PairwisePreference.objects.get().winner == "a"


def test_cancel_before_admission_prevents_all_evaluations(auth_client, prepared):
    job = start(auth_client, prepared)
    response = auth_client.post(url(prepared, f"{job.pk}/cancel/"), {}, format="json")
    assert response.status_code == 200
    assert response.data["status"] == "cancelled"
    with patch(
        "model_hub.services.pairwise_judge_runner.PairwiseJudgeRunner.evaluate"
    ) as evaluate:
        for item in job.items.all():
            service.execute_item(item.pk)
    assert evaluate.call_count == 0
    assert job.items.filter(status="cancelled").count() == 4


@pytest.mark.parametrize("reverse", [False, True])
def test_saved_jobs_list_only_the_selected_pair_in_pages(
    auth_client, prepared, reverse
):
    first = start(auth_client, prepared)
    pin = prepared[3]
    newer_pin = EvalTemplateVersion.objects.create_version(
        pin.eval_template,
        model=pin.model,
        config_snapshot=pin.config_snapshot,
        organization=pin.organization,
        workspace=pin.workspace,
    )
    second_response = submit(auth_client, prepared, judge_version=str(newer_pin.pk))
    assert second_response.status_code == 202
    second = PairwiseJudgeJob.objects.get(pk=second_response.data["id"])
    other_run = ExperimentRun.no_workspace_objects.create(
        experiment=prepared[0],
        dataset_version=prepared[1][0].dataset_version,
    )
    a, b = sorted((prepared[1][0], other_run), key=lambda run: str(run.pk))
    PairwiseJudgeJob.objects.create(
        experiment=prepared[0],
        organization=first.organization,
        workspace=first.workspace,
        run_a=a,
        run_b=b,
        dataset_version=first.dataset_version,
        judge_version=pin,
        created_by=first.created_by,
    )
    runs = list(reversed(prepared[1])) if reverse else prepared[1]
    query = {"run_a": str(runs[0].pk), "run_b": str(runs[1].pk), "limit": 1}
    response = saved_jobs(auth_client, prepared, **query)
    assert response.status_code == 200
    assert response.data == {
        "jobs": [service.job_data(second)],
        "next_offset": 1,
    }
    response = saved_jobs(auth_client, prepared, **query, offset=1)
    assert response.status_code == 200
    assert response.data == {
        "jobs": [service.job_data(first)],
        "next_offset": None,
    }
    assert PairwiseJudgeJob.objects.count() == 3


def test_saved_jobs_discovery_does_not_create_or_dispatch(auth_client, prepared):
    with patch("model_hub.services.pairwise_judges.enqueue_job") as enqueue:
        response = saved_jobs(auth_client, prepared)
    assert response.status_code == 200
    assert response.data == {"jobs": [], "next_offset": None}
    assert PairwiseJudgeJob.objects.count() == 0
    assert enqueue.call_count == 0


@pytest.mark.parametrize(
    "query",
    [{"limit": 101}, {"offset": -1}, {"run_a": "invalid"}, {"status": "queued"}],
)
def test_saved_jobs_discovery_validates_the_query(auth_client, prepared, query):
    assert saved_jobs(auth_client, prepared, **query).status_code == 400


@pytest.mark.parametrize("hidden_by", ["run", "version", "job_scope"])
def test_saved_jobs_discovery_excludes_unavailable_scope(
    auth_client, prepared, hidden_by
):
    job = start(auth_client, prepared)
    if hidden_by == "job_scope":
        PairwiseJudgeJob.objects.filter(pk=job.pk).update(
            organization=Organization.objects.create(name="Other saved scope")
        )
        response = saved_jobs(auth_client, prepared)
        assert response.status_code == 200
        assert response.data == {"jobs": [], "next_offset": None}
    else:
        instance = (
            prepared[1][0] if hidden_by == "run" else prepared[1][0].dataset_version
        )
        instance.deleted = True
        instance.save()
        assert saved_jobs(auth_client, prepared).status_code == 404
