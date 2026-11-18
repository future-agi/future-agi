# ruff: noqa: F811 -- shared real application enrollment fixture
"""Real application/PG A04 integration; no serving/producer completeness claim."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from django.db import close_old_connections, connection, connections, transaction
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIRequestFactory

from tracer.models.metric_pg_revision import MetricPgResponseRevision
from tracer.models.project import Project
from tracer.models.trace_session import TraceSessionOverlay
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.derived_metrics.base_list_current_pg_capture import (
    PgResponseCapture,
    assemble_pg_response,
)
from tracer.services.derived_metrics.base_list_current_pg_state import (
    PgRevisionUnavailable,
    read_pg_response_vector,
)
from tracer.tests.test_base_list_current_pg_state import enrolled, label, score  # noqa: F401
from tracer.views.trace_session import TraceSessionView

pytestmark = pytest.mark.django_db(transaction=True)


def request_for(project, user):
    view = TraceSessionView()
    view.action_map = {"get": "list_sessions"}
    request = view.initialize_request(APIRequestFactory().get("/sessions/"))
    request.organization = project.organization
    request.workspace = project.workspace
    request.user = user
    return request


def independently(function):
    def run():
        close_old_connections()
        try:
            return function()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(run).result(10)


def test_vector_precedes_reload_and_offpage_user_materialization(enrolled, user):
    project = enrolled
    annotation = label(project)
    page_id, offpage_id = uuid4(), uuid4()
    score(project, annotation, trace_session_id=page_id, value={"text": "page"})
    score(project, annotation, trace_session_id=offpage_id, annotator=user)
    TraceSessionOverlay.objects.create(
        project_id=project.pk, trace_session_id=page_id, display_name="renamed"
    )
    project.session_config = [{"id": "session_name", "name": "Old config"}]
    project.save(update_fields=["session_config"])
    # The caller's cached object is now stale; capture must reload AFTER vector.
    Project.objects.filter(pk=project.pk).update(session_config=None)
    with CaptureQueriesContext(connection) as queries:
        capture = PgResponseCapture(request_for(project, user), project.pk)
        values = capture.materialize([page_id]).values()
        payload = capture.finish(values)
    sql = [item["sql"].lower() for item in queries]
    vector_position = next(
        i for i, text in enumerate(sql) if "metric_pg_response_revision" in text
    )
    project_position = next(
        i for i, text in enumerate(sql) if f'from "{Project._meta.db_table}"' in text
    )
    assert vector_position < project_position
    assert capture.project.session_config is None
    assert values["overlays"][str(page_id)] == "renamed"
    column = next(c for c in values["config"] if c["id"] == str(annotation.pk))
    assert str(user.pk) in column["annotators"]
    assert str(offpage_id) not in values["scores"]
    assert json.loads(payload) == values
    with pytest.raises(PgRevisionUnavailable):
        capture.materialize([page_id])


@pytest.mark.parametrize(
    "kind",
    ["project", "overlay", "label", "score", "offpage", "user", "aba", "missing"],
)
def test_actual_dependency_mutations_invalidate_complete_assembly(enrolled, user, kind):
    project = enrolled
    annotation = label(project)
    page_id = uuid4()
    entry = score(project, annotation, trace_session_id=page_id, annotator=user)
    overlay = TraceSessionOverlay.objects.create(
        project_id=project.pk, trace_session_id=page_id
    )
    capture = PgResponseCapture(request_for(project, user), project.pk)
    values = capture.materialize([page_id]).values()

    def mutate():
        if kind == "project":
            Project.objects.filter(pk=project.pk).update(
                session_config=[{"name": "new"}]
            )
        elif kind == "overlay":
            TraceSessionOverlay.objects.filter(pk=overlay.pk).update(display_name="new")
        elif kind == "label":
            annotation.name = "new"
            annotation.save(update_fields=["name"])
        elif kind == "score":
            entry.value = {"text": "new"}
            entry.save(update_fields=["value"])
        elif kind == "offpage":
            score(project, annotation, annotator=user)
        elif kind == "user":
            user.name = "new"
            user.save(update_fields=["name"])
        elif kind == "aba":
            old = Project.objects.get(pk=project.pk).name
            Project.objects.filter(pk=project.pk).update(name="temporary")
            Project.objects.filter(pk=project.pk).update(name=old)
        else:
            MetricPgResponseRevision.objects.filter(project_id=project.pk).delete()

    independently(mutate)
    with pytest.raises(PgRevisionUnavailable):
        capture.finish(values)


def test_rollback_keeps_capture_coherent(enrolled, user):
    capture = PgResponseCapture(request_for(enrolled, user), enrolled.pk)

    def rollback():
        with transaction.atomic():
            Project.objects.filter(pk=enrolled.pk).update(name="rolled back")
            transaction.set_rollback(True)

    independently(rollback)
    assert capture.finish({"empty": []}) == b'{"empty":[]}'


def test_tenant_loss_missing_vector_and_replica_are_unavailable(enrolled, user):
    request = request_for(enrolled, user)
    with pytest.raises(PgRevisionUnavailable):
        PgResponseCapture(request, uuid4())
    with pytest.raises(PgRevisionUnavailable):
        read_pg_response_vector([], using="replica")
    capture = PgResponseCapture(request, enrolled.pk)
    Project.objects.filter(pk=enrolled.pk).update(deleted=True)
    with pytest.raises(PgRevisionUnavailable):
        capture.finish({})


def test_whole_retry_uses_new_project_and_original_deadline(enrolled, user):
    request = request_for(enrolled, user)
    deadline = ReadDeadline.start(20_000)
    calls = []

    def assemble(capture, original):
        assert original is deadline
        old = capture.materialize([]).values()
        Project.objects.filter(pk=enrolled.pk).update(
            session_config=[{"id": "new", "name": "new"}]
        )
        return old

    def retry(actual_request, project_id, original):
        assert (
            calls == ["drained"] and original is deadline and actual_request is request
        )
        fresh = PgResponseCapture(actual_request, project_id)
        return fresh.finish(fresh.materialize([]).values())

    payload = assemble_pg_response(
        request,
        enrolled.pk,
        assemble=assemble,
        retry_native=retry,
        deadline=deadline,
        privacy_safe=lambda: True,
        drain_and_release=lambda: calls.append("drained"),
    )
    assert json.loads(payload)["config"][0]["id"] == "new"
    assert calls == ["drained"]


def test_failed_drain_or_privacy_forbids_retry(enrolled, user):
    request = request_for(enrolled, user)

    def assemble(capture, deadline):
        raise PgRevisionUnavailable("changed")

    def retry(*args):
        pytest.fail("unsafe retry")

    with pytest.raises(PgRevisionUnavailable, match="privacy"):
        assemble_pg_response(
            request,
            enrolled.pk,
            assemble=assemble,
            retry_native=retry,
            deadline=ReadDeadline.start(10_000),
            privacy_safe=lambda: False,
            drain_and_release=lambda: None,
        )

    def failed_drain():
        raise RuntimeError("cleanup pending")

    with pytest.raises(RuntimeError, match="cleanup pending"):
        assemble_pg_response(
            request,
            enrolled.pk,
            assemble=assemble,
            retry_native=retry,
            deadline=ReadDeadline.start(10_000),
            privacy_safe=lambda: True,
            drain_and_release=failed_drain,
        )


def test_late_serializer_read_is_inside_final_vector(enrolled, user):
    capture = PgResponseCapture(request_for(enrolled, user), enrolled.pk)

    class LazyValues:
        def __iter__(self):
            Project.objects.filter(pk=enrolled.pk).update(name="during serialization")
            yield "serialized"

    with pytest.raises(PgRevisionUnavailable):
        capture.finish({"lazy": LazyValues()})
