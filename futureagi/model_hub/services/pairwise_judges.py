"""Pinned, idempotent pairwise judgments through the ordinary evaluator runner."""

from datetime import timedelta
from secrets import choice

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Count, F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from model_hub.models.dataset_versions import DatasetVersionItem
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion
from model_hub.models.experiments import ExperimentRunItem, ExperimentsTable
from model_hub.models.pairwise_judges import PairwiseJudgeItem, PairwiseJudgeJob
from model_hub.models.pairwise_preferences import PairwisePreference
from model_hub.models.score import Score
from model_hub.services.eval_group import filter_eval_templates_for_group_scope
from model_hub.services.results_catalog import bounded_list, live_runs
from tfc.routers import primary_required

ACTIVE = ("queued", "running")
ITEM_SECONDS = 900


def require_version(version_id, organization, workspace):
    templates = filter_eval_templates_for_group_scope(
        EvalTemplate.no_workspace_objects.filter(deleted=False),
        organization,
        workspace,
    )
    version = get_object_or_404(
        EvalTemplateVersion.objects.select_related("eval_template").filter(
            eval_template__in=templates, deleted=False
        ),
        pk=version_id,
    )
    if version.organization_id not in (
        None,
        organization.pk,
    ) or version.workspace_id not in (None, workspace.pk):
        raise Http404
    config = version.config_snapshot or {}
    if not isinstance(config, dict):
        raise ValidationError("The saved judge definition must be an object.")
    # The pinned engine expands dataset references without a workspace. Like
    # stored evaluations, accept only literal examples, at request AND worker
    # admission, so later expansion cannot read a different workspace's data.
    examples = config.get("few_shot_examples")
    if examples is not None and (
        not isinstance(examples, list)
        or any(
            not isinstance(example, dict)
            or not ("input" in example or "output" in example)
            for example in examples
        )
    ):
        raise ValidationError("Pairwise judges require literal few-shot examples.")
    choices = config.get("choices")
    keys = config.get("required_keys")
    if (
        version.eval_template.eval_type != "llm"
        or version.eval_template.template_type != "single"
        or config.get("eval_type_id") != "CustomPromptEvaluator"
        or config.get("output") != "choices"
        or config.get("multi_choice", False)
        or not isinstance(choices, list)
        or any(not isinstance(value, str) for value in choices)
        or sorted(choices) != ["A", "B", "tie"]
        or not isinstance(keys, list)
        or any(not isinstance(value, str) for value in keys)
        or sorted(keys) != ["input", "output_a", "output_b"]
        or not version.model
    ):
        raise ValidationError(
            "Choose a saved LLM judge with input, output_a and output_b inputs, "
            "single A/B/tie choices, and a pinned model."
        )
    return version


def comparable_items(pair):
    a, b = pair["run_a"], pair["run_b"]
    if (
        a.dataset_version_id != b.dataset_version_id
        or a.status != "completed"
        or b.status != "completed"
    ):
        raise ValidationError("Both runs must complete on the same dataset version.")
    grouped = []
    for run in (a, b):
        items = bounded_list(
            ExperimentRunItem.no_workspace_objects.filter(run=run, deleted=False),
            ["input", "output"],
        )
        members = set(
            DatasetVersionItem.no_workspace_objects.filter(
                dataset_version_id=a.dataset_version_id,
                deleted=False,
                example_id__in=[item.example_id for item in items],
            ).values_list("example_id", flat=True)
        )
        if not items or any(
            item.status != "completed"
            or item.output is None
            or item.example_id not in members
            for item in items
        ):
            raise ValidationError("Every compared item must have a completed output.")
        grouped.append({(i.example_id, i.trial_number): i for i in items})
    left, right = grouped
    if left.keys() != right.keys() or any(
        left[key].input != right[key].input for key in left
    ):
        raise ValidationError("Runs must have the same examples, trials and inputs.")
    return left, right


def create_job(pair, version, actor):
    dataset = pair["experiment"].dataset
    if dataset.workspace_id is None:
        raise ValidationError("A workspace is required for a judge job.")
    a, b = sorted((pair["run_a"], pair["run_b"]), key=lambda run: str(run.pk))
    pair = {**pair, "run_a": a, "run_b": b}
    with transaction.atomic():
        # Also serializes a repeated/reversed request before constructing items.
        ExperimentsTable.no_workspace_objects.select_for_update().get(
            pk=pair["experiment"].pk
        )
        job = PairwiseJudgeJob.objects.filter(**pair, judge_version=version).first()
        if job is not None:
            return job, False
        left, right = comparable_items(pair)
        job = PairwiseJudgeJob.objects.create(
            **pair,
            judge_version=version,
            created_by=actor,
            dataset_version_id=a.dataset_version_id,
            organization_id=dataset.organization_id,
            workspace_id=dataset.workspace_id,
        )
        PairwiseJudgeItem.objects.bulk_create(
            [
                PairwiseJudgeItem(
                    job=job,
                    example_id=key[0],
                    trial_number=key[1],
                    presentation_order=choice(("ab", "ba")),
                    snapshot={
                        "input": left[key].input,
                        "output_a": left[key].output,
                        "output_b": right[key].output,
                    },
                )
                for key in left
            ]
        )
        transaction.on_commit(lambda: enqueue_job(job.pk))
    return job, True


def enqueue_job(job_id):
    from model_hub.tasks.pairwise_judges import dispatch_pairwise_job

    try:
        dispatch_pairwise_job.apply_async(args=(str(job_id),), queue="tasks_s")
    except Exception:
        fail_queued(job_id)


@primary_required
def fail_queued(job_id):
    with transaction.atomic():
        job = PairwiseJudgeJob.objects.select_for_update().get(pk=job_id)
        job.items.filter(status="queued").update(
            status="failed", error="dispatch_failed", finished_at=timezone.now()
        )
        _settle(job)


@primary_required
def dispatch_job(job_id):
    from model_hub.tasks.pairwise_judges import evaluate_pairwise_item

    try:
        ids = PairwiseJudgeItem.objects.filter(
            job_id=job_id, job__status__in=ACTIVE, status="queued"
        ).values_list("pk", flat=True)
        for item_id in ids.iterator():
            evaluate_pairwise_item.apply_async(args=(str(item_id),), queue="tasks_s")
    except Exception:
        fail_queued(job_id)


def _settle(job):
    states = set(job.items.values_list("status", flat=True))
    if job.status != "cancelled":
        if "running" in states:
            job.status = "running"
        elif "queued" in states:
            job.status = "queued"
        else:
            job.status = "failed" if "failed" in states else "completed"
    if not states.intersection(ACTIVE):
        job.finished_at = job.finished_at or timezone.now()
    job.save(update_fields=["status", "finished_at"])


def reconcile_job(job_id):
    with transaction.atomic():
        job = PairwiseJudgeJob.objects.select_for_update().get(pk=job_id)
        now = timezone.now()
        job.items.filter(status="running", lease_until__lt=now).update(
            status="failed", error="attempt_interrupted", finished_at=now
        )
        # A lost dispatch is explicit evidence, never an implicit provider retry.
        if job.created_at < now - timedelta(days=1):
            job.items.filter(status="queued").update(
                status="failed", error="dispatch_expired", finished_at=now
            )
        _settle(job)
    return job


def cancel_job(job_id):
    with transaction.atomic():
        job = PairwiseJudgeJob.objects.select_for_update().get(pk=job_id)
        if job.status in ACTIVE:
            job.status = "cancelled"
            job.items.filter(status="queued").update(
                status="cancelled", finished_at=timezone.now()
            )
            # A call admitted under this same lock may finish and retain usage.
            _settle(job)
    return job


def _worker_version(job):
    dataset = job.experiment.dataset
    if (
        dataset.deleted
        or job.experiment.deleted
        or job.workspace.deleted
        or not job.workspace.is_active
        or not job.created_by.is_active
        or dataset.organization_id != job.organization_id
        or dataset.workspace_id != job.workspace_id
        or not job.created_by.can_write_to_workspace(job.workspace)
        or live_runs(dataset)
        .filter(
            pk__in=[job.run_a_id, job.run_b_id],
            experiment=job.experiment,
            status="completed",
        )
        .count()
        != 2
        or job.run_a.dataset_version_id != job.dataset_version_id
        or job.run_b.dataset_version_id != job.dataset_version_id
    ):
        raise ValidationError("Judge scope is no longer available.")
    return require_version(job.judge_version_id, job.organization, job.workspace)


def run_judge(job, item, version):
    from model_hub.services.pairwise_judge_runner import PairwiseJudgeRunner

    return PairwiseJudgeRunner(job, item, version).evaluate()


def verdict(result, order):
    output = result.get("output")
    if isinstance(output, dict):
        output = output.get("choice")
    if not isinstance(output, str) or output not in ("A", "B", "tie"):
        raise ValueError("Invalid preference")
    reason = result.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Missing reasoning")
    winner = output.lower()
    if order == "ba" and winner != "tie":
        winner = "b" if winner == "a" else "a"
    return winner, reason[:4000]


@primary_required
def execute_item(item_id):
    item = PairwiseJudgeItem.objects.filter(pk=item_id).first()
    if item is None:
        return
    with transaction.atomic():
        job = PairwiseJudgeJob.objects.select_for_update().get(pk=item.job_id)
        item = PairwiseJudgeItem.objects.select_for_update().get(pk=item_id)
        if job.status not in ACTIVE or item.status != "queued":
            return
        try:
            version = _worker_version(job)
        except (ValidationError, Http404, ObjectDoesNotExist):
            item.status, item.error = "failed", "scope_unavailable"
            item.finished_at = timezone.now()
            item.save(update_fields=["status", "error", "finished_at"])
            _settle(job)
            return
        item.status = "running"
        item.lease_until = timezone.now() + timedelta(seconds=ITEM_SECONDS)
        item.save(update_fields=["status", "lease_until"])
        job.status = "running"
        job.save(update_fields=["status"])
    # No transaction spans a provider call. A duplicate cannot reclaim running.
    fields = {"status": "failed", "error": "evaluation_failed"}
    try:
        result = run_judge(job, item, version)
        fields["log_id"] = str(result.get("log_id") or "")[:255]
        try:
            winner, reason = verdict(result, item.presentation_order)
            fields.update(status="completed", error="", winner=winner, reason=reason)
        except (TypeError, ValueError):
            fields["error"] = "invalid_judge_output"
    except Exception:
        # Provider errors may contain credentials or customer content.
        pass
    with transaction.atomic():
        job = PairwiseJudgeJob.objects.select_for_update().get(pk=item.job_id)
        PairwiseJudgeItem.objects.filter(pk=item_id, status="running").update(
            **fields, finished_at=timezone.now()
        )
        _settle(job)


def job_data(job):
    return {
        "id": str(job.pk),
        "source": "judge",
        "run_a": str(job.run_a_id),
        "run_b": str(job.run_b_id),
        "judge_version": str(job.judge_version_id),
        "dataset_version": str(job.dataset_version_id),
        "status": job.status,
        "counts": dict(
            job.items.values("status")
            .annotate(n=Count("id"))
            .values_list("status", "n")
        ),
        "total": job.items.count(),
        "created_at": job.created_at,
        "finished_at": job.finished_at,
    }


def _counts(votes):
    return {
        "a_wins": sum(w == "a" for _, w in votes),
        "b_wins": sum(w == "b" for _, w in votes),
        "ties": sum(w == "tie" for _, w in votes),
        "rows_reviewed": len({row for row, _ in votes}),
    }


def _flip(winner):
    return {"a": "b", "b": "a", "tie": "tie"}[winner]


def _review_votes(pair, rows, job):
    """Read the live review evidence, not a mirror that can outlive edits/clears."""
    dataset = pair["experiment"].dataset
    examples = dict(
        DatasetVersionItem.no_workspace_objects.filter(
            dataset_version_id=job.dataset_version_id,
            example_id__in=rows,
            deleted=False,
            legacy_row__deleted=False,
            legacy_row__dataset=dataset,
        ).values_list("legacy_row_id", "example_id")
    )
    a, b = str(pair["run_a"].pk), str(pair["run_b"].pk)
    scores = Score.no_workspace_objects.filter(
        Q(
            queue_item__metadata__run_a=a,
            queue_item__metadata__run_b=b,
        )
        | Q(
            queue_item__metadata__run_a=b,
            queue_item__metadata__run_b=a,
        ),
        Q(workspace_id=job.workspace_id) | Q(workspace__isnull=True),
        Q(label__workspace_id=job.workspace_id) | Q(label__workspace__isnull=True),
        organization_id=job.organization_id,
        deleted=False,
        score_source="human",
        source_type="dataset_row",
        dataset_row_id__in=examples,
        dataset_row_id=F("queue_item__dataset_row_id"),
        queue_item__source_type="dataset_row",
        queue_item__organization_id=job.organization_id,
        queue_item__workspace_id=job.workspace_id,
        queue_item__deleted=False,
        queue_item__metadata__kind="experiment_review",
        queue_item__metadata__mode="pairwise",
        queue_item__queue__organization_id=job.organization_id,
        queue_item__queue__workspace_id=job.workspace_id,
        queue_item__queue__dataset=dataset,
        queue_item__queue__deleted=False,
        label__organization_id=job.organization_id,
        label__deleted=False,
        label__settings__kind="pairwise",
        label__queue_memberships__queue_id=F("queue_item__queue_id"),
        label__queue_memberships__deleted=False,
    ).values_list("dataset_row_id", "value", "queue_item__metadata__run_a")
    votes = []
    for row, value, run_a in scores:
        selected = value.get("selected") if isinstance(value, dict) else None
        if not isinstance(selected, list) or len(selected) != 1:
            continue
        winner = selected[0]
        if winner not in ("A", "B", "Tie", "tie"):
            continue
        winner = winner.lower()
        # The review buttons already submit canonical A/B, even when B is
        # displayed on the left. Only the run pair, not `left`, is inverted.
        votes.append((examples[row], winner if run_a == a else _flip(winner)))
    return votes


def judge_summary(pair, rows, job):
    requested = (pair["run_a"].pk, pair["run_b"].pk)
    if set(requested) != {job.run_a_id, job.run_b_id}:
        raise ValidationError("judge_job must belong to the requested run pair.")
    reverse = requested[0] != job.run_a_id
    judged = job.items.filter(status="completed", example_id__in=rows).values_list(
        "example_id", "winner"
    )
    judge_votes = [
        (row, _flip(winner) if reverse else winner) for row, winner in judged
    ]
    human_votes = _review_votes(pair, rows, job)
    humans = PairwisePreference.objects.filter(
        Q(run_a_id=requested[0], run_b_id=requested[1])
        | Q(run_a_id=requested[1], run_b_id=requested[0]),
        experiment=pair["experiment"],
        source="human",
        row_id__in=rows,
    ).values_list("row_id", "winner", "run_a_id")
    for row, winner, run_a in humans:
        human_votes.append((row, winner if run_a == requested[0] else _flip(winner)))
    # Human votes have no trial identity. Do not invent agreement for repeated
    # trials, including rows whose other trials failed or are still running.
    single_rows = set(
        job.items.values("example_id")
        .annotate(n=Count("id"))
        .filter(n=1)
        .values_list("example_id", flat=True)
    )
    by_row = {row: winner for row, winner in judge_votes if row in single_rows}
    matches = sum(by_row[row] == winner for row, winner in human_votes if row in by_row)
    compared = sum(row in by_row for row, _ in human_votes)
    return {
        "human": _counts(human_votes),
        "judge": _counts(judge_votes),
        "agreement": {
            "matches": matches,
            "compared": compared,
            "rate": matches / compared if compared else None,
        },
    }
