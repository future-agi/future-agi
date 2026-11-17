"""A dataset's experiment runs for the Experiments UI and the SDK (SRV-4).

- ``list_dataset_runs``: one page of the runs on a dataset, newest first, each
  with its "#N" label, status, item counts, finality and link into the app.
- The baseline pin: the run a dataset's comparisons and release gates use by
  default (``futureagi run --gate baseline``). Only a finished run of the same
  dataset can be pinned, and never a non-final sample run.

Reads never write. The plan size is read inside the database, so a page of
runs never loads their up-to-10,000-identity plans.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import (
    Case,
    CharField,
    Count,
    F,
    Func,
    IntegerField,
    QuerySet,
    Value,
    When,
)
from django.db.models.fields.json import KeyTransform
from django.utils import timezone

from model_hub.models.develop_dataset import Dataset
from model_hub.models.experiments import (
    DatasetBaseline,
    ExperimentRun,
    ExperimentRunItem,
)
from model_hub.services.experiment_run_read import ITEM_STATUSES
from model_hub.services.experiment_run_writes import _RUN_KEY

RUN_STATUSES = tuple(ExperimentRun.Status.values)
BASELINE_STATUSES = frozenset({"completed", "partial"})


class BaselineError(Exception):
    """The run cannot be pinned as the dataset baseline."""


class BaselineRunNotFound(BaselineError):
    """No live run with that id on this dataset (404)."""


class BaselineRunNotEligible(BaselineError):
    """The run exists but cannot be a baseline (409)."""


@dataclass(frozen=True)
class DatasetRunPage:
    runs: list[dict[str, Any]]
    total: int
    limit: int
    offset: int
    has_more: bool


class _JsonbTypeof(Func):
    function = "jsonb_typeof"
    output_field = CharField()


class _JsonbArrayLength(Func):
    function = "jsonb_array_length"
    output_field = IntegerField()


def run_label(ordinal: int | None) -> str | None:
    return f"#{ordinal}" if ordinal else None


def run_app_url(dataset_id: UUID | str, run_id: UUID | str) -> str | None:
    """A link to the run on the dataset's Experiments tab, from the
    deployment's frontend URL; None when unset (there is no default host).

    ``run`` names the run: with it the tab shows the run's panel (its label
    and status) and the run's traces, joined by run id
    (GET /model-hub/experiment-runs/<run>/traces/). The tab's experiments
    table itself lists the app's experiments, not SDK runs.
    """
    base = (getattr(settings, "FRONTEND_BASE_URL", "") or "").rstrip("/")
    if not base:
        return None
    return f"{base}/dashboard/develop/{dataset_id}?tab=experiments&run={run_id}"


def _live_runs(dataset: Dataset) -> QuerySet:
    return ExperimentRun.no_workspace_objects.filter(
        dataset_version__dataset_id=dataset.id,
        dataset_version__deleted=False,
        deleted=False,
    )


def _plan_size():
    identities = KeyTransform(
        "identities", KeyTransform("plan", KeyTransform(_RUN_KEY, "metadata"))
    )
    # jsonb_array_length raises on a non-array, so check the type first; a
    # run without the write envelope reads as an empty plan (as in SRV-3).
    return Case(
        When(
            plan_type="array",
            then=_JsonbArrayLength(identities),
        ),
        default=Value(0),
        output_field=IntegerField(),
    ), _JsonbTypeof(identities)


def _counts_by_run(run_ids: Collection[UUID]) -> dict[UUID, dict[str, int]]:
    counts = {run_id: dict.fromkeys(ITEM_STATUSES, 0) for run_id in run_ids}
    rows = (
        ExperimentRunItem.no_workspace_objects.filter(
            run_id__in=list(run_ids), deleted=False
        )
        .order_by()
        .values("run_id", "status")
        .annotate(n=Count("id"))
    )
    for row in rows:
        per_run = counts[row["run_id"]]
        per_run[row["status"]] = per_run.get(row["status"], 0) + row["n"]
    return counts


def get_baseline(dataset: Dataset) -> DatasetBaseline | None:
    """The live pin, or None when unpinned or its run is gone."""
    return (
        DatasetBaseline.no_workspace_objects.select_related("run", "pinned_by")
        .filter(
            dataset_id=dataset.id,
            run__deleted=False,
            run__dataset_version__deleted=False,
        )
        .first()
    )


def baseline_payload(pin: DatasetBaseline | None) -> dict[str, Any]:
    if pin is None:
        return {
            "baseline_run_id": None,
            "label": None,
            "pinned_by": None,
            "pinned_at": None,
        }
    user = pin.pinned_by
    return {
        "baseline_run_id": pin.run_id,
        "label": run_label(pin.run.ordinal),
        "pinned_by": (getattr(user, "name", "") or user.email) if user else None,
        "pinned_at": pin.updated_at,
    }


def list_dataset_runs(
    dataset: Dataset,
    *,
    statuses: Collection[str] = (),
    dataset_version_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
    baseline_run_id: UUID | None = None,
) -> DatasetRunPage:
    """One page of the dataset's live runs, newest first (highest ordinal)."""
    runs = _live_runs(dataset)
    if statuses:
        runs = runs.filter(status__in=list(statuses))
    if dataset_version_id is not None:
        runs = runs.filter(dataset_version_id=dataset_version_id)
    total = runs.count()
    plan_size, plan_type = _plan_size()
    page = list(
        runs.annotate(
            plan_type=plan_type,
            run_name=KeyTransform("name", "metadata"),
            run_non_final=KeyTransform("non_final", "metadata"),
        )
        .annotate(plan_size=plan_size)
        .only(
            "id",
            "ordinal",
            "status",
            "task_kind",
            "dataset_version",
            "experiment",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        )
        .order_by(F("ordinal").desc(nulls_last=True), "-created_at", "-id")[
            offset : offset + limit
        ]
    )
    counts = _counts_by_run([run.id for run in page])
    items = []
    for run in page:
        per_run = counts[run.id]
        per_run["not_started"] = max(run.plan_size - sum(per_run.values()), 0)
        items.append(
            {
                "id": run.id,
                "ordinal": run.ordinal,
                "label": run_label(run.ordinal),
                "name": run.run_name if isinstance(run.run_name, str) else None,
                "status": run.status,
                "task_kind": run.task_kind,
                "dataset_version_id": run.dataset_version_id,
                "experiment_id": run.experiment_id,
                # The SDK marks sampled runs (rows=sample(...)) non_final.
                "final": run.run_non_final is not True,
                "is_baseline": baseline_run_id is not None
                and run.id == baseline_run_id,
                "plan_size": run.plan_size,
                "item_counts": per_run,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "created_at": run.created_at,
                "updated_at": run.updated_at,
                "app_url": run_app_url(dataset.id, run.id),
            }
        )
    return DatasetRunPage(
        runs=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
    )


@transaction.atomic
def pin_baseline(dataset: Dataset, *, run_id: UUID, user) -> DatasetBaseline:
    """Point the dataset's baseline at ``run_id``; idempotent for the same run."""
    # Serialize pins of one dataset, so the one-row-per-dataset reuse below
    # never races into the unique constraint.
    Dataset.no_workspace_objects.select_for_update().filter(pk=dataset.pk).only(
        "id"
    ).first()
    run = _live_runs(dataset).filter(id=run_id).first()
    if run is None:
        raise BaselineRunNotFound("Experiment run not found on this dataset")
    if run.status not in BASELINE_STATUSES:
        raise BaselineRunNotEligible(
            "Only a finished run (completed or partial) can be the baseline; "
            f"this run is {run.status}"
        )
    metadata = run.metadata if isinstance(run.metadata, dict) else {}
    if metadata.get("non_final") is True:
        raise BaselineRunNotEligible(
            "A non-final run (a sample of the rows) cannot be the baseline"
        )
    pin = DatasetBaseline.all_objects.filter(dataset_id=dataset.id).first()
    if pin is None:
        pin = DatasetBaseline(dataset=dataset)
    pin.run = run
    pin.pinned_by = user
    pin.deleted = False
    pin.deleted_at = None
    pin.save()
    return pin


@transaction.atomic
def unpin_baseline(dataset: Dataset) -> None:
    """Remove the pin; comparisons then name their baseline explicitly."""
    DatasetBaseline.no_workspace_objects.filter(dataset_id=dataset.id).update(
        deleted=True, deleted_at=timezone.now()
    )
