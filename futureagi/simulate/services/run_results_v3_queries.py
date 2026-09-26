"""Database queries for the v3 simulation run-results surface."""

from __future__ import annotations

from collections import Counter
from typing import Any

from django.core.cache import cache
from django.db.models import (
    Avg,
    Case,
    CharField,
    Count,
    F,
    FloatField,
    Func,
    JSONField,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Sum,
    TextField,
    Value,
    When,
)
from django.db.models.fields.json import KeyTextTransform, KeyTransform
from django.db.models.functions import Cast, Coalesce, Lower, NullIf, Trim
from django.db.models.lookups import Exact, GreaterThan, In

from model_hub.models.develop_dataset import Cell
from simulate.models import CallExecution, SimulateEvalConfig, TestExecution
from simulate.models.hosted_harness import HostedHarnessJob, HostedHarnessScenario
from simulate.semantics import SupportedProviders
from simulate.services.harness_scenarios import GROUP_BY as SCENARIO_GROUP_BY
from simulate.services.harness_scenarios import level_label
from simulate.services.run_results_v3 import build_evaluation_catalog
from simulate.services.run_results_v3_expressions import (
    PercentileCont,
    _json_text,
    _safe_json_float,
)

OUTCOMES = ("passed", "failed", "error", "inconclusive")
# The Scenarios tab's axes, plus the run's own outcome.
GROUP_FIELDS = {
    **{axis: f"result_{axis}" for axis in SCENARIO_GROUP_BY},
    "status": "result_outcome",
}
UNGROUPED = "Ungrouped"


def _authored_level(field: str):
    """One authored-scenario value for grouping; a list axis groups by its first entry."""
    head, _, tail = field.partition(".")
    if tail:
        return KeyTextTransform(tail, head)
    return KeyTextTransform("0", head)


def _json_value(field: str, *keys: str):
    expression = F(field)
    for key in keys:
        expression = KeyTransform(key, expression)
    return expression


def _eval_verdict_q(eval_ids: set[str], values: list[Any]) -> Q:
    verdict = Q(pk__in=[])
    for eval_id in eval_ids:
        output = Lower(Trim(_json_text("eval_outputs", eval_id, "output")))
        verdict |= Q(
            In(output, [str(value).lower() for value in values])
        ) & _eval_measured_q(eval_id)
    return verdict


def _eval_measured_q(eval_id: str) -> Q:
    status = Coalesce(
        _json_text("eval_outputs", eval_id, "status"),
        Value(""),
        output_field=TextField(),
    )
    return ~Q(In(Lower(Trim(status)), ["pending", "skipped", "error"]))


def _eval_score(eval_id: str):
    numeric = _safe_json_float("eval_outputs", eval_id, "output")
    numeric_type = Exact(
        Func(
            _json_value("eval_outputs", eval_id, "output"),
            function="jsonb_typeof",
            output_field=TextField(),
        ),
        Value("number"),
    )
    return Case(
        When(~_eval_measured_q(eval_id), then=Value(None, output_field=FloatField())),
        When(
            _eval_verdict_q(
                {eval_id}, [True, "true", "pass", "passed", "success", "successful"]
            ),
            then=Value(1.0),
        ),
        When(
            _eval_verdict_q(
                {eval_id}, [False, "false", "fail", "failed", "failure", "unsuccessful"]
            ),
            then=Value(0.0),
        ),
        When(
            numeric_type,
            then=Case(
                When(GreaterThan(numeric, Value(1.0)), then=numeric / Value(100.0)),
                default=numeric,
                output_field=FloatField(),
            ),
        ),
        default=None,
        output_field=FloatField(),
    )


def run_calls_queryset(
    execution: TestExecution, execution_ids: list[Any] | None = None
) -> QuerySet[CallExecution]:
    live_eval_ids = set(
        SimulateEvalConfig.objects.filter(
            run_test=execution.run_test, deleted=False
        ).values_list("id", flat=True)
    )
    live_eval_ids = {str(value) for value in live_eval_ids}
    failed_eval = _eval_verdict_q(
        live_eval_ids,
        [False, "false", "fail", "failed", "failure", "unsuccessful"],
    )
    passed_eval = Q() if live_eval_ids else Q(pk__in=[])
    for eval_id in live_eval_ids:
        passed_eval &= _eval_verdict_q(
            {eval_id}, [True, "true", "pass", "passed", "success", "successful"]
        )

    # The common hosted-harness fields live in JSONB today. These annotations
    # keep filtering, grouping, ordering and aggregation inside PostgreSQL while
    # the page serializer continues to preserve the richer fallback behavior.
    execution_filter = (
        Q(test_execution_id__in=execution_ids)
        if execution_ids is not None
        else Q(test_execution=execution)
    )
    provider_cases = [
        When(provider_call_data__has_key=provider, then=Value(provider))
        for provider in sorted(SupportedProviders)
    ]
    dataset_goal = Cell.all_objects.filter(
        row_id=OuterRef("row_id"),
        column__name__in=["use_case", "goal"],
    ).order_by(
        Case(
            When(column__name="use_case", then=Value(0)),
            default=Value(1),
        ),
        "created_at",
    )
    # A hosted call's use case, sub-goals, persona and coverage live on its
    # authored scenario: linked to the call on a direct run, or found by the
    # call's scenario key on the run's own job or its parent environment.
    run_jobs = HostedHarnessJob.no_workspace_objects.filter(
        run_test_id=execution.run_test_id
    )
    authored = HostedHarnessScenario.no_workspace_objects.filter(
        Q(call_execution_id=OuterRef("pk"))
        | Q(
            Q(job_id__in=run_jobs.values("id"))
            | Q(job_id__in=run_jobs.values("environment_id")),
            scenario_key=OuterRef("result_scenario_key"),
        )
    ).order_by("-created_at")
    queryset = CallExecution.objects.filter(execution_filter).annotate(
        result_scenario_key=_json_text("call_metadata", "harness_scenario_key")
    )
    queryset = queryset.annotate(
        **{
            GROUP_FIELDS[axis]: Coalesce(
                NullIf(
                    Subquery(
                        authored.values(level=_authored_level(field))[:1],
                        output_field=TextField(),
                    ),
                    Value(""),
                ),
                Value(UNGROUPED),
                output_field=CharField(),
            )
            for axis, field in SCENARIO_GROUP_BY.items()
            if axis != "goal"
        },
        result_eval_outcome=Case(
            When(failed_eval, then=Value("failed")),
            When(passed_eval, then=Value("passed")),
            default=Value("inconclusive"),
            output_field=CharField(),
        ),
        result_goal=Coalesce(
            NullIf(
                Subquery(authored.values("use_case")[:1], output_field=TextField()),
                Value(""),
            ),
            _json_text("call_metadata", "use_case"),
            _json_text("call_metadata", "goal"),
            _json_text("call_metadata", "row_data", "use_case"),
            _json_text("call_metadata", "row_data", "goal"),
            Subquery(dataset_goal.values("value")[:1], output_field=TextField()),
            _json_text("scenario__metadata", "use_case"),
            _json_text("scenario__metadata", "goal"),
            F("scenario__name"),
            output_field=CharField(),
        ),
        result_outcome=Case(
            When(
                call_metadata__harness_outcome_status__in=[
                    "passed",
                    "pass",
                    "success",
                    "successful",
                ],
                then=Value("passed"),
            ),
            When(
                call_metadata__harness_outcome_status__in=[
                    "failed",
                    "fail",
                    "failure",
                ],
                then=Value("failed"),
            ),
            When(
                call_metadata__harness_outcome_status__in=[
                    "error",
                    "errored",
                    "cancelled",
                    "canceled",
                ],
                then=Value("error"),
            ),
            When(
                call_metadata__harness_outcome_status__in=[
                    "inconclusive",
                    "unknown",
                    "skipped",
                ],
                then=Value("inconclusive"),
            ),
            When(status__in=["failed", "cancelled"], then=Value("error")),
            When(~Q(status="completed"), then=Value("inconclusive")),
            When(failed_eval, then=Value("failed")),
            When(passed_eval, then=Value("passed")),
            default=Value("inconclusive"),
            output_field=CharField(),
        ),
        result_latency_ms=Coalesce(
            Cast("avg_agent_latency_ms", FloatField()),
            _safe_json_float("conversation_metrics_data", "avg_latency_ms"),
        ),
        result_turn_count=Coalesce(
            _safe_json_float("conversation_metrics_data", "turn_count"),
            _safe_json_float("conversation_metrics_data", "bot_message_count"),
        ),
        result_tokens=_safe_json_float("conversation_metrics_data", "total_tokens"),
        result_provider=Case(
            *provider_cases,
            default=Coalesce(
                F("test_execution__agent_definition__provider"), Value("Unknown")
            ),
            output_field=CharField(),
        ),
    )
    return queryset.select_related("scenario", "test_execution__agent_definition")


def apply_run_call_query(queryset: QuerySet, query: dict[str, Any]) -> QuerySet:
    search = str(query.get("search") or "").strip()
    filters = query.get("filters") or {}
    if search:
        queryset = queryset.filter(
            Q(result_goal__icontains=search)
            | Q(scenario__name__icontains=search)
            | Q(ended_reason__icontains=search)
            | Q(error_message__icontains=search)
        )

    if values := filters.get("goal"):
        queryset = queryset.filter(result_goal__in=values)
    if values := filters.get("status"):
        queryset = queryset.filter(
            result_outcome__in=[str(value).lower() for value in values]
        )
    if values := filters.get("sub_goal"):
        sub_goal_query = Q(pk__in=[])
        for value in values:
            sub_goal_query |= Q(
                call_metadata__hosted_harness_receipt__sub_goals__contains=[
                    {"name": value}
                ]
            )
            sub_goal_query |= Q(call_metadata__sub_goals__contains=[value])
            sub_goal_query |= Q(call_metadata__sub_goals__contains=[{"name": value}])
        queryset = queryset.filter(sub_goal_query)

    group_by = query.get("group_by")
    group_key = query.get("group_key")
    if group_by in GROUP_FIELDS and group_key is not None:
        queryset = queryset.filter(**{GROUP_FIELDS[group_by]: group_key})

    ordering = str(query.get("ordering") or "-started_at")
    descending = ordering.startswith("-")
    requested = ordering.lstrip("-")
    fields = {
        "started_at": "started_at",
        "duration_seconds": "duration_seconds",
        "latency_ms": "result_latency_ms",
        "turn_count": "result_turn_count",
        "tokens": "result_tokens",
        "cost_cents": "cost_cents",
        "scenario": "scenario__name",
        "goal": "result_goal",
        "outcome": "result_outcome",
    }
    field = fields.get(requested, "started_at")
    return queryset.order_by(f"-{field}" if descending else field, "id")


def _aggregate_expressions(include_percentiles: bool = True) -> dict[str, Any]:
    expressions: dict[str, Any] = {
        "total": Count("id"),
        "measured": Count(
            "id", filter=Q(result_outcome__in=["passed", "failed", "error"])
        ),
        "tokens_total_value": Sum("result_tokens"),
        "cost_cents_total_value": Sum("cost_cents"),
        "csat_average": Avg("overall_score"),
        "turns_average": Avg("result_turn_count"),
    }
    for outcome in OUTCOMES:
        expressions[f"outcome_{outcome}"] = Count(
            "id", filter=Q(result_outcome=outcome)
        )
    for name, field in {
        "duration": "duration_seconds",
        "latency": "result_latency_ms",
        "tokens": "result_tokens",
        "cost_cents": "cost_cents",
    }.items():
        expressions[f"{name}_average"] = Avg(field)
        expressions[f"{name}_measured"] = Count(field)
        if include_percentiles:
            for percentile in (50, 75, 90, 95, 99):
                expressions[f"{name}_p{percentile}"] = PercentileCont(
                    field, percentile / 100
                )
    return expressions


def _summary_from_values(values: dict[str, Any]) -> dict[str, Any]:
    total = values.get("total") or 0
    measured = values.get("measured") or 0
    outcomes = {outcome: values.get(f"outcome_{outcome}") or 0 for outcome in OUTCOMES}

    def stats(name: str) -> dict[str, Any]:
        return {
            "average": values.get(f"{name}_average"),
            "p50": values.get(f"{name}_p50"),
            "p75": values.get(f"{name}_p75"),
            "p90": values.get(f"{name}_p90"),
            "p95": values.get(f"{name}_p95"),
            "p99": values.get(f"{name}_p99"),
            "measured": values.get(f"{name}_measured") or 0,
            "total": total,
        }

    tokens = stats("tokens")
    tokens["total_value"] = values.get("tokens_total_value")
    cost = stats("cost_cents")
    cost["total_value"] = values.get("cost_cents_total_value")
    return {
        "total": total,
        "outcomes": outcomes,
        "measured": measured,
        "pass_rate": (
            round(outcomes["passed"] / measured * 100, 2) if measured else None
        ),
        "duration": stats("duration"),
        "latency": stats("latency"),
        "tokens": tokens,
        "cost_cents": cost,
    }


def summarize_run_calls(
    queryset: QuerySet, include_percentiles: bool = True
) -> dict[str, Any]:
    return _summary_from_values(
        queryset.aggregate(**_aggregate_expressions(include_percentiles))
    )


def run_call_facets(
    queryset: QuerySet, facets_cache_key: str | None = None
) -> dict[str, list[dict[str, Any]]]:
    if facets_cache_key and (cached := cache.get(facets_cache_key)) is not None:
        return cached
    facets = {}
    for name, field in (("goal", "result_goal"), ("status", "result_outcome")):
        facets[name] = [
            {"value": row[field], "count": row["count"]}
            for row in queryset.values(field)
            .annotate(count=Count("id"))
            .order_by("-count", field)
        ]

    sub_goals = Counter()
    metadata_rows = (
        queryset.order_by()
        .annotate(
            result_sub_goals=Coalesce(
                _json_value("call_metadata", "hosted_harness_receipt", "sub_goals"),
                _json_value("call_metadata", "sub_goals"),
                Value([], output_field=JSONField()),
                output_field=JSONField(),
            )
        )
        .values_list("result_sub_goals", flat=True)
        .iterator(chunk_size=2000)
    )
    for values in metadata_rows:
        if not isinstance(values, list):
            continue
        for value in values:
            name = value.get("name") if isinstance(value, dict) else value
            if name:
                sub_goals[str(name)] += 1
    facets["sub_goal"] = [
        {"value": value, "count": count}
        for value, count in sorted(
            sub_goals.items(), key=lambda item: (-item[1], item[0].lower())
        )
    ]
    if facets_cache_key:
        cache.set(facets_cache_key, facets, timeout=60 * 60)
    return facets


def group_run_calls(
    queryset: QuerySet,
    group_by: str | None,
    page_rows: list[dict[str, Any]],
    columns: list[dict[str, str]],
) -> list[dict[str, Any]]:
    if group_by not in GROUP_FIELDS:
        return []
    field = GROUP_FIELDS[group_by]
    expressions = _aggregate_expressions(include_percentiles=False)

    for index, column in enumerate(columns):
        eval_id = str(column["id"])
        score = _eval_score(eval_id)
        expressions[f"eval_{index}_average"] = Avg(score)
        expressions[f"eval_{index}_scored"] = Count(score)
    summaries = queryset.order_by().values(field).annotate(**expressions)
    page_ids = [str(row["id"]) for row in page_rows]
    key_by_id = {
        str(call_id): str(key)
        for call_id, key in queryset.filter(id__in=page_ids).values_list("id", field)
    }
    ids_by_key: dict[str, list[str]] = {}
    for call_id in page_ids:
        if call_id in key_by_id:
            ids_by_key.setdefault(key_by_id[call_id], []).append(call_id)
    labels = {
        "passed": "Passed",
        "failed": "Failed",
        "error": "Errored",
        "inconclusive": "Not measured",
    }
    # Coverage levels read as the Scenarios tab names them ("none" is "No attack").
    coverage_axis = group_by in {"attack", "task"}
    groups = []
    for values in summaries:
        key = str(values[field])
        if key not in ids_by_key:
            continue
        summary = _summary_from_values(values)
        evaluation_aggregates = {}
        for index, column in enumerate(columns):
            scored = values.get(f"eval_{index}_scored") or 0
            average = values.get(f"eval_{index}_average")
            evaluation_aggregates[str(column["id"])] = {
                "scored": scored,
                "score_sum": average * scored if average is not None else 0,
            }
        groups.append(
            {
                "key": key,
                "label": (
                    level_label(key)
                    if coverage_axis and key != UNGROUPED
                    else labels.get(key, key)
                ),
                "result_ids": ids_by_key[key],
                "aggregates": {
                    "csat": values.get("csat_average"),
                    "turns": values.get("turns_average"),
                    "latency_ms": summary["latency"]["average"],
                    "tokens": summary["tokens"]["total_value"],
                    "evaluations": evaluation_aggregates,
                },
                **summary,
            }
        )
    return sorted(groups, key=lambda group: (-group["total"], group["label"].lower()))


def _breakdown_run_calls(queryset: QuerySet, field: str) -> list[dict[str, Any]]:
    orm_fields = {
        "goal": "result_goal",
        "provider": "result_provider",
        "modality": "simulation_call_type",
    }
    orm_field = orm_fields[field]
    rows = (
        queryset.order_by()
        .values(orm_field)
        .annotate(**_aggregate_expressions(include_percentiles=False))
    )
    result = [
        {field: str(values[orm_field] or "Unknown"), **_summary_from_values(values)}
        for values in rows
    ]
    return sorted(result, key=lambda row: (-row["total"], row[field].lower()))


def build_run_analytics(execution: TestExecution) -> dict[str, Any]:
    from simulate.services.run_dashboard_v3 import build_run_dashboard

    queryset = run_calls_queryset(execution)
    summary = summarize_run_calls(queryset)
    configs, _ = build_evaluation_catalog(execution)

    risk = _breakdown_run_calls(queryset, "goal")
    risk.sort(
        key=lambda item: (
            item["pass_rate"] is None,
            item["pass_rate"] or 0,
            -item["total"],
        )
    )
    turn_rows = (
        queryset.filter(result_turn_count__isnull=False)
        .order_by()
        .values("result_turn_count", "result_outcome")
        .annotate(count=Count("id"))
        .order_by("result_turn_count")
    )
    turn_distribution: dict[int, dict[str, Any]] = {}
    for row in turn_rows:
        turn = int(row["result_turn_count"])
        bucket = turn_distribution.setdefault(
            turn, {"turn_count": turn, **dict.fromkeys(OUTCOMES, 0)}
        )
        bucket[row["result_outcome"]] = row["count"]

    evaluation_expressions = {}
    for config in configs:
        eval_id = config["id"]
        passed_q = _eval_verdict_q(
            {eval_id}, [True, "true", "pass", "passed", "success", "successful"]
        )
        failed_q = _eval_verdict_q(
            {eval_id},
            [False, "false", "fail", "failed", "failure", "unsuccessful"],
        )
        evaluation_expressions[f"passed_{eval_id}"] = Count("id", filter=passed_q)
        evaluation_expressions[f"failed_{eval_id}"] = Count("id", filter=failed_q)
        evaluation_expressions[f"present_{eval_id}"] = Count(
            "id", filter=Q(eval_outputs__has_key=eval_id)
        )
        output_type_key = f"eval_outputs__{eval_id}__output_type"
        evaluation_expressions[f"score_{eval_id}"] = Avg(
            Case(
                When(
                    **{
                        output_type_key: "score",
                        "then": _safe_json_float("eval_outputs", eval_id, "output"),
                    }
                ),
                default=None,
                output_field=FloatField(),
            )
        )
    evaluation_values = (
        queryset.aggregate(**evaluation_expressions) if evaluation_expressions else {}
    )
    evaluations = []
    for config in configs:
        eval_id = config["id"]
        passed = evaluation_values.get(f"passed_{eval_id}") or 0
        failed = evaluation_values.get(f"failed_{eval_id}") or 0
        measured = passed + failed
        present = evaluation_values.get(f"present_{eval_id}") or 0
        evaluations.append(
            {
                "id": eval_id,
                "name": config["name"],
                "passed": passed,
                "failed": failed,
                "measured": measured,
                "missing": summary["total"] - present,
                "pass_rate": round(passed / measured * 100, 2) if measured else None,
                "average_score": evaluation_values.get(f"score_{eval_id}"),
            }
        )

    failure_rows = (
        queryset.filter(result_outcome__in=["failed", "error"])
        .annotate(
            failure_reason=Case(
                When(result_outcome="failed", then=Value("Evaluation failure")),
                default=Coalesce(
                    NullIf(F("ended_reason"), Value("")),
                    NullIf(F("error_message"), Value("")),
                    F("status"),
                    Value("Unknown"),
                    output_field=CharField(),
                ),
                output_field=CharField(),
            )
        )
        .order_by()
        .values("failure_reason")
        .annotate(failures=Count("id"))
    )
    failure_counts = Counter(
        {str(row["failure_reason"]): row["failures"] for row in failure_rows}
    )
    failure_total = sum(failure_counts.values())

    cost_fields = {
        "stt": "stt_cost_cents",
        "llm": "llm_cost_cents",
        "tts": "tts_cost_cents",
        "storage": "storage_cost_cents",
        "customer": "customer_cost_cents",
    }
    cost_expressions = {}
    for key, field in cost_fields.items():
        cost_expressions[f"{key}_total"] = Sum(field)
        cost_expressions[f"{key}_measured"] = Count(field)
    cost_values = queryset.aggregate(**cost_expressions)
    cost_breakdown = {
        key: {
            "total": cost_values[f"{key}_total"],
            "measured": cost_values[f"{key}_measured"],
            "calls": summary["total"],
        }
        for key in cost_fields
    }

    siblings = list(
        TestExecution.objects.filter(
            run_test=execution.run_test,
            status=TestExecution.ExecutionStatus.COMPLETED,
        )
        .order_by("-created_at")
        .values("id", "started_at")[:20]
    )
    trend_queryset = run_calls_queryset(
        execution, [sibling["id"] for sibling in siblings]
    )
    trend_rows = (
        trend_queryset.order_by()
        .values("test_execution_id")
        .annotate(**_aggregate_expressions(include_percentiles=False))
    )
    trend_summaries = {
        row["test_execution_id"]: _summary_from_values(row) for row in trend_rows
    }
    trends = [
        {
            "execution_id": str(sibling["id"]),
            "started_at": sibling["started_at"],
            **trend_summaries.get(sibling["id"], _summary_from_values({})),
        }
        for sibling in reversed(siblings)
    ]

    return {
        "execution": {
            "id": str(execution.id),
            "name": execution.run_test.name,
            "started_at": execution.started_at,
            "completed_at": execution.completed_at,
        },
        "summary": {**summary, "evaluators": len(configs)},
        "scenario_risk": risk,
        "turn_distribution": list(turn_distribution.values()),
        "evaluations": sorted(
            evaluations,
            key=lambda row: (
                row["pass_rate"] is None,
                row["pass_rate"] or 0,
                row["name"],
            ),
        ),
        "dashboard": build_run_dashboard(queryset, summary, evaluations, risk),
        "failure_breakdown": [
            {
                "reason": reason,
                "failures": count,
                "share": round(count / failure_total * 100, 2) if failure_total else 0,
            }
            for reason, count in failure_counts.most_common()
        ],
        "distributions": {
            "duration_seconds": summary["duration"],
            "latency_ms": summary["latency"],
            "tokens": summary["tokens"],
            "cost_cents": summary["cost_cents"],
        },
        "cost_breakdown_cents": cost_breakdown,
        "provider_breakdown": _breakdown_run_calls(queryset, "provider"),
        "modality_breakdown": _breakdown_run_calls(queryset, "modality"),
        "trends": trends,
    }
