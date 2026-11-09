"""Tests for migration 0127's ordinal backfill (``assign_ordinals``, RunPython).

Existing runs are numbered 1, 2, 3 ... per dataset, across the dataset's
versions, in ``(created_at, id)`` order, soft-deleted runs included. Like the
other backfill tests here (0114, 0117), the test calls the migration's function
directly with the live app registry. The function reads through
``_base_manager``, which is a plain manager on both the historical and the live
model, so this exercises the same query the migration runs. ``makemigrations
--check`` and ``migrate`` on the VM prove the schema half.
"""

import importlib
from datetime import timedelta

import pytest
from django.apps import apps as django_apps
from django.utils import timezone

from model_hub.models.dataset_versions import DatasetVersion
from model_hub.models.develop_dataset import Dataset
from model_hub.models.experiments import ExperimentRun

_migration = importlib.import_module(
    "model_hub.migrations.0127_experimentrun_ordinal_datasetbaseline"
)


def _run_backfill():
    _migration.assign_ordinals(django_apps, None)


def _dataset(organization, workspace, user, name):
    return Dataset.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )


def _run(version, created_at, *, deleted=False):
    """A run as it was before 0127: no ordinal, a fixed creation time."""
    run = ExperimentRun.all_objects.create(
        dataset_version=version, task_kind="external"
    )
    ExperimentRun.all_objects.filter(pk=run.pk).update(
        created_at=created_at, deleted=deleted, ordinal=None
    )
    return run.pk


def _ordinals(run_ids):
    return dict(
        ExperimentRun.all_objects.filter(pk__in=run_ids).values_list("id", "ordinal")
    )


@pytest.mark.django_db
def test_runs_are_numbered_per_dataset_by_creation_time_then_id(
    organization, workspace, user
):
    t0 = timezone.now() - timedelta(days=2)
    t1 = t0 + timedelta(hours=1)
    t2 = t0 + timedelta(hours=2)

    first = _dataset(organization, workspace, user, "backfill-first")
    first_v1 = DatasetVersion.no_workspace_objects.create(dataset=first)
    first_v2 = DatasetVersion.no_workspace_objects.create(dataset=first)
    second = _dataset(organization, workspace, user, "backfill-second")
    second_v1 = DatasetVersion.no_workspace_objects.create(dataset=second)

    # Inserted out of order; two pairs share a created_at.
    latest = _run(first_v2, t2)
    tie_a = _run(first_v2, t1)
    tie_b = _run(first_v1, t1, deleted=True)  # soft-deleted runs are numbered too
    earliest = _run(first_v1, t0)
    other_a = _run(second_v1, t0)
    other_b = _run(second_v1, t0)

    _run_backfill()

    tie_low, tie_high = sorted([tie_a, tie_b])
    assert _ordinals([earliest, tie_a, tie_b, latest]) == {
        earliest: 1,
        tie_low: 2,
        tie_high: 3,
        latest: 4,
    }
    other_low, other_high = sorted([other_a, other_b])
    assert _ordinals([other_a, other_b]) == {other_low: 1, other_high: 2}


@pytest.mark.django_db
def test_the_backfill_is_idempotent_and_leaves_numbered_runs_alone(
    organization, workspace, user
):
    t0 = timezone.now() - timedelta(days=1)
    dataset = _dataset(organization, workspace, user, "backfill-idempotent")
    version = DatasetVersion.no_workspace_objects.create(dataset=dataset)
    runs = [_run(version, t0 + timedelta(minutes=minute)) for minute in range(3)]

    _run_backfill()
    numbered = _ordinals(runs)
    _run_backfill()

    assert _ordinals(runs) == numbered
    assert sorted(numbered.values()) == [1, 2, 3]
