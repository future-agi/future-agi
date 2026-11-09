"""Red-first contract for the Score value-clock backfill (model_hub 0124 -> 0125).

This file is committed before any Score value-clock migration exists, so on that
head it fails at its first assertion: the 0125 backfill migration is missing.
Once the backfill lands, each test applies the migration's own operations to
fixture rows through Django's migration machinery and asserts the stamped
values. One test then unapplies it (the recorded noop reverse), asserts that
nothing moved, and applies it again to prove idempotence. The others cover an
empty table, the 1000-row statement bound with a partial last page, an
interrupted run that resumes without overwriting current writers, and a
production-shaped mix: two tenants, soft-deleted rows, rows with value history,
notes-only edits and rows that current writers have already stamped.
"""

import importlib
import importlib.util
import math
import uuid
from datetime import timedelta

import pytest
from django.db import connection, migrations
from django.db.migrations.loader import MigrationLoader
from django.db.models.query import QuerySet
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models.organization import Organization
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score

APP = "model_hub"
SCHEMA = "0124_score_value_updated_at"
BACKFILL = "0125_backfill_score_value_updated_at"
MODULE = f"{APP}.migrations.{BACKFILL}"
BATCH = 1000
FIELDS = (
    "created_at",
    "updated_at",
    "value",
    "value_history",
    "notes",
    "deleted",
    "organization_id",
    "value_updated_at",
)
UNTOUCHED = tuple(name for name in FIELDS if name != "value_updated_at")
_LOADED = {}


def _backfill_module():
    spec = importlib.util.find_spec(MODULE)
    missing = f"{MODULE} is missing: the value-clock backfill has not landed"
    assert spec is not None, missing
    return importlib.import_module(MODULE)


def _migration_and_state():
    """The loaded backfill migration and a fresh project state just before it."""
    _backfill_module()
    if "state" not in _LOADED:
        loader = MigrationLoader(connection, ignore_no_migrations=True)
        assert (APP, BACKFILL) in loader.graph.nodes
        _LOADED["migration"] = loader.graph.nodes[(APP, BACKFILL)]
        _LOADED["state"] = loader.project_state((APP, SCHEMA), at_end=True)
    return _LOADED["migration"], _LOADED["state"].clone()


def _run(direction):
    migration, state = _migration_and_state()
    with connection.schema_editor(atomic=migration.atomic) as editor:
        if direction == "forwards":
            migration.apply(state, editor)
        else:
            migration.unapply(state, editor)


class _Statements:
    """Rows written by each value-clock UPDATE and the id SELECTs that page it."""

    def __init__(self, patch, *, fail_on=None, ceiling=None):
        self.rowcounts = []
        self.selects = []
        original = QuerySet.update
        statements = self

        def update(queryset, **kwargs):
            clock = set(kwargs) == {"value_updated_at"}
            if not clock or queryset.model._meta.label != "model_hub.Score":
                return original(queryset, **kwargs)
            call = len(statements.rowcounts) + 1
            if ceiling is not None and call > ceiling:
                raise AssertionError("the backfill exceeded its statement bound")
            if call == fail_on:
                raise RuntimeError("synthetic backfill interruption")
            count = original(queryset, **kwargs)
            statements.rowcounts.append(count)
            return count

        patch.setattr(QuerySet, "update", update)


def _run_measured(monkeypatch, direction="forwards", **bounds):
    table = f'"{Score._meta.db_table}"'
    with monkeypatch.context() as patch:
        statements = _Statements(patch, **bounds)
        with CaptureQueriesContext(connection) as captured:
            _run(direction)
    statements.selects = [
        query["sql"]
        for query in captured.captured_queries
        if query["sql"].startswith("SELECT")
        and table in query["sql"]
        and "IS NULL" in query["sql"]
    ]
    return statements


def _tenant(name):
    organization = Organization.objects.create(name=f"value-clock-{name}")
    label = AnnotationsLabels.no_workspace_objects.create(
        name=f"value-clock-{name}",
        type="categorical",
        organization=organization,
        settings={
            "rule_prompt": "",
            "multi_choice": False,
            "options": [{"label": "Pass"}, {"label": "Fail"}],
            "auto_annotate": False,
            "strategy": None,
        },
    )
    return organization, label


def _scores(organization, label, count, *, history=(), deleted=False, stamped=None):
    created = Score._base_manager.bulk_create(
        [
            Score(
                organization=organization,
                label=label,
                source_type="trace",
                trace_id=uuid.uuid4(),
                score_source="human",
                value={"selected": ["Pass"]},
                value_history=list(history),
                deleted=deleted,
                value_updated_at=stamped,
            )
            for _ in range(count)
        ],
        batch_size=500,
    )
    return [score.pk for score in created]


def _set_times(ids, *, created_at, updated_at):
    Score._base_manager.filter(pk__in=ids).update(
        created_at=created_at, updated_at=updated_at
    )


def _snapshot(ids):
    rows = Score._base_manager.filter(pk__in=ids).values_list("pk", *FIELDS)
    return {row[0]: dict(zip(FIELDS, row[1:], strict=True)) for row in rows}


def _bounded(statements):
    return all(f"LIMIT {BATCH}" in sql for sql in statements.selects)


def test_backfill_is_a_separate_migration_with_a_recorded_noop_reverse():
    module = _backfill_module()
    backfill = module.Migration
    assert backfill.atomic is False
    assert backfill.dependencies == [(APP, SCHEMA)]
    assert len(backfill.operations) == 1
    operation = backfill.operations[0]
    assert isinstance(operation, migrations.RunPython)
    assert operation.code is module.preserve_existing_order
    assert operation.reverse_code is migrations.RunPython.noop
    assert operation.reversible
    schema = importlib.import_module(f"{APP}.migrations.{SCHEMA}").Migration
    assert schema.atomic is True
    assert schema.dependencies == [(APP, "0123_usereval_is_experiment_default")]
    assert not any(isinstance(op, migrations.RunPython) for op in schema.operations)
    added = [op for op in schema.operations if isinstance(op, migrations.AddField)]
    assert [(op.model_name, op.name, op.field.null) for op in added] == [
        ("score", "value_updated_at", True)
    ]


@pytest.mark.django_db(transaction=True)
def test_empty_table_reads_one_bounded_page_and_writes_nothing(monkeypatch):
    _backfill_module()
    Score._base_manager.all().delete()
    assert not Score._base_manager.exists()
    statements = _run_measured(monkeypatch, ceiling=0)
    assert statements.rowcounts == []
    assert len(statements.selects) == 1
    assert _bounded(statements)


@pytest.mark.django_db(transaction=True)
def test_each_statement_writes_at_most_one_batch(monkeypatch):
    _backfill_module()
    organization, label = _tenant("batches")
    total = 2 * BATCH + 345
    ids = _scores(organization, label, total)
    created = timezone.now() - timedelta(days=9)
    _set_times(ids, created_at=created, updated_at=created + timedelta(days=1))
    pages = math.ceil(total / BATCH)
    statements = _run_measured(monkeypatch, ceiling=pages)
    assert statements.rowcounts == [BATCH, BATCH, total - 2 * BATCH]
    assert len(statements.selects) == pages + 1
    assert _bounded(statements)
    stamped = Score._base_manager.filter(pk__in=ids).values_list(
        "value_updated_at", flat=True
    )
    assert set(stamped) == {created}


@pytest.mark.django_db(transaction=True)
def test_apply_reverse_reapply_on_production_shaped_rows(monkeypatch):
    _backfill_module()
    now = timezone.now()
    created = now - timedelta(days=20)
    later = now - timedelta(days=2)
    writer_time = now - timedelta(hours=3)
    history = [{"value": {"selected": ["Fail"]}, "at": later.isoformat()}]
    expected = {}
    for name in ("tenant-a", "tenant-b"):
        organization, label = _tenant(name)
        unchanged = _scores(organization, label, 3)
        changed = _scores(organization, label, 2, history=history)
        gone_unchanged = _scores(organization, label, 1, deleted=True)
        gone_changed = _scores(organization, label, 1, history=history, deleted=True)
        current = _scores(organization, label, 2, stamped=writer_time)
        groups = unchanged + changed + gone_unchanged + gone_changed + current
        _set_times(groups, created_at=created, updated_at=later)
        Score._base_manager.filter(pk__in=unchanged).update(notes="Later note")
        expected.update(dict.fromkeys(unchanged + gone_unchanged, created))
        expected.update(dict.fromkeys(changed + gone_changed, later))
        expected.update(dict.fromkeys(current, writer_time))
    ids = list(expected)
    with connection.cursor() as cursor:
        columns = connection.introspection.get_table_description(
            cursor, Score._meta.db_table
        )
    assert next(c for c in columns if c.name == "value_updated_at").null_ok
    before = _snapshot(ids)
    pending = [pk for pk in ids if before[pk]["value_updated_at"] is None]
    assert len(pending) == 14

    applied = _run_measured(monkeypatch, ceiling=1)
    assert applied.rowcounts == [len(pending)]
    assert len(applied.selects) == 2
    assert _bounded(applied)
    after = _snapshot(ids)
    assert {pk: row["value_updated_at"] for pk, row in after.items()} == expected
    for pk in ids:
        kept = {f: after[pk][f] for f in UNTOUCHED}
        assert kept == {f: before[pk][f] for f in UNTOUCHED}

    reversed_ = _run_measured(monkeypatch, "backwards", ceiling=0)
    assert reversed_.rowcounts == []
    assert reversed_.selects == []
    assert _snapshot(ids) == after

    again = _run_measured(monkeypatch, ceiling=0)
    assert again.rowcounts == []
    assert len(again.selects) == 1
    assert _snapshot(ids) == after


@pytest.mark.django_db(transaction=True)
def test_interrupted_backfill_resumes_without_overwriting_writers(monkeypatch):
    _backfill_module()
    organization, label = _tenant("resume")
    created = timezone.now() - timedelta(days=5)
    ids = _scores(organization, label, BATCH + 7)
    _set_times(ids, created_at=created, updated_at=created + timedelta(days=1))
    with pytest.raises(RuntimeError, match="synthetic backfill interruption"):
        _run_measured(monkeypatch, fail_on=2)
    nulls = Score._base_manager.filter(pk__in=ids, value_updated_at__isnull=True)
    pending = sorted(nulls.values_list("pk", flat=True))
    assert len(pending) == 7
    left = set(pending)
    done = _snapshot([pk for pk in ids if pk not in left])
    assert {row["value_updated_at"] for row in done.values()} == {created}

    writer = Score._base_manager.get(pk=pending[0])
    writer.value = {"selected": ["Fail"]}
    writer.save(update_fields=["value"])
    writer.refresh_from_db()
    assert writer.value_updated_at is not None

    resumed = _run_measured(monkeypatch, ceiling=1)
    assert resumed.rowcounts == [6]
    final = _snapshot(ids)
    assert final[writer.pk]["value_updated_at"] == writer.value_updated_at
    assert all(final[pk] == row for pk, row in done.items())
    assert all(final[pk]["value_updated_at"] == created for pk in pending[1:])
