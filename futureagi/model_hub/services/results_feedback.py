"""Project classic feedback identities without changing recorded run evidence."""

from django.db.models import F, Subquery

from model_hub.models.choices import SourceChoices
from model_hub.models.develop_dataset import Column
from model_hub.models.evals_metric import Feedback
from model_hub.models.experiments import ExperimentsTable
from model_hub.models.native_experiment_runs import (
    NativeExampleBinding,
    NativeRunBinding,
)
from model_hub.services.persisted_run_comparison import _bindings


def feedback_targets(runs):
    """Resolve only pinned experiment members on already authorized native runs."""
    by_id = {
        run.id: run
        for run in runs
        if getattr(run, "experiment_id", None) and run.task_kind != "external"
    }
    if not by_id:
        return {}
    bindings = NativeRunBinding.objects.filter(run_id__in=by_id).values(
        "run_id",
        "snapshot_id",
        "snapshot__experiment_id",
        "snapshot__dataset_version_id",
        "snapshot__snapshot_dataset_id",
        "snapshot__snapshot_dataset__organization_id",
        "snapshot__snapshot_dataset__workspace_id",
        "snapshot__snapshot_dataset__deleted",
        "snapshot__experiment__snapshot_dataset_id",
        "variant_id",
        "output_column_id",
    )
    members = set(
        ExperimentsTable.user_eval_template_ids.through.objects.filter(
            experimentstable_id__in={run.experiment_id for run in by_id.values()},
            experimentstable__deleted=False,
            userevalmetric__deleted=False,
            userevalmetric__organization_id=F(
                "experimentstable__dataset__organization_id"
            ),
        ).values_list("experimentstable_id", "userevalmetric_id")
    )
    targets = {}
    for binding in bindings:
        run = by_id[binding["run_id"]]
        version = run.dataset_version
        if (
            binding["snapshot__experiment_id"] != run.experiment_id
            or binding["snapshot__dataset_version_id"] != run.dataset_version_id
            or binding["snapshot__snapshot_dataset__deleted"]
            or binding["snapshot__snapshot_dataset_id"]
            != binding["snapshot__experiment__snapshot_dataset_id"]
            or binding["snapshot__snapshot_dataset__organization_id"]
            != version.organization_id
            or binding["snapshot__snapshot_dataset__workspace_id"]
            != version.workspace_id
        ):
            continue
        member_ids = {
            str(metric_id)
            for experiment_id, metric_id in members
            if experiment_id == run.experiment_id
        }
        targets[str(run.id)] = {
            **binding,
            "organization_id": version.organization_id,
            "metrics": {key: None for key in _bindings(run) if key in member_ids},
        }
    if not targets:
        return {}
    from model_hub.services.results_catalog import bounded_list

    columns = bounded_list(
        Column.no_workspace_objects.filter(
            dataset_id__in={
                target["snapshot__snapshot_dataset_id"] for target in targets.values()
            },
            deleted=False,
            source__in=[
                SourceChoices.EXPERIMENT_EVALUATION.value,
                SourceChoices.EVALUATION.value,
            ],
        ).only("id", "dataset_id", "source_id"),
        ("source_id",),
    )
    by_source = {}
    for column in columns:
        by_source.setdefault((column.dataset_id, column.source_id), []).append(
            str(column.id)
        )
    for target in targets.values():
        dataset_id = target["snapshot__snapshot_dataset_id"]
        for key in target["metrics"]:
            source = (
                f"{target['variant_id']}-{target['output_column_id']}-sourceid-{key}"
            )
            candidates = by_source.get((dataset_id, source)) or by_source.get(
                (dataset_id, key), []
            )
            if len(candidates) == 1:
                target["metrics"][key] = candidates[0]
    return targets


def project_feedback(rows, runs):
    """Overlay the latest row correction; trial values and aggregates stay intact."""
    targets = feedback_targets(runs)
    for row in rows:
        for cell in row["results"].values():
            cell["feedback_row_id"] = None
    if not targets or not rows:
        return
    from model_hub.services.results_catalog import bounded_list

    links = NativeExampleBinding.objects.filter(
        snapshot_id__in={target["snapshot_id"] for target in targets.values()},
        version_item__example_id__in=[row["example_id"] for row in rows],
        version_item__deleted=False,
        version_item__dataset_version_id=F("snapshot__dataset_version_id"),
        snapshot_row__deleted=False,
        snapshot_row__dataset_id=F("snapshot__snapshot_dataset_id"),
    ).values_list("snapshot_id", "version_item__example_id", "snapshot_row_id")
    row_ids = {
        (snapshot_id, str(example_id)): str(row_id)
        for snapshot_id, example_id, row_id in links
    }
    column_ids = {
        column_id
        for target in targets.values()
        for column_id in target["metrics"].values()
        if column_id
    }
    latest = {}
    if column_ids and row_ids:
        identity_fields = (
            "organization_id",
            "source_id",
            "row_id",
            "user_eval_metric_id",
        )
        latest_ids = (
            Feedback.objects.filter(
                deleted=False,
                source="experiment",
                source_id__in=column_ids,
                row_id__in=set(row_ids.values()),
                user_eval_metric_id__in={
                    key for target in targets.values() for key in target["metrics"]
                },
                organization_id__in={
                    target["organization_id"] for target in targets.values()
                },
            )
            .order_by(*identity_fields, "-created_at", "-id")
            .distinct(*identity_fields)
            .values("id")
        )
        feedbacks = bounded_list(
            Feedback.objects.filter(
                id__in=Subquery(latest_ids),
            )
            .select_related("user")
            .only(
                "organization_id",
                "source_id",
                "row_id",
                "user_eval_metric_id",
                "value",
                "explanation",
                "created_at",
                "user__name",
            ),
            ("value", "explanation"),
        )
        for feedback in feedbacks:
            identity = (
                feedback.organization_id,
                feedback.source_id,
                feedback.row_id,
                str(feedback.user_eval_metric_id),
            )
            latest.setdefault(
                identity,
                {
                    "value": feedback.value,
                    "explanation": feedback.explanation,
                    "at": feedback.created_at.isoformat(),
                    "user_name": feedback.user.name,
                },
            )
    for row in rows:
        for rid, cell in row["results"].items():
            target = targets.get(rid)
            if target is None:
                continue
            row_id = row_ids.get((target["snapshot_id"], row["example_id"]))
            cell["feedback_row_id"] = row_id
            if row_id is None:
                continue
            for key, column_id in target["metrics"].items():
                override = latest.get(
                    (target["organization_id"], column_id, row_id, key)
                )
                # A cell no try has scored yet keeps no score; the override
                # shows once the score exists.
                score = cell["scores"].get(key)
                if override is not None and score is not None:
                    score["override"] = override
