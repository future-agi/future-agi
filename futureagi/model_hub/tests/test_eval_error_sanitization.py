from __future__ import annotations

import uuid
from datetime import timedelta
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status

from model_hub.models.choices import CellStatus, OwnerChoices
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


# ── Snapshot freshness ─────────────────────────────────────────────────────
#
# An exact usage snapshot is served until refreshed. On dev the 30D chart
# snapshot for one template was computed at 00:40 with 2 successful runs and
# kept serving that count at 01:23, while 7D (first computed 01:05) showed 17.


def _usage_row(template, organization, workspace, status="success", **backdated):
    from ee.usage.models.usage import APICallLog

    row = APICallLog.objects.create(
        organization=organization,
        workspace=workspace,
        cost=0,
        status=status,
        source="tracer",
        source_id=str(template.id),
    )
    if backdated:
        APICallLog.objects.filter(id=row.id).update(**backdated)
        row.refresh_from_db()
    return row


def _public_poll_refresh_flag(auth_client, template, monkeypatch, completed_at):
    """The ``refresh`` flag a public poll hands the snapshot scheduler."""

    cache.clear()
    monkeypatch.setattr(separate_evals, "is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(
        separate_evals,
        "read_exact_snapshot",
        lambda _namespace, _identity: {
            "query_completed_at": completed_at.isoformat(),
        },
    )
    seen = {}

    def _schedule(_namespace, _identity, **kwargs):
        seen["refresh"] = kwargs["refresh"]
        return kwargs["pending_payload"]

    monkeypatch.setattr(separate_evals, "read_or_schedule_exact_snapshot", _schedule)

    response = auth_client.get(
        f"/model-hub/eval-templates/{template.id}/usage/",
        {"page": 0, "page_size": 1, "period": "30d"},
    )

    assert response.status_code == status.HTTP_200_OK
    return seen["refresh"]


@pytest.mark.django_db
def test_eval_usage_refreshes_a_snapshot_taken_before_newer_runs(
    auth_client, organization, workspace, monkeypatch
):
    template = _usage_template(organization, workspace)
    completed_at = timezone.now() - timedelta(minutes=10)
    _usage_row(template, organization, workspace)

    assert _public_poll_refresh_flag(auth_client, template, monkeypatch, completed_at)


@pytest.mark.django_db
@pytest.mark.parametrize("cell_status", [CellStatus.ERROR.value, CellStatus.PASS.value])
def test_eval_usage_refreshes_when_the_dataset_runner_settles_an_in_flight_run(
    auth_client, organization, workspace, monkeypatch, cell_status
):
    """A run in flight at snapshot time counts once the dataset runner settles it.

    The settle goes through the runner's own ledger write, not a direct
    ``updated_at`` update: that write must move ``updated_at`` or the snapshot
    keeps serving the in-flight state.
    """

    from ee.usage.models.usage import APICallLog
    from model_hub.views.eval_runner import EvaluationRunner

    template = _usage_template(organization, workspace)
    now = timezone.now()
    completed_at = now - timedelta(minutes=10)
    row = _usage_row(
        template,
        organization,
        workspace,
        status="processing",
        created_at=now - timedelta(minutes=20),
        updated_at=now - timedelta(minutes=20),
    )

    EvaluationRunner._handle_api_call_status(MagicMock(), row, cell_status)

    assert APICallLog.objects.get(id=row.id).status != "processing"
    assert _public_poll_refresh_flag(auth_client, template, monkeypatch, completed_at)


@pytest.mark.django_db
def test_eval_usage_refreshes_for_a_run_that_landed_just_before_the_snapshot(
    auth_client, organization, workspace, monkeypatch
):
    """A run that landed while the read ran, or inside the CDC lag, is re-read.

    The snapshot's time is its publish time, not the time ClickHouse was read,
    and ClickHouse reads a CDC copy that lags Postgres by seconds.
    """

    template = _usage_template(organization, workspace)
    completed_at = timezone.now() - timedelta(minutes=10)
    landed = completed_at - timedelta(seconds=30)
    _usage_row(template, organization, workspace, created_at=landed, updated_at=landed)

    assert _public_poll_refresh_flag(auth_client, template, monkeypatch, completed_at)


@pytest.mark.django_db
def test_eval_usage_keeps_a_snapshot_no_run_has_changed_since(
    auth_client, organization, workspace, monkeypatch
):
    template = _usage_template(organization, workspace)
    other_template = _usage_template(organization, workspace)
    now = timezone.now()
    completed_at = now - timedelta(minutes=10)
    _usage_row(
        template,
        organization,
        workspace,
        created_at=now - timedelta(minutes=20),
        updated_at=now - timedelta(minutes=20),
    )
    # A newer run of a different template does not stale this one.
    _usage_row(other_template, organization, workspace)

    assert not _public_poll_refresh_flag(
        auth_client, template, monkeypatch, completed_at
    )


# ── Automatic refresh must settle ──────────────────────────────────────────
#
# These run the real snapshot scheduler; only the Temporal dispatch is
# stubbed, and the "worker" is the real refresh activity with its ClickHouse
# read replaced by a complete payload.


def _complete_usage_payload(template):
    return {
        **separate_evals._pending_eval_usage_payload(template.id, 0, 1),
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
    }


def _run_refresh_worker(enqueue, monkeypatch, payload=None):
    """Run the dispatched refresh; ``payload=None`` makes the read fail."""

    from tracer.tasks import exact_aggregation as task_module

    def _load(_namespace, _identity):
        if payload is None:
            raise RuntimeError("exact read failed")
        return payload

    monkeypatch.setattr(task_module, "_load_exact_payload", _load)
    task = enqueue.call_args.kwargs["kwargs"]
    # The activity body itself: ``run_sync`` also closes the DB connection,
    # which would end this test's transaction.
    worker = task_module.refresh_exact_aggregation_snapshot._original_func
    if payload is None:
        with pytest.raises(RuntimeError):
            worker(**task)
    else:
        worker(**task)


def _age_published_snapshot(enqueue, age):
    """Backdate the published snapshot as if it were taken ``age`` ago."""

    from tracer.services.exact_aggregation_cache import snapshot_cache_key

    task = enqueue.call_args.kwargs["kwargs"]
    key = snapshot_cache_key(task["namespace"], task["identity"])
    stored = cache.get(key)
    stored["completed_at"] = (timezone.now() - age).isoformat()
    cache.set(key, stored)


def _poll_usage(auth_client, template, **extra):
    response = auth_client.get(
        f"/model-hub/eval-templates/{template.id}/usage/",
        {"page": 0, "page_size": 1, "period": "30d", **extra},
    )
    assert response.status_code == status.HTTP_200_OK
    return response.json()["result"]


@pytest.fixture
def _exact_usage_scheduler(settings, monkeypatch):
    settings.EXACT_AGGREGATION_TASK_QUEUE = "exact_aggregation"
    monkeypatch.setattr(separate_evals, "is_clickhouse_enabled", lambda: True)
    cache.clear()
    with patch(
        "tracer.tasks.exact_aggregation.refresh_exact_aggregation_snapshot.apply_async"
    ) as enqueue:
        yield enqueue
    cache.clear()


@pytest.mark.django_db
def test_eval_usage_polls_stop_once_a_refresh_publishes_on_a_busy_template(
    auth_client, organization, workspace, monkeypatch, _exact_usage_scheduler
):
    """Runs landing between polls must not chain exact refreshes forever.

    A refresh publishes with a completion time; on a template that runs every
    few seconds a newer run already exists by the next poll. That poll must
    serve the new snapshot as settled, not dispatch another exact read.
    """

    enqueue = _exact_usage_scheduler
    template = _usage_template(organization, workspace)

    cold = _poll_usage(auth_client, template)
    assert cold["query_refreshing"] is True
    assert enqueue.call_count == 1

    for _cycle in range(3):
        _run_refresh_worker(enqueue, monkeypatch, _complete_usage_payload(template))
        _usage_row(template, organization, workspace)

        polled = _poll_usage(auth_client, template)

        assert polled["query_status"] == "complete"
        assert polled["query_refreshing"] is False
        assert enqueue.call_count == 1


@pytest.mark.django_db
def test_eval_usage_failed_refresh_waits_for_an_explicit_refresh(
    auth_client, organization, workspace, monkeypatch, _exact_usage_scheduler
):
    """A failed refresh never publishes, so its snapshot stays old.

    Newer runs must not resubmit the failing exact read on every poll; the
    user's Refresh does.
    """

    enqueue = _exact_usage_scheduler
    template = _usage_template(organization, workspace)

    _poll_usage(auth_client, template)
    _run_refresh_worker(enqueue, monkeypatch, _complete_usage_payload(template))
    _age_published_snapshot(enqueue, timedelta(minutes=10))
    _usage_row(template, organization, workspace)

    stale = _poll_usage(auth_client, template)
    assert stale["query_refreshing"] is True
    assert enqueue.call_count == 2

    _run_refresh_worker(enqueue, monkeypatch, payload=None)
    _usage_row(template, organization, workspace)

    failed = _poll_usage(auth_client, template)
    assert failed["query_refresh_failed"] is True
    assert failed["query_refreshing"] is False
    assert enqueue.call_count == 2

    retried = _poll_usage(auth_client, template, refresh="true")
    assert retried["query_refreshing"] is True
    assert enqueue.call_count == 3
