"""Database queries for the v3 simulation run-results surface."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from typing import Any

from django.core.cache import cache
from django.db.models import (
    Avg,
    BooleanField,
    Case,
    CharField,
    Count,
    Expression,
    F,
    FloatField,
    Func,
    IntegerField,
    JSONField,
    Max,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Sum,
    TextField,
    Value,
    When,
)
from django.db.models.fields.json import HasKey, KeyTextTransform, KeyTransform
from django.db.models.functions import (
    Cast,
    Coalesce,
    Greatest,
    Least,
    Lower,
    NullIf,
    Trim,
)
from django.db.models.lookups import (
    Exact,
    GreaterThan,
    GreaterThanOrEqual,
    In,
    LessThan,
    LessThanOrEqual,
)

from model_hub.models.develop_dataset import Cell
from simulate.models import CallExecution, SimulateEvalConfig, TestExecution
from simulate.models.hosted_harness import HostedHarnessScenario
from simulate.semantics import SupportedProviders
from simulate.services.harness_scenarios import GROUP_BY as SCENARIO_GROUP_BY
from simulate.services.harness_scenarios import level_label
from simulate.services.run_reliability_v3 import build_reliability
from simulate.services.run_results_v3 import build_evaluation_catalog
from simulate.services.run_results_v3_expressions import (
    MatchingListGroups,
    NormalizedEvalNumber,
    PercentileCont,
    _json_text,
    _safe_json_float,
    project_annotation,
)
from simulate.services.run_results_v3_scoring import (
    EvalScoringSpec,
    resolve_eval_scoring_spec,
    warn_invalid_eval_threshold,
)

OUTCOMES = ("passed", "failed", "error", "inconclusive")
# Outcomes that judge the agent. Errored and inconclusive calls never ran to a verdict,
# so they are reported as run health rather than counted against the agent.
EVALUATED_OUTCOMES = ("passed", "failed")
# Fewer evaluated calls than this and a slice's pass rate is too noisy to rank.
MIN_RANKED_SLICE = 3
# The Scenarios tab's axes, plus the run's own outcome.
GROUP_FIELDS = {
    **{axis: f"result_{axis}" for axis in SCENARIO_GROUP_BY},
    "status": "result_outcome",
}
UNGROUPED = "Ungrouped"
LIST_AXES = frozenset({"sub_goal"})


def _authored_level(field: str):
    """One authored-scenario value for grouping, read from its JSON document."""
    head, _, tail = field.partition(".")
    return KeyTextTransform(tail, head)


def _group_q(group_by: str, key: str) -> Q:
    field = GROUP_FIELDS[group_by]
    if group_by not in LIST_AXES:
        return Q(**{field: key})
    if key == UNGROUPED:
        return Q(**{field: []})
    return Q(**{f"{field}__contains": [key]})


def _group_keys(group_by: str, value: Any) -> list[str]:
    if group_by not in LIST_AXES:
        return [str(value)]
    held = value if isinstance(value, list) else []
    keys = [str(one).strip() for one in held if str(one or "").strip()]
    return list(dict.fromkeys(keys)) or [UNGROUPED]


def _json_value(field: str | Expression, *keys: str):
    expression = F(field) if isinstance(field, str) else field
    for key in keys:
        expression = KeyTransform(key, expression)
    return expression


# The rows show the authored scenario's sub-goals when the call carries none of
# its own (see ``build_call_rows``), so the filter and facets read the same way.
_NO_RESULT_SUB_GOALS = (
    Q(call_metadata__hosted_harness_receipt__sub_goals__isnull=True)
    | Q(call_metadata__hosted_harness_receipt__sub_goals=[])
) & (Q(call_metadata__sub_goals__isnull=True) | Q(call_metadata__sub_goals=[]))


def _authored_sub_goal_q(value: Any) -> Q:
    """Match a call whose linked scenario lists ``value`` as a sub-goal.

    A trial's source scenario wins over a registration, as in the rows.
    """
    execution = "hosted_harness_execution__source_scenario__sub_goals"
    registration = "hosted_registration__sub_goals"
    return (
        Q(**{f"{execution}__contains": [value]})
        | Q(**{f"{execution}__contains": [{"name": value}]})
        | (
            Q(hosted_harness_execution__isnull=True)
            & (
                Q(**{f"{registration}__contains": [value]})
                | Q(**{f"{registration}__contains": [{"name": value}]})
            )
        )
    )


def _authored_sub_goals_expression():
    empty = Value([], output_field=JSONField())
    return Coalesce(
        NullIf(F("hosted_harness_execution__source_scenario__sub_goals"), empty),
        NullIf(F("hosted_registration__sub_goals"), empty),
        output_field=JSONField(),
    )


def _eval_verdict_q(eval_ids: set[str], values: list[Any]) -> Q:
    verdict = Q(pk__in=[])
    for eval_id in eval_ids:
        output = Lower(Trim(_json_text("eval_outputs", eval_id, "output")))
        verdict |= Q(
            In(output, [str(value).lower() for value in values])
        ) & _eval_measured_q(eval_id)
    return verdict


def _eval_measured_q(eval_id: str, field: str | Expression = "eval_outputs") -> Q:
    status = Coalesce(
        _json_text(field, eval_id, "status"),
        Value(""),
        output_field=TextField(),
    )
    return Q(HasKey(_json_value(field), eval_id)) & ~Q(
        In(Lower(Trim(status)), ["pending", "skipped", "error", "failed"])
    )


def _eval_errored_q(eval_id: str) -> Q:
    status = Coalesce(
        _json_text("eval_outputs", eval_id, "status"),
        Value(""),
        output_field=TextField(),
    )
    return Q(eval_outputs__has_key=eval_id) & Q(
        In(Lower(Trim(status)), ["error", "failed"])
    )


def _choice_array_matches(
    eval_id: str, label: str, *, nested: bool, field: str | Expression = "eval_outputs"
) -> Q:
    keys = (eval_id, "output", "choices") if nested else (eval_id, "output")
    pattern = f"^[[:space:]]*{re.escape(label)}[[:space:]]*$"
    path = f'$[*] ? (@ like_regex {json.dumps(pattern)} flag "i")'
    return Q(
        Func(
            _json_value(field, *keys),
            Value(path),
            function="jsonb_path_exists",
            template="%(function)s(%(expressions)s::jsonpath)",
            output_field=BooleanField(),
        )
    )


def _expanded_eval_score(
    eval_id: str,
    spec: EvalScoringSpec | None = None,
    field: str | Expression = "eval_outputs",
):
    output = _json_value(field, eval_id, "output")
    raw_text = Coalesce(
        *(
            KeyTextTransform(key, output)
            for key in ("score", "result", "output", "choice", "value")
        ),
        KeyTextTransform("output", _json_value(field, eval_id)),
        output_field=TextField(),
    )
    normalized = Lower(Trim(raw_text))
    numeric_score = NormalizedEvalNumber(
        normalized, pass_fail=spec is not None and spec.output_type == "pass_fail"
    )
    choice_scores = (
        spec.choice_scores
        if spec is not None and spec.output_type == "deterministic"
        else {}
    )
    choice_cases = [
        When(
            Q(
                Exact(
                    Lower(Trim(_json_text(field, eval_id, "output"))),
                    Value(label),
                )
            )
            | (
                Q(
                    Exact(
                        Lower(Trim(_json_text(field, eval_id, "output", "choice"))),
                        Value(label),
                    )
                )
                & ~Q(HasKey(output, "choices"))
            ),
            then=Value(min(max(float(score), 0.0), 1.0)),
        )
        for label, score in choice_scores.items()
    ]
    choice_score = None
    if spec is not None and spec.output_type == "deterministic" and choice_scores:
        weighted = [Value(0.0)]
        count = [Value(0.0)]
        for label, weight in choice_scores.items():
            present = _choice_array_matches(
                eval_id, label, nested=False, field=field
            ) | _choice_array_matches(eval_id, label, nested=True, field=field)
            weighted.append(
                Case(
                    When(present, then=Value(weight)),
                    default=Value(0.0),
                    output_field=FloatField(),
                )
            )
            count.append(
                Case(
                    When(present, then=Value(1.0)),
                    default=Value(0.0),
                    output_field=FloatField(),
                )
            )
        weighted_sum = Func(
            *weighted,
            function="",
            template="(%(expressions)s)",
            arg_joiner=" + ",
            output_field=FloatField(),
        )
        count_sum = Func(
            *count,
            function="",
            template="(%(expressions)s)",
            arg_joiner=" + ",
            output_field=FloatField(),
        )
        choice_score = Case(
            When(
                GreaterThan(count_sum, Value(0.0)),
                then=Least(
                    Greatest(weighted_sum / NullIf(count_sum, Value(0.0)), Value(0.0)),
                    Value(1.0),
                ),
            ),
            output_field=FloatField(),
        )
    positive = ["true"]
    negative = ["false"]
    if spec is None or spec.output_type == "pass_fail":
        positive += ["pass", "yes", "success", "successful"]
        negative += ["fail", "no", "failure", "unsuccessful"]
    if spec is None:
        positive.append("passed")
        negative.append("failed")
    raw_score = Case(
        When(
            In(normalized, positive),
            then=Value(1.0),
        ),
        When(
            In(normalized, negative),
            then=Value(0.0),
        ),
        default=numeric_score,
        output_field=FloatField(),
    )
    selected_score = Case(
        *choice_cases,
        default=(
            Coalesce(choice_score, raw_score, output_field=FloatField())
            if choice_score is not None
            else raw_score
        ),
        output_field=FloatField(),
    )
    if spec is None:
        object_score = _json_value(field, eval_id, "output", "score")
        selected_score = Case(
            When(
                Exact(
                    Func(output, function="jsonb_typeof", output_field=TextField()),
                    Value("object"),
                ),
                then=Case(
                    When(
                        Exact(
                            Func(
                                object_score,
                                function="jsonb_typeof",
                                output_field=TextField(),
                            ),
                            Value("number"),
                        ),
                        then=numeric_score,
                    ),
                    output_field=FloatField(),
                ),
            ),
            default=selected_score,
            output_field=FloatField(),
        )
    return Case(
        When(
            ~_eval_measured_q(eval_id, field),
            then=Value(None, output_field=FloatField()),
        ),
        default=selected_score,
        output_field=FloatField(),
    )


class _EvalScoreExpression(Func):
    """Reuse compiled scoring across clones of a request's expression."""

    output_field = FloatField()

    def __init__(
        self,
        eval_id: str,
        output_type: str | None,
        choices: tuple[tuple[str, float], ...],
    ):
        super().__init__(F("eval_outputs"))
        self._compiled_sql = {}
        self.eval_id = eval_id
        # Threshold and reversal are applied separately by the verdict expression.
        self.spec = (
            None
            if output_type is None
            else EvalScoringSpec(output_type, 0.5, False, dict(choices))
        )

    def as_sql(self, compiler, connection, **extra_context):
        source = self.source_expressions[0]
        key = (
            self.eval_id,
            self.spec.output_type if self.spec else None,
            tuple(self.spec.choice_scores.items()) if self.spec else (),
            source,
        )
        expression_key = (connection.alias, connection.vendor, type(compiler), key)
        if expression_key in self._compiled_sql:
            sql, params = self._compiled_sql[expression_key]
            return sql, list(params)
        cache = getattr(compiler, "_simulation_score_sql", None)
        # SQL only: this cache never survives its compiler or stores query results.
        if cache is None:
            cache = compiler._simulation_score_sql = {}
        if key not in cache:
            expression = _expanded_eval_score(self.eval_id, self.spec, source)
            sql, params = compiler.compile(
                expression.resolve_expression(compiler.query)
            )
            cache[key] = (sql, tuple(params))
        sql, params = cache[key]
        self._compiled_sql[expression_key] = (sql, params)
        return sql, list(params)


def _eval_score(eval_id: str, spec: EvalScoringSpec | None = None):
    return _EvalScoreExpression(
        eval_id,
        spec.output_type if spec else None,
        tuple(spec.choice_scores.items()) if spec else (),
    )


def _configured_eval_verdict(
    eval_id: str, config: SimulateEvalConfig, spec: EvalScoringSpec | None = None
):
    if spec is None:
        spec = resolve_eval_scoring_spec(config)
    raw_score = (
        _eval_score(eval_id, spec)
        if spec.threshold is not None
        else Value(None, output_field=FloatField())
    )
    final_pass = Q(
        Exact(
            Lower(Trim(_json_text("eval_outputs", eval_id, "output"))),
            Value("passed"),
        )
    ) | Q(
        Exact(
            _json_value("eval_outputs", eval_id, "output", "failure"),
            Value(False, output_field=JSONField()),
        )
    )
    final_fail = Q(
        Exact(
            Lower(Trim(_json_text("eval_outputs", eval_id, "output"))),
            Value("failed"),
        )
    ) | Q(
        Exact(
            _json_value("eval_outputs", eval_id, "output", "failure"),
            Value(True, output_field=JSONField()),
        )
    )
    if spec.output_type != "pass_fail":
        stored_verdict = Q(
            In(
                Lower(Trim(_json_text("eval_outputs", eval_id, "output_type"))),
                ["pass/fail", "pass_fail"],
            )
        )
        final_pass &= stored_verdict
        final_fail &= stored_verdict
    measured = _eval_measured_q(eval_id)
    score = Case(
        When(~measured, then=Value(None, output_field=FloatField())),
        When(final_pass, then=Value(1.0)),
        When(final_fail, then=Value(0.0)),
        default=Value(1.0) - raw_score if spec.reverse_output else raw_score,
        output_field=FloatField(),
    )
    if spec.threshold is None:
        return score, measured & final_pass, measured & final_fail
    raw_pass = (
        GreaterThan(raw_score, Value(0.0))
        if spec.output_type == "pass_fail"
        else GreaterThanOrEqual(raw_score, Value(spec.threshold))
    )
    raw_fail = (
        LessThanOrEqual(raw_score, Value(0.0))
        if spec.output_type == "pass_fail"
        else LessThan(raw_score, Value(spec.threshold))
    )
    passed = measured & Q(
        Case(
            When(final_pass, then=Value(True)),
            When(final_fail, then=Value(False)),
            default=raw_fail if spec.reverse_output else raw_pass,
            output_field=BooleanField(),
        )
    )
    failed = measured & Q(
        Case(
            When(final_fail, then=Value(True)),
            When(final_pass, then=Value(False)),
            default=raw_pass if spec.reverse_output else raw_fail,
            output_field=BooleanField(),
        )
    )
    return score, passed, failed


class _NativeHarnessVerdict(Func):
    """Judge template-less harness checks using the call-row verdict rules."""

    output_field = BooleanField()

    def __init__(self, configured_ids: list[str], verdict: str):
        super().__init__(
            F("eval_outputs"), Value(configured_ids, output_field=JSONField())
        )
        self.verdict = verdict

    def as_sql(self, compiler, connection, **extra_context):
        source_sql, source_params = compiler.compile(self.source_expressions[0])
        ids_sql, ids_params = compiler.compile(self.source_expressions[1])
        status_sql = "lower(btrim(COALESCE(check_result.value->>'status', '')))"
        if self.verdict == "error":
            condition = f"{status_sql} IN ('error', 'failed')"
            verdict_params = []
        else:
            tokens = (
                ["pass", "passed", "true", "success", "successful"]
                if self.verdict == "passed"
                else ["fail", "failed", "false", "failure", "unsuccessful"]
            )
            condition = (
                f"{status_sql} NOT IN ('pending', 'skipped', 'error', 'failed') "
                "AND jsonb_typeof(check_result.value->'output') IN ('string', 'boolean') "
                "AND lower(btrim(check_result.value->>'output')) = ANY(%s)"
            )
            verdict_params = [tokens]
        sql = (
            "EXISTS (SELECT 1 FROM jsonb_each("
            f"CASE WHEN jsonb_typeof({source_sql}) = 'object' "
            f"THEN {source_sql} ELSE '{{}}'::jsonb END) AS check_result(key, value) "
            "WHERE jsonb_typeof(check_result.value) = 'object' "
            "AND check_result.value->>'source' = 'harness' "
            f"AND NOT ({ids_sql} ? check_result.key) AND {condition})"
        )
        return sql, [*source_params, *source_params, *ids_params, *verdict_params]


def run_calls_queryset(
    execution: TestExecution, execution_ids: list[Any] | None = None
) -> QuerySet[CallExecution]:
    live_configs = list(
        SimulateEvalConfig.objects.filter(
            run_test=execution.run_test, deleted=False
        ).select_related("eval_template")
    )
    failed_eval = Q(pk__in=[])
    passed_eval = Q(pk__in=[])
    errored_eval = Q(pk__in=[])
    for config in live_configs:
        eval_id = str(config.id)
        spec = resolve_eval_scoring_spec(config)
        warn_invalid_eval_threshold(config, spec)
        _, passed_q, failed_q = _configured_eval_verdict(eval_id, config, spec)
        failed_eval |= failed_q
        passed_eval |= passed_q
        errored_eval |= _eval_errored_q(eval_id)

    configured_ids = [str(config.id) for config in live_configs]
    failed_eval |= Q(_NativeHarnessVerdict(configured_ids, "failed"))
    passed_eval |= Q(_NativeHarnessVerdict(configured_ids, "passed"))
    errored_eval |= Q(_NativeHarnessVerdict(configured_ids, "error"))

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
    own_run = Q(job__test_execution_id=OuterRef("test_execution_id"))
    own_environment = Q(
        job__simulation_runs__test_execution_id=OuterRef("test_execution_id")
    )
    authored = (
        HostedHarnessScenario.all_objects.filter(
            Q(call_execution_id=OuterRef("pk"))
            | Q(own_run | own_environment, scenario_key=OuterRef("result_scenario_key"))
        )
        .annotate(
            match_rank=Case(
                When(call_execution_id=OuterRef("pk"), then=Value(0)),
                When(own_run, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            )
        )
        .order_by("match_rank", "-created_at")
    )
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
            if axis != "goal" and axis not in LIST_AXES
        },
        **{
            GROUP_FIELDS[axis]: Coalesce(
                Subquery(
                    authored.values(SCENARIO_GROUP_BY[axis])[:1],
                    output_field=JSONField(),
                ),
                Value([], output_field=JSONField()),
                output_field=JSONField(),
            )
            for axis in LIST_AXES
        },
        result_eval_outcome=Case(
            When(failed_eval, then=Value("failed")),
            When(errored_eval, then=Value("inconclusive")),
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
                    "error",
                    "errored",
                    "cancelled",
                    "canceled",
                ],
                then=Value("error"),
            ),
            When(status__in=["failed", "cancelled"], then=Value("error")),
            When(~Q(status="completed"), then=Value("inconclusive")),
            When(
                call_metadata__harness_outcome_status__in=[
                    "failed",
                    "fail",
                    "failure",
                ],
                then=Value("failed"),
            ),
            When(failed_eval, then=Value("failed")),
            When(errored_eval, then=Value("inconclusive")),
            When(
                call_metadata__harness_outcome_status__in=[
                    "inconclusive",
                    "unknown",
                    "skipped",
                ],
                then=Value("inconclusive"),
            ),
            When(
                call_metadata__harness_outcome_status__in=[
                    "passed",
                    "pass",
                    "success",
                    "successful",
                ],
                then=Value("passed"),
            ),
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
        result_cost_cents=Cast("customer_cost_cents", FloatField()),
        result_provider=Case(
            *provider_cases,
            default=Coalesce(
                F("test_execution__agent_definition__provider"), Value("Unknown")
            ),
            output_field=CharField(),
        ),
        result_scenario_key=Coalesce(
            _json_text("call_metadata", "harness_scenario_key"),
            _json_text("call_metadata", "hosted_harness_receipt", "scenario_key"),
            Cast("row_id", TextField()),
            Cast("scenario_id", TextField()),
            output_field=CharField(),
        ),
        result_scenario=Coalesce(
            NullIf(F("hosted_registration__name"), Value("")),
            _json_text("call_metadata", "harness_scenario_key"),
            _json_text("call_metadata", "hosted_harness_receipt", "scenario_key"),
            NullIf(F("scenario__name"), Value("")),
            Cast("row_id", TextField()),
            output_field=CharField(),
        ),
        result_csat=Case(
            When(
                Q(
                    GreaterThanOrEqual(
                        _safe_json_float("conversation_metrics_data", "csat_score"),
                        Value(0.0),
                    )
                )
                & Q(
                    LessThanOrEqual(
                        _safe_json_float("conversation_metrics_data", "csat_score"),
                        Value(10.0),
                    )
                ),
                then=_safe_json_float("conversation_metrics_data", "csat_score"),
            ),
            default=None,
            output_field=FloatField(),
        ),
    )
    return project_annotation(queryset, "result_outcome").select_related(
        "scenario", "test_execution__agent_definition"
    )


def run_call_rows_queryset(queryset: QuerySet) -> QuerySet:
    """Keep SQL verdicts for filtering; call-row serialization judges each row."""
    return queryset.alias(
        result_outcome=F("result_outcome"),
        result_eval_outcome=F("result_eval_outcome"),
    )


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

    if values := filters.get("call_execution_id"):
        queryset = queryset.filter(id__in=values)
    if values := filters.get("goal"):
        queryset = queryset.filter(result_goal__in=values)
    if values := filters.get("status"):
        queryset = queryset.filter(
            result_outcome__in=[str(value).lower() for value in values]
        )
    if values := filters.get("goal_outcome"):
        from simulate.services.run_dashboard_v3 import annotate_goal_outcome

        queryset = annotate_goal_outcome(queryset).filter(dashboard_goal__in=values)
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
            sub_goal_query |= _NO_RESULT_SUB_GOALS & _authored_sub_goal_q(value)
        queryset = queryset.filter(sub_goal_query)

    group_by = query.get("group_by")
    group_key = query.get("group_key")
    if group_by in GROUP_FIELDS and group_key is not None:
        queryset = queryset.filter(_group_q(group_by, group_key))

    ordering = str(query.get("ordering") or "-started_at")
    descending = ordering.startswith("-")
    requested = ordering.lstrip("-")
    fields = {
        "started_at": "started_at",
        "duration_seconds": "duration_seconds",
        "latency_ms": "result_latency_ms",
        "turn_count": "result_turn_count",
        "tokens": "result_tokens",
        "cost_cents": "result_cost_cents",
        "scenario": "scenario__name",
        "goal": "result_goal",
        "outcome": "result_outcome",
    }
    field = fields.get(requested, "started_at")
    return queryset.order_by(f"-{field}" if descending else field, "id")


def _aggregate_expressions(include_percentiles: bool = True) -> dict[str, Any]:
    expressions: dict[str, Any] = {
        "total": Count("id"),
        "measured": Count("id", filter=Q(result_outcome__in=EVALUATED_OUTCOMES)),
        "tokens_total_value": Sum("result_tokens"),
        "cost_cents_total_value": Sum("result_cost_cents"),
        "csat_average": Avg("result_csat"),
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
        "cost_cents": "result_cost_cents",
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
                NullIf(
                    _json_value("call_metadata", "hosted_harness_receipt", "sub_goals"),
                    Value([], output_field=JSONField()),
                ),
                NullIf(
                    _json_value("call_metadata", "sub_goals"),
                    Value([], output_field=JSONField()),
                ),
                _authored_sub_goals_expression(),
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
    *,
    execution: TestExecution,
) -> list[dict[str, Any]]:
    if group_by not in GROUP_FIELDS or not page_rows:
        return []
    field = GROUP_FIELDS[group_by]
    expressions = _aggregate_expressions(include_percentiles=False)
    expressions["stop_latency_average"] = Avg("avg_stop_time_after_interruption_ms")
    expressions["ai_interruptions_average"] = Avg("ai_interruption_count")
    scoring_configs = (
        {
            str(config.id): config
            for config in SimulateEvalConfig.objects.filter(
                run_test=execution.run_test, deleted=False
            ).select_related("eval_template")
        }
        if columns
        else {}
    )

    for index, column in enumerate(columns):
        eval_id = str(column["id"])
        config = scoring_configs.get(eval_id)
        score = (
            _configured_eval_verdict(eval_id, config)[0]
            if config is not None
            else _eval_score(eval_id)
        )
        expressions[f"eval_{index}_average"] = Avg(score)
        expressions[f"eval_{index}_scored"] = Count(score)
    page_ids = [str(row["id"]) for row in page_rows]
    keys_by_id = {
        str(call_id): _group_keys(group_by, value)
        for call_id, value in queryset.filter(id__in=page_ids).values_list("id", field)
    }
    ids_by_key: dict[str, list[str]] = {}
    for call_id in page_ids:
        for key in keys_by_id.get(call_id, []):
            ids_by_key.setdefault(key, []).append(call_id)
    if group_by in LIST_AXES:
        grouped = queryset.annotate(
            result_group_key=MatchingListGroups(F(field), list(ids_by_key), UNGROUPED)
        )
        # Keep empty summaries for normalized page keys with no exact JSON match.
        found = {
            row["result_group_key"]: row
            for row in grouped.order_by()
            .values("result_group_key")
            .annotate(**expressions)
        }
        summaries = [{field: key, **found.get(key, {})} for key in ids_by_key]
    else:
        visible = Q(**{f"{field}__in": list(ids_by_key)})
        if str(None) in ids_by_key:
            visible |= Q(**{f"{field}__isnull": True})
        summaries = (
            queryset.filter(visible).order_by().values(field).annotate(**expressions)
        )
    labels = {
        "passed": "Passed",
        "failed": "Failed",
        "error": "Errored",
        "inconclusive": "Not measured",
    }
    # Levels read as the Scenarios tab names them ("none" is "No attack").
    labelled_axis = group_by in {"sub_goal", "attack", "task"}
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
                    if labelled_axis and key != UNGROUPED
                    else labels.get(key, key)
                ),
                "result_ids": ids_by_key[key],
                "aggregates": {
                    "csat": values.get("csat_average"),
                    "turns": values.get("turns_average"),
                    "latency_ms": summary["latency"]["average"],
                    "avg_stop_time_after_interruption": values.get("stop_latency_average"),
                    "ai_interruptions": values.get("ai_interruptions_average"),
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
        "scenario": "result_scenario",
        "provider": "result_provider",
        "modality": "simulation_call_type",
    }
    orm_field = orm_fields[field]
    if field == "scenario":
        rows = (
            queryset.order_by()
            .values("result_scenario_key")
            .annotate(
                scenario_label=Max("result_scenario"),
                **_aggregate_expressions(include_percentiles=False),
            )
        )
        result = [
            {
                "scenario": str(values["scenario_label"] or "Unknown"),
                "scenario_key": str(values["result_scenario_key"]),
                **_summary_from_values(values),
            }
            for values in rows
        ]
    else:
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


def build_run_comparison(
    execution: TestExecution, queryset: QuerySet
) -> dict[str, Any]:
    """Compare scenario verdicts with the immediately preceding completed run.

    Only scenarios evaluated in both runs participate. A scenario passes when every
    evaluated trial passed; any evaluated failure makes it failing. Infrastructure
    errors and unevaluated trials do not invent a verdict.
    """

    def verdicts(calls: QuerySet) -> tuple[dict[str, bool], dict[str, str]]:
        counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        labels: dict[str, str] = {}
        for scenario_key, scenario_label, outcome in (
            calls.order_by()
            .values_list("result_scenario_key", "result_scenario", "result_outcome")
            .iterator(chunk_size=2000)
        ):
            key = str(scenario_key)
            labels[key] = str(scenario_label or key)
            if outcome == "passed":
                counts[key][0] += 1
            elif outcome == "failed":
                counts[key][1] += 1
        return (
            {
                scenario: failed == 0
                for scenario, (passed, failed) in counts.items()
                if passed or failed
            },
            labels,
        )

    previous = (
        TestExecution.objects.filter(
            run_test=execution.run_test,
            status=TestExecution.ExecutionStatus.COMPLETED,
            created_at__lt=execution.created_at,
        )
        .order_by("-created_at")
        .first()
    )
    if previous is None:
        return {
            "available": False,
            "previous_execution_id": None,
            "shared_scenarios": 0,
            "newly_passing": [],
            "newly_failing": [],
        }
    current_verdicts, current_labels = verdicts(queryset)
    previous_verdicts, _ = verdicts(run_calls_queryset(previous))
    shared = sorted(current_verdicts.keys() & previous_verdicts.keys())
    return {
        "available": True,
        "previous_execution_id": str(previous.id),
        "shared_scenarios": len(shared),
        "newly_passing": [
            current_labels[scenario]
            for scenario in shared
            if current_verdicts[scenario] and not previous_verdicts[scenario]
        ],
        "newly_failing": [
            current_labels[scenario]
            for scenario in shared
            if not current_verdicts[scenario] and previous_verdicts[scenario]
        ],
    }


def build_run_analytics(execution: TestExecution) -> dict[str, Any]:
    from simulate.services.run_dashboard_v3 import build_run_dashboard

    queryset = run_calls_queryset(execution)
    configs, _ = build_evaluation_catalog(execution)
    scoring_configs = {
        str(config.id): config
        for config in SimulateEvalConfig.objects.filter(
            run_test=execution.run_test, deleted=False
        ).select_related("eval_template")
    }

    risk = _breakdown_run_calls(queryset, "scenario")
    # Weakest first; slices with too few evaluated calls to rank go last.
    risk.sort(
        key=lambda item: (
            item["measured"] < MIN_RANKED_SLICE,
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
        scoring_config = scoring_configs.get(eval_id)
        if scoring_config is None:
            score = _eval_score(eval_id)
            passed_q = _eval_verdict_q(
                {eval_id}, [True, "true", "pass", "passed", "success", "successful"]
            )
            failed_q = _eval_verdict_q(
                {eval_id},
                [False, "false", "fail", "failed", "failure", "unsuccessful"],
            )
        else:
            score, passed_q, failed_q = _configured_eval_verdict(
                eval_id, scoring_config
            )
        evaluation_expressions[f"passed_{eval_id}"] = Count("id", filter=passed_q)
        evaluation_expressions[f"failed_{eval_id}"] = Count("id", filter=failed_q)
        evaluation_expressions[f"present_{eval_id}"] = Count(
            "id", filter=Q(eval_outputs__has_key=eval_id)
        )
        evaluation_expressions[f"errored_{eval_id}"] = Count(
            "id", filter=_eval_errored_q(eval_id)
        )
        evaluation_expressions[f"score_{eval_id}"] = Avg(score)
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
    evaluation_values = queryset.aggregate(
        **_aggregate_expressions(), **evaluation_expressions, **cost_expressions
    )
    summary = _summary_from_values(evaluation_values)
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
                # The evaluator ran but could not produce a verdict; not a fail.
                "errored": evaluation_values.get(f"errored_{eval_id}") or 0,
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

    cost_breakdown = {
        key: {
            "total": evaluation_values[f"{key}_total"],
            "measured": evaluation_values[f"{key}_measured"],
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
    reliability = build_reliability(queryset, execution.trials)
    comparison = build_run_comparison(execution, queryset)
    return {
        "execution": {
            "id": str(execution.id),
            "name": execution.run_test.name,
            "started_at": execution.started_at,
            "completed_at": execution.completed_at,
        },
        "summary": {**summary, "evaluators": len(configs)},
        "scenario_risk": risk,
        "reliability": reliability,
        "turn_distribution": list(turn_distribution.values()),
        "evaluations": sorted(
            evaluations,
            key=lambda row: (
                row["pass_rate"] is None,
                row["pass_rate"] or 0,
                row["name"],
            ),
        ),
        "dashboard": build_run_dashboard(
            queryset, summary, evaluations, risk, reliability, comparison
        ),
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
