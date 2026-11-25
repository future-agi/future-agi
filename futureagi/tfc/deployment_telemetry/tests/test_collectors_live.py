"""Heartbeat usage counts read from real ClickHouse and Postgres rows.

Every other telemetry test patches ``collect_counts``. These run it on rows in
an isolated past UTC hour, so a self-hosted heartbeat is known to carry the
trace, span and eval counts of its window: exact numbers when there was
activity, null only when a collector failed, never a silent 0.

The ClickHouse cases write to an owned test database with the repo's v2
schema on the test sidecar (conftest's guarded helpers), so the deployment-wide
counts are exact. They run wherever the sidecar is named, as in backend-ci
(``FI_CH_TEST_SIDECAR_NATIVE_PORT`` / ``FI_CH_TEST_SIDECAR_HTTP_PORT``), and
skip only when no test ClickHouse is named. The Postgres and failure cases
always run.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from structlog.testing import capture_logs

from tfc.deployment_telemetry import ch_counts
from tfc.deployment_telemetry.collectors import collect_counts
from tfc.deployment_telemetry.payloads import build_heartbeat_payload

TRACE_A = "a7420000-0000-4000-8000-00000000000a"
TRACE_B = "b7420000-0000-4000-8000-00000000000b"
TRACE_OUTSIDE = "c7420000-0000-4000-8000-00000000000c"
# Its only span's newest version is a tombstone, in a project of its own.
TRACE_GONE = "d7420000-0000-4000-8000-00000000000d"

EVAL_LOGGERS = 2
MODEL_HUB_EVALUATIONS = 3
DATASET_EVAL_RUNS = 4


def _isolated_window() -> tuple[datetime, datetime]:
    """A whole past UTC hour 30-300 days back, different on each run."""
    hours_back = 30 * 24 + uuid.uuid4().int % (270 * 24)
    start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start -= timedelta(hours=hours_back)
    return start, start + timedelta(hours=1)


def _ch_text(moment: datetime) -> str:
    """A DateTime64(6, 'UTC') literal, independent of the client's timezone."""
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


@pytest.fixture
def fresh_ch_client():
    """The counts client is a module singleton; never let one leak across tests."""
    ch_counts._ch25_client = None
    yield
    ch_counts._ch25_client = None


@pytest.fixture(scope="module")
def ch_spans_database():
    """An owned test database with the repo's v2 schema, dropped after the module."""
    from conftest import _ch_test_apply_v2_schema, _ch_test_owned_database

    with _ch_test_owned_database("test_th7420_") as database:
        _ch_test_apply_v2_schema(database)
        yield database


@pytest.fixture
def scratch_spans(ch_spans_database, monkeypatch, fresh_ch_client):
    """Empty ``spans`` in the owned database, which the telemetry counts read
    through ``get_v2_config`` for the test. Yields an insert function."""
    from conftest import (
        _ch_test_http_port,
        _ch_test_native_port,
        _open_ch_test_http_client,
    )
    from tracer.services.clickhouse import v2 as v2_module

    database = ch_spans_database
    ch = _open_ch_test_http_client(database=database)
    try:
        ch.command("TRUNCATE TABLE spans")
        # Keep every inserted version and tombstone in its own part, as an
        # unmerged table is in production, so only FINAL collapses them.
        ch.command("SYSTEM STOP MERGES spans")
        monkeypatch.setattr(
            v2_module,
            "get_v2_config",
            lambda: {
                "host": os.environ.get("CH25_HOST", "127.0.0.1"),
                "tcp_port": _ch_test_native_port().port,
                "http_port": _ch_test_http_port().port,
                "user": os.environ.get("CH25_USER")
                or os.environ.get("CH_USERNAME")
                or "default",
                "password": os.environ.get("CH25_PASSWORD")
                or os.environ.get("CH_PASSWORD")
                or "",
                "database": database,
                "server_enforced_readonly": False,
            },
        )
        project_id = uuid.uuid4()

        def insert(*, trace_id, span_id, start, version, deleted=0, project=None):
            ch.insert(
                "spans",
                [
                    [
                        str(project or project_id),
                        "llm",
                        "svc",
                        _ch_text(start),
                        trace_id,
                        span_id,
                        "",
                        "heartbeat-count",
                        deleted,
                        version,
                    ]
                ],
                column_names=[
                    "project_id",
                    "observation_type",
                    "service_name",
                    "start_time",
                    "trace_id",
                    "id",
                    "parent_span_id",
                    "name",
                    "is_deleted",
                    "_version",
                ],
            )

        insert.project_id = project_id
        yield insert
    finally:
        ch.close()


def _seed_spans(insert, window_start, window_end):
    """2 traces with 3 live spans in the window, plus rows that must not count."""
    t = window_start + timedelta(minutes=10)
    insert(trace_id=TRACE_A, span_id="a-root", start=t, version=10)
    # Exactly at the window start: counted.
    insert(trace_id=TRACE_A, span_id="a-child", start=window_start, version=10)
    # Written twice: one span after FINAL.
    insert(trace_id=TRACE_B, span_id="b-root", start=t, version=10)
    insert(trace_id=TRACE_B, span_id="b-root", start=t, version=20)
    # Its newest version is a tombstone: not counted.
    insert(trace_id=TRACE_B, span_id="b-gone", start=t, version=10)
    insert(trace_id=TRACE_B, span_id="b-gone", start=t, version=20, deleted=1)
    # A trace whose only span's newest version is a tombstone: neither the
    # trace nor its project counts.
    gone_project = uuid.uuid4()
    insert(
        trace_id=TRACE_GONE, span_id="d-only", start=t, version=10, project=gone_project
    )
    insert(
        trace_id=TRACE_GONE,
        span_id="d-only",
        start=t,
        version=20,
        deleted=1,
        project=gone_project,
    )
    # Outside the half-open window [start, end).
    insert(trace_id=TRACE_OUTSIDE, span_id="at-end", start=window_end, version=10)
    insert(
        trace_id=TRACE_OUTSIDE,
        span_id="before-start",
        start=window_start - timedelta(microseconds=1),
        version=10,
    )


def _seed_evaluations(organization, workspace, user, window_start, window_end):
    """N EvalLoggers, M model_hub Evaluations and K evaluation Cells in the
    window, plus one of each outside it and a non-evaluation cell inside it."""
    from model_hub.models.ai_model import AIModel
    from model_hub.models.choices import DataTypeChoices, OwnerChoices, SourceChoices
    from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
    from model_hub.models.evals_metric import EvalTemplate
    from model_hub.models.evaluation import Evaluation
    from tracer.models.observation_span import EvalLogger, EvalTargetType
    from tracer.models.project import Project
    from tracer.models.trace_session import TraceSession

    inside = window_start + timedelta(minutes=20)
    outside = window_start - timedelta(hours=2)

    def place(queryset, moment, *fields):
        # auto_now_add / auto_now ignore constructor values.
        queryset.update(**dict.fromkeys(fields, moment))

    project = Project.no_workspace_objects.create(
        name=f"th7420-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        trace_type="observe",
        model_type=AIModel.ModelTypes.NUMERIC,
    )
    session = TraceSession.no_workspace_objects.create(project=project)
    loggers = [
        EvalLogger.no_workspace_objects.create(
            target_type=EvalTargetType.SESSION, trace_session=session
        )
        for _ in range(EVAL_LOGGERS + 2)
    ]
    # One inside, one just before the window end (counted), one outside and
    # one exactly at the window end (not counted: the window is half-open).
    for logger, moment in zip(
        loggers,
        (inside, window_end - timedelta(microseconds=1), outside, window_end),
        strict=True,
    ):
        place(
            EvalLogger.no_workspace_objects.filter(id=logger.id),
            moment,
            "created_at",
            "updated_at",
        )

    template = EvalTemplate.no_workspace_objects.create(
        name=f"th7420-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail", "eval_type_id": "DeterministicEvaluator"},
    )
    evaluations = [
        Evaluation.no_workspace_objects.create(
            user=user,
            organization=organization,
            workspace=workspace,
            eval_template=template,
        )
        for _ in range(MODEL_HUB_EVALUATIONS + 1)
    ]
    place(
        Evaluation.no_workspace_objects.filter(id__in=[e.id for e in evaluations[:-1]]),
        inside,
        "created_at",
        "updated_at",
    )
    place(
        Evaluation.no_workspace_objects.filter(id=evaluations[-1].id),
        outside,
        "created_at",
        "updated_at",
    )

    dataset = Dataset.no_workspace_objects.create(
        name=f"th7420-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
    )
    eval_column = Column.no_workspace_objects.create(
        name="Eval",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION.value,
    )
    other_column = Column.no_workspace_objects.create(
        name="Output",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    eval_cells: list[uuid.UUID] = []
    outside_cells: list[uuid.UUID] = []
    other_cells: list[uuid.UUID] = []
    for order in range(DATASET_EVAL_RUNS + 1):
        row = Row.no_workspace_objects.create(dataset=dataset, order=order)
        cell = Cell.no_workspace_objects.create(
            dataset=dataset, column=eval_column, row=row, value="Passed"
        )
        (eval_cells if order < DATASET_EVAL_RUNS else outside_cells).append(cell.id)
        other_cells.append(
            Cell.no_workspace_objects.create(
                dataset=dataset, column=other_column, row=row, value="text"
            ).id
        )
    place(
        Cell.no_workspace_objects.filter(id__in=eval_cells + other_cells),
        inside,
        "created_at",
        "updated_at",
    )
    place(
        Cell.no_workspace_objects.filter(id__in=outside_cells),
        outside,
        "created_at",
        "updated_at",
    )


def _assert_eval_counts(counts):
    assert counts["eval_logger_count"] == EVAL_LOGGERS
    assert counts["model_hub_evaluations_count"] == MODEL_HUB_EVALUATIONS
    assert counts["dataset_eval_runs_count"] == DATASET_EVAL_RUNS
    assert counts["total_evaluations_count"] == (
        EVAL_LOGGERS + MODEL_HUB_EVALUATIONS + DATASET_EVAL_RUNS
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_heartbeat_counts_activity_in_the_window(
    scratch_spans, organization, workspace, user
):
    window_start, window_end = _isolated_window()
    _seed_spans(scratch_spans, window_start, window_end)
    _seed_evaluations(organization, workspace, user, window_start, window_end)

    with capture_logs() as logs:
        counts = collect_counts(window_start, window_end)

    assert counts["traces_count"] == 2
    assert counts["spans_count"] == 3
    _assert_eval_counts(counts)
    assert all(value is not None for value in counts.values()), counts
    assert not [
        e for e in logs if e["event"] == "deployment_telemetry_collector_failed"
    ]
    assert ch_counts.ingesting_project_ids(window_start, window_end) == [
        str(scratch_spans.project_id)
    ]

    # The heartbeat carries them as collected.
    payload: dict[str, object] = dict(
        build_heartbeat_payload(
            instance_id=uuid.uuid4(),
            window_start=window_start,
            window_end=window_end,
            counts=counts,
        )
    )
    assert payload["traces_count"] == 2
    assert payload["spans_count"] == 3
    assert payload["total_evaluations_count"] == 9


@pytest.mark.integration
@pytest.mark.django_db
def test_heartbeat_counts_do_not_depend_on_the_worker_process_timezone(
    scratch_spans, organization, workspace, user
):
    """The window is UTC whatever TZ the Python worker runs in.

    This covers the worker only: clickhouse-driver ignores the process TZ.
    The ClickHouse server's own timezone is the next test.
    """
    window_start, window_end = _isolated_window()
    _seed_spans(scratch_spans, window_start, window_end)

    original_tz = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Kolkata"
    time.tzset()
    try:
        counts = collect_counts(window_start, window_end)
    finally:
        if original_tz is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = original_tz
        time.tzset()

    assert counts["traces_count"] == 2
    assert counts["spans_count"] == 3


@pytest.mark.integration
@pytest.mark.django_db
def test_heartbeat_counts_on_a_clickhouse_server_not_in_utc(
    scratch_spans, organization, workspace, user, monkeypatch
):
    """A ClickHouse server whose timezone is not UTC still counts the UTC window.

    clickhouse-driver renders an aware datetime parameter in the timezone the
    server reported at connect (``ServerInfo.get_timezone``), so this server
    reports Asia/Kolkata. The counts must be exact, not those of a window
    5h30 later.
    """
    from clickhouse_driver.connection import ServerInfo

    window_start, window_end = _isolated_window()
    _seed_spans(scratch_spans, window_start, window_end)
    monkeypatch.setattr(ServerInfo, "get_timezone", lambda _self: "Asia/Kolkata")

    assert ch_counts.count_traces(window_start, window_end) == 2
    assert ch_counts.count_spans(window_start, window_end) == 3
    assert ch_counts.ingesting_project_ids(window_start, window_end) == [
        str(scratch_spans.project_id)
    ]
    # The heartbeat path too: exact counts, no collector failure.
    counts = collect_counts(window_start, window_end)
    assert (counts["traces_count"], counts["spans_count"]) == (2, 3)


@pytest.mark.parametrize(
    "bounds",
    [
        (datetime(2026, 1, 1, 12), datetime(2026, 1, 1, 13, tzinfo=UTC)),
        (datetime(2026, 1, 1, 12, tzinfo=UTC), datetime(2026, 1, 1, 13)),
    ],
)
def test_count_window_must_be_timezone_aware(bounds, fresh_ch_client, monkeypatch):
    """A naive bound is refused before any read, never read as local time."""

    class _Unreachable:
        def execute_read(self, *args, **kwargs):
            raise AssertionError("no read for a naive window")

    monkeypatch.setattr(ch_counts, "_ch25_client", _Unreachable())
    for count in (
        ch_counts.count_spans,
        ch_counts.count_traces,
        ch_counts.ingesting_project_ids,
    ):
        with pytest.raises(ValueError, match="timezone-aware"):
            count(*bounds)


def test_counts_client_takes_the_v2_server_enforced_readonly(
    fresh_ch_client, settings, monkeypatch
):
    """The counts client follows CH25's readonly setting, not the legacy one."""
    from tracer.services.clickhouse import v2 as v2_module

    settings.CLICKHOUSE = {
        **(getattr(settings, "CLICKHOUSE", {}) or {}),
        "CH_SERVER_ENFORCED_READONLY": False,
    }
    monkeypatch.setattr(
        v2_module,
        "get_v2_config",
        lambda: {
            "host": "127.0.0.1",
            "tcp_port": 9,
            "http_port": 9,
            "user": "default",
            "password": "",
            "database": "test_th7420_readonly",
            "server_enforced_readonly": True,
        },
    )
    assert ch_counts._get_ch25_client().server_enforced_readonly is True


@pytest.mark.integration
@pytest.mark.django_db
def test_heartbeat_cycle_sends_the_window_counts(
    scratch_spans, organization, workspace, user, monkeypatch, tmp_path
):
    """The scheduled cycle, end to end: it computes the previous closed UTC
    window, collects it, buffers and sends a heartbeat carrying its counts."""
    from django.utils import timezone

    from tfc.deployment_telemetry import sender
    from tfc.deployment_telemetry.models import DeploymentTelemetryState
    from tfc.deployment_telemetry.state import get_or_create_telemetry_state
    from tfc.deployment_telemetry.transport import TelemetryResponse

    window_start, window_end = _isolated_window()
    _seed_spans(scratch_spans, window_start, window_end)
    _seed_evaluations(organization, workspace, user, window_start, window_end)

    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "false")
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_BUFFER_DIR", str(tmp_path / "buffer"))
    state = get_or_create_telemetry_state()
    state.registered_at = timezone.now()
    state.registration_kind = DeploymentTelemetryState.RegistrationKind.FULL
    state.instance_secret = "test-secret"
    state.save()

    real_window = sender.compute_previous_utc_window
    # The real window function, run half an hour after the seeded hour closed.
    monkeypatch.setattr(
        sender,
        "compute_previous_utc_window",
        lambda: real_window(window_end + timedelta(minutes=30), 1),
    )
    monkeypatch.setattr(sender, "is_self_hosted_deployment", lambda: True)
    posted: list[tuple[str, dict]] = []

    def post(_client, path, payload):
        posted.append((path, payload))
        return TelemetryResponse(ok=True, data={"instance_secret": "test-secret"})

    monkeypatch.setattr(sender.TelemetryClient, "post", post)

    result = sender.run_telemetry_cycle()

    assert result["sent"] is True, result
    heartbeats = [
        payload for path, payload in posted if path == "/telemetry/heartbeat/"
    ]
    assert len(heartbeats) == 1
    heartbeat = heartbeats[0]
    assert heartbeat["window_start"] == window_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert heartbeat["window_end"] == window_end.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert heartbeat["traces_count"] == 2
    assert heartbeat["spans_count"] == 3
    assert heartbeat["eval_logger_count"] == EVAL_LOGGERS
    assert heartbeat["model_hub_evaluations_count"] == MODEL_HUB_EVALUATIONS
    assert heartbeat["dataset_eval_runs_count"] == DATASET_EVAL_RUNS
    assert heartbeat["total_evaluations_count"] == (
        EVAL_LOGGERS + MODEL_HUB_EVALUATIONS + DATASET_EVAL_RUNS
    )


@pytest.mark.django_db
def test_heartbeat_eval_counts_from_postgres(
    organization, workspace, user, fresh_ch_client, monkeypatch
):
    """The Postgres eval counts of the window, exact, with ClickHouse stubbed."""
    window_start, window_end = _isolated_window()
    _seed_evaluations(organization, workspace, user, window_start, window_end)
    monkeypatch.setattr(
        "tfc.deployment_telemetry.collectors.count_traces", lambda *_: 0
    )
    monkeypatch.setattr("tfc.deployment_telemetry.collectors.count_spans", lambda *_: 0)
    monkeypatch.setattr(
        "tfc.deployment_telemetry.collectors.ingesting_project_ids", lambda *_: []
    )

    counts = collect_counts(window_start, window_end)

    _assert_eval_counts(counts)
    assert all(value is not None for value in counts.values()), counts


@pytest.mark.django_db
def test_heartbeat_reports_null_not_zero_when_clickhouse_fails(
    organization, workspace, user, fresh_ch_client, monkeypatch
):
    window_start, window_end = _isolated_window()
    _seed_evaluations(organization, workspace, user, window_start, window_end)

    class _Down:
        def execute_read(self, *args, **kwargs):
            raise ConnectionError("ClickHouse unreachable")

    monkeypatch.setattr(ch_counts, "_ch25_client", _Down())

    with capture_logs() as logs:
        counts = collect_counts(window_start, window_end)

    assert counts["traces_count"] is None
    assert counts["spans_count"] is None
    # Postgres counts are unaffected.
    _assert_eval_counts(counts)
    failed = {
        e["collector"]
        for e in logs
        if e["event"] == "deployment_telemetry_collector_failed"
        and e["log_level"] == "warning"
    }
    assert {"traces", "spans", "active_users"} <= failed
