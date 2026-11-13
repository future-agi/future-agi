"""Reconcile PG editor tags against real PG and an isolated CH25 sidecar DB."""

import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import clickhouse_connect
import pytest
from django.core.management import call_command
from django.db import connection

from tracer.management.commands import reconcile_legacy_trace_tags as reconcile
from tracer.services.clickhouse.v2 import get_v2_config
from tracer.services.entity_tag_reads import attach_legacy_trace_tags
from tracer.tests.test_entity_tag_assignment_postgres import assignment_schema  # noqa: F401
from tracer.tests.test_entity_tag_target import PROJECT
from tracer.tests.test_entity_tag_targets_ch25 import OTHER_PROJECT


def test_upgrade_docs_run_second_reconcile_immediately_after_rollout():
    guide = (Path(__file__).resolve().parents[3] / "deploy/README.md").read_text()
    upgrade = guide.split("### Upgrading a production install", 1)[1].split("### ", 1)[
        0
    ]
    steps = (
        "reconcile_legacy_trace_tags --dry-run",
        "reconcile_legacy_trace_tags\n",
        "$COMPOSE up -d",
        "reconcile_legacy_trace_tags\n",
        "If it reports rows changed during the scan",
    )
    position = 0
    for step in steps:
        position = upgrade.index(step, position) + len(step)
    assert "run the second apply immediately after" in upgrade
    assert "there is no mechanism to gate the new editor" in upgrade
    assert "collector's root-span writes" in upgrade
    assert "tags DEFAULT '[]'" in upgrade


ORG = "55555555-5555-5555-5555-555555555555"
OTHER_ORG = "66666666-6666-6666-6666-666666666666"


@pytest.fixture
def stores(request, django_db_blocker, monkeypatch):
    # The assignment migration fixture gives us a disposable PG schema with
    # projects, without installing the production telemetry schema.
    request.getfixturevalue("assignment_schema")
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute("ALTER TABLE tracer_project ADD COLUMN organization_id uuid")
        cursor.execute(
            "UPDATE tracer_project SET organization_id = CASE WHEN id = %s "
            "THEN %s::uuid ELSE %s::uuid END",
            [PROJECT, ORG, OTHER_ORG],
        )
        cursor.execute("""CREATE TABLE tracer_trace (
            id uuid PRIMARY KEY, project_id uuid NOT NULL, project_version_id uuid,
            name varchar(2000), session_id uuid, external_id varchar(255),
            tags jsonb, metadata jsonb, input jsonb, output jsonb, error jsonb,
            error_analysis_status varchar(20), created_at timestamptz,
            updated_at timestamptz, deleted boolean, deleted_at timestamptz
        )""")

    cfg = get_v2_config()
    admin = clickhouse_connect.get_client(
        host=cfg["host"],
        port=cfg["http_port"],
        username=cfg["user"],
        password=cfg["password"],
    )
    database = "test_tag_reconcile_" + uuid4().hex
    admin.command(f"CREATE DATABASE {database}")
    ch = clickhouse_connect.get_client(
        host=cfg["host"],
        port=cfg["http_port"],
        username=cfg["user"],
        password=cfg["password"],
        database=database,
    )
    ch.command("""CREATE TABLE traces (
        id UUID, project_id UUID, project_version_id Nullable(UUID),
        name Nullable(String), session_id Nullable(UUID), external_id Nullable(String),
        tags String, metadata String, input String, output String, error String,
        error_analysis_status String, created_at DateTime64(6, 'UTC'),
        updated_at DateTime64(6, 'UTC'), is_deleted UInt8, _version UInt64
    ) ENGINE = ReplacingMergeTree(_version, is_deleted)
    ORDER BY (project_id, id)""")
    monkeypatch.setattr(
        "tracer.management.commands.reconcile_legacy_trace_tags.get_v2_config",
        lambda: {**cfg, "database": database},
    )

    def seed(
        project, pg_tags, ch_tags=None, *, ch_version=1, ch_name=None, ch_deleted=0
    ):
        trace = str(uuid4())
        with django_db_blocker.unblock(), connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO tracer_trace
                (id, project_id, tags, created_at, updated_at, deleted,
                 error_analysis_status)
                VALUES (%s, %s, %s::jsonb, now(), now(), false, 'pending')""",
                [trace, project, json.dumps(pg_tags)],
            )
        if ch_tags is not None:
            ch.insert(
                "traces",
                [
                    [
                        trace,
                        project,
                        json.dumps(ch_tags),
                        ch_version,
                        ch_name,
                        ch_deleted,
                    ]
                ],
                column_names=[
                    "id",
                    "project_id",
                    "tags",
                    "_version",
                    "name",
                    "is_deleted",
                ],
            )
        return trace

    def latest(trace):
        rows = ch.query(
            "SELECT tags FROM traces FINAL WHERE id = %(id)s", {"id": trace}
        ).result_rows
        return json.loads(rows[0][0]) if rows else None

    def version(trace):
        return ch.query(
            "SELECT max(_version) FROM traces WHERE id = %(id)s", {"id": trace}
        ).result_rows[0][0]

    def legacy_tags(trace):
        row = {"project_id": PROJECT, "trace_id": trace, "parent_span_id": ""}

        def execute(sql, params, **kwargs):
            return SimpleNamespace(
                data=list(ch.query(sql, parameters=params).named_results())
            )

        attach_legacy_trace_tags(execute, [row])
        return json.loads(row["trace_tags"])

    def column(trace, name):
        return ch.query(
            f"SELECT {name} FROM traces FINAL WHERE id = %(id)s", {"id": trace}
        ).result_rows[0][0]

    latest.column = column
    try:
        yield seed, latest, version, legacy_tags
    finally:
        ch.close()
        admin.command(f"DROP DATABASE {database}")
        admin.close()
        with django_db_blocker.unblock(), connection.cursor() as cursor:
            cursor.execute("DROP TABLE tracer_trace")
            cursor.execute("ALTER TABLE tracer_project DROP COLUMN organization_id")


def test_dry_run_reconcile_and_second_run_do_not_duplicate(stores):
    seed, latest, version, legacy_tags = stores
    stale = seed(PROJECT, [{"name": "old", "color": "#123456"}], ["stale"])
    equal = seed(PROJECT, ["equal"], ["equal"])
    missing = seed(PROJECT, ["missing"])
    cleared = seed(PROJECT, [], ["old"])
    other = seed(OTHER_PROJECT, ["other"], ["stale"])
    before = {
        trace: version(trace) for trace in (stale, equal, missing, cleared, other)
    }
    output = StringIO()
    call_command(
        "reconcile_legacy_trace_tags",
        "--project-id",
        PROJECT,
        "--batch-size",
        "2",
        "--dry-run",
        stdout=output,
    )
    assert "would mirror 2 trace(s)" in output.getvalue()
    assert {trace: version(trace) for trace in before} == before
    assert latest(stale) == ["stale"] and latest(missing) is None
    assert legacy_tags(stale) == ["stale"]

    output = StringIO()
    call_command(
        "reconcile_legacy_trace_tags",
        "--project-id",
        PROJECT,
        "--batch-size",
        "2",
        stdout=output,
    )
    # A trace with no CH row is a mirror gap, not a tag difference: skipped.
    assert "mirrored 2 trace(s)" in output.getvalue()
    assert "no CH trace row 1" in output.getvalue()
    assert latest(cleared) == []
    assert latest(stale) == [{"name": "old", "color": "#123456"}]
    assert legacy_tags(stale) == [{"name": "old", "color": "#123456"}]
    assert latest(missing) is None
    assert latest(equal) == ["equal"] and latest(other) == ["stale"]
    assert version(equal) == before[equal] and version(other) == before[other]
    assert version(stale) == before[stale] + 1
    assert version(cleared) == before[cleared] + 1
    after = {trace: version(trace) for trace in before}

    output = StringIO()
    call_command("reconcile_legacy_trace_tags", "--project-id", PROJECT, stdout=output)
    assert "mirrored 0 trace(s)" in output.getvalue()
    assert {trace: version(trace) for trace in before} == after


@pytest.mark.parametrize("dry_run", [False, True])
def test_final_summary_reports_zero_changed_during_scan(stores, dry_run):
    seed, _, _, _ = stores
    seed(PROJECT, ["pg"], ["ch"])
    output = StringIO()
    call_command(
        "reconcile_legacy_trace_tags",
        *(["--dry-run"] if dry_run else []),
        stdout=output,
    )
    lines = output.getvalue().splitlines()
    assert "changed during scan 0" in lines[-1]
    assert ("would mirror 1 trace(s)" if dry_run else "mirrored 1 trace(s)") in lines[
        -1
    ]
    assert "rerun" not in output.getvalue().lower()


def test_organization_scope_and_newest_ch_tags_replaced(stores):
    seed, latest, version, _ = stores
    trace = seed(PROJECT, ["pg"], ["ch-only"], ch_version=2**63)
    outside = seed(OTHER_PROJECT, ["outside"], ["ch-only"])
    original = (version(trace), version(outside))
    call_command(
        "reconcile_legacy_trace_tags", "--organization-id", OTHER_ORG, stdout=StringIO()
    )
    assert latest(trace) == ["ch-only"]
    assert latest(outside) == ["outside"]
    assert version(trace) == original[0] and version(outside) > original[1]
    call_command(
        "reconcile_legacy_trace_tags",
        "--organization-id",
        ORG,
        "--project-id",
        PROJECT,
        stdout=StringIO(),
    )
    assert latest(trace) == ["pg"]
    assert version(trace) > original[0]


def test_dual_write_between_ch_candidate_and_pg_tag_reads_is_not_reverted(
    stores, monkeypatch
):
    seed, latest, version, _ = stores
    trace = seed(PROJECT, ["old-pg"], ["stale-ch"])
    cfg = reconcile.get_v2_config()
    ch = clickhouse_connect.get_client(
        host=cfg["host"],
        port=cfg["http_port"],
        username=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
    )
    original_get_client = clickhouse_connect.get_client
    raced = False

    class RacingClient:
        def __init__(self, client):
            self.client = client

        def query(self, sql, *args, **kwargs):
            nonlocal raced
            result = self.client.query(sql, *args, **kwargs)
            if not raced:
                raced = True
                # Both halves of a new editor write land after the CH read.
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE tracer_trace SET tags = %s::jsonb WHERE id = %s",
                        [json.dumps(["fresh"]), trace],
                    )
                ch.insert(
                    "traces",
                    [[trace, PROJECT, json.dumps(["fresh"]), 2]],
                    column_names=["id", "project_id", "tags", "_version"],
                )
            return result

        def __getattr__(self, name):
            return getattr(self.client, name)

    monkeypatch.setattr(
        clickhouse_connect,
        "get_client",
        lambda **kwargs: RacingClient(original_get_client(**kwargs)),
    )
    try:
        call_command("reconcile_legacy_trace_tags", stdout=StringIO())
        assert raced
        assert latest(trace) == ["fresh"]
        assert version(trace) == 2
    finally:
        ch.close()


@pytest.mark.parametrize("dry_run", [False, True])
def test_dual_write_between_pg_tags_and_full_ch_read_is_skipped(
    stores, monkeypatch, dry_run
):
    seed, latest, version, _ = stores
    trace = seed(PROJECT, ["old-pg"], ["stale-ch"])
    cfg = reconcile.get_v2_config()
    ch = clickhouse_connect.get_client(
        host=cfg["host"],
        port=cfg["http_port"],
        username=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
    )
    original_get_client = clickhouse_connect.get_client
    raced = False

    class RacingClient:
        def __init__(self, client):
            self.client = client

        def query(self, sql, *args, **kwargs):
            nonlocal raced
            if "SELECT * FROM traces" in sql and not raced:
                raced = True
                # The PG half of the dual write commits after the PG snapshot;
                # the CH half lands before the full row is read.
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE tracer_trace SET tags = %s::jsonb WHERE id = %s",
                        [json.dumps(["fresh"]), trace],
                    )
                ch.insert(
                    "traces",
                    [[trace, PROJECT, json.dumps(["fresh"]), 2]],
                    column_names=["id", "project_id", "tags", "_version"],
                )
            return self.client.query(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.client, name)

    monkeypatch.setattr(
        clickhouse_connect,
        "get_client",
        lambda **kwargs: RacingClient(original_get_client(**kwargs)),
    )
    try:
        output = StringIO()
        call_command(
            "reconcile_legacy_trace_tags",
            *(["--dry-run"] if dry_run else []),
            stdout=output,
        )
        assert raced
        assert latest(trace) == ["fresh"]
        assert version(trace) == 2
        lines = output.getvalue().splitlines()
        assert "changed during scan 1" in lines[-2]
        assert "rerun" in lines[-1].lower()
        assert (
            "would mirror 0 trace(s)" if dry_run else "mirrored 0 trace(s)"
        ) in lines[-2]
    finally:
        ch.close()


def test_reconcile_fetches_full_ch_rows_only_for_differences(stores, monkeypatch):
    seed, latest, _, _ = stores
    different = seed(PROJECT, ["pg"], ["ch"])
    seed(PROJECT, ["equal"], ["equal"])
    seed(PROJECT, ["missing"])
    seed(PROJECT, ["deleted"], ["stale"], ch_deleted=1)
    issued = []
    original_get_client = clickhouse_connect.get_client

    class RecordingClient:
        def __init__(self, client):
            self.client = client

        def query(self, sql, *args, **kwargs):
            issued.append((sql, kwargs.get("parameters")))
            return self.client.query(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.client, name)

    monkeypatch.setattr(
        clickhouse_connect,
        "get_client",
        lambda **kwargs: RecordingClient(original_get_client(**kwargs)),
    )
    call_command("reconcile_legacy_trace_tags", stdout=StringIO())
    assert "argMax(tuple(tags, is_deleted), _version)" in issued[0][0]
    full_reads = [(sql, params) for sql, params in issued if "SELECT *" in sql]
    assert len(full_reads) == 1
    assert full_reads[0][1]["ids"] == (different,)
    assert latest(different) == ["pg"]


def test_resume_after_id_skips_earlier_traces(stores):
    seed, latest, _, _ = stores
    traces = sorted(seed(PROJECT, ["pg"], ["ch"]) for _ in range(3))
    call_command(
        "reconcile_legacy_trace_tags",
        "--after-id",
        traces[1],
        stdout=StringIO(),
    )
    assert [latest(trace) for trace in traces] == [["ch"], ["ch"], ["pg"]]


def test_reconcile_reads_pg_page_and_tags_from_primary(stores, monkeypatch):
    from tfc.routers import ReadReplicaRouter, _is_force_primary

    seed, _, _, _ = stores
    seed(PROJECT, ["pg"], ["ch"])
    primary_reads = []
    original_db_for_read = ReadReplicaRouter.db_for_read

    def recording_db_for_read(self, model, **hints):
        if model.__name__ == "Trace":
            primary_reads.append(_is_force_primary())
        return original_db_for_read(self, model, **hints)

    monkeypatch.setattr(ReadReplicaRouter, "db_for_read", recording_db_for_read)
    call_command("reconcile_legacy_trace_tags", stdout=StringIO())
    assert len(primary_reads) >= 2
    assert all(primary_reads)


def test_committed_batches_print_resume_cursor(stores):
    seed, latest, _, _ = stores
    traces = sorted(seed(PROJECT, ["pg"], ["ch"]) for _ in range(3))
    output = StringIO()
    call_command("reconcile_legacy_trace_tags", "--batch-size", "1", stdout=output)
    cursors = [
        line.split("resume with --after-id ", 1)[1]
        for line in output.getvalue().splitlines()
        if "resume with --after-id " in line
    ]
    assert cursors == traces
    assert [latest(trace) for trace in traces] == [["pg"]] * 3


def test_only_tags_change_and_deleted_traces_stay_deleted(stores):
    """The latest CH row keeps every other column; a deleted row is skipped."""
    seed, latest, version, _ = stores
    renamed = seed(PROJECT, ["pg"], ["ch"], ch_name="name written to CH only")
    deleted = seed(PROJECT, ["pg"], ["ch"], ch_deleted=1)
    before = version(deleted)
    call_command(
        "reconcile_legacy_trace_tags", "--project-id", PROJECT, stdout=StringIO()
    )
    assert latest(renamed) == ["pg"]
    assert latest.column(renamed, "name") == "name written to CH only"
    assert version(deleted) == before


def test_operators_may_run_the_reconcile_under_mutation_free_startup(monkeypatch):
    """The deploy step runs it with the operator pair (deploy/README.md); the
    startup guard must let exactly that through, and refuse it otherwise."""
    from model_hub.apps import (
        explicit_management_mutation_authorized,
        guarded_management_command,
    )

    argv = ["manage.py", "reconcile_legacy_trace_tags", "--dry-run"]
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", "true")
    monkeypatch.delenv("SERVICE_TYPE", raising=False)
    monkeypatch.delenv("STARTUP_DB_MUTATION_MODE", raising=False)
    assert guarded_management_command(argv) == "reconcile_legacy_trace_tags"
    assert explicit_management_mutation_authorized(argv) is False

    monkeypatch.setenv("SERVICE_TYPE", "bootstrap")
    monkeypatch.setenv("STARTUP_DB_MUTATION_MODE", "operator")
    assert explicit_management_mutation_authorized(argv) is True
    guide = (Path(__file__).resolve().parents[3] / "deploy/README.md").read_text()
    assert (
        "-e SERVICE_TYPE=bootstrap -e STARTUP_DB_MUTATION_MODE=operator \\\n"
        "  --entrypoint python backend manage.py reconcile_legacy_trace_tags"
    ) in guide
