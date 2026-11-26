"""Routing of the READ_REPLICA_OPT_IN candidate models (TH-3119).

The audit found nothing to pin for EndUser, ErrorMemory, TraceAnnotation and
EvalTaskLogger: their writes go through get_or_create or update_or_create, and
their other reads are listings or code nothing calls. These tests check that
against the lagging replica (`tfc.tests.lagging_replica`). Ordinary reads of
the four models go to the replica, which is the load the opt-in sheds.
get_or_create and update_or_create find a row the replica has not applied,
because Django runs their lookup on the db_for_write alias, so those calls need
no pin. The last tests cover rules the opt-in documentation states: an atomic
block does not pin reads, on_commit does not wait for the replica, and a
feature-key-only value leaves every model on the primary.
"""

import uuid

import pytest
from django.db import router, transaction

from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from tfc.routers import ReadReplicaRouter
from tfc.tests.lagging_replica import OPTED_IN_MODELS, lagging_replica  # noqa: F401
from tracer.models.eval_task import EvalTaskLogger, EvalTaskStatus
from tracer.models.observation_span import EndUser, UserIdType
from tracer.models.trace import Trace
from tracer.models.trace_annotation import TraceAnnotation
from tracer.models.trace_error_analysis import ErrorMemory
from tracer.queries.error_analysis import TraceErrorAnalysisDB

pytestmark = pytest.mark.django_db(transaction=True)


def _end_user(ctx):
    lookup = {
        "project": ctx["project"],
        "organization": ctx["project"].organization,
        "user_id": "user-" + uuid.uuid4().hex,
        "user_id_type": UserIdType.CUSTOM,
    }
    return lookup, {}


def _error_memory(ctx):
    lookup = {
        "project": ctx["project"],
        "memory_type": "episodic",
        "memory_key": "note-" + uuid.uuid4().hex,
    }
    return lookup, {"memory_data": {"note": "first"}}


def _trace_annotation(ctx):
    lookup = {
        "trace": ctx["trace"],
        "annotation_label": ctx["label"],
        "user": ctx["user"],
    }
    return lookup, {"annotation_value": "first"}


def _eval_task_logger(ctx):
    from tracer.tests.eval_task_factories import make_task

    task = make_task(project=ctx["project"], name="logger " + uuid.uuid4().hex[:8])
    return {"eval_task": task}, {"status": EvalTaskStatus.RUNNING}


ROWS = {
    EndUser: _end_user,
    ErrorMemory: _error_memory,
    TraceAnnotation: _trace_annotation,
    EvalTaskLogger: _eval_task_logger,
}


@pytest.fixture
def ctx(project, trace, user, organization, workspace):
    label = AnnotationsLabels.objects.create(
        name="Replica routing label",
        organization=organization,
        workspace=workspace,
        type=AnnotationTypeChoices.TEXT.value,
        settings={"placeholder": "", "min_length": 0, "max_length": 100},
    )
    return {"project": project, "trace": trace, "user": user, "label": label}


@pytest.mark.parametrize("model", list(ROWS), ids=lambda m: m.__name__)
def test_plain_reads_use_the_replica_and_get_or_create_the_primary(
    lagging_replica,  # noqa: F811
    ctx,
    model,
):
    assert model.__name__ in OPTED_IN_MODELS
    lookup, defaults = ROWS[model](ctx)
    lagging_replica.freeze()
    row = model.objects.create(**lookup, **defaults)

    with lagging_replica.capture() as replica_queries:
        assert not model.objects.filter(pk=row.pk).exists()
    assert len(replica_queries.captured_queries) == 1

    with lagging_replica.capture() as replica_queries:
        found, created = model.objects.get_or_create(**lookup, defaults=defaults)
        if model is not EndUser:
            # EndUser's workspace-scoped default manager adds an outer join,
            # which Postgres cannot lock (update_or_create selects FOR UPDATE);
            # nothing calls update_or_create on it.
            updated, updated_created = model.objects.update_or_create(
                **lookup, defaults=defaults
            )
            assert (updated.pk, updated_created) == (row.pk, False)
    assert replica_queries.captured_queries == []
    assert (found.pk, created) == (row.pk, False)
    assert model.objects.using("default").filter(**lookup).count() == 1


def test_error_memory_get_or_create_helper_reuses_a_row_the_replica_lacks(
    lagging_replica,  # noqa: F811
    project,
):
    db = TraceErrorAnalysisDB()
    lagging_replica.freeze()
    first = db.get_or_create_memory(project.id, "semantic", "pattern", {"n": 1})
    again = db.get_or_create_memory(project.id, "semantic", "pattern", {"n": 2})

    assert again.pk == first.pk
    stored = ErrorMemory.objects.using("default").get(pk=first.pk)
    assert stored.access_count == 2
    assert stored.memory_data == {"n": 1}


def test_atomic_block_does_not_pin_router_reads(
    lagging_replica,  # noqa: F811
    project,
    project_version,
):
    lagging_replica.freeze()
    with transaction.atomic():
        trace = Trace.objects.create(project=project, project_version=project_version)
        assert not Trace.objects.filter(pk=trace.pk).exists()
        assert Trace.objects.using("default").filter(pk=trace.pk).exists()


def test_on_commit_callback_runs_before_the_replica_applies_the_commit(
    lagging_replica,  # noqa: F811
    project,
    project_version,
):
    seen = []
    lagging_replica.freeze()
    with transaction.atomic():
        trace = Trace.objects.create(project=project, project_version=project_version)
        transaction.on_commit(
            lambda: seen.append(
                (
                    Trace.objects.filter(pk=trace.pk).exists(),
                    Trace.objects.using("default").filter(pk=trace.pk).exists(),
                )
            )
        )
    assert seen == [(False, True)]


def test_feature_key_only_value_reads_every_candidate_model_from_the_primary(
    lagging_replica,  # noqa: F811
    settings,
    project,
    project_version,
):
    from django.apps import apps

    feature_only = ["feature:project_list", "feature:org_config_bulk"]
    models = [
        model
        for model in apps.get_models()
        if model.__name__ in OPTED_IN_MODELS and model._meta.app_label == "tracer"
    ]
    assert sorted(m.__name__ for m in models) == sorted(OPTED_IN_MODELS)
    for model in models:
        assert ReadReplicaRouter(opt_in=feature_only).db_for_read(model) == "default"
        assert router.db_for_read(model) == "replica"

    settings.READ_REPLICA_OPT_IN = feature_only
    lagging_replica.freeze()
    trace = Trace.objects.create(project=project, project_version=project_version)
    with lagging_replica.capture() as replica_queries:
        assert Trace.objects.filter(pk=trace.pk).exists()
    assert replica_queries.captured_queries == []
    assert all(router.db_for_read(model) == "default" for model in models)
