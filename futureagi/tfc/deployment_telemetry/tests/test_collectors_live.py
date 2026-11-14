"""Heartbeat usage counts read from real ClickHouse and Postgres rows.

Every other telemetry test patches ``collect_counts``. These run it on rows in
an isolated past UTC hour, so a self-hosted heartbeat is known to carry the
trace, span and eval counts of its window: exact numbers when there was
activity, null only when a collector failed, never a silent 0.

The ClickHouse case writes to a scratch database holding a copy of ``spans``,
so the deployment-wide counts are exact. It is opt-in like the other live
ClickHouse suites: set ``FI_LIVE_CH_TESTS=1`` with a loopback ``CH25_HOST``.
The Postgres and failure cases always run.
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

LIVE_CH_TESTS_ENV_VAR = "FI_LIVE_CH_TESTS"

TRACE_A = "a7420000-0000-4000-8000-00000000000a"
TRACE_B = "b7420000-0000-4000-8000-00000000000b"
TRACE_OUTSIDE = "c7420000-0000-4000-8000-00000000000c"

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


@pytest.fixture
def scratch_spans(settings, fresh_ch_client):
    """A scratch ClickHouse database with a copy of ``spans``, which the
    telemetry counts read through ``get_v2_config`` for the test."""
    if os.environ.get(LIVE_CH_TESTS_ENV_VAR, "").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        pytest.skip(f"live ClickHouse tests are opt-in: set {LIVE_CH_TESTS_ENV_VAR}=1")

    import clickhouse_connect

    from conftest import _require_safe_ch25_test_target
    from tracer.services.clickhouse.v2 import get_v2_config

    config = get_v2_config()
    scratch = f"test_th7420_{uuid.uuid4().hex[:8]}"
    _require_safe_ch25_test_target(host=config["host"], database=config["database"])
    _require_safe_ch25_test_target(host=config["host"], database=scratch)
    ch = clickhouse_connect.get_client(
        host=config["host"],
        port=config["http_port"],
        username=config["user"],
        password=config["password"],
        database=config["database"],
    )
    ch.command(f"CREATE DATABASE {scratch}")
    try:
        ch.command(f"CREATE TABLE {scratch}.spans AS {config['database']}.spans")
        # Keep every inserted version and tombstone in its own part, as an
        # unmerged table is in production, so only FINAL collapses them.
        ch.command(f"SYSTEM STOP MERGES {scratch}.spans")
        settings.CLICKHOUSE_V2 = {
            **(getattr(settings, "CLICKHOUSE_V2", {}) or {}),
            "CH25_DATABASE": scratch,
        }

        def insert(*, trace_id, span_id, start, version, deleted=0):
            ch.insert(
                f"{scratch}.spans",
                [
                    [
                        str(project_id),
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

        project_id = uuid.uuid4()
        yield insert
    finally:
        ch.command(f"DROP DATABASE IF EXISTS {scratch}")
        ch.close()


def _seed_spans(insert, window_start, window_end):
    """2 traces with 3 live spans in the window, plus rows that must not count."""
    t = window_start + timedelta(minutes=10)
    insert(trace_id=TRACE_A, span_id="a-root", start=t, version=10)
    insert(trace_id=TRACE_A, span_id="a-child", start=t, version=10)
    # Written twice: one span after FINAL.
    insert(trace_id=TRACE_B, span_id="b-root", start=t, version=10)
    insert(trace_id=TRACE_B, span_id="b-root", start=t, version=20)
    # Its newest version is a tombstone: not counted.
    insert(trace_id=TRACE_B, span_id="b-gone", start=t, version=10)
    insert(trace_id=TRACE_B, span_id="b-gone", start=t, version=20, deleted=1)
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
        for _ in range(EVAL_LOGGERS + 1)
    ]
    place(
        EvalLogger.no_workspace_objects.filter(id__in=[e.id for e in loggers[:-1]]),
        inside,
        "created_at",
        "updated_at",
    )
    place(
        EvalLogger.no_workspace_objects.filter(id=loggers[-1].id),
        outside,
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
def test_heartbeat_counts_do_not_depend_on_the_process_timezone(
    scratch_spans, organization, workspace, user, monkeypatch
):
    """The window is UTC whatever TZ the worker runs in."""
    window_start, window_end = _isolated_window()
    _seed_spans(scratch_spans, window_start, window_end)

    monkeypatch.setenv("TZ", "Asia/Kolkata")
    time.tzset()
    try:
        counts = collect_counts(window_start, window_end)
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()

    assert counts["traces_count"] == 2
    assert counts["spans_count"] == 3


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
