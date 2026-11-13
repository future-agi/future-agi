"""Real PG migration/CAS plus CH25-only HTTP writes on a disposable cloud worker.

Opt in with TAG_ASSIGNMENT_DISPOSABLE_PG=1 and an isolated loopback database
whose name starts test_th7275. The test owns a fresh random schema only; it
neither needs nor creates legacy PostgreSQL telemetry tables. This exercises
the new migration against its actual parent state, not a full-stack install.
"""

import importlib
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, connections
from django.db.migrations.loader import MigrationLoader

from tracer.models.entity_tag_assignment import EntityTagAssignment
from tracer.services import entity_tag_assignments as assignments
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.entity_tag_targets import resolve_tag_target
from tracer.tests import test_entity_tag_targets_ch25 as ch25
from tracer.tests.test_entity_tag_target import PROJECT, TRACE, reference
from tracer.tests.test_entity_tag_targets_ch25 import OTHER_PROJECT
from tracer.tests.test_entity_tag_writes import invoke, make_request, payload

engine = ch25.engine


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


@pytest.fixture(scope="module")
def assignment_schema(django_db_blocker):
    if os.environ.get("TAG_ASSIGNMENT_DISPOSABLE_PG") != "1":
        pytest.skip("Requires explicitly provisioned disposable PostgreSQL")
    config = connection.settings_dict
    assert config["HOST"] in ("localhost", "127.0.0.1")
    assert config["NAME"].startswith("test_th7275")
    schema = "tag_write_" + uuid4().hex
    quoted = connection.ops.quote_name(schema)
    with django_db_blocker.unblock():
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE SCHEMA {quoted}")
            cursor.execute(f"SET search_path TO {quoted}")
            cursor.execute("CREATE TABLE tracer_project (id uuid PRIMARY KEY)")
            cursor.execute(
                "INSERT INTO tracer_project (id) VALUES (%s), (%s)",
                [PROJECT, OTHER_PROJECT],
            )
        try:
            loader = MigrationLoader(None, ignore_no_migrations=True)
            # One tracer leaf: a second leaf (for example two 0132s after a
            # dev merge) makes `migrate` refuse to run at all.
            assert "tracer" not in loader.detect_conflicts()
            assert ("tracer", "0133_entity_tag_assignment") in loader.graph.nodes
            migration = importlib.import_module(
                "tracer.migrations.0133_entity_tag_assignment"
            ).Migration("0133_entity_tag_assignment", "tracer")
            assert migration.dependencies == [
                ("tracer", "0132_merge_alert_webhook_and_sql_workbench")
            ]
            state = loader.project_state(migration.dependencies)
            with connection.schema_editor() as editor:
                migration.apply(state.clone(), editor)
            # Rollback/reapply verifies reversibility, not just model create_model.
            with connection.schema_editor() as editor:
                migration.unapply(state.clone(), editor)
            with connection.schema_editor() as editor:
                migration.apply(state.clone(), editor)
            yield schema
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SET search_path TO public")
                cursor.execute(f"DROP SCHEMA {quoted} CASCADE")


@pytest.fixture(autouse=True)
def isolated_stores(assignment_schema, engine, monkeypatch):
    engine.reset()
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM tracer_entity_tag_assignment")
    monkeypatch.setattr(assignments, "V2AnalyticsQueryService", lambda: engine)
    # HTTP request auth/scope has separate fail-closed unit coverage. This
    # fixture provisions only the project FK table, not the entire accounts DB.
    monkeypatch.setattr(
        assignments,
        "_authorize_project",
        lambda request, project_id: SimpleNamespace(id=project_id),
    )


@pytest.mark.parametrize("span,parent", [(False, ""), (True, ""), (True, "parent")])
def test_ch_only_http_save_with_real_assignment_and_no_pg_telemetry(
    engine, span, parent
):
    engine.insert(
        _version=2,
        parent_span_id=parent,
        attrs_string={"tag.tags": '["sdk-attribute"]'},
    )
    before = engine.checksum()
    response = invoke(make_request(span=span), span=span)
    assert response.status_code == 200, response.data
    assert response.data["result"]["tags"] == [
        {"name": "sdk"},
        {"name": "sdk-attribute"},
        {"name": "ui", "color": "#123456"},
    ]
    saved = EntityTagAssignment.objects.using("default").get()
    assert saved.manual_tags == [{"name": "ui", "color": "#123456"}]
    assert saved.revision == 1
    assert saved.entity_kind == ("span" if parent else "trace")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT to_regclass('tracer_trace'), to_regclass('tracer_observation_span')"
        )
        assert cursor.fetchone() == (None, None)
    assert engine.checksum() == before


def test_root_alias_revision_clear_and_collector_replay(engine):
    engine.insert(_version=2)
    assert invoke(make_request()).status_code == 200
    # Root POST is not a separate assignment. Retry after a lost response cannot
    # overwrite the first committed save using revision zero.
    conflict = invoke(make_request(span=True), span=True)
    assert conflict.status_code == 409
    assert conflict.data["code"] == "tag_revision_conflict"
    engine.insert(_version=3, attrs_string={"tag.tags": '["later-sdk"]'})
    before = engine.checksum()
    response = invoke(
        make_request(
            span=True,
            body=payload(expected_version="3", expected_tag_revision=1, tags=[]),
        ),
        span=True,
    )
    assert response.status_code == 200, response.data
    assert response.data["result"]["tags"] == [{"name": "sdk"}, {"name": "later-sdk"}]
    saved = EntityTagAssignment.objects.using("default").get()
    assert saved.revision == 2 and saved.manual_tags == []
    assert engine.checksum() == before


@pytest.mark.parametrize(
    "change",
    [
        {"project_id": OTHER_PROJECT},
        {"trace_id": "other-trace"},
        {"span_id": "other-span"},
        {"service_name": "other-service"},
        {"observation_type": "other-type"},
        {
            "start_hour": "2020-01-01T01:00:00+00:00",
            "expected_start_time": "2020-01-01T01:30:00.000001+00:00",
        },
    ],
)
def test_complete_physical_identity_keeps_child_assignments_separate(engine, change):
    engine.insert(_version=2, parent_span_id="parent")
    assert invoke(make_request(span=True), span=True).status_code == 200
    row_change = dict(change)
    if "span_id" in row_change:
        row_change["id"] = row_change.pop("span_id")
    if "start_hour" in row_change:
        row_change.pop("start_hour")
        row_change["start_time"] = (
            row_change.pop("expected_start_time")
            .replace("T", " ")
            .replace("+00:00", "")
        )
    engine.insert(_version=2, parent_span_id="parent", **row_change)
    response = invoke(
        make_request(span=True, body=payload(tags=["separate"], **change)), span=True
    )
    assert response.status_code == 200, response.data
    assert EntityTagAssignment.objects.using("default").count() == 2
    assert sorted(
        row.manual_tags[0]["name"]
        for row in EntityTagAssignment.objects.using("default").all()
    ) == ["separate", "ui"]


@pytest.mark.parametrize(
    "state,status",
    [("absent", 404), ("deleted", 404), ("changed", 409), ("unavailable", 503)],
)
def test_incomplete_or_missing_target_never_creates_assignment(
    engine, monkeypatch, state, status
):
    if state == "deleted":
        engine.insert(_version=2, is_deleted=1)
    if state == "changed":
        engine.insert(_version=3)
    if state == "unavailable":
        monkeypatch.setattr(
            engine,
            "execute_ch_query",
            Mock(side_effect=RuntimeError("private-diagnostic")),
        )
    response = invoke(make_request())
    assert response.status_code == status
    assert not EntityTagAssignment.objects.using("default").exists()


def test_pg_constraint_failure_is_unavailable_not_conflict(engine):
    engine.insert(_version=2)
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE tracer_entity_tag_assignment ADD CONSTRAINT fail_test_write CHECK (false)"
        )
    try:
        response = invoke(make_request())
        assert response.status_code == 503, response.data
        assert response.data["code"] == "tag_assignment_unavailable"
        assert not EntityTagAssignment.objects.using("default").exists()
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE tracer_entity_tag_assignment DROP CONSTRAINT fail_test_write"
            )


def test_migration_enforces_shape_and_nonzero_revision(engine):
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(
            cursor, EntityTagAssignment._meta.db_table
        )
    assert constraints["unique_entity_tag_assignment"]["unique"]
    assert constraints["entity_tag_assignment_shape"]["check"]
    assert constraints["entity_tag_revision_positive"]["check"]
    assert set(constraints["unique_entity_tag_assignment"]["columns"]) == {
        "project_id",
        "entity_kind",
        "trace_id",
        "span_id",
        "start_hour",
        "observation_type",
        "service_name",
    }


def test_two_simultaneous_inserts_and_updates_have_one_winner(
    engine, assignment_schema
):
    engine.insert(_version=2)
    target = resolve_tag_target(
        analytics=engine,
        authorized_project_id=PROJECT,
        reference=reference(),
        trace_id=TRACE,
        deadline=ReadDeadline.start(3000, enforce_on_server=True),
    )

    def race(expected):
        barrier = Barrier(2)

        def save(name):
            db = connections["default"]
            try:
                with db.cursor() as cursor:
                    cursor.execute(
                        "SET search_path TO " + db.ops.quote_name(assignment_schema)
                    )
                barrier.wait(timeout=10)
                return assignments._save_assignment(
                    target,
                    {"tags": [{"name": name}], "expected_tag_revision": expected},
                    ReadDeadline.start(3000),
                )
            except assignments.TagAssignmentError as exc:
                return exc.code
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(save, ["first", "second"]))
        assert sorted(map(str, results)) == [str(expected + 1), "tag_revision_conflict"]
        assert EntityTagAssignment.objects.using("default").count() == 1
        saved = EntityTagAssignment.objects.using("default").get()
        assert saved.revision == expected + 1
        assert saved.manual_tags in ([{"name": "first"}], [{"name": "second"}])

    race(0)
    race(1)


def test_primary_timeout_is_restored_and_pg_wait_is_bounded(engine):
    engine.insert(_version=2)
    with connection.cursor() as cursor:
        cursor.execute("SET statement_timeout = '11s'")
    try:
        assert invoke(make_request()).status_code == 200
        with connection.cursor() as cursor:
            cursor.execute("SHOW statement_timeout")
            assert cursor.fetchone()[0] == "11s"
        with pytest.raises(DatabaseError):
            with assignments._postgres_budget(ReadDeadline.start(40)):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_sleep(0.3)")
        with connection.cursor() as cursor:
            cursor.execute("SHOW statement_timeout")
            assert cursor.fetchone()[0] == "11s"
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SET statement_timeout = 0")
