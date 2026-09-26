"""Eval template Usage counts and lists only successful runs.

Owner rule (2026-09-25): every eval run shows in the eval logs, but only a
successful run is usage. On dev, template b6b42361 had one ``success`` and two
``error`` ledger rows (eval_playground, "Evaluation failed…") in its 30D
window, so the Usage tab read Runs 3 / Success 1 / Errors 2 and listed all
three. The logs keep every settled run; only the Usage read narrows.
"""

from __future__ import annotations

import uuid

import pytest

from ee.usage.models.usage import APICallLog, APICallStatusChoices
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.views import separate_evals
from tracer.services import exact_aggregation_cache
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
