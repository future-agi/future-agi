"""Exercise delegated migration helpers with default SQL forbidden.

Only ClickHouse responses are synthetic; PostgreSQL reads, writes, validation,
relations and savepoints use real connections. A swallowed routing exception is
still a failure because the guard records every attempted default statement.
"""

import importlib
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import DatabaseError, connections
from django.utils import timezone

ALIAS = "default_direct"
pytestmark = pytest.mark.django_db(transaction=True, databases={"default", ALIAS})


def migration(app, name):
    return importlib.import_module(f"{app}.migrations.{name}")


@contextmanager
def forbidden_default():
    attempts = []

    def reject(execute, sql, params, many, context):
        attempts.append(sql.split()[0])
        raise AssertionError("migration attempted forbidden default SQL")

    with connections["default"].execute_wrapper(reject):
        try:
            yield
        finally:
            assert attempts == [], f"forbidden default accesses: {attempts}"


def run(function):
    with forbidden_default(), connections[ALIAS].schema_editor() as editor:
        function(apps, editor)


def session_response(monkeypatch, session_id, project_id):
    from tracer.services.clickhouse.v2 import trace_session_dict_reader as reader

    client = SimpleNamespace(query=lambda *args, **kwargs: SimpleNamespace(
        result_rows=[(str(session_id), str(session_id), "Synthetic session",
                      timezone.now(), str(project_id))]
    ))
    monkeypatch.setattr(reader, "_get_client", lambda: client)


def queue_item(project, *, stamp_project=False):
    from model_hub.models.annotation_queues import AnnotationQueue, QueueItem

    queue = AnnotationQueue.objects.create(
        name=f"Alias queue {uuid.uuid4()}", organization=project.organization,
    )
    return QueueItem.objects.create(
        queue=queue, organization=project.organization,
        source_type="trace_session", trace_session_id=uuid.uuid4(),
        project=project if stamp_project else None,
    )


def test_0117_session_stamps_without_default_overlay(monkeypatch, project):
    from model_hub.models.annotation_queues import QueueItem

    item = queue_item(project)
    session_response(monkeypatch, item.trace_session_id, project.id)
    run(migration("model_hub", "0117_backfill_queueitem_project").backfill_queueitem_project)
    assert QueueItem.objects.using(ALIAS).get(pk=item.pk).project_id == project.id


def test_0117_resolve_failure_is_visible(monkeypatch, project, capsys):
    from tracer.services.clickhouse.v2 import trace_session_dict_reader as reader

    queue_item(project)
    monkeypatch.setattr(reader, "_get_client", lambda: (_ for _ in ()).throw(
        DatabaseError("synthetic routing failure")
    ))
    run(migration("model_hub", "0117_backfill_queueitem_project").backfill_queueitem_project)
    assert "session resolution failed (DatabaseError)" in capsys.readouterr().out


def test_0120_reads_projects_and_updates_scores_on_alias(monkeypatch, project):
    from model_hub.management.commands import backfill_score_tracer_project as helper
    from model_hub.models.develop_annotations import AnnotationsLabels
    from model_hub.models.score import Score

    label = AnnotationsLabels.objects.create(
        name="Alias label", type="numeric", organization=project.organization,
    )
    score = Score(
        label=label, organization=project.organization, source_type="trace",
        trace_id=uuid.uuid4(), value=1,
    )
    Score.no_workspace_objects.bulk_create([score])
    monkeypatch.setattr(helper, "_read_project_matches", lambda pid, **kwargs: {
        ("trace", str(score.trace_id))
    })
    run(migration("model_hub", "0120_backfill_score_tracer_project")._backfill)
    assert Score.no_workspace_objects.using(ALIAS).get(pk=score.pk).tracer_project_id == project.id


def test_0122_opt_in_reads_overlay_and_bulk_writes_on_alias(monkeypatch, project):
    from model_hub.models.annotation_queues import QueueItem
    from tracer.models.trace_session import TraceSessionOverlay

    item = queue_item(project, stamp_project=True)
    TraceSessionOverlay.objects.create(
        trace_session_id=item.trace_session_id, project_id=project.id,
        display_name="Alias session", bookmarked=True,
    )
    session_response(monkeypatch, item.trace_session_id, project.id)
    monkeypatch.setenv("ANNOTATION_PREVIEW_BACKFILL_ON_MIGRATE", "1")
    def reject_policy(**kwargs):
        raise AssertionError("migration must retain its own SQL timeout policy")

    monkeypatch.setattr(
        "tracer.services.postgres_read_policy.application_postgres_reads", reject_policy,
    )
    observed = []

    def record(execute, sql, params, many, context):
        observed.append(sql)
        return execute(sql, params, many, context)

    with connections[ALIAS].execute_wrapper(record):
        run(migration("model_hub", "0122_backfill_queueitem_source_preview")._backfill)
    assert any("trace_session_overlay" in sql for sql in observed)
    assert not any("statement_timeout" in sql for sql in observed)
    payload = QueueItem.objects.using(ALIAS).get(pk=item.pk).source_preview
    assert payload and not payload.get("deleted")
    assert "Alias session" in str(payload)


def test_0122_routing_failure_propagates_but_live_default_stays_fail_open(monkeypatch):
    from model_hub.management.commands.backfill_queue_item_source_preview import (
        backfill_queue_item_source_previews,
    )

    def reject(*args):
        raise DatabaseError("synthetic routing failure")

    monkeypatch.setenv("ANNOTATION_PREVIEW_BACKFILL_ON_MIGRATE", "1")
    with connections[ALIAS].execute_wrapper(reject):
        with pytest.raises(DatabaseError, match="synthetic routing failure"):
            run(migration("model_hub", "0122_backfill_queueitem_source_preview")._backfill)
    with connections["default"].execute_wrapper(reject):
        assert backfill_queue_item_source_previews() == (0, 0, 0)


def test_0122_write_failure_is_not_recorded_as_cache_miss(monkeypatch, project):
    item = queue_item(project, stamp_project=True)
    session_response(monkeypatch, item.trace_session_id, project.id)
    monkeypatch.setenv("ANNOTATION_PREVIEW_BACKFILL_ON_MIGRATE", "1")

    def reject_update(execute, sql, params, many, context):
        if sql.lstrip().upper().startswith("UPDATE"):
            raise DatabaseError("synthetic write routing failure")
        return execute(sql, params, many, context)

    with connections[ALIAS].execute_wrapper(reject_update):
        with pytest.raises(DatabaseError, match="synthetic write routing failure"):
            run(migration("model_hub", "0122_backfill_queueitem_source_preview")._backfill)


def test_0087_sql_configs_and_transitive_hash_reads_use_alias(project, custom_eval_config):
    from model_hub.models.evals_metric import CompositeEvalChild, EvalTemplate
    from tracer.models.observation_span import EvalLogger
    from tracer.services.eval_tasks.config_hash import resolved_config_hash

    template = custom_eval_config.eval_template
    EvalTemplate.no_workspace_objects.filter(pk=template.pk).update(template_type="composite")
    child = EvalTemplate.objects.create(name="Alias child", organization=project.organization)
    CompositeEvalChild.objects.create(parent=template, child=child, order=0)
    custom_eval_config.refresh_from_db()
    expected = resolved_config_hash(custom_eval_config)
    entry = EvalLogger(
        custom_eval_config=custom_eval_config, error=True, config_hash=None,
        observation_span_id="synthetic-alias-span", trace_id=uuid.uuid4(),
    )
    EvalLogger.objects.bulk_create([entry])
    run(migration("tracer", "0087_eval_entry_baseline_backfill")._forward)
    stored = EvalLogger.objects.using(ALIAS).get(pk=entry.pk)
    assert stored.status == "errored"
    assert stored.config_hash == expected
    run(migration("tracer", "0087_eval_entry_baseline_backfill")._forward)
    assert EvalLogger.objects.using(ALIAS).get(pk=entry.pk).config_hash == expected


def seed_demo_templates():
    from model_hub.models.evals_metric import EvalTemplate

    for eval_id in (100, 101, 47, 66, 72, 64, 15):
        EvalTemplate.no_workspace_objects.get_or_create(
            eval_id=eval_id,
            defaults={"name": f"Demo eval {eval_id}", "config": {}},
        )


def test_0020_complete_reseed_and_savepoints_stay_on_alias(user, capsys):
    from model_hub.models.develop_dataset import Dataset, Row
    from tracer.models.project import Project

    seed_demo_templates()
    broken = Dataset.objects.create(
        name="Demo-dataset", source="demo", organization=user.organization,
        model_type="GenerativeLLM",
    )
    run(migration("accounts", "0020_reseed_broken_demo_data").reseed_broken_demo_data)
    output = capsys.readouterr().out
    assert "OK: 1, Failed: 0" in output
    assert not Dataset.objects.using(ALIAS).filter(pk=broken.pk).exists()
    datasets = Dataset.objects.using(ALIAS).filter(organization=user.organization, source="demo")
    assert set(datasets.values_list("name", flat=True)) == {"Demo-dataset", "Image-Demo-dataset"}
    assert Row.objects.using(ALIAS).filter(dataset__in=datasets).count() > 50
    assert Project.objects.using(ALIAS).filter(organization=user.organization, source="demo").count() == 2


@pytest.mark.parametrize("raise_after_seed", [False, True])
def test_0020_failure_rolls_back_deletions_and_helper_writes(
    user, monkeypatch, capsys, raise_after_seed
):
    from accounts import user_onboard
    from model_hub.models.develop_dataset import Dataset

    broken = Dataset.objects.create(
        name="Demo-dataset", source="demo", organization=user.organization,
        model_type="GenerativeLLM",
    )
    original = user_onboard.upload_demo_dataset

    def incomplete(org_id, user_id, **kwargs):
        # Exercise the real helper then force verification to see no rows.
        original(org_id, user_id, **kwargs)
        if raise_after_seed:
            raise RuntimeError("synthetic delegated failure")

    monkeypatch.setattr(user_onboard, "upload_demo_dataset", incomplete)
    # No eval templates: the normal helper cannot complete, and the migration
    # must retain the old broken data rather than commit its deletion.
    run(migration("accounts", "0020_reseed_broken_demo_data").reseed_broken_demo_data)
    assert "Failed: 1" in capsys.readouterr().out
    assert Dataset.objects.using(ALIAS).filter(pk=broken.pk).exists()


def test_onboarding_default_path_still_uses_default_and_restores_context(user):
    from accounts.user_onboard import create_demo_traces_and_spans
    from tfc.migration_database import delegated_database_alias, migration_database
    from tfc.routers import ReadReplicaRouter
    from tracer.models.project import Project

    calls = []

    def record(execute, sql, params, many, context):
        calls.append(context["connection"].alias)
        return execute(sql, params, many, context)

    with connections["default"].execute_wrapper(record):
        assert create_demo_traces_and_spans(user.organization_id, user.id) is None
    assert calls and set(calls) == {"default"}
    assert Project.objects.filter(organization=user.organization, source="demo").count() == 2
    router = ReadReplicaRouter()

    @migration_database
    def failing(*, using=None):
        assert router.db_for_read(Project) == ALIAS
        assert router.db_for_write(Project) == ALIAS
        raise RuntimeError("synthetic failure")

    with pytest.raises(RuntimeError):
        failing(using=ALIAS)
    assert delegated_database_alias() is None
    assert router.db_for_write(Project) == "default"
