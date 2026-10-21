"""One guarded PG/CH flow: persisted sampling -> weighted metric -> scoped SQL.

Only PG-to-CH fixture transport is test-local. Selection, entry lifecycle, metric
publication, catalog compilation and the restricted read runner are production.
"""

import ipaddress
import json
import secrets
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import connection
from django.utils import timezone

from conftest import (
    _ch_test_apply_v2_schema,
    _ch_test_http_port,
    _ch_test_native_client,
    _ch_test_owned_database,
)
from tracer.models.derived_metric import DerivedMetricRun
from tracer.models.eval_sampling import EvalSamplingCohort, EvalSamplingMember
from tracer.models.eval_task import EvalTask, RunType
from tracer.models.observation_span import EvalEntryStatus, EvalLogger, ObservationSpan
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.services.clickhouse import client as app_client
from tracer.services.clickhouse.v2.query_service import reset_v2_query_client
from tracer.services.derived_metrics.service import (
    create_run,
    execute_run,
    save_definition,
)
from tracer.services.eval_tasks.config_hash import resolved_config_hash
from tracer.services.eval_tasks.entries import (
    claim_pending_batch,
    mark_terminal,
    persist_eval_result,
    running_entry_epoch,
    writing_onto_entry,
)
from tracer.services.eval_tasks.reconciler import reconcile
from tracer.sql_workbench.access import Actor, authorize
from tracer.sql_workbench.catalog import catalog
from tracer.sql_workbench.compiler import compile_query
from tracer.sql_workbench.provision import service_access_ddl
from tracer.sql_workbench.runner import derived_metrics_availability
from tracer.sql_workbench.runner import run as run_sql
from tracer.sql_workbench.service import query_scope
from tracer.tests._ch_seed import seed_ch_spans

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.integration]

RESULT_COLUMNS = (
    "run_id, result_id, group_key, value, numerator, denominator, status, "
    "unit_count, valid_count, missing_count, invalid_probability_count, sampling_scope"
)


def _reset_clients():
    reset_v2_query_client()
    if app_client._clickhouse_client is not None:
        app_client._clickhouse_client.close()
        app_client._clickhouse_client = None


@pytest.fixture
def acceptance_ch(settings, monkeypatch):
    """A unique guarded database and host-restricted, locked SQL service login."""
    with _ch_test_owned_database("test_sampling_metrics_sql_") as database:
        _ch_test_apply_v2_schema(database)
        with _ch_test_native_client(database=database) as client:
            _reset_clients()
            settings.CLICKHOUSE_V2 = {
                "CH25_HOST": client.connection.host,
                "CH25_TCP_PORT": client.connection.port,
                "CH25_HTTP_PORT": _ch_test_http_port().port,
                "CH25_DATABASE": database,
                "CH25_USER": client.connection.user,
                "CH25_PASSWORD": client.connection.password,
                "CH25_SERVER_ENFORCED_READONLY": False,
            }
            settings.CLICKHOUSE = {
                **settings.CLICKHOUSE,
                "CH_HOST": client.connection.host,
                "CH_PORT": client.connection.port,
                "CH_DATABASE": database,
                "CH_USERNAME": client.connection.user,
                "CH_PASSWORD": client.connection.password,
                "CH_SERVER_ENFORCED_READONLY": False,
            }
            settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_v2"
            principal = f"sql_test_{uuid4().hex[:12]}"
            password = secrets.token_urlsafe(32)
            address = ipaddress.ip_address(
                str(
                    client.execute(
                        "SELECT address FROM system.processes WHERE query_id=currentQueryID()"
                    )[0][0]
                )
            )
            address = getattr(address, "ipv4_mapped", None) or address
            cidr = f"{address}/{32 if address.version == 4 else 128}"
            created = False
            try:
                for index, statement in enumerate(
                    service_access_ddl(
                        database=database,
                        principal=principal,
                        password=password,
                        cidr=cidr,
                        eval_table="tracer_eval_logger_v2",
                        include_derived_metrics=True,
                    )
                ):
                    client.execute(statement)
                    if index == 0:
                        created = True
                monkeypatch.setenv("SQL_WORKBENCH_CH_USER", principal)
                monkeypatch.setenv("SQL_WORKBENCH_CH_PASSWORD", password)
                monkeypatch.setenv("SQL_WORKBENCH_CH_SECURE", "false")
                monkeypatch.setenv("SQL_WORKBENCH_DERIVED_METRICS_ENABLED", "true")
                yield client, database
            finally:
                _reset_clients()
                if created:
                    client.execute(f"DROP USER {principal}")


def _copy_completed_entries(client, entries):
    """Transport exactly the persisted PG rows, including their nested lineage."""
    columns = (
        "id",
        "trace_id",
        "observation_span_id",
        "trace_session_id",
        "target_type",
        "custom_eval_config_id",
        "eval_task_id",
        "config_hash",
        "status",
        "error",
        "output_float",
        "output_metadata",
        "created_at",
        "updated_at",
        "_version",
        "is_deleted",
    )
    rows = []
    for entry in entries:
        assert entry.status == EvalEntryStatus.COMPLETED and not entry.error
        metadata = json.dumps(entry.output_metadata)
        assert json.loads(metadata) == entry.output_metadata
        rows.append(
            (
                entry.id,
                entry.trace_id,
                entry.observation_span_id,
                entry.trace_session_id,
                entry.target_type,
                entry.custom_eval_config_id,
                entry.eval_task_id,
                entry.config_hash,
                entry.status,
                int(entry.error),
                entry.output_float,
                metadata,
                entry.created_at,
                entry.updated_at,
                int(entry.updated_at.timestamp() * 1_000_000) * 1000,
                int(entry.deleted),
            )
        )
    client.execute(
        f"INSERT INTO tracer_eval_logger_v2 ({', '.join(columns)}) VALUES", rows
    )
    copied = client.execute(
        "SELECT id, output_metadata, eval_task_id, config_hash, status, error, output_float "
        "FROM tracer_eval_logger_v2 FINAL WHERE is_deleted = 0"
    )
    assert len(copied) == len(entries) == 2
    expected = {entry.id: entry for entry in entries}
    for identity, metadata, task_id, config_hash, status, error, score in copied:
        entry = expected[identity]
        assert json.loads(metadata) == entry.output_metadata
        assert (task_id, config_hash, status, error, score) == (
            entry.eval_task_id,
            entry.config_hash,
            entry.status,
            0,
            entry.output_float,
        )


def _assert_weighted_result(row):
    assert row["group_key"] == ""
    assert row["status"] == "ok"
    assert row["value"] == pytest.approx(1 / 3)
    assert row["numerator"] == 2
    assert row["denominator"] == 6
    assert row["unit_count"] == 6
    assert row["valid_count"] == 2
    assert row["missing_count"] == 4
    assert row["invalid_probability_count"] == 0


def test_closed_sampling_weighted_metric_is_readable_through_scoped_sql(
    acceptance_ch, project, custom_eval_config, workspace, user
):
    assert connection.vendor == "postgresql"
    client, database = acceptance_ch
    now = timezone.now()
    start, end = now - timedelta(hours=1), now - timedelta(minutes=1)
    spans = []
    regions = {}
    for region in ("A", "A", "B", "B", "B", "B"):
        session = TraceSession.objects.create(project=project, name=f"region-{region}")
        trace = Trace.objects.create(
            project=project, session=session, name="root-trace"
        )
        span = ObservationSpan.objects.create(
            id=uuid4().hex[:16],
            project=project,
            trace=trace,
            name="sampling-metric-root",
            observation_type="llm",
            parent_span_id="",
            span_attributes={"region": region},
            start_time=now - timedelta(minutes=5),
            end_time=now - timedelta(minutes=4),
        )
        spans.append(span)
        regions[f"session:{session.id}"] = region
    assert seed_ch_spans(spans) == 6
    spans_by_unit = {f"session:{span.trace.session_id}": span for span in spans}
    task = EvalTask.objects.create(
        project=project,
        name="Closed two-stratum acceptance",
        row_type="spans",
        run_type=RunType.CONTINUOUS,
        filters={},
        spans_limit=None,
        sampling_policy={
            "version": 1,
            "sampling_unit": "session",
            "mode": "closed",
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "strata": [
                {
                    "name": region,
                    "filters": [{"key": "region", "op": "in", "values": [region]}],
                    "target_n": 1,
                }
                for region in ("A", "B")
            ],
            "default": {"target_n": 0},
        },
    )
    task.evals.add(custom_eval_config)
    reconciled = reconcile(task)
    assert (reconciled.created, reconciled.requeued, reconciled.dropped) == (2, 0, 0)
    cohort = EvalSamplingCohort.objects.get(task=task)
    assert cohort.complete
    members = list(EvalSamplingMember.objects.filter(cohort=cohort))
    assert len(members) == 2 and all(member.selected for member in members)
    assert {regions[member.unit_id] for member in members} == {"A", "B"}
    assert sum(member.population_size for member in members) == 6
    expected_metadata = {}
    for member in members:
        region = regions[member.unit_id]
        population = 2 if region == "A" else 4
        assert member.population_size == population
        assert member.inclusion_probability == 1 / population
        assert json.loads(member.stratum) == [region, []]
        expected_metadata[spans_by_unit[member.unit_id].id] = {
            "_task_selection": {
                "sampling": {
                    "policy_version": cohort.policy_version,
                    "cohort_id": str(cohort.id),
                    "cohort_frozen_at": cohort.frozen_at.isoformat(),
                    "membership_mode": "closed",
                    "sampling_unit": "session",
                    "requested_sampling_unit": "session",
                    "missing_id_fallback": False,
                    "sampling_unit_id": member.unit_id,
                    "stratum": member.stratum,
                    "inclusion_probability": 1 / population,
                    "population_size": population,
                    "window": {"start": start.isoformat(), "end": end.isoformat()},
                }
            }
        }
    entries = list(EvalLogger.objects.filter(eval_task_id=str(task.id)))
    assert {entry.observation_span_id for entry in entries} == set(expected_metadata)
    config_hash = resolved_config_hash(custom_eval_config)
    for entry in entries:
        assert entry.status == EvalEntryStatus.PENDING
        assert entry.output_metadata == expected_metadata[entry.observation_span_id]
        assert entry.custom_eval_config_id == custom_eval_config.id
        assert entry.config_hash == config_hash
    unsampled = {span.id for span in spans} - set(expected_metadata)
    assert len(unsampled) == 4
    assert not EvalLogger.objects.filter(observation_span_id__in=unsampled).exists()

    claimed = claim_pending_batch(task, 6)
    assert {entry.id for entry in claimed} == {entry.id for entry in entries}
    for entry in claimed:
        metadata = deepcopy(entry.output_metadata)
        unit = metadata["_task_selection"]["sampling"]["sampling_unit_id"]
        with (
            running_entry_epoch(entry.updated_at),
            writing_onto_entry(str(entry.id), output_metadata=metadata),
        ):
            persisted = persist_eval_result(
                {
                    "output_float": 1.0 if regions[unit] == "A" else 0.0,
                    "output_metadata": {},
                }
            )
            assert persisted.id == entry.id
            assert mark_terminal(
                entry, EvalEntryStatus.COMPLETED, config_hash=config_hash
            )
        entry.refresh_from_db()
        assert entry.status == EvalEntryStatus.COMPLETED
        assert entry.output_metadata == metadata
        assert (entry.eval_task_id, entry.custom_eval_config_id, entry.config_hash) == (
            str(task.id),
            custom_eval_config.id,
            config_hash,
        )
    assert EvalLogger.objects.filter(eval_task_id=str(task.id)).count() == 2
    _copy_completed_entries(client, claimed)

    metric = save_definition(
        {
            "name": "Weighted sampled positive rate",
            "description": "Six eligible sessions; two observed scores.",
            "selection_unit": "session",
            "weighting": "inverse_probability",
            "cohort_key": "",
            "formula": "ratio(score == 1, score >= 0)",
            "inputs": [
                {
                    "alias": "score",
                    "source": "eval",
                    "config_id": str(custom_eval_config.id),
                    "config_hash": config_hash,
                    "reducer": "latest",
                }
            ],
            "filters": [],
        },
        project,
        workspace,
        user,
    )
    # The closed window bounds source events. Evaluations are produced afterward
    # and observed at as_of, without rewriting their persisted timestamps.
    run, created = create_run(
        metric,
        {
            "window_start": start,
            "window_end": end,
            "source_run_id": str(task.id),
            "filters": [],
            "idempotency_key": uuid4().hex,
        },
        user,
    )
    assert created
    execute_run(run.id)
    run.refresh_from_db()
    assert run.status == DerivedMetricRun.Status.COMPLETE, run.error
    assert run.result_id and run.snapshot_id and run.completed_at
    assert run.row_count > 0
    manifest = run.source_manifest
    assert manifest["schema_version"] == manifest["compiler_version"] == 1
    assert manifest["definition_hash"] == run.definition.definition_hash
    assert manifest["evaluator_table"] == "tracer_eval_logger_v2"
    sampling_scope = manifest["sampling_scope"]
    prefix = f"{cohort.id}:{cohort.policy_version}:"
    assert sampling_scope.startswith(prefix)
    lineage_hash = sampling_scope.removeprefix(prefix)
    assert len(lineage_hash) == 64 and int(lineage_hash, 16) >= 0
    assert client.execute(
        "SELECT count(), countIf(input_values['score'] IS NOT NULL), "
        "countIf(input_values['score'] IS NULL), groupUniqArrayIf(sampling_scope, notEmpty(sampling_scope)) "
        "FROM derived_metric_inputs WHERE snapshot_id = %(snapshot)s",
        {"snapshot": run.snapshot_id},
    ) == [(6, 2, 4, [sampling_scope])]
    committed = client.execute(
        "SELECT result_id FROM derived_metric_commits FINAL WHERE run_id = %(run)s",
        {"run": run.id},
    )
    assert committed == [(run.result_id,)]
    rows, columns = client.execute(
        f"SELECT {RESULT_COLUMNS} FROM derived_metrics WHERE run_id = %(run)s AND rollup = 'overall'",
        {"run": run.id},
        with_column_types=True,
    )
    assert len(rows) == 1
    owned = dict(zip((column[0] for column in columns), rows[0], strict=True))
    _assert_weighted_result(owned)
    assert owned["sampling_scope"] == sampling_scope

    # A committed foreign-project result and an uncommitted owned attempt are
    # both present physically. The scoped committed view must return neither.
    foreign_project, foreign_run, foreign_result = uuid4(), uuid4(), uuid4()
    client.execute(
        "INSERT INTO derived_metric_results SELECT * REPLACE "
        "(%(project)s AS project_id, %(foreign_run)s AS run_id, %(result)s AS result_id) "
        "FROM derived_metric_results WHERE run_id = %(run)s AND rollup = 'overall'",
        {
            "project": foreign_project,
            "foreign_run": foreign_run,
            "result": foreign_result,
            "run": run.id,
        },
    )
    client.execute(
        "INSERT INTO derived_metric_commits (org_id, workspace_id, project_id, metric_id, "
        "run_id, result_id, committed_at, version) VALUES",
        [
            (
                project.organization_id,
                workspace.id,
                foreign_project,
                metric.id,
                foreign_run,
                foreign_result,
                timezone.now(),
                1,
            )
        ],
    )
    client.execute(
        "INSERT INTO derived_metric_results SELECT * REPLACE (%(result)s AS result_id) "
        "FROM derived_metric_results WHERE run_id = %(run)s AND rollup = 'overall'",
        {"result": uuid4(), "run": run.id},
    )
    assert client.execute(
        "SELECT count() FROM derived_metric_results WHERE rollup = 'overall'"
    ) == [(3,)]
    available, reason = derived_metrics_availability()
    assert available, reason
    dataset = next(
        item
        for item in catalog(derived_metrics_available=available)
        if item["name"] == "derived_metrics"
    )
    assert dataset["available"]
    public_columns = {column["name"] for column in dataset["columns"]}
    assert {
        "run_id",
        "result_id",
        "value",
        "numerator",
        "denominator",
        "sampling_scope",
    } <= public_columns
    assert "output_metadata" not in public_columns
    actor = Actor(user.id, project.organization_id, workspace.id)
    owned_project = authorize(actor, project.id)
    query = f"SELECT {RESULT_COLUMNS} FROM derived_metrics WHERE rollup = 'overall'"
    scope = query_scope(actor, owned_project, start, end, query)
    assert scope.derived_metrics_enabled and scope.database == database
    compiled = compile_query(query, scope)
    for identity in (project.organization_id, workspace.id, project.id):
        assert str(identity) in compiled
    result = run_sql(compiled, uuid4(), lambda: False)
    assert result["row_count"] == 1 and result["truncated"] is False
    names = [column["name"] for column in result["columns"]]
    assert names == RESULT_COLUMNS.split(", ")
    returned = dict(zip(names, result["rows"][0], strict=True))
    _assert_weighted_result(returned)
    assert returned == {**owned, "run_id": str(run.id), "result_id": str(run.result_id)}
    assert returned["sampling_scope"] == sampling_scope
