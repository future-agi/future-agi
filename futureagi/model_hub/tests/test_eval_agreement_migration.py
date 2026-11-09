"""The value-clock backfill resumes without repeating committed schema DDL."""

import importlib
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection
from django.db.models.query import QuerySet
from django.utils import timezone

from model_hub.models.score import Score
from model_hub.tests.test_eval_agreement import AgreementTenant


@pytest.mark.django_db(transaction=True)
def test_interrupted_value_clock_backfill_resumes_and_preserves_new_writes(monkeypatch):
    migration = importlib.import_module(
        "model_hub.migrations.0125_backfill_score_value_updated_at"
    )
    schema = importlib.import_module("model_hub.migrations.0124_score_value_updated_at")
    assert migration.Migration.atomic is False
    assert schema.Migration.atomic is True
    assert migration.Migration.dependencies == [
        ("model_hub", "0124_score_value_updated_at")
    ]
    tenant = AgreementTenant("clock-backfill")
    records = Score.no_workspace_objects.bulk_create(
        [
            Score(
                organization=tenant.org,
                workspace=tenant.workspace,
                dataset_row=tenant.rows[0],
                source_type="dataset_row",
                label=tenant.label,
                annotator=tenant.user,
                score_source="human",
                value={"selected": ["Pass"]},
                value_updated_at=None,
            )
            for _ in range(1002)
        ]
    )
    ids = [record.id for record in records]
    original_update = QuerySet.update
    updates = []

    def interrupt_second_batch(query, **kwargs):
        if query.model is Score and "value_updated_at" in kwargs:
            updates.append(True)
            if len(updates) == 2:
                raise RuntimeError("synthetic backfill interruption")
        return original_update(query, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(QuerySet, "update", interrupt_second_batch)
            with pytest.raises(RuntimeError, match="synthetic backfill interruption"):
                migration.preserve_existing_order(
                    apps, SimpleNamespace(connection=connection)
                )
        pending = Score.no_workspace_objects.filter(
            id__in=ids, value_updated_at__isnull=True
        )
        assert pending.count() == 2
        survivor = pending.first()
        survivor.value = {"selected": ["Fail"]}
        survivor.save(update_fields=["value"])
        saved_time = survivor.value_updated_at
        migration.preserve_existing_order(apps, SimpleNamespace(connection=connection))
        assert pending.count() == 0
        survivor.refresh_from_db()
        assert survivor.value_updated_at == saved_time
        rows = Score.no_workspace_objects.filter(id__in=ids).exclude(id=survivor.id)
        assert all(
            value_time == created_at
            for value_time, created_at in rows.values_list(
                "value_updated_at", "created_at"
            )
        )
    finally:
        tenant.client.stop_workspace_injection()


@pytest.mark.django_db
def test_legacy_notes_do_not_make_an_unchanged_value_the_newest_vote():
    migration = importlib.import_module(
        "model_hub.migrations.0125_backfill_score_value_updated_at"
    )
    tenant = AgreementTenant("legacy-notes")
    newest = Score.no_workspace_objects.create(
        organization=tenant.org,
        workspace=tenant.workspace,
        dataset_row=tenant.rows[0],
        source_type="dataset_row",
        label=tenant.label,
        annotator=tenant.user,
        score_source="human",
        value={"selected": ["Fail"]},
    )
    Score.no_workspace_objects.filter(id__in=[tenant.scores[0].id, newest.id]).update(
        value_updated_at=None
    )
    Score.no_workspace_objects.filter(id=tenant.scores[0].id).update(
        notes="Later clarification", updated_at=timezone.now()
    )
    try:
        migration.preserve_existing_order(apps, SimpleNamespace(connection=connection))
        assert tenant.read()["report"]["accuracy"] == pytest.approx(1 / 3)
        older = Score.no_workspace_objects.get(id=tenant.scores[0].id)
        assert older.value_updated_at == older.created_at
    finally:
        tenant.client.stop_workspace_injection()
