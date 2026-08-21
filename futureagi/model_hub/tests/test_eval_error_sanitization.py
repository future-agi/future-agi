from __future__ import annotations

import uuid
from inspect import unwrap
from types import SimpleNamespace

import pytest
from rest_framework import status

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.selectors.eval_usage import (
    EvalUsageChartBucket,
    EvalUsageRead,
    EvalUsageReadCompleteness,
    EvalUsageReadError,
    EvalUsageReadErrorCode,
)
from model_hub.serializers.contracts import EvalUsageQuerySerializer
from model_hub.views import separate_evals


@pytest.fixture(autouse=True)
def _enable_clickhouse_eval_usage(settings):
    """This module exercises the ClickHouse usage reader explicitly."""

    settings.EVAL_USAGE_CLICKHOUSE_ENABLED = True


def _usage_template(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name=f"bounded-usage-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail", "eval_type_id": "AgentEvaluator"},
        eval_tags=["llm"],
        criteria="Check {{response}}",
        model="turing_large",
        visible_ui=True,
    )


def _usage_worker_response(template, organization, workspace):
    query_serializer = EvalUsageQuerySerializer(
        data={
            "page": 0,
            "page_size": 25,
            "period": "30d",
            "refresh": True,
        }
    )
    query_serializer.is_valid(raise_exception=True)
    request = SimpleNamespace(
        validated_query_data=query_serializer.validated_data,
        organization=organization,
        workspace=workspace,
        user=SimpleNamespace(organization=organization),
        _exact_aggregation_worker=True,
    )
    return unwrap(separate_evals.EvalUsageStatsView.get)(
        separate_evals.EvalUsageStatsView(),
        request,
        template.id,
    )


def test_finite_usage_metric_rejects_clickhouse_empty_average_sentinels():
    assert separate_evals._finite_usage_metric(float("nan")) is None
    assert separate_evals._finite_usage_metric(float("inf")) is None
    assert separate_evals._finite_usage_metric("not-a-number") is None
    assert separate_evals._finite_usage_metric(0.25) == 0.25


@pytest.mark.django_db
def test_eval_logs_table_internal_error_is_sanitized(auth_client, monkeypatch):
    """ClickHouse/database internals never cross the eval-settings API boundary."""

    private_error = "Code: 159. DB::Exception: private query and stack"

    def fail_access_check(*_args, **_kwargs):
        raise RuntimeError(private_error)

    # The OSS test lane intentionally has no EE APICallLog model. A non-None
    # placeholder reaches the same guarded endpoint branch without requiring
    # any external table or write.
    monkeypatch.setattr(separate_evals, "APICallLog", SimpleNamespace())
    monkeypatch.setattr(
        separate_evals,
        "_get_accessible_eval_template_for_request",
        fail_access_check,
    )

    response = auth_client.get(
        "/model-hub/get-eval-logs-details",
        {
            "eval_template_id": str(uuid.uuid4()),
            "source": "eval_playground",
            "current_page_index": 0,
            "page_size": 10,
        },
    )

    rendered = response.content.decode()
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Unable to load evaluation logs. Please try again later." in rendered
    assert private_error not in rendered


@pytest.mark.django_db
@pytest.mark.parametrize(
    "failure_code",
    [
        EvalUsageReadErrorCode.DEADLINE_EXCEEDED,
        EvalUsageReadErrorCode.QUERY_FAILED,
    ],
)
def test_eval_usage_typed_failure_propagates_from_exact_worker(
    organization,
    workspace,
    monkeypatch,
    failure_code,
):
    template = _usage_template(organization, workspace)
    monkeypatch.setattr(separate_evals, "APICallLog", SimpleNamespace())
    monkeypatch.setattr(separate_evals, "is_clickhouse_enabled", lambda: True)

    def fail_bounded_read(**_kwargs):
        raise EvalUsageReadError(
            failure_code,
            operations=("total",),
        )

    monkeypatch.setattr(separate_evals, "read_eval_usage", fail_bounded_read)

    with pytest.raises(EvalUsageReadError) as raised:
        _usage_worker_response(template, organization, workspace)

    assert raised.value.code == failure_code


@pytest.mark.django_db
def test_eval_usage_public_poll_never_runs_clickhouse_inline(
    auth_client,
    organization,
    workspace,
    monkeypatch,
):
    template = _usage_template(organization, workspace)
    monkeypatch.setattr(separate_evals, "APICallLog", SimpleNamespace())
    monkeypatch.setattr(separate_evals, "is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(
        separate_evals,
        "read_eval_usage",
        lambda **_kwargs: pytest.fail("public poll ran the exact selector inline"),
    )
    monkeypatch.setattr(
        separate_evals,
        "read_or_schedule_exact_snapshot",
        lambda _namespace, _identity, **kwargs: kwargs["pending_payload"],
    )

    response = auth_client.get(
        f"/model-hub/eval-templates/{template.id}/usage/",
        {"page": 0, "page_size": 25, "period": "30d"},
    )

    result = response.data["result"]
    assert response.status_code == status.HTTP_200_OK
    assert result["query_complete"] is False
    assert result["query_status"] == "pending"
    assert result["query_sampled"] is False
    assert result["table"] == []


@pytest.mark.django_db
def test_eval_usage_clickhouse_response_preserves_required_total_runs(
    organization,
    workspace,
    monkeypatch,
):
    template = _usage_template(organization, workspace)
    monkeypatch.setattr(separate_evals, "APICallLog", SimpleNamespace())
    monkeypatch.setattr(separate_evals, "is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(
        separate_evals,
        "read_eval_usage",
        lambda **_kwargs: EvalUsageRead(
            total_runs=0,
            runs_period=0,
            success_count=0,
            error_count=0,
            chart=[],
            logs=[],
            completeness=EvalUsageReadCompleteness.COMPLETE,
            unavailable_fields=(),
        ),
    )

    response = _usage_worker_response(template, organization, workspace)

    result = response.data["result"]
    assert response.status_code == status.HTTP_200_OK
    assert result["completeness"] == "complete"
    assert result["unavailable_fields"] == []
    assert result["stats"]["total_runs"] == 0


@pytest.mark.django_db
def test_eval_usage_non_finite_chart_averages_do_not_crash_api(
    organization,
    workspace,
    monkeypatch,
):
    template = _usage_template(organization, workspace)
    monkeypatch.setattr(separate_evals, "APICallLog", SimpleNamespace())
    monkeypatch.setattr(separate_evals, "is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(
        separate_evals,
        "read_eval_usage",
        lambda **kwargs: EvalUsageRead(
            total_runs=1,
            runs_period=1,
            success_count=1,
            error_count=0,
            chart=[
                EvalUsageChartBucket(
                    bucket=separate_evals._round_to_usage_bucket(
                        kwargs["start_date"], kwargs["bucket_minutes"]
                    ),
                    calls=1,
                    avg_duration=float("nan"),
                    avg_score=float("inf"),
                    pass_count=1,
                    fail_count=0,
                )
            ],
            logs=[],
            completeness=EvalUsageReadCompleteness.COMPLETE,
            unavailable_fields=(),
        ),
    )

    response = _usage_worker_response(template, organization, workspace)

    assert response.status_code == status.HTTP_200_OK
    chart = response.data["result"]["chart"]
    assert chart[0]["calls"] == 1
    assert chart[0]["avg_latency_ms"] == 0
    assert chart[0]["avg_score"] is None


@pytest.mark.django_db
def test_eval_usage_programming_defect_re_raises_through_exact_worker(
    organization,
    workspace,
    monkeypatch,
):
    template = _usage_template(organization, workspace)
    monkeypatch.setattr(separate_evals, "APICallLog", SimpleNamespace())
    monkeypatch.setattr(separate_evals, "is_clickhouse_enabled", lambda: True)

    def fail_with_bug(**_kwargs):
        raise KeyError("eval usage application bug")

    monkeypatch.setattr(separate_evals, "read_eval_usage", fail_with_bug)

    with pytest.raises(KeyError, match="eval usage application bug"):
        _usage_worker_response(template, organization, workspace)
