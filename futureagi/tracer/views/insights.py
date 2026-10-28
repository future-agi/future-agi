"""
Insights: LangSmith-Insights-style reports over traces, sessions and users.

GET    /tracer/insights/configs/                saved reports of a project
GET    /tracer/insights/configs/<id>/           one report: config and its runs
PATCH  /tracer/insights/configs/<id>/           edit a report, or reset its taxonomy
DELETE /tracer/insights/configs/<id>/           delete a report
POST   /tracer/insights/draft/                  guided setup from a goal + examples
POST   /tracer/insights/preview/                what the extractor reads from 3 units
POST   /tracer/insights/estimate/               unit counts and action credit costs
POST   /tracer/insights/runs/                   start a run (new or saved report)
GET    /tracer/insights/runs/<id>/              run status and results
GET    /tracer/insights/runs/<id>/units/        analysed units, by category
GET    /tracer/insights/runs/<id>/media/        an image a unit's evidence cites
POST   /tracer/insights/runs/<id>/stop/         stop a run in flight
POST   /tracer/insights/runs/<id>/ask/          a question about a run's report

A run reads a sample of a scope: the rows of a Traces, Sessions or Users view
(its filters and window), which the server resolves itself like Instant eval
does, or rows handed over by id (a selection).
"""

from datetime import timedelta

import structlog
from django.core.cache import cache
from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone
from drf_yasg import openapi
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.authentication import workspace_read_only
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer, EmptyRequestSerializer
from tfc.utils.general_methods import GeneralMethods
from tracer.models.insights import (
    InsightConfig,
    InsightLens,
    InsightRun,
    InsightUnit,
    InsightUnitType,
)
from tracer.models.project import Project
from tracer.serializers.filters import SessionFilterListField
from tracer.services import instant_eval_rows as rows_service
from tracer.services import instant_eval_users as users_service
from tracer.services.insight_lost_runs import settle_lost_runs
from tracer.services.user_filter_capabilities import (
    validate_users_filter_capabilities,
)
from tracer.views._license import InsightsLicenseRequired

logger = structlog.get_logger(__name__)

ERROR_RESPONSES = {
    400: ApiErrorResponseSerializer,
    402: ApiErrorResponseSerializer,
    403: ApiErrorResponseSerializer,
    404: ApiErrorResponseSerializer,
    429: ApiErrorResponseSerializer,
    500: ApiErrorResponseSerializer,
    503: ApiErrorResponseSerializer,
}

RUNS_PER_HOUR = 30
# A run older than this is no longer in flight: its workflow is started with
# a limit no longer than this (tfc/temporal/insights/client.py RUN_TIMEOUT).
ACTIVE_RUN_WINDOW = timedelta(hours=2)
DRAFTS_PER_HOUR = 60
MAX_POPULATION = 20000
DEFAULT_SAMPLE = 300
MIN_SAMPLE = 10
MAX_SAMPLE = 1000
# Units an estimate names from the start of a run's sample.
ESTIMATE_SAMPLE_IDS = 20
UNITS_PAGE = 50
MAX_CONTENT_UNITS = 20
ASKS_PER_HOUR = 120
# Each cited image shown rebuilds its unit from ClickHouse (the browser keeps
# it an hour): a bound on what one organization can make that cost.
MEDIA_VIEWS_PER_HOUR = 600
# Units of the last reports whose measured size sizes a new estimate.
TYPICAL_UNITS = 200


# ─── Request serializers ─────────────────────────────────────────────────────
class InsightAttributeSerializer(serializers.Serializer):
    key = serializers.CharField(max_length=60)
    type = serializers.ChoiceField(choices=["boolean", "number", "string"])
    description = serializers.CharField(
        max_length=300, required=False, allow_blank=True
    )
    filter_by = serializers.BooleanField(required=False, default=False)


class InsightConfigInputSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    unit = serializers.ChoiceField(choices=InsightUnitType.choices)
    lens = serializers.ChoiceField(choices=InsightLens.choices)
    question = serializers.CharField(max_length=500)
    agent_description = serializers.CharField(
        max_length=500, required=False, allow_blank=True
    )
    instructions = serializers.CharField(
        max_length=1000, required=False, allow_blank=True
    )
    attributes = InsightAttributeSerializer(many=True, required=False)
    sample_size = serializers.IntegerField(min_value=10, max_value=1000, required=False)


class InsightPopulationItemSerializer(serializers.Serializer):
    id = serializers.CharField(max_length=255)
    time = serializers.CharField(max_length=64, required=False, allow_blank=True)
    user_id = serializers.CharField(max_length=255, required=False, allow_blank=True)


# Observe's date options, and two of Insights' own: "24h" (the past 24 hours,
# from before Insights used Observe's options) and "fixed" (rows handed over by
# id, which can't be collected again from filters).
SCOPE_PRESETS = frozenset(
    {"Today", "Yesterday", "7D", "30D", "3M", "6M", "12M", "Custom", "24h", "fixed"}
)
FIXED_PRESET = "fixed"
# Insights' earlier options, in Observe's terms.
LEGACY_PRESETS = {"7d": "7D", "30d": "30D"}


def _window_item(window) -> dict:
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": list(window),
        },
    }


def normalize_scope(scope) -> dict:
    """A scope with its preset in Observe's terms."""
    scope = dict(scope or {})
    preset = scope.get("preset")
    if preset in LEGACY_PRESETS:
        scope["preset"] = LEGACY_PRESETS[preset]
    return scope


class InsightScopeSerializer(serializers.Serializer):
    filters = serializers.ListField(child=serializers.JSONField(), required=False)
    label = serializers.CharField(max_length=300, required=False, allow_blank=True)
    # A preset window is re-anchored to now on rerun; a custom one stays.
    preset = serializers.CharField(max_length=16, required=False, allow_blank=True)
    window = serializers.ListField(
        child=serializers.CharField(max_length=64), required=False, max_length=2
    )
    # The Users view's search text, for a report on users.
    search = serializers.CharField(max_length=200, required=False, allow_blank=True)

    def validate_preset(self, value):
        value = LEGACY_PRESETS.get(value, value)
        if value and value not in SCOPE_PRESETS:
            raise serializers.ValidationError(f"“{value}” isn't a date option.")
        return value

    def validate(self, attrs):
        # A window the lists would refuse is refused, instead of being read
        # as the last 30 days: the scope's own window and any date filter.
        filters = [
            item for item in attrs.get("filters") or [] if isinstance(item, dict)
        ]
        window = attrs.get("window") or []
        if len(window) == 2:
            filters.append(_window_item(window))
        if problem := rows_service.window_problem(filters):
            raise serializers.ValidationError(problem)
        return attrs


class InsightRunCreateSerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    config_id = serializers.UUIDField(required=False)
    config = InsightConfigInputSerializer(required=False)
    scope = InsightScopeSerializer(required=False)
    # Rows handed over by id (a selection). Without them the server reads a
    # sample of the scope's filters and window.
    population = InsightPopulationItemSerializer(
        many=True, min_length=1, max_length=MAX_POPULATION, required=False
    )
    sample_size = serializers.IntegerField(min_value=10, max_value=1000, required=False)
    # Forget the saved report's categories so this run discovers afresh.
    # Applied only once the run has started, so a refused run (credits, rate
    # limit, a bad range) or one that couldn't start leaves the saved
    # categories as they were.
    reset_taxonomy = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        if not attrs.get("config_id") and not attrs.get("config"):
            raise serializers.ValidationError("Provide config_id or config.")
        if not attrs.get("population"):
            if (attrs.get("scope") or {}).get("preset") == FIXED_PRESET:
                raise serializers.ValidationError(
                    "This report reads rows handed over to it. Hand them over "
                    "again, or choose a date range to run it on."
                )
        return attrs


class InsightEstimateRequestSerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    action = serializers.ChoiceField(
        choices=["run", "ask", "draft", "preview"], required=False, default="run"
    )
    unit = serializers.ChoiceField(choices=InsightUnitType.choices, required=False)
    # A scope the server counts, or how many rows are handed over by id (a
    # selection, or a run again on the same rows), which need no count.
    scope = InsightScopeSerializer(required=False)
    population_size = serializers.IntegerField(min_value=1, required=False)
    sample_size = serializers.IntegerField(min_value=10, max_value=1000, required=False)

    def validate(self, attrs):
        if attrs["action"] != "run":
            return attrs
        if "unit" not in attrs:
            raise serializers.ValidationError({"unit": "This field is required."})
        if ("scope" in attrs) == ("population_size" in attrs):
            raise serializers.ValidationError(
                "Provide either scope or population_size."
            )
        return attrs


class InsightDraftSerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    unit = serializers.ChoiceField(choices=InsightUnitType.choices)
    lens = serializers.ChoiceField(choices=InsightLens.choices)
    goal = serializers.CharField(max_length=500, required=False, allow_blank=True)
    agent_description = serializers.CharField(
        max_length=500, required=False, allow_blank=True
    )
    sample_ids = serializers.ListField(
        child=serializers.CharField(max_length=255), min_length=1, max_length=5
    )


class InsightPreviewSerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    config = InsightConfigInputSerializer()
    unit_ids = serializers.ListField(
        child=serializers.CharField(max_length=255), min_length=1, max_length=3
    )


class InsightConfigsQuerySerializer(serializers.Serializer):
    project_id = serializers.UUIDField()


class InsightConfigPatchSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    reset_taxonomy = serializers.BooleanField(required=False, default=False)
    question = serializers.CharField(max_length=500, required=False)
    lens = serializers.ChoiceField(choices=InsightLens.choices, required=False)
    agent_description = serializers.CharField(
        max_length=500, required=False, allow_blank=True
    )
    instructions = serializers.CharField(
        max_length=1000, required=False, allow_blank=True
    )
    attributes = InsightAttributeSerializer(many=True, required=False, max_length=8)
    sample_size = serializers.IntegerField(min_value=10, max_value=1000, required=False)
    scope = InsightScopeSerializer(required=False)


class InsightUnitsQuerySerializer(serializers.Serializer):
    category_id = serializers.CharField(max_length=64, required=False, allow_blank=True)
    sub_category_id = serializers.CharField(
        max_length=64, required=False, allow_blank=True
    )
    unit_ids = serializers.CharField(max_length=4000, required=False, allow_blank=True)
    # A run reads at most MAX_SAMPLE units; no page starts past them.
    offset = serializers.IntegerField(
        min_value=0, max_value=MAX_SAMPLE, required=False, default=0
    )
    limit = serializers.IntegerField(
        min_value=1, max_value=200, required=False, default=UNITS_PAGE
    )
    # The unit's full text (what the model read), for showing its evidence in
    # place. Only for small pages.
    include_content = serializers.BooleanField(required=False, default=False)


class InsightMediaQuerySerializer(serializers.Serializer):
    unit_id = serializers.CharField(max_length=255)
    media = serializers.RegexField(r"^m\d{1,6}$")


class InsightAskMessageSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=["user", "assistant"])
    content = serializers.CharField(max_length=4000, allow_blank=True)


class InsightAskSerializer(serializers.Serializer):
    question = serializers.CharField(max_length=500)
    history = InsightAskMessageSerializer(many=True, required=False, max_length=20)


# ─── Response serializers ────────────────────────────────────────────────────
class InsightRunSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.CharField()
    stage = serializers.CharField()
    created_at = serializers.DateTimeField()
    finished_at = serializers.DateTimeField(allow_null=True)
    population_size = serializers.IntegerField()
    sample_size = serializers.IntegerField()
    analysed_count = serializers.IntegerField()
    skipped_count = serializers.IntegerField()
    taxonomy_mode = serializers.CharField()
    distribution = serializers.ListField(child=serializers.JSONField())
    error = serializers.CharField(allow_blank=True)
    error_code = serializers.CharField(allow_blank=True)
    scope = serializers.JSONField()


class InsightConfigItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    unit = serializers.CharField()
    lens = serializers.CharField()
    question = serializers.CharField()
    agent_description = serializers.CharField(allow_blank=True)
    instructions = serializers.CharField(allow_blank=True)
    attributes = serializers.ListField(child=serializers.JSONField())
    sample_size = serializers.IntegerField()
    scope = serializers.JSONField()
    has_taxonomy = serializers.BooleanField()
    taxonomy = serializers.JSONField()
    created_at = serializers.DateTimeField()
    latest_run = InsightRunSummarySerializer(allow_null=True)
    runs = InsightRunSummarySerializer(many=True, required=False)


class InsightConfigListResultSerializer(serializers.Serializer):
    configs = InsightConfigItemSerializer(many=True)


class InsightConfigListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = InsightConfigListResultSerializer()


class InsightConfigResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = InsightConfigItemSerializer()


class InsightRunDetailSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    config_id = serializers.UUIDField()
    project_id = serializers.UUIDField()
    status = serializers.CharField()
    stage = serializers.CharField()
    progress = serializers.JSONField()
    unit = serializers.CharField()
    scope = serializers.JSONField()
    settings = serializers.JSONField()
    population_size = serializers.IntegerField()
    sample_size = serializers.IntegerField()
    analysed_count = serializers.IntegerField()
    skipped_count = serializers.IntegerField()
    taxonomy_mode = serializers.CharField()
    result = serializers.JSONField()
    models_used = serializers.JSONField()
    cost_usd = serializers.FloatField()
    error = serializers.CharField(allow_blank=True)
    error_code = serializers.CharField(allow_blank=True)
    created_at = serializers.DateTimeField()
    started_at = serializers.DateTimeField(allow_null=True)
    finished_at = serializers.DateTimeField(allow_null=True)
    previous_run_id = serializers.UUIDField(allow_null=True)
    next_run_id = serializers.UUIDField(allow_null=True)


class InsightRunResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = InsightRunDetailSerializer()


class InsightRunCreatedResultSerializer(serializers.Serializer):
    run_id = serializers.UUIDField()
    config_id = serializers.UUIDField()


class InsightRunCreatedResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = InsightRunCreatedResultSerializer()


class InsightRunStopResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.CharField()
    error = serializers.CharField(allow_blank=True)
    error_code = serializers.CharField(allow_blank=True)


class InsightRunStopResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = InsightRunStopResultSerializer()


class InsightUnitItemSerializer(serializers.Serializer):
    unit_id = serializers.CharField()
    label = serializers.CharField(allow_blank=True)
    user_id = serializers.CharField(allow_blank=True)
    session_id = serializers.CharField(allow_blank=True)
    time = serializers.DateTimeField(allow_null=True)
    excerpt = serializers.CharField(allow_blank=True)
    summary = serializers.CharField(allow_blank=True)
    lens_answer = serializers.CharField(allow_blank=True)
    facets = serializers.JSONField()
    metrics = serializers.JSONField()
    category_id = serializers.CharField(allow_blank=True)
    sub_category_id = serializers.CharField(allow_blank=True)
    content = serializers.CharField(allow_blank=True, required=False)


class InsightUnitsResultSerializer(serializers.Serializer):
    units = InsightUnitItemSerializer(many=True)
    total = serializers.IntegerField()


class InsightUnitsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = InsightUnitsResultSerializer()


class InsightDraftResultSerializer(serializers.Serializer):
    name = serializers.CharField()
    question = serializers.CharField()
    agent_description = serializers.CharField(allow_blank=True)
    instructions = serializers.CharField(allow_blank=True)
    attributes = serializers.ListField(child=serializers.JSONField())
    examples_read = serializers.IntegerField()


class InsightDraftResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = InsightDraftResultSerializer()


class InsightEstimateResultSerializer(serializers.Serializer):
    population = serializers.IntegerField()
    population_is_lower_bound = serializers.BooleanField()
    # The units a run reads: the sample size, or every unit when fewer.
    sample_size = serializers.IntegerField()
    # The absolute window the scope reads: {"from", "to"}; null for rows
    # handed over by id.
    window = serializers.JSONField(allow_null=True)
    # The first units a run reads, in its sample's order (up to 20), for
    # drafting and previewing the report.
    sample_ids = serializers.ListField(child=serializers.CharField())
    # About what the report costs in AI credits (it is charged its actual
    # model cost once written), and the organization's credit cap against it.
    # Null where reports aren't billed (off Cloud).
    credits_estimate = serializers.FloatField(allow_null=True)
    credit_limited = serializers.BooleanField()
    # A failed check needs retrying, not a change to the billing plan.
    credit_check_unavailable = serializers.BooleanField(required=False)
    # Credits left this month, when the cap is what refuses the report.
    credits_left = serializers.FloatField(allow_null=True)
    # The largest sample (10 or more) the credits left pay for.
    credit_max_sample = serializers.IntegerField(allow_null=True)
    credit_message = serializers.CharField(allow_null=True)


class InsightEstimateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = InsightEstimateResultSerializer()


class InsightPreviewResultSerializer(serializers.Serializer):
    units = serializers.ListField(child=serializers.JSONField())


class InsightPreviewResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = InsightPreviewResultSerializer()


# ─── Helpers ─────────────────────────────────────────────────────────────────
def _org(request):
    return getattr(request, "organization", None) or request.user.organization


def _workspace(request):
    return getattr(request, "workspace", None)


def _project(request, project_id):
    return Project.objects.filter(
        id=project_id, organization=_org(request), deleted=False
    ).first()


def _spend(kind: str, org_id, limit: int) -> bool:
    key = f"insight-{kind}:{org_id}"
    cache.add(key, 0, timeout=3600)
    try:
        return cache.incr(key) <= limit
    except ValueError:
        cache.set(key, 1, timeout=3600)
        return True


def _distribution(run) -> list[dict]:
    categories = (run.result or {}).get("categories") or []
    tops = [c for c in categories if c.get("level") == 1]
    tops.sort(key=lambda c: -c["stats"]["count"])
    return [
        {
            "category_id": c["id"],
            "name": c["name"],
            "share": c["stats"]["share"],
            "count": c["stats"]["count"],
            "status": c.get("status", "active"),
        }
        for c in tops
    ]


def _error_code(run) -> str:
    """Why a failed run ended when running it again can't help as it is:
    "license" or "model_setup" (an administrator must fix the licence or the
    model settings), "stopped" (someone stopped it); else ""."""
    if run.status != "failed":
        return ""
    return str((run.progress or {}).get("error_code") or "")


def _run_summary(run) -> dict:
    return {
        "id": run.id,
        "status": run.status,
        "stage": run.stage,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
        "population_size": run.population_size,
        "sample_size": run.sample_size,
        "analysed_count": (
            run.read_count if run.status == "failed" else run.analysed_count
        ),
        "skipped_count": run.skipped_count,
        "taxonomy_mode": run.taxonomy_mode,
        "distribution": _distribution(run),
        "error": run.error,
        "error_code": _error_code(run),
        "scope": normalize_scope(run.scope),
    }


def _config_item(config, *, with_runs=False) -> dict:
    runs = list(
        config.runs.filter(deleted=False)
        .annotate(
            skipped_count=Count("units", filter=Q(units__status="skipped")),
            read_count=Count(
                "units", filter=Q(units__status="done", units__included=True)
            ),
        )
        .order_by("-created_at")
    )
    item = {
        "id": config.id,
        "name": config.name,
        "unit": config.unit,
        "lens": config.lens,
        "question": config.question,
        "agent_description": config.agent_description,
        "instructions": config.instructions,
        "attributes": config.attributes or [],
        "sample_size": config.sample_size,
        "scope": normalize_scope(config.scope),
        "has_taxonomy": bool((config.taxonomy or {}).get("subs")),
        "taxonomy": config.taxonomy or {},
        "created_at": config.created_at,
        "latest_run": _run_summary(runs[0]) if runs else None,
    }
    if with_runs:
        item["runs"] = [_run_summary(r) for r in runs[:50]]
    return item


def _rate_limited(gm, message="Too many reports this hour. Try again later."):
    return gm.custom_error_response(
        status.HTTP_429_TOO_MANY_REQUESTS, message, code="rate_limited"
    )


def _gate(gm, org):
    """The model gate of the steps that call a model. Insights itself is
    licensed on every view (InsightsLicenseRequired); its models also need
    Turing when they are Turing models, as they are unless the deployment
    configures its own."""
    from tfc.ee_gates import insights_turing_denied, insights_turing_denied_message

    if insights_turing_denied(org.id):
        return gm.custom_error_response(
            status.HTTP_402_PAYMENT_REQUIRED,
            insights_turing_denied_message(),
            code="CAPABILITY_DENIED",
        )
    return None


def _start(run) -> None:
    try:
        from tfc.temporal.insights.client import start_insight_run

        run.workflow_id = start_insight_run(str(run.id))
        run.save(update_fields=["workflow_id", "updated_at"])
    except Exception:
        logger.exception("insight_workflow_start_failed", run_id=str(run.id))
        run.status = "failed"
        run.error = "The report could not be started. Try again shortly."
        run.save(update_fields=["status", "error", "updated_at"])


def _pipeline():
    try:
        from ee.agenthub.insights import draft, pipeline
        from ee.agenthub.insights.llm import InsightModelError
    except ImportError:
        return None
    return draft, pipeline, InsightModelError


def _billing():
    """Insights' billing, or None where reports aren't billed: off Cloud. On
    a Cloud whose billing config doesn't load, its credit checks refuse."""
    try:
        from ee.agenthub.insights import billing
    except ImportError:
        return None
    return billing if billing.applies() else None


def _charge(
    org, cost_usd, source: str, source_id, *, unpriced=False, spend=None, models=None
) -> None:
    """Charge a step's model spend as AI credits (a no-op without billing).
    ``spend`` puts its calls and tokens on the usage event, and its flag for
    a cost priced at the ceiling; ``models`` are the models it ran on
    (``_ran_on``)."""
    billing = _billing()
    if billing is not None:
        billing.charge(
            org.id,
            float(cost_usd or 0),
            source=source,
            source_id=str(source_id),
            spend=spend,
            unpriced=unpriced,
            models=models,
        )


def _ran_on(engine: dict | None = None) -> dict | None:
    """The models a step ran on, for its usage event: on the agent, the
    models of ``engine`` (a report's, for its questions), else the ones the
    agent reports (empty when it can't say); None on the built-in engine,
    which runs on this deployment's tier models."""
    try:
        from ee.agenthub.insights.agent import enabled, expected_models, is_omega
    except ImportError:
        return None
    if not enabled() or (engine is not None and not is_omega(engine)):
        return None
    return (engine or {}).get("models") or expected_models() or {}


CREDIT_CHECK_UNAVAILABLE = (
    "Credits can't be checked right now, so this wasn't started. "
    "Please retry in a moment."
)

# Credit refusals that no smaller report gets past.
CREDIT_MESSAGES = {
    "BUDGET_PAUSED": (
        "AI features are paused by your budget limit. Raise or remove it in "
        "billing to run this."
    ),
    "PAYMENT_REQUIRED": (
        "Your subscription has unpaid invoices. Update your payment method "
        "in billing to run this."
    ),
}


def _credit_amount(credits: float) -> str:
    """Credits as the Ask page prints them: whole from 10 up, else to a tenth."""
    if credits >= 10:
        return f"{credits:,.0f}"
    return f"{credits:.1f}".rstrip("0").rstrip(".")


def _credit_count(credits: float) -> str:
    amount = _credit_amount(credits)
    return f"{amount} credit{'' if amount == '1' else 's'}"


def _credit_verdict(
    billing,
    org,
    cost_usd: float,
    *,
    what: str,
    unit: str | None = None,
    sizing: dict | None = None,
):
    """The organization's credit cap against a step costing about
    ``cost_usd``: None when it may run, else why not, what is left and, for a
    report, the largest sample that fits."""
    refused = billing.check(org, cost_usd)
    if refused is None:
        return None
    needed = billing.credits(cost_usd)
    left = fits = None
    # The free tier's credits, or a pause budget the step would cross: what
    # is left, and the largest report sample it pays for.
    if refused.error_code in ("FREE_TIER_LIMIT", "BUDGET_LIMIT"):
        budget = refused.error_code == "BUDGET_LIMIT"
        left = max(0.0, float(refused.limit or 0) - float(refused.current_usage or 0))
        if unit is not None:
            fits = billing.largest_sample(
                left, MIN_SAMPLE, MAX_SAMPLE, **(sizing or {})
            )
        some = left >= 0.05
        if budget:
            amount = _credit_amount(left) if some else "no"
            have = f"your budget limit allows {amount} more"
        else:
            have = f"you have {_credit_amount(left) if some else 'none'} left"
        message = f"{what} needs about {_credit_count(needed)}; {have} this month."
        if fits:
            message += f" A sample of up to {fits:,} {unit} fits."
        elif budget:
            message += " Raise or remove it in billing to run this."
        else:
            message += " Upgrade your plan in billing to run it."
    else:
        message = CREDIT_MESSAGES.get(refused.error_code) or (
            refused.reason or "Your plan doesn't allow this right now."
        )
    return {
        "refused": refused,
        "credits_estimate": needed,
        "credits_left": left,
        "credit_max_sample": fits,
        "message": message,
    }


# What each billed step is called in a refusal.
_STEPS = {
    "run": "This report",
    "ask": "This question",
    "draft": "Drafting a report",
    "preview": "This preview",
}


def _typical_unit_chars(project, unit: str) -> int | None:
    """How large a unit of this project reads, in rendered characters: the
    mean the agent measured on the units of its last completed reports on
    the same kind of unit (insights-agent/v2 runs record it), or None."""
    if project is None:
        return None
    rows = (
        InsightUnit.objects.filter(
            run__project=project,
            run__unit=unit,
            run__status="completed",
            run__deleted=False,
            status="done",
        )
        .order_by("-run__created_at", "-id")
        .values_list("metrics", flat=True)[:TYPICAL_UNITS]
    )
    sizes = []
    for metrics in rows:
        chars = ((metrics or {}).get("coverage") or {}).get("chars_total")
        if isinstance(chars, int) and not isinstance(chars, bool) and chars > 0:
            sizes.append(chars)
    return sum(sizes) // len(sizes) if sizes else None


def _sizing(unit: str | None, project=None, engine: dict | None = None) -> dict:
    """What a step's cost depends on: the engine the run is pinned to
    (``engine``), else the one it would likely start on, the models that
    engine runs on, the kind of unit and how large one typically reads."""
    try:
        from ee.agenthub.insights.agent import (
            CONTRACT_V2,
            expected_contract,
            expected_models,
            is_omega,
        )
    except ImportError:
        return {}
    if engine is None:
        contract = expected_contract()
        models = expected_models()
    else:
        contract = engine.get("contract") if is_omega(engine) else None
        models = (engine.get("models") or expected_models()) if contract else None
    sizing = {"contract": contract, "unit": unit}
    if models:
        sizing["models"] = models
    if contract == CONTRACT_V2:
        sizing["unit_chars"] = _typical_unit_chars(project, unit)
    return sizing


def _step_cost(
    billing, step: str, sample_size: int | None = None, sizing: dict | None = None
) -> float:
    sizing = sizing or {}
    if step == "run":
        return billing.run_cost(sample_size or DEFAULT_SAMPLE, **sizing)
    if step == "preview":
        return billing.preview_cost(sizing.get("models"))
    if step == "ask":
        return billing.ask_cost(
            agent=bool(sizing.get("contract")), models=sizing.get("models")
        )
    return billing.draft_cost()


def _credit_estimate(
    org, sample_size: int, unit: str | None, project=None, *, step="run"
) -> dict:
    """A step's cost and whether the credit cap lets it start; null off Cloud.
    Reports are sized for the engine and units they would read. Other actions
    use the same per-step estimate their submission checks use."""
    out = {
        "credits_estimate": None,
        "credit_limited": False,
        "credits_left": None,
        "credit_max_sample": None,
        "credit_message": None,
    }
    billing = _billing()
    if billing is None:
        return out
    out["credit_check_unavailable"] = False
    out["credits_estimate"] = 0.0
    if step == "run" and sample_size <= 0:
        return out
    # A draft is the built-in engine's; the other steps run on the agent's
    # models when there is one.
    sizing = _sizing(unit, project) if step != "draft" else {}
    cost = _step_cost(billing, step, sample_size, sizing)
    out["credits_estimate"] = billing.credits(cost)
    try:
        verdict = _credit_verdict(
            billing,
            org,
            cost,
            what=_STEPS[step],
            unit=unit if step == "run" else None,
            sizing=sizing,
        )
    except billing.CreditCheckUnavailable:
        # Fail closed: the report can't start until billing answers.
        out.update(
            {
                "credit_limited": True,
                "credit_check_unavailable": True,
                "credit_message": CREDIT_CHECK_UNAVAILABLE,
            }
        )
        return out
    if verdict is not None:
        out.update(
            {
                "credit_limited": True,
                "credits_left": verdict["credits_left"],
                "credit_max_sample": verdict["credit_max_sample"],
                "credit_message": verdict["message"],
            }
        )
    return out


def _credits_refused(
    gm, org, step: str, *, sample_size=None, unit=None, project=None, engine=None
):
    """A 402/403 when billing refuses a step: why, what it needs, what is
    left and, for a report, the largest sample that fits on ``engine``."""
    billing = _billing()
    if billing is None:
        return None
    sizing = _sizing(unit, project, engine) if step != "draft" else {}
    try:
        verdict = _credit_verdict(
            billing,
            org,
            _step_cost(billing, step, sample_size, sizing),
            what=_STEPS[step],
            unit=unit,
            sizing=sizing,
        )
    except billing.CreditCheckUnavailable:
        # Fail closed: reports run on Future AGI's model keys, so an outage
        # (billing's Redis, or its config) must not mean unchecked (and
        # unbilled) spend.
        return gm.custom_error_response(
            503, CREDIT_CHECK_UNAVAILABLE, code="credit_check_unavailable"
        )
    if verdict is None:
        return None
    refused = verdict["refused"]
    return gm.custom_error_response(
        402 if refused.error_code == "PAYMENT_REQUIRED" else 403,
        {
            "message": verdict["message"],
            "error_code": refused.error_code or "INSIGHTS_CREDITS",
            "credits_estimate": verdict["credits_estimate"],
            "credits_left": verdict["credits_left"],
            "sample_size": verdict["credit_max_sample"],
        },
        code="insights_credits",
    )


def _is_date_item(item) -> bool:
    config = item.get("filter_config") or {}
    return item.get("column_id") in ("created_at", "start_time") and config.get(
        "filter_type"
    ) in ("datetime", "date")


def _scope_filters(scope: dict) -> list[dict]:
    """The scope's filters, its window among them as their created_at item.
    A scope without a window can't be read on the server."""
    filters = [dict(item) for item in scope.get("filters") or [] if item]
    window = scope.get("window") or []
    if not any(_is_date_item(item) for item in filters):
        if len(window) != 2:
            raise serializers.ValidationError("Choose a date range for the report.")
        filters.append(_window_item(window))
    return SessionFilterListField().run_validation(filters)


def _scope_population(request, project, unit: str, scope: dict, *, finish=False):
    """The units a scope holds, resolved on the server like Instant eval
    resolves a view: the same lists' queries, so the counts agree."""
    from tracer.services.filter_principal_context import (
        bind_request_my_annotations_principal,
    )

    filters = _scope_filters(scope)
    search = (scope.get("search") or "").strip()
    if unit == "users":
        validate_users_filter_capabilities(filters)
        if refused := users_service.unsupported_filter_message(filters):
            raise serializers.ValidationError(refused)
    elif search:
        raise serializers.ValidationError("Search text only narrows a report on users.")
    filters = bind_request_my_annotations_principal(request, filters)
    return users_service.count_view(
        str(project.id), unit, filters, search=search, finish=finish
    )


def _population_error(gm, exc):
    """The response for a scope the server couldn't resolve, or None."""
    if isinstance(exc, serializers.ValidationError):
        detail = exc.detail[0] if isinstance(exc.detail, list) else exc.detail
        return gm.bad_request(str(detail))
    if isinstance(exc, users_service.UsersCountIncomplete):
        # What was counted is kept: asking again carries on from there.
        return gm.custom_error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE, str(exc), code="users_still_counting"
        )
    if isinstance(exc, rows_service.PopulationUnavailable):
        return gm.custom_error_response(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            str(exc),
            code="population_unavailable",
        )
    logger.exception("insight_population_failed")
    return gm.custom_error_response(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "The scope can't be read right now. Please retry.",
    )


def _window(population) -> dict:
    return {
        "from": population.date_from.isoformat() if population.date_from else None,
        "to": population.date_to.isoformat() if population.date_to else None,
    }


def _unavailable(gm):
    return gm.custom_error_response(
        status.HTTP_403_FORBIDDEN,
        "Insights is not available in this edition.",
        code="feature_unavailable",
    )


# ─── Views ───────────────────────────────────────────────────────────────────
class InsightConfigListView(InsightsLicenseRequired, APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=InsightConfigsQuerySerializer,
        responses={200: InsightConfigListResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request):
        """Saved Insights reports of a project, newest first, with their latest run."""
        project = _project(request, request.validated_query_data["project_id"])
        if project is None:
            return self._gm.not_found("Project not found")
        configs = InsightConfig.objects.filter(project=project, deleted=False).order_by(
            "-created_at"
        )
        return self._gm.success_response(
            {"configs": [_config_item(c) for c in configs]}
        )


class InsightConfigDetailView(InsightsLicenseRequired, APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def _config(self, request, config_id):
        return InsightConfig.objects.filter(
            id=config_id, organization=_org(request), deleted=False
        ).first()

    @validated_request(
        responses={200: InsightConfigResponseSerializer, **ERROR_RESPONSES}
    )
    def get(self, request, config_id):
        """One saved report with every run, for the trend chart and run pager."""
        config = self._config(request, config_id)
        if config is None:
            return self._gm.not_found("Report not found")
        return self._gm.success_response(_config_item(config, with_runs=True))

    @validated_request(
        request_serializer=InsightConfigPatchSerializer,
        responses={200: InsightConfigResponseSerializer, **ERROR_RESPONSES},
    )
    def patch(self, request, config_id):
        """Edit a report's setup, or forget its saved categories so the next
        run discovers afresh. Changing the question or lens changes what the
        categories mean, so it resets them too. Applies from the next run."""
        try:
            from ee.agenthub.insights.facets import clean_attributes
        except ImportError:
            return _unavailable(self._gm)

        config = self._config(request, config_id)
        if config is None:
            return self._gm.not_found("Report not found")
        data = request.validated_data
        fields = {"updated_at"}
        reset = bool(data.get("reset_taxonomy"))
        if data.get("name"):
            config.name = data["name"]
            fields.add("name")
        for key in ("question", "lens"):
            if key in data and data[key] != getattr(config, key):
                setattr(config, key, data[key])
                fields.add(key)
                reset = True
        for key in ("agent_description", "instructions", "sample_size"):
            if key in data:
                setattr(config, key, data[key])
                fields.add(key)
        if "attributes" in data:
            config.attributes = clean_attributes([dict(a) for a in data["attributes"]])
            fields.add("attributes")
        if "scope" in data:
            scope = dict(data["scope"])
            if config.unit == "users":
                refused = users_service.unsupported_filter_message(
                    [i for i in scope.get("filters") or [] if isinstance(i, dict)]
                )
                if refused:
                    return self._gm.bad_request(refused)
            config.scope = scope
            fields.add("scope")
        if reset and (config.taxonomy or {}).get("subs"):
            config.taxonomy = {}
            fields.add("taxonomy")
        config.save(update_fields=sorted(fields))
        return self._gm.success_response(_config_item(config, with_runs=True))

    @validated_request(
        responses={200: InsightConfigResponseSerializer, **ERROR_RESPONSES}
    )
    def delete(self, request, config_id):
        """Delete a saved report and hide its runs."""
        config = self._config(request, config_id)
        if config is None:
            return self._gm.not_found("Report not found")
        item = _config_item(config)
        config.delete()
        InsightRun.objects.filter(config=config).update(
            deleted=True, deleted_at=timezone.now()
        )
        return self._gm.success_response(item)


class InsightDraftView(InsightsLicenseRequired, APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=InsightDraftSerializer,
        responses={200: InsightDraftResponseSerializer, **ERROR_RESPONSES},
    )
    def post(self, request):
        """Read a few real units and draft a report setup for the team's goal."""
        modules = _pipeline()
        if modules is None:
            return _unavailable(self._gm)
        draft, _, InsightModelError = modules
        data = request.validated_data
        org = _org(request)
        if _project(request, data["project_id"]) is None:
            return self._gm.not_found("Project not found")
        denied = _gate(self._gm, org)
        if denied:
            return denied
        if not _spend("draft", org.id, DRAFTS_PER_HOUR):
            return self._gm.custom_error_response(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many drafts this hour. Try again later.",
                code="rate_limited",
            )
        refused = _credits_refused(self._gm, org, "draft")
        if refused:
            return refused
        try:
            result = draft.draft_config(
                project_id=str(data["project_id"]),
                unit=data["unit"],
                lens_key=data["lens"],
                goal=data.get("goal") or "",
                agent_description=data.get("agent_description") or "",
                sample_ids=data["sample_ids"],
            )
        except InsightModelError as exc:
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                str(exc),
                code="insight_unavailable",
            )
        _charge(org, result.pop("cost_usd", 0), "insights_draft", data["project_id"])
        return self._gm.success_response(result)


class InsightPreviewView(InsightsLicenseRequired, APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=InsightPreviewSerializer,
        responses={200: InsightPreviewResponseSerializer, **ERROR_RESPONSES},
    )
    def post(self, request):
        """Show what the report will read from up to three units before running it."""
        modules = _pipeline()
        if modules is None:
            return _unavailable(self._gm)
        draft, _, InsightModelError = modules
        data = request.validated_data
        org = _org(request)
        project = _project(request, data["project_id"])
        if project is None:
            return self._gm.not_found("Project not found")
        denied = _gate(self._gm, org)
        if denied:
            return denied
        if not _spend("draft", org.id, DRAFTS_PER_HOUR):
            return self._gm.custom_error_response(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many previews this hour. Try again later.",
                code="rate_limited",
            )
        refused = _credits_refused(
            self._gm, org, "preview", unit=data["config"].get("unit"), project=project
        )
        if refused:
            return refused
        try:
            from ee.agenthub.insights.llm import Spend
        except ImportError:
            return _unavailable(self._gm)

        config = data["config"]
        spend = Spend()
        try:
            units = draft.preview(
                project_id=str(data["project_id"]),
                unit=config["unit"],
                lens_key=config["lens"],
                question=config["question"],
                agent_description=config.get("agent_description") or "",
                instructions=config.get("instructions") or "",
                attributes=config.get("attributes") or [],
                unit_ids=data["unit_ids"],
                spend=spend,
            )
        except InsightModelError as exc:
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                str(exc),
                code="insight_unavailable",
            )
        _charge(
            org,
            spend.cost_usd,
            "insights_preview",
            data["project_id"],
            spend=spend,
            models=_ran_on(),
        )
        return self._gm.success_response({"units": units})


# A count and a price, open to Viewers: nothing is written or spent.
@workspace_read_only
class InsightEstimateView(InsightsLicenseRequired, APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=InsightEstimateRequestSerializer,
        responses={
            200: InsightEstimateResponseSerializer,
            422: ApiErrorResponseSerializer,
            **ERROR_RESPONSES,
        },
    )
    def post(self, request):
        """How many units a scope holds, and how many a report on it reads.
        Rows handed over by id are only sized: they have no window to count."""
        data = request.validated_data
        project = _project(request, data["project_id"])
        if project is None:
            return self._gm.not_found("Project not found")
        if data["action"] != "run":
            return self._gm.success_response(
                {
                    "population": 0,
                    "population_is_lower_bound": False,
                    "sample_size": 0,
                    "window": None,
                    "sample_ids": [],
                    **_credit_estimate(
                        _org(request),
                        0,
                        data.get("unit"),
                        project,
                        step=data["action"],
                    ),
                }
            )
        unit = data["unit"]
        if "population_size" in data:
            handed = data["population_size"]
            reads = min(data.get("sample_size") or DEFAULT_SAMPLE, handed)
            return self._gm.success_response(
                {
                    "population": handed,
                    "population_is_lower_bound": False,
                    "sample_size": reads,
                    "window": None,
                    "sample_ids": [],
                    **_credit_estimate(_org(request), reads, unit, project),
                }
            )
        scope = normalize_scope(data["scope"])
        try:
            population = _scope_population(request, project, unit, scope)
            # A users count still under way holds at least the 20 the pick
            # walks to, and the pick stops there.
            first = users_service.pick_view(
                str(project.id),
                unit,
                population.filters,
                n=(
                    ESTIMATE_SAMPLE_IDS
                    if population.lower_bound
                    else min(ESTIMATE_SAMPLE_IDS, population.count)
                ),
                after=None,
                search=(scope.get("search") or "").strip(),
                budget_s=users_service.estimate_budget_s(),
            )
        except Exception as exc:
            return _population_error(self._gm, exc)
        size = data.get("sample_size") or DEFAULT_SAMPLE
        reads = min(size, population.count)
        return self._gm.success_response(
            {
                "population": population.count,
                "population_is_lower_bound": population.lower_bound,
                "sample_size": reads,
                "window": _window(population),
                "sample_ids": [rid for rid, _ in first],
                **_credit_estimate(_org(request), reads, unit, project),
            }
        )


class InsightRunCreateView(InsightsLicenseRequired, APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=InsightRunCreateSerializer,
        responses={200: InsightRunCreatedResponseSerializer, **ERROR_RESPONSES},
    )
    def post(self, request):
        """Start a report run over a sample of its scope (or the rows handed
        over); creates the report when new."""
        modules = _pipeline()
        if modules is None:
            return _unavailable(self._gm)
        _, pipeline, InsightModelError = modules
        data = request.validated_data
        org = _org(request)
        project = _project(request, data["project_id"])
        if project is None:
            return self._gm.not_found("Project not found")
        denied = _gate(self._gm, org)
        if denied:
            return denied
        scope = normalize_scope(data.get("scope"))
        config = None
        if data.get("config_id"):
            config = InsightConfig.objects.filter(
                id=data["config_id"], project=project, deleted=False
            ).first()
            if config is None:
                return self._gm.not_found("Report not found")
            # One run per report at a time: a second "Run again" joins the run
            # in flight instead of racing it (a run stuck for hours doesn't block).
            active = (
                InsightRun.objects.filter(
                    config=config,
                    status__in=["pending", "running"],
                    deleted=False,
                    created_at__gte=timezone.now() - ACTIVE_RUN_WINDOW,
                )
                .order_by("-created_at")
                .first()
            )
            if active is not None:
                # A run whose workflow is gone isn't one to join.
                settle_lost_runs([active])
            if active is not None and active.status in ("pending", "running"):
                return self._gm.success_response(
                    {"run_id": active.id, "config_id": config.id}
                )
            unit = config.unit
            size = data.get("sample_size") or config.sample_size
        else:
            spec = data["config"]
            unit = spec["unit"]
            size = spec.get("sample_size") or data.get("sample_size") or DEFAULT_SAMPLE
        population = data.get("population")
        resolved = None
        if not population:
            # The scope's own rows: every unit of it counted, and a sample of
            # them read in hash order (a uniform sample, the same every time).
            try:
                resolved = _scope_population(request, project, unit, scope, finish=True)
                picked = users_service.pick_view(
                    str(project.id),
                    unit,
                    resolved.filters,
                    n=min(int(data.get("sample_size") or size), resolved.count),
                    after=None,
                    search=(scope.get("search") or "").strip(),
                    budget_s=users_service.run_budget_s(),
                )
            except Exception as exc:
                return _population_error(self._gm, exc)
            if not picked and resolved.count:
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    f"The {unit} to analyse couldn't be read in time. Please retry.",
                )
            if not picked:
                return self._gm.bad_request(
                    f"There are no {unit} in this scope to analyse."
                )
            population = [{"id": rid} for rid, _ in picked]
            scope["window"] = [
                resolved.date_from.isoformat(),
                resolved.date_to.isoformat(),
            ]
        try:
            from ee.agenthub.insights.agent import resolve_engine

            # The engine the run is pinned to, asked before anything is saved
            # and before the run is priced: it costs what that engine spends.
            engine = resolve_engine()
        except InsightModelError as exc:
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                str(exc),
                code="insight_unavailable",
            )
        refused = _credits_refused(
            self._gm,
            org,
            "run",
            sample_size=min(int(data.get("sample_size") or size), len(population)),
            unit=unit,
            project=project,
            engine=engine,
        )
        if refused:
            return refused
        if not _spend("run", org.id, RUNS_PER_HOUR):
            return _rate_limited(self._gm)
        if config is not None:
            if scope:
                config.scope = scope
                config.save(update_fields=["scope", "updated_at"])
        else:
            config = InsightConfig.objects.create(
                project=project,
                organization=org,
                workspace=_workspace(request) or project.workspace,
                created_by=request.user,
                name=spec["name"],
                unit=spec["unit"],
                lens=spec["lens"],
                question=spec["question"],
                agent_description=spec.get("agent_description") or "",
                instructions=spec.get("instructions") or "",
                attributes=list(spec.get("attributes") or []),
                scope=scope,
                sample_size=size,
            )
        rediscover = bool(data.get("reset_taxonomy")) and bool(
            (config.taxonomy or {}).get("subs")
        )
        run = pipeline.create_run(
            config=config,
            scope=scope,
            population=population,
            sample_size=data.get("sample_size") or config.sample_size,
            user=request.user,
            population_size=resolved.count if resolved is not None else None,
            discover=rediscover,
            engine=engine,
        )
        _start(run)
        if rediscover and run.status != "failed":
            # The discovery run is under way: its categories replace these.
            config.taxonomy = {}
            config.save(update_fields=["taxonomy", "updated_at"])
        return self._gm.success_response({"run_id": run.id, "config_id": config.id})


class InsightRunDetailView(InsightsLicenseRequired, APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(responses={200: InsightRunResponseSerializer, **ERROR_RESPONSES})
    def get(self, request, run_id):
        """A run's progress while it works, and its full report once complete."""
        run = (
            InsightRun.objects.filter(
                id=run_id, organization=_org(request), deleted=False
            )
            .select_related("config")
            .first()
        )
        if run is None:
            return self._gm.not_found("Report run not found")
        siblings = InsightRun.objects.filter(config_id=run.config_id, deleted=False)
        previous = (
            siblings.filter(created_at__lt=run.created_at)
            .order_by("-created_at")
            .first()
        )
        following = (
            siblings.filter(created_at__gt=run.created_at)
            .order_by("created_at")
            .first()
        )
        return self._gm.success_response(
            {
                "id": run.id,
                "config_id": run.config_id,
                "project_id": run.project_id,
                "status": run.status,
                "stage": run.stage,
                "progress": run.progress or {},
                "unit": run.unit,
                "scope": normalize_scope(run.scope),
                "settings": run.settings or {},
                "population_size": run.population_size,
                "sample_size": run.sample_size,
                "analysed_count": (
                    run.units.filter(status="done", included=True).count()
                    if run.status == "failed"
                    else run.analysed_count
                ),
                "skipped_count": run.units.filter(status="skipped").count(),
                "taxonomy_mode": run.taxonomy_mode,
                "result": run.result or {},
                "models_used": run.models_used or {},
                "cost_usd": run.cost_usd,
                "error": run.error,
                "error_code": _error_code(run),
                "created_at": run.created_at,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "previous_run_id": previous.id if previous else None,
                "next_run_id": following.id if following else None,
            }
        )


class InsightRunStopView(InsightsLicenseRequired, APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=EmptyRequestSerializer,
        responses={200: InsightRunStopResponseSerializer, **ERROR_RESPONSES},
    )
    def post(self, request, run_id):
        """Stop a run in flight: it reads nothing more and isn't written. The
        work finished before the stop is charged, and its counts are final:
        what calls under way read is dropped. Stopping it again answers the
        same."""
        modules = _pipeline()
        if modules is None:
            return _unavailable(self._gm)
        _, pipeline, _ = modules
        run = InsightRun.objects.filter(
            id=run_id, organization=_org(request), deleted=False
        ).first()
        if run is None:
            return self._gm.not_found("Report run not found")
        user = request.user
        if pipeline.stop(run.id, f"Stopped by {user.name or user.email}."):
            _cancel_workflow(run)
        run.refresh_from_db(fields=["status", "error", "progress"])
        if _error_code(run) != pipeline.STOPPED:
            return self._gm.bad_request("This run has already finished.")
        return self._gm.success_response(
            {
                "id": run.id,
                "status": run.status,
                "error": run.error,
                "error_code": _error_code(run),
            }
        )


def _cancel_workflow(run) -> None:
    """Cancel a stopped run's workflow. The run has stopped either way: a
    stage under way gives up its calls, and one that starts later reads
    nothing."""
    try:
        from tfc.temporal.common.client import cancel_workflow_sync
        from tfc.temporal.insights.client import workflow_id_for

        cancel_workflow_sync(run.workflow_id or workflow_id_for(run.id))
    except Exception:
        logger.warning("insight_workflow_cancel_failed", run_id=str(run.id))


class InsightRunUnitsView(InsightsLicenseRequired, APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=InsightUnitsQuerySerializer,
        responses={200: InsightUnitsResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request, run_id):
        """Analysed units of a run, optionally one category's, oldest first."""
        run = InsightRun.objects.filter(
            id=run_id, organization=_org(request), deleted=False
        ).first()
        if run is None:
            return self._gm.not_found("Report run not found")
        params = request.validated_query_data
        units = InsightUnit.objects.filter(run=run, status="done", included=True)
        if params.get("sub_category_id"):
            units = units.filter(sub_category_id=params["sub_category_id"])
        elif params.get("category_id"):
            units = units.filter(
                Q(category_id=params["category_id"])
                | Q(sub_category_id=params["category_id"])
            )
        if params.get("unit_ids"):
            wanted = [u for u in params["unit_ids"].split(",") if u][:100]
            units = units.filter(unit_id__in=wanted)
        total = units.count()
        offset, limit = params.get("offset", 0), params.get("limit", UNITS_PAGE)
        with_content = params.get("include_content") and limit <= MAX_CONTENT_UNITS
        page = units.order_by("time", "id")[offset : offset + limit]
        return self._gm.success_response(
            {
                "units": [
                    {
                        "unit_id": u.unit_id,
                        "label": u.label,
                        "user_id": u.user_id,
                        "session_id": u.session_id,
                        "time": u.time,
                        "excerpt": u.excerpt,
                        "summary": u.summary,
                        "lens_answer": u.lens_answer,
                        "facets": u.facets or {},
                        "metrics": u.metrics or {},
                        "category_id": u.category_id,
                        "sub_category_id": u.sub_category_id,
                        **({"content": u.content} if with_content else {}),
                    }
                    for u in page
                ],
                "total": total,
            }
        )


class InsightRunMediaView(InsightsLicenseRequired, APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=InsightMediaQuerySerializer,
        responses={
            200: openapi.Response(
                description="The image, downscaled and without metadata.",
                schema=openapi.Schema(type=openapi.TYPE_FILE),
            ),
            **ERROR_RESPONSES,
        },
    )
    def get(self, request, run_id):
        """An image an analysed unit's evidence cites, as the agent viewed it
        (at most 1,568 px on its longest side and 5 MiB), for the example
        panel. Only a unit of this organization's run that the report kept,
        and only media its evidence names."""
        try:
            from ee.agenthub.insights import broker
        except ImportError:
            return _unavailable(self._gm)

        run = InsightRun.objects.filter(
            id=run_id, organization=_org(request), deleted=False, project__deleted=False
        ).first()
        if run is None:
            return self._gm.not_found("Report run not found")
        params = request.validated_query_data
        row = InsightUnit.objects.filter(
            run=run, unit_id=params["unit_id"], status="done", included=True
        ).first()
        if row is None:
            return self._gm.not_found("This example is not part of the report.")
        if not _spend("media", run.organization_id, MEDIA_VIEWS_PER_HOUR):
            return _rate_limited(
                self._gm, "Too many images opened this hour; try again later."
            )
        try:
            image = broker.cited_image(run, row, params["media"])
        except broker.BrokerError as refused:
            if refused.status == 503:
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "The image can't be loaded right now. Please retry.",
                    code=refused.code,
                )
            return self._gm.not_found("This image can't be shown.")
        response = HttpResponse(image.content, content_type=image.mime)
        response["Cache-Control"] = "private, max-age=3600"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return response


class InsightAskEvidenceSerializer(serializers.Serializer):
    unit_id = serializers.CharField()
    quote = serializers.CharField(allow_blank=True)
    evidence_id = serializers.CharField(required=False, allow_blank=True)


class InsightAskParagraphSerializer(serializers.Serializer):
    text = serializers.CharField()
    unit_ids = serializers.ListField(child=serializers.CharField())
    category_ids = serializers.ListField(child=serializers.CharField())
    evidence = InsightAskEvidenceSerializer(many=True)


class InsightAskResultSerializer(serializers.Serializer):
    answer = InsightAskParagraphSerializer(many=True)
    follow_ups = serializers.ListField(child=serializers.CharField())
    dropped = serializers.IntegerField()
    # A report on the Insights agent's v2 engine: an answer its verifier did
    # not pass comes here instead of ``answer``, with the verifier's verdict.
    unverified_answer = InsightAskParagraphSerializer(many=True, required=False)
    verification = serializers.JSONField(required=False)


class InsightAskResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = InsightAskResultSerializer()


class InsightRunAskView(InsightsLicenseRequired, APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=InsightAskSerializer,
        responses={200: InsightAskResponseSerializer, **ERROR_RESPONSES},
    )
    def post(self, request, run_id):
        """Answer a follow-up question about a completed report, citing its examples."""
        try:
            from ee.agenthub.insights import report
            from ee.agenthub.insights.agent import pinned
            from ee.agenthub.insights.llm import InsightModelError, Spend
            from ee.agenthub.insights.pipeline import _unit_dict, add_spend
        except ImportError:
            return _unavailable(self._gm)

        org = _org(request)
        run = InsightRun.objects.filter(
            id=run_id, organization=org, deleted=False
        ).first()
        if run is None:
            return self._gm.not_found("Report run not found")
        if run.status != "completed" or not (run.result or {}).get("categories"):
            return self._gm.bad_request("The report is not ready yet.")
        denied = _gate(self._gm, org)
        if denied:
            return denied
        if not _spend("ask", org.id, ASKS_PER_HOUR):
            return _rate_limited(self._gm)
        # Asked on the engine that wrote the report.
        refused = _credits_refused(self._gm, org, "ask", engine=pinned(run.settings))
        if refused:
            return refused
        data = request.validated_data
        rows = list(InsightUnit.objects.filter(run=run, status="done", included=True))
        units = [_unit_dict(u) for u in rows]
        contents = {str(u.pk): u.content or "" for u in rows}
        spend = Spend()
        try:
            answer = report.answer(
                run,
                run.result,
                units,
                contents,
                data["question"],
                [dict(m) for m in data.get("history") or []],
                spend,
            )
        except InsightModelError as exc:
            # What the agent reported for the failed call is spend too.
            add_spend(run.id, spend)
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE, str(exc), code="model_unavailable"
            )
        add_spend(run.id, spend)
        _charge(
            org,
            spend.cost_usd,
            "insights_ask",
            run.id,
            spend=spend,
            models=_ran_on(pinned(run.settings)),
        )
        return self._gm.success_response(answer)
