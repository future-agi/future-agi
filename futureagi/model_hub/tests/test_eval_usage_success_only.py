"""Eval template Usage counts and lists only successful runs.

Owner rule (2026-09-25): every eval run shows in the eval logs, but only a
successful run is usage. On dev, template b6b42361 had one ``success`` and two
``error`` ledger rows (eval_playground, "Evaluation failed…") in its 30D
window, so the Usage tab read Runs 3 / Success 1 / Errors 2 and listed all
three. The logs keep every settled run; only the Usage read narrows.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from inspect import unwrap
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from conftest import _ch_test_native_client, _ch_test_native_port
from ee.usage.models.usage import APICallLog, APICallStatusChoices
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.selectors import eval_usage
from model_hub.serializers.contracts import EvalUsageQuerySerializer
from model_hub.views import separate_evals
from tracer.services import exact_aggregation_cache
from tracer.services.clickhouse import trace_project_scope
from tracer.services.clickhouse.client import ClickHouseClient
from tracer.services.exact_aggregation_cache import publish_exact_snapshot

USAGE_QUERY = {"page": 0, "page_size": 25, "period": "30d"}


def _template(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name=f"usage-success-only-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail", "eval_type_id": "AgentEvaluator"},
        eval_tags=["llm"],
        criteria="Check {{response}}",
        model="turing_large",
        visible_ui=True,
    )


def _ledger_row(template, organization, workspace, *, status, source, config):
    return APICallLog.objects.create(
        organization=organization,
        workspace=workspace,
        status=status,
        cost=0.01,
        deducted_cost=0,
        source=source,
        source_id=str(template.id),
        config=config,
    )


@pytest.fixture
def seeded_runs(organization, workspace):
    template = _template(organization, workspace)
    success = _ledger_row(
        template,
        organization,
        workspace,
        status=APICallStatusChoices.SUCCESS.value,
        source="eval_playground",
        config={
            "output": {"output": "Passed", "reason": "under the threshold"},
            "mappings": {"response": "hello"},
        },
    )
    failures = [
        # Evaluator started and failed (dev rows 1017619 / 1017620).
        _ledger_row(
            template,
            organization,
            workspace,
            status=APICallStatusChoices.ERROR.value,
            source="eval_playground",
            config={
                "output": {
                    "reason": (
                        "Evaluation failed. Please try again. If the problem "
                        "persists, contact support."
                    )
                },
                "mappings": {"response": "hello"},
            },
        ),
        # Input validation rejected the run before the evaluator started.
        _ledger_row(
            template,
            organization,
            workspace,
            status=APICallStatusChoices.ERROR.value,
            source="eval_playground",
            config={
                "output": {"reason": "Required input 'response' is empty."},
                "mappings": {"response": ""},
            },
        ),
        # Task and composite-child runs that errored.
        _ledger_row(
            template,
            organization,
            workspace,
            status=APICallStatusChoices.ERROR.value,
            source="tracer",
            config={"output": {"reason": "Evaluation failed."}},
        ),
        _ledger_row(
            template,
            organization,
            workspace,
            status=APICallStatusChoices.ERROR.value,
            source="tracer_composite",
            config={"output": {"reason": "Evaluation failed."}},
        ),
    ]
    in_flight = _ledger_row(
        template,
        organization,
        workspace,
        status=APICallStatusChoices.PROCESSING.value,
        source="dataset_evaluation",
        config={},
    )
    return template, success, failures, in_flight


@pytest.mark.django_db
def test_usage_counts_and_lists_only_successful_runs(auth_client, seeded_runs):
    template, success, _failures, _in_flight = seeded_runs

    response = auth_client.get(
        f"/model-hub/eval-templates/{template.id}/usage/", USAGE_QUERY
    )

    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["stats"] == {
        "total_runs": 1,
        "runs_period": 1,
        "success_count": 1,
        "error_count": 0,
        "pass_rate": 100.0,
    }
    assert [row["row_id"] for row in result["table"]] == [str(success.log_id)]
    assert [row["status"]["cell_value"] for row in result["table"]] == ["success"]
    assert result["logs"]["total"] == 1
    assert sum(point["calls"] for point in result["chart"]) == 1


@pytest.mark.django_db
def test_failed_runs_stay_in_the_eval_logs(auth_client, seeded_runs):
    template, success, failures, _in_flight = seeded_runs

    response = auth_client.get(
        "/model-hub/get-eval-logs-details",
        {"eval_template_id": str(template.id), "page_size": 25},
    )

    assert response.status_code == 200, response.content
    result = response.json()["result"]
    logged = {row["row_id"] for row in result["table"]}
    assert logged == {str(success.log_id), *(str(row.log_id) for row in failures)}
    assert result["metadata"]["total_rows"] == 1 + len(failures)


@pytest.mark.django_db
def test_usage_never_serves_a_snapshot_computed_under_the_old_rule(
    auth_client, organization, workspace, settings, monkeypatch, seeded_runs
):
    """A cached payload from before this rule still counts error rows; the
    current request identity must not resolve to it."""

    template, _success, _failures, _in_flight = seeded_runs
    settings.EVAL_USAGE_CLICKHOUSE_ENABLED = True
    monkeypatch.setattr(separate_evals, "is_clickhouse_enabled", lambda: True)
    pre_rule_identity = {
        "organization_id": str(organization.id),
        "workspace_id": str(workspace.id),
        "template_id": str(template.id),
        "page": 0,
        "page_size": 25,
        "period": "30d",
        "start_date": None,
        "end_date": None,
    }
    publish_exact_snapshot(
        "eval-usage",
        pre_rule_identity,
        {
            "template_id": str(template.id),
            "stats": {"runs_period": 5, "success_count": 1, "error_count": 4},
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        },
    )
    # Serve from the cache only: no refresh is scheduled in this test.
    cache_only = exact_aggregation_cache.read_or_schedule_exact_snapshot
    monkeypatch.setattr(
        separate_evals,
        "read_or_schedule_exact_snapshot",
        lambda namespace, identity, **kwargs: cache_only(
            namespace, identity, **{**kwargs, "schedule_on_miss": False}
        ),
    )

    response = auth_client.get(
        f"/model-hub/eval-templates/{template.id}/usage/", USAGE_QUERY
    )

    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["query_status"] == "pending"
    assert result["stats"]["error_count"] == 0


@pytest.mark.django_db
def test_evaluations_usage_page_counts_only_successful_runs(
    auth_client, organization, workspace, seeded_runs
):
    """Evaluations > Usage (the tab on the Groups page) reads "30 Days run"
    from get_all_templates. On dev, toxicity read 245 there with 115
    successful runs, and lateny_test read 3 with one."""

    template, _success, failures, _in_flight = seeded_runs
    busier = _template(organization, workspace)
    for _ in range(2):
        _ledger_row(
            busier,
            organization,
            workspace,
            status=APICallStatusChoices.SUCCESS.value,
            source="eval_playground",
            config={"output": {"output": "Passed"}},
        )

    response = auth_client.post(
        "/model-hub/get-eval-templates",
        {
            "search_text": "usage-success-only-",
            "current_page_index": 0,
            "page_size": 10,
            "sort": [{"column_id": "last_30_run", "type": "descending"}],
        },
        format="json",
    )

    assert response.status_code == 200, response.content
    rows = response.data["result"]["row_data"]
    assert [(row["id"], row["last30_run"]) for row in rows] == [
        (busier.id, 2),
        (template.id, 1),
    ]
    # The "30 days error rate" column counts error rows by design; whether it
    # stays is an owner decision, so it is pinned unchanged here.
    error_rate = rows[1]["error_rate"]
    assert max(point["value"] for point in error_rate) == len(failures)


# ── Constant success-only fields ────────────────────────────────────────────
#
# Usage counts only successful runs, so ``success_count`` equals
# ``runs_period``, ``error_count`` is 0 and ``pass_rate`` is 100 whenever there
# are runs. The response keeps the fields; these pin their values and prove the
# read no longer asks the database for them.


def _usage_stats(auth_client, template):
    response = auth_client.get(
        f"/model-hub/eval-templates/{template.id}/usage/", USAGE_QUERY
    )
    assert response.status_code == 200, response.content
    return response.json()["result"]


def _backdate(row, days):
    APICallLog.objects.filter(id=row.id).update(
        created_at=timezone.now() - timedelta(days=days)
    )


@pytest.mark.django_db
def test_usage_stats_publish_success_only_constants(
    auth_client, organization, workspace, seeded_runs
):
    template, *_ = seeded_runs
    for output in ("Failed", 0.5):
        _ledger_row(
            template,
            organization,
            workspace,
            status=APICallStatusChoices.SUCCESS.value,
            source="tracer",
            config={"output": {"output": output}},
        )
    older = _ledger_row(
        template,
        organization,
        workspace,
        status=APICallStatusChoices.SUCCESS.value,
        source="tracer",
        config={"output": {"output": "Passed"}},
    )
    _backdate(older, days=40)

    result = _usage_stats(auth_client, template)

    assert result["stats"] == {
        "total_runs": 4,
        "runs_period": 3,
        "success_count": 3,
        "error_count": 0,
        "pass_rate": 100.0,
    }
    assert result["logs"]["total"] == 3
    assert sum(point["calls"] for point in result["chart"]) == 3
    assert sum(point["pass_count"] for point in result["chart"]) == 1
    assert sum(point["fail_count"] for point in result["chart"]) == 1


@pytest.mark.django_db
def test_usage_stats_for_an_empty_period(auth_client, organization, workspace):
    template = _template(organization, workspace)
    older = _ledger_row(
        template,
        organization,
        workspace,
        status=APICallStatusChoices.SUCCESS.value,
        source="tracer",
        config={"output": {"output": "Passed"}},
    )
    _backdate(older, days=40)

    result = _usage_stats(auth_client, template)

    assert result["stats"] == {
        "total_runs": 1,
        "runs_period": 0,
        "success_count": 0,
        "error_count": 0,
        "pass_rate": 0.0,
    }
    assert result["logs"]["total"] == 0
    assert sum(point["calls"] for point in result["chart"]) == 0


@pytest.mark.django_db
def test_usage_fallback_does_not_count_constant_status_fields(auth_client, seeded_runs):
    """The Postgres fallback counts total runs, period runs and the log total.

    It ran two more COUNTs, success rows and error rows over the success-only
    period queryset, which always returned ``runs_period`` and 0.
    """

    template, *_ = seeded_runs
    usage_table = f'"{APICallLog._meta.db_table}"'

    with CaptureQueriesContext(connection) as queries:
        _usage_stats(auth_client, template)

    usage_counts = [
        query["sql"]
        for query in queries.captured_queries
        if "COUNT(" in query["sql"] and usage_table in query["sql"]
    ]
    assert len(usage_counts) == 3, usage_counts


@pytest.fixture
def clickhouse_usage_table(settings, monkeypatch):
    """A usage table on the test ClickHouse that the Usage view reads."""

    settings.EVAL_USAGE_CLICKHOUSE_ENABLED = True
    monkeypatch.setattr(separate_evals, "is_clickhouse_enabled", lambda: True)
    suffix = uuid.uuid4().hex[:10]
    usage_table = f"_test_eval_usage_stats_{suffix}"
    trace_table = f"_test_eval_usage_stats_trace_{suffix}"
    with _ch_test_native_client() as admin:
        admin.execute(
            f"""
            CREATE TABLE {usage_table} (
                id Int64,
                log_id UUID,
                organization_id UUID,
                workspace_id Nullable(UUID),
                source_id String,
                status String,
                config String,
                eval_trace_id String,
                deleted UInt8,
                created_at DateTime64(6, 'UTC'),
                _peerdb_is_deleted UInt8,
                _peerdb_version Int64
            ) ENGINE = MergeTree
            ORDER BY id
            """
        )
        admin.execute(
            f"""
            CREATE TABLE {trace_table} (
                id UUID, project_id UUID, is_deleted UInt8, _version UInt64
            ) ENGINE = ReplacingMergeTree(_version, is_deleted)
            ORDER BY (project_id, id)
            """
        )
        read_client = ClickHouseClient(
            host=os.environ.get("CH25_HOST", "127.0.0.1"),
            port=_ch_test_native_port().port,
            database="default",
        )
        monkeypatch.setattr(eval_usage, "_USAGE_TABLE", usage_table)
        monkeypatch.setattr(trace_project_scope, "_TRACE_TABLE", trace_table)
        monkeypatch.setattr(eval_usage, "get_clickhouse_client", lambda: read_client)

        def insert(rows):
            admin.execute(f"INSERT INTO {usage_table} VALUES", rows)

        try:
            yield insert
        finally:
            read_client.close()
            admin.execute(f"DROP TABLE IF EXISTS {trace_table}")
            admin.execute(f"DROP TABLE IF EXISTS {usage_table}")


def _clickhouse_usage_result(template, organization, workspace):
    """The payload the exact-aggregation worker publishes for a 30D read."""

    query = EvalUsageQuerySerializer(
        data={**USAGE_QUERY, "refresh": True},
    )
    query.is_valid(raise_exception=True)
    request = SimpleNamespace(
        validated_query_data=query.validated_data,
        organization=organization,
        workspace=workspace,
        user=SimpleNamespace(organization=organization),
        _exact_aggregation_worker=True,
    )
    response = unwrap(separate_evals.EvalUsageStatsView.get)(
        separate_evals.EvalUsageStatsView(), request, template.id
    )
    assert response.status_code == 200, response.data
    return response.data["result"]


@pytest.mark.integration
@pytest.mark.django_db
def test_clickhouse_usage_stats_publish_success_only_constants(
    organization, workspace, clickhouse_usage_table
):
    template = _template(organization, workspace)
    quiet = _template(organization, workspace)
    now = datetime.now(UTC).replace(microsecond=0)

    def row(row_id, source, status, days_ago, version=1, output=None):
        config = json.dumps({"output": {"output": output}} if output else {})
        return (
            row_id,
            uuid.uuid4(),
            organization.id,
            workspace.id,
            str(source.id),
            status,
            config,
            "",
            0,
            now - timedelta(days=days_ago, minutes=row_id),
            0,
            version,
        )

    clickhouse_usage_table(
        [
            row(1, template, "success", 1, output="Passed"),
            row(2, template, "success", 2, output="Failed"),
            row(3, template, "success", 3, output=0.5),
            row(4, template, "success", 40, output="Passed"),
            row(5, template, "error", 1),
            row(6, template, "processing", 1),
            # The newest version decides: this run errored after a success.
            row(7, template, "success", 1, output="Passed"),
            row(7, template, "error", 1, version=2),
            row(8, quiet, "success", 40, output="Passed"),
        ]
    )

    busy = _clickhouse_usage_result(template, organization, workspace)
    empty = _clickhouse_usage_result(quiet, organization, workspace)

    assert busy["stats"] == {
        "total_runs": 4,
        "runs_period": 3,
        "success_count": 3,
        "error_count": 0,
        "pass_rate": 100.0,
    }
    assert busy["logs"]["total"] == 3
    assert sum(point["pass_count"] for point in busy["chart"]) == 1
    assert sum(point["fail_count"] for point in busy["chart"]) == 1
    assert empty["stats"] == {
        "total_runs": 1,
        "runs_period": 0,
        "success_count": 0,
        "error_count": 0,
        "pass_rate": 0.0,
    }
