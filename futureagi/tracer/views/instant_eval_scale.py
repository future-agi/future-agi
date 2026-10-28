"""
Instant eval at any size: from a view of 100 rows to one of 100,000.

POST /tracer/instant-eval/estimate/  how many rows a view holds and what reading
                                     a sample or all of them would cost
POST /tracer/instant-eval/start/     start a run over a view (or rows handed over)
GET  /tracer/instant-eval/summary/   everything the charts draw, as counts
GET/POST /tracer/instant-eval/rows/   one page of rows, filtered like the charts
GET/POST /tracer/instant-eval/export/ every row the filter selects, as CSV
POST /tracer/instant-eval/control/   pause, resume, cancel, retry the failed
                                     rows, or read more of a sample

The server resolves a view's rows itself (``tracer.services.instant_eval_rows``)
instead of taking ids from the browser, reads a deterministic hash-ordered
sample of it when asked to, and keeps every row's facets in a table so the
summary and rows endpoints aggregate in SQL
(``tracer.services.instant_eval_summary``). Every read is scoped to a project
of the caller's organization.
"""

from __future__ import annotations

import hashlib
import json
import math
import time

import structlog
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import TooManyFieldsSent
from django.db import transaction
from django.db.models import F
from django.http import StreamingHttpResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.gzip import gzip_page
from drf_yasg import openapi
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import workspace_read_only
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.serializer_fields import StringOrObjectField
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import (
    RESUMABLE_TASK_STATUSES,
    EvalTask,
    EvalTaskStatus,
    RunType,
)
from tracer.models.instant_eval import InstantEvalRow, InstantEvalRun
from tracer.models.observation_span import EvalEntryStatus, EvalLogger
from tracer.serializers.filters import SessionFilterListField
from tracer.services import instant_eval_rows as rows_service
from tracer.services import instant_eval_summary as summary_service
from tracer.services import instant_eval_users as users_service
from tracer.services.instant_eval_run import (
    ACTIVE,
    DEFAULT_SAMPLE,
    MAX_QUESTIONS,
    STALL_CHECK_TTL,
    STALLED,
    CreditCheckUnavailable,
    _credits,
    _in_flight,
    _iso,
    _live_summary_age,
    _question,
    _rows_for,
    _run_of,
    _status,
    _summary_key,
    _task_ids,
    _unit_of,
    _view,
    _workflow_gone,
    check_credits,
    compute_summary,
    credits_per_answer,
    max_rows,
)
from tracer.services.user_filter_capabilities import (
    validate_users_filter_capabilities,
)
from tracer.views._license import InstantEvalLicenseRequired
from tracer.views.instant_eval import (
    INSTANT_TASK_PREFIX,
    MAX_ANSWER_ROWS,
    _org,
    _project,
)

logger = structlog.get_logger(__name__)

UNITS = ["traces", "spans", "sessions", "users"]
# The Observe date options a view's window comes from.
DATE_PRESETS = ["Today", "Yesterday", "7D", "30D", "3M", "6M", "12M", "Custom"]
SAMPLE_SIZES = (500, 1000, 2000, 5000)
MAX_HANDOVER_IDS = 5000
MAX_PAGE_SIZE = 200
MAX_COMPACT_PAGE = 1500


def confirm_rows() -> int:
    return int(getattr(settings, "INSTANT_EVAL_CONFIRM_ROWS", 5_000))


def max_active() -> int:
    return int(getattr(settings, "INSTANT_EVAL_MAX_ACTIVE_PER_ORG", 2))


def answers_per_minute() -> float:
    # What a run is assumed to drain at; the summary reports the real rate
    # once answers land.
    return float(getattr(settings, "INSTANT_EVAL_ASSUMED_ROWS_PER_MIN", 300))


ERRORS = {
    400: ApiErrorResponseSerializer,
    403: ApiErrorResponseSerializer,
    404: ApiErrorResponseSerializer,
    500: ApiErrorResponseSerializer,
    503: ApiErrorResponseSerializer,
}


class InstantEvalBusyRunSerializer(serializers.Serializer):
    task_id = serializers.CharField()
    project_id = serializers.CharField()
    question = serializers.CharField(allow_blank=True)
    rows = serializers.IntegerField()
    started_by = serializers.CharField(allow_blank=True)
    started_at = serializers.CharField(allow_null=True)


class InstantEvalLimitDetailSerializer(serializers.Serializer):
    message = serializers.CharField()
    error_code = serializers.CharField()
    # A run waiting for its confirm: what it would read and cost (null where
    # Instant eval isn't billed).
    rows_to_read = serializers.IntegerField(required=False)
    credits_estimate = serializers.FloatField(required=False, allow_null=True)
    # Refused for credits: what is left, and the largest sample that fits.
    credits_left = serializers.FloatField(required=False, allow_null=True)
    sample_size = serializers.IntegerField(required=False, allow_null=True)
    # The organization's runs in flight, when it is at its limit.
    runs = InstantEvalBusyRunSerializer(many=True, required=False)


class InstantEvalLimitErrorSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    type = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    code = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    detail = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    message = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    error = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    result = InstantEvalLimitDetailSerializer(required=False)


class InstantEvalRefusalField(StringOrObjectField):
    """A refusal's text, or for a question on Jev without the organization's
    TypeSafe key {"message", "error_code": "JEV_KEY_MISSING", "settings_path"}."""

    class Meta:
        swagger_schema_fields = {
            "x-string-or-object": True,
            "description": "String or JSON object.",
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "error_code": {"type": "string"},
                "settings_path": {"type": "string"},
            },
        }


class InstantEvalBadRequestSerializer(ApiErrorResponseSerializer):
    result = InstantEvalRefusalField(required=False)


LIMIT_ERRORS = {
    **ERRORS,
    400: InstantEvalBadRequestSerializer,
    402: InstantEvalLimitErrorSerializer,
    403: InstantEvalLimitErrorSerializer,
    409: InstantEvalLimitErrorSerializer,
    422: ApiErrorResponseSerializer,
    429: InstantEvalLimitErrorSerializer,
}


# ─── Serializers ─────────────────────────────────────────────────────────────
class InstantEvalScopeSerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    unit = serializers.ChoiceField(choices=UNITS)
    # The view's filters, as its grid sends them (the window included). The
    # session list's filter contract is the widest of the grids'.
    filters = SessionFilterListField(required=False, default=list)
    # Rows handed over by id (a selection, an Insights category).
    ids = serializers.ListField(
        child=serializers.CharField(max_length=255),
        required=False,
        max_length=MAX_HANDOVER_IDS,
    )
    # None (or absent) reads every row of the view. Up to the view's rows, and
    # the rows one run reads (``sample_error`` and the start's 400 say so).
    sample_size = serializers.IntegerField(
        min_value=1,
        required=False,
        allow_null=True,
        error_messages={"min_value": "Choose a sample of at least 1."},
    )
    # The view's date option and how its scope reads ("Past 7D · 2 filters"),
    # kept with the run so it can be asked again: the page re-anchors a
    # preset's window to now, and a custom window stays.
    date_preset = serializers.ChoiceField(
        choices=DATE_PRESETS, required=False, allow_null=True
    )
    label = serializers.CharField(
        max_length=300, required=False, allow_blank=True, allow_null=True
    )
    # The Users view's search text: a user question reads only the users it
    # finds.
    search = serializers.CharField(max_length=200, required=False, allow_blank=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["unit"] != "users" and (attrs.get("search") or "").strip():
            raise serializers.ValidationError(
                {"search": "Search text only narrows a question about users."}
            )
        if attrs["unit"] == "users":
            validate_users_filter_capabilities(attrs.get("filters") or [])
            if refused := users_service.unsupported_filter_message(
                attrs.get("filters") or []
            ):
                raise serializers.ValidationError(refused)
        # A window the lists would refuse is refused here too, instead of
        # falling back to the last 30 days.
        if problem := rows_service.window_problem(attrs.get("filters") or []):
            raise serializers.ValidationError(problem)
        return attrs


class InstantEvalEstimateRequestSerializer(InstantEvalScopeSerializer):
    questions = serializers.IntegerField(
        min_value=1, max_value=MAX_QUESTIONS, required=False, default=1
    )


class InstantEvalChoiceSerializer(serializers.Serializer):
    sample_size = serializers.IntegerField(allow_null=True)
    rows_to_read = serializers.IntegerField()
    # Null where Instant eval isn't billed (off Cloud, or no billing config),
    # as is every credit figure below.
    credits_estimate = serializers.FloatField(allow_null=True)
    seconds_estimate = serializers.IntegerField()
    needs_confirm = serializers.BooleanField()
    allowed = serializers.BooleanField()
    # More credits than the organization has left, or billing stops runs.
    credit_limited = serializers.BooleanField()


class InstantEvalCreditCheckSerializer(serializers.Serializer):
    # Whether the chosen size (at the questions asked) can start.
    allowed = serializers.BooleanField()
    # FREE_TIER_LIMIT, BUDGET_LIMIT, BUDGET_PAUSED, PAYMENT_REQUIRED, ...:
    # why the chosen size can't start, else why a larger run can't (the cap
    # ``credits_left`` is: the plan's, or a pause budget's), else null.
    error_code = serializers.CharField(allow_null=True)
    current_usage = serializers.FloatField(allow_null=True)
    limit = serializers.FloatField(allow_null=True)
    # Credits left this month, or None when no cap this view can reach.
    credits_left = serializers.FloatField(allow_null=True)
    # A paused budget or an unpaid invoice: no run can start.
    blocked = serializers.BooleanField()
    message = serializers.CharField(allow_null=True)


class InstantEvalSampleErrorSerializer(serializers.Serializer):
    # INSTANT_EVAL_SAMPLE_TOO_LARGE.
    error_code = serializers.CharField()
    message = serializers.CharField()
    # The largest sample, in the view's unit, that can run.
    sample_size = serializers.IntegerField()


class InstantEvalEstimateSerializer(serializers.Serializer):
    population = serializers.IntegerField()
    population_is_lower_bound = serializers.BooleanField()
    rows_to_read = serializers.IntegerField()
    questions = serializers.IntegerField()
    credits_estimate = serializers.FloatField(allow_null=True)
    # What Jev charges for one answer, in credits.
    credits_per_answer = serializers.FloatField(allow_null=True)
    seconds_estimate = serializers.IntegerField()
    needs_confirm = serializers.BooleanField()
    max_rows = serializers.IntegerField()
    confirm_rows = serializers.IntegerField()
    default_sample_size = serializers.IntegerField(allow_null=True)
    # Sessions read per user, for a user question.
    sessions_per_user = serializers.IntegerField(allow_null=True)
    choices = InstantEvalChoiceSerializer(many=True)
    # The organization's credit cap against the chosen size, and the
    # largest sample (in the view's unit) its credits left pay for.
    credit_check = InstantEvalCreditCheckSerializer()
    credit_max_sample = serializers.IntegerField(allow_null=True)
    # The absolute window the run reads: {"from", "to"}.
    window = serializers.JSONField()
    # Why the chosen sample size can't run (more than the view holds, or than
    # one run reads), or None. Starting it is refused with the same message.
    sample_error = InstantEvalSampleErrorSerializer(allow_null=True)


class InstantEvalEstimateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = InstantEvalEstimateSerializer()


class InstantEvalStartRequestSerializer(InstantEvalScopeSerializer):
    config_ids = serializers.ListField(
        child=serializers.UUIDField(), min_length=1, max_length=MAX_QUESTIONS
    )
    confirm = serializers.BooleanField(required=False, default=False)
    # The rows the person confirmed (the browser's count). A start that would
    # read more than that asks again with the server's numbers, so a confirm
    # never covers more than it said.
    confirmed_rows = serializers.IntegerField(min_value=0, required=False)


class InstantEvalStartSerializer(serializers.Serializer):
    task_id = serializers.CharField()
    unit = serializers.CharField()
    population = serializers.IntegerField()
    rows_requested = serializers.IntegerField()
    is_sample = serializers.BooleanField()
    credits_estimate = serializers.FloatField(allow_null=True)
    date_preset = serializers.CharField(allow_null=True)
    label = serializers.CharField(allow_null=True, allow_blank=True)


class InstantEvalStartResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = InstantEvalStartSerializer()


class InstantEvalRunQuerySerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    eval_task_id = serializers.UUIDField()
    unit = serializers.ChoiceField(choices=UNITS, required=False)
    config_ids = serializers.CharField(
        max_length=2000, required=False, allow_blank=True
    )
    # The viewer's IANA time zone: every time bucket is local to it.
    tz = serializers.CharField(max_length=64, required=False, default="UTC")


class InstantEvalGroupListField(serializers.ListField):
    def get_value(self, dictionary):
        # Read POST preserves JSON values inside a QueryDict. Its getlist()
        # would otherwise wrap the JSON array as one nested list; GET still
        # reads repeated parameters through ListField's normal path.
        value = dictionary.get(self.field_name)
        if isinstance(value, list):
            return value
        return super().get_value(dictionary)


class InstantEvalRowsQuerySerializer(InstantEvalRunQuerySerializer):
    config_id = serializers.UUIDField(required=False)
    answer = serializers.CharField(max_length=500, required=False)
    # [{"config_id", "answer"}, ...] as JSON: every one must hold.
    answers = serializers.CharField(max_length=4000, required=False)
    time_from = serializers.CharField(max_length=64, required=False)
    time_to = serializers.CharField(max_length=64, required=False)
    # "<dim>:<key>"; repeat the parameter for any of several groups. A key is
    # taken whole (it may hold a comma). A pattern can select one key per
    # answered row, even when its custom range spans more than 400 days.
    group = InstantEvalGroupListField(
        child=serializers.CharField(max_length=300),
        required=False,
        max_length=max_rows(),
    )
    user = serializers.CharField(max_length=255, required=False)
    model = serializers.CharField(max_length=255, required=False)
    error = serializers.BooleanField(required=False, allow_null=True, default=None)
    unsure = serializers.BooleanField(required=False, default=False)
    q = serializers.CharField(max_length=200, required=False, allow_blank=True)
    cursor = serializers.CharField(max_length=500, required=False, allow_blank=True)
    page_size = serializers.IntegerField(
        min_value=1, max_value=MAX_COMPACT_PAGE, required=False, default=50
    )
    # Rows for the charts' squares: up to 1,500 a page, without explanations.
    compact = serializers.BooleanField(required=False, default=False)
    sort = serializers.ChoiceField(
        choices=["time", "-time", "confidence"], required=False, default="-time"
    )


class InstantEvalExportQuerySerializer(InstantEvalRowsQuerySerializer):
    # A user question's sessions, a line each, instead of a line per user.
    sessions = serializers.BooleanField(required=False, default=False)


class InstantEvalQuestionSerializer(serializers.Serializer):
    config_id = serializers.CharField()
    question = serializers.CharField(allow_blank=True)
    answer_type = serializers.CharField()
    labels = serializers.ListField(child=serializers.CharField())


class InstantEvalPersonSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField(allow_blank=True)
    email = serializers.CharField(allow_blank=True)


class InstantEvalSampleSerializer(serializers.Serializer):
    population = serializers.IntegerField()
    population_is_lower_bound = serializers.BooleanField()
    requested = serializers.IntegerField()
    read = serializers.IntegerField()
    is_sample = serializers.BooleanField()
    can_extend = serializers.BooleanField()
    next_size = serializers.IntegerField()


class InstantEvalFailureSerializer(serializers.Serializer):
    # The explanation most of the run's failed answers carry, and whether
    # every one of them carries it.
    message = serializers.CharField()
    every = serializers.BooleanField()
    # A missing or rejected TypeSafe key, fixed under Settings → AI Providers.
    ai_providers = serializers.BooleanField()


class InstantEvalSummarySerializer(serializers.Serializer):
    status = serializers.CharField()
    unit = serializers.CharField()
    preparing = serializers.BooleanField()
    total = serializers.IntegerField()
    answered = serializers.IntegerField()
    failed = serializers.IntegerField()
    pending = serializers.IntegerField()
    not_read = serializers.IntegerField()
    started_at = serializers.CharField(allow_null=True)
    finished_at = serializers.CharField(allow_null=True)
    rate_per_min = serializers.FloatField(allow_null=True)
    eta_s = serializers.IntegerField(allow_null=True)
    created_by = InstantEvalPersonSerializer(allow_null=True)
    # Both null where Instant eval isn't billed.
    credits_estimate = serializers.FloatField(allow_null=True)
    # What the answers given so far cost (a failed answer isn't billed).
    credits_used = serializers.FloatField(allow_null=True)
    # Paused, with this many rows still being read (their answers will land).
    in_flight = serializers.IntegerField(allow_null=True)
    # Running, but no answer has landed for this many seconds (two minutes
    # or more): the workers are busy elsewhere, or coming back.
    waiting_s = serializers.IntegerField(allow_null=True)
    # A finished run that answered nothing: why, from its rows.
    failure = InstantEvalFailureSerializer(allow_null=True, required=False)
    # How long the run actually answered (minutes in which answers landed),
    # and how long it sat paused or stopped since it started.
    active_s = serializers.IntegerField(allow_null=True)
    idle_s = serializers.IntegerField(allow_null=True)
    sample = InstantEvalSampleSerializer()
    window = serializers.JSONField()
    scope = serializers.JSONField()
    questions = InstantEvalQuestionSerializer(many=True)
    counts = serializers.JSONField()
    unsure = serializers.JSONField()
    timeline = serializers.JSONField()
    groups = serializers.JSONField()
    cross = serializers.JSONField()
    # A user question's sessions read, behind its users; null otherwise.
    sessions_read = serializers.IntegerField(allow_null=True, required=False)


class InstantEvalSummaryResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = InstantEvalSummarySerializer()


class InstantEvalRowsResultSerializer(serializers.Serializer):
    rows = serializers.ListField(child=serializers.JSONField())
    next_cursor = serializers.CharField(allow_null=True)
    total = serializers.IntegerField()


class InstantEvalRowsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = InstantEvalRowsResultSerializer()


class InstantEvalControlRequestSerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    eval_task_id = serializers.UUIDField()
    action = serializers.ChoiceField(
        choices=["pause", "resume", "cancel", "retry_failed", "read_more"]
    )
    # read_more: how many more rows; absent or null reads the rest of the view.
    more = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    confirm = serializers.BooleanField(required=False, default=False)


class InstantEvalControlSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    rows_requested = serializers.IntegerField(required=False)
    retried = serializers.IntegerField(required=False)


class InstantEvalControlResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = InstantEvalControlSerializer()


# ─── Helpers ─────────────────────────────────────────────────────────────────


# Credit refusals that no smaller run gets past.
CREDIT_MESSAGES = {
    "BUDGET_PAUSED": (
        "Jev runs are paused by your budget limit. Raise or remove it in "
        "billing to run this."
    ),
    "PAYMENT_REQUIRED": (
        "Your subscription has unpaid invoices. Update your payment method "
        "in billing to run this."
    ),
}


CREDIT_CHECK_UNAVAILABLE = (
    "Credits can't be checked right now, so the run wasn't started. "
    "Please retry in a moment."
)

# Refusals that say what is left, so a smaller run may fit: the free tier's
# credits, or a pause budget the run would cross.
CAP_CODES = ("FREE_TIER_LIMIT", "BUDGET_LIMIT")


def credit_check_unavailable(gm):
    return gm.custom_error_response(
        503, CREDIT_CHECK_UNAVAILABLE, code="credit_check_unavailable"
    )


def shown_credits_per_answer() -> float | None:
    """The rate for what only shows credits (a run's summary, History): no
    credits shown, rather than a page that won't open, while a Cloud's
    billing config fails to load. Whatever starts answers refuses instead."""
    try:
        return credits_per_answer()
    except CreditCheckUnavailable:
        return None


def _credits_left(*results) -> float | None:
    """Credits left this month, from whichever refusal reported the cap."""
    for result in results:
        if result is not None and result.error_code in CAP_CODES:
            return max(0.0, float(result.limit or 0) - float(result.current_usage or 0))
    return None


def _blocked(*results) -> bool:
    """Billing stops every run: a paused budget, an unpaid invoice."""
    return any(
        result is not None and result.error_code not in ("", *CAP_CODES)
        for result in results
    )


def _fits(
    left: float | None, unit: str, questions: int, population: int, rate: float | None
) -> int | None:
    """The largest sample, in the view's unit, that ``left`` credits pay for
    at ``rate`` credits per answer."""
    if left is None or not rate:
        return None
    per_unit = _rows_for(unit, 1) * max(1, questions) * rate
    return min(population, int((left + 1e-9) // per_unit))


def _amount(credits: float) -> str:
    """Credits as people read them, as the Ask page prints them: whole from
    10 up, else to a tenth ("0.7")."""
    if credits >= 10:
        return f"{credits:,.0f}"
    return f"{credits:.1f}".rstrip("0").rstrip(".")


def _credit_count(credits: float) -> str:
    """Credits with their noun: "120 credits", "0.7 credits", "1 credit"."""
    amount = _amount(credits)
    return f"{amount} credit{'' if amount == '1' else 's'}"


def _credit_message(result, credits: float, left: float | None, fits, unit) -> str:
    if result.error_code in CAP_CODES and left is not None:
        budget = result.error_code == "BUDGET_LIMIT"
        some = left >= 0.05
        if budget:
            have = f"your budget limit allows {_amount(left) if some else 'no'} more"
        else:
            have = f"you have {_amount(left) if some else 'none'} left"
        message = f"This run needs about {_credit_count(credits)}; {have} this month."
        if fits:
            return f"{message} A sample of up to {fits:,} {unit} fits."
        if budget:
            return f"{message} Raise or remove it in billing to run this."
        return f"{message} Upgrade your plan in billing to run it."
    return CREDIT_MESSAGES.get(result.error_code) or (
        result.reason or "Your plan doesn't allow this run right now."
    )


def _credit_refusal(gm, refused, credits, unit, questions, population, rate):
    """Billing refused a run costing ``credits``: why, and the largest
    sample of ``population`` that fits what is left."""
    left = _credits_left(refused)
    fits = None if _blocked(refused) else _fits(left, unit, questions, population, rate)
    return gm.custom_error_response(
        402 if refused.error_code == "PAYMENT_REQUIRED" else 403,
        {
            "message": _credit_message(refused, credits, left, fits, unit),
            "error_code": refused.error_code or "INSTANT_EVAL_CREDITS",
            "credits_estimate": credits,
            "credits_left": left,
            "sample_size": fits or None,
            "rows_to_read": _rows_for(unit, fits) if fits else 0,
        },
        code="instant_eval_credits",
    )


def _confirm_response(gm, reading: str, rows: int, credits: float | None):
    """409 holding a large run for its confirm: "Reading 6,000 rows costs
    about 360 credits. Confirm to start.", without the credits where the run
    isn't billed."""
    cost = f" costs about {credits:,.0f} credits" if credits is not None else ""
    return gm.custom_error_response(
        409,
        {
            "message": f"{reading}{cost}. Confirm to start.",
            "error_code": "INSTANT_EVAL_CONFIRM",
            "rows_to_read": rows,
            "credits_estimate": credits,
        },
        code="confirm_required",
    )


def _seconds(rows: int, questions: int) -> int:
    return int(math.ceil(10 + rows * questions / answers_per_minute() * 60))


def _choice(
    unit: str,
    population: int,
    size: int | None,
    questions: int,
    rate: float | None,
    left: float | None = None,
    blocked: bool = False,
) -> dict:
    count = population if size is None else min(size, population)
    rows = _rows_for(unit, count)
    credits = _credits(rows, questions, rate)
    limited = blocked or (
        left is not None and credits is not None and credits > left + 1e-9
    )
    return {
        "sample_size": size,
        "rows_to_read": rows,
        "credits_estimate": credits,
        "seconds_estimate": _seconds(rows, questions),
        "needs_confirm": rows > confirm_rows(),
        "allowed": 0 < rows <= max_rows() and not limited,
        "credit_limited": limited,
    }


SAMPLE_TOO_LARGE = "INSTANT_EVAL_SAMPLE_TOO_LARGE"


def _noun(unit: str, n: int) -> str:
    return unit[:-1] if n == 1 else unit


def _sample_error(
    unit: str, size: int | None, population: int, lower_bound: bool = False
) -> dict | None:
    """Why a chosen sample can't run, with the largest that can: more rows
    than one run reads, or (when that isn't the limit) more than the view
    holds. A user question's sample is in users and its rows are their
    sessions; a view still being counted is held to the rows alone."""
    if size is None or population <= 0:
        return None
    per_unit = _rows_for(unit, 1)
    cap = max_rows() // per_unit
    if lower_bound or population > cap:
        if size <= cap:
            return None
        largest = cap
        per = f" ({cap:,} users at {per_unit} sessions each)" if unit == "users" else ""
        message = (
            f"One run reads at most {max_rows():,} rows{per}; choose up to {cap:,}."
        )
    else:
        if size <= population:
            return None
        largest = population
        verb = "is" if population == 1 else "are"
        message = (
            f"There {verb} only {population:,} {_noun(unit, population)} in this "
            f"view; choose up to {population:,}."
        )
    return {"error_code": SAMPLE_TOO_LARGE, "message": message, "sample_size": largest}


def _active_runs(org):
    return list(
        InstantEvalRun.objects.filter(
            organization=org,
            cancelled_at__isnull=True,
            eval_task__deleted=False,
            eval_task__status__in=ACTIVE,
        )
        # A continuous task never finishes: it is not a run in flight.
        .exclude(eval_task__run_type=RunType.CONTINUOUS)
        .select_related("eval_task", "created_by")
        .order_by("created_at")
    )


def _busy_response(gm, runs):
    """429 naming the runs already in flight."""
    described = []
    for run in runs:
        name = (run.eval_task.name or "").removeprefix(f"{INSTANT_TASK_PREFIX} · ")
        who = run.created_by.name if run.created_by else "someone"
        described.append(
            {
                "task_id": str(run.eval_task_id),
                "project_id": str(run.project_id),
                "question": name,
                "rows": run.rows_requested,
                "started_by": who,
                "started_at": _iso(run.created_at),
            }
        )
    names = "; ".join(
        f"“{d['question']}” ({d['rows']:,} rows, started by {d['started_by']})"
        for d in described
    )
    message = (
        f"Your organization already has {len(runs)} Instant eval "
        f"run{'s' if len(runs) != 1 else ''} in progress, the most it runs at once: {names}. "
        "Pause or cancel one, or wait for it to finish."
    )
    return gm.custom_error_response(
        429,
        {"message": message, "error_code": "INSTANT_EVAL_BUSY", "runs": described},
        code="instant_eval_busy",
    )


JEV_KEY_MISSING = "JEV_KEY_MISSING"
AI_PROVIDERS_SETTINGS_PATH = "/dashboard/settings/ai-providers"


def _questions(task):
    """A task's questions with their templates, for the Jev key check."""
    return task.evals.select_related("eval_template")


def _jev_key_missing(gm, project, configs):
    """400 when a question runs on Jev and the organization has no TypeSafe
    key saved (the lookup the engine makes for every answer): every row would
    fail on it, so the run doesn't start. None when it can run."""
    from model_hub.utils.jev_templates import binding_model
    from tfc.ee_gates import is_jev_model

    if not any(
        is_jev_model(
            binding_model(c.eval_template, model=c.model, runtime_config=c.config)
        )
        for c in configs
    ):
        return None
    from accounts.models.workspace import Workspace
    from agentic_eval.core_evals.run_prompt.litellm_models import (
        MissingProviderKeyError,
    )
    from evaluations.engine.instance import _get_typesafe_api_key
    from tfc.middleware.workspace_context import (
        get_current_workspace,
        set_workspace_context,
    )

    # Read as the eval runner reads it, outside any request: in the project's
    # workspace (else the organization's default), falling back to a key saved
    # for the whole organization, which the request's workspace scope would
    # hide from a non-default workspace.
    scope = get_current_workspace()
    set_workspace_context(workspace=None)
    try:
        workspace_id = project.workspace_id or (
            Workspace.objects.filter(
                organization_id=project.organization_id,
                is_default=True,
                is_active=True,
            )
            .values_list("id", flat=True)
            .first()
        )
        _get_typesafe_api_key(
            str(project.organization_id), str(workspace_id) if workspace_id else None
        )
    except MissingProviderKeyError as missing:
        return gm.custom_error_response(
            400,
            {
                # Names the TypeSafe key, as every Jev answer's lookup would.
                "message": str(missing),
                "error_code": JEV_KEY_MISSING,
                "settings_path": AI_PROVIDERS_SETTINGS_PATH,
            },
            code=JEV_KEY_MISSING,
        )
    finally:
        set_workspace_context(workspace=scope)
    return None


def _start_workflow_on_commit(task, *, replace: bool) -> None:
    from tfc.temporal.eval_tasks.client import start_eval_task_workflow_sync

    def start():
        try:
            start_eval_task_workflow_sync(task, replace_existing=replace)
        except Exception:
            logger.exception("instant_eval_workflow_start_failed", task_id=str(task.id))
            EvalTask.objects.filter(id=task.id).update(status=EvalTaskStatus.FAILED)

    transaction.on_commit(start)


def _resolve_scope(
    request, data, *, finish: bool = False
) -> tuple[rows_service.Population, list[str] | None]:
    """The view's population: the handed-over ids, or the grid's own count.
    Users handed over keep the view's window: their sessions are read in it."""
    from tracer.services.filter_principal_context import (
        bind_request_my_annotations_principal,
    )

    unit = data["unit"]
    ids = data.get("ids")
    if ids:
        ids = list(dict.fromkeys(str(i) for i in ids if i))
        pinned, start, end = [], None, None
        if unit == "users":
            pinned, start, end = rows_service._frozen_filters(
                list(data.get("filters") or []), rows_service.ROW_TYPE[unit]
            )
        return rows_service.Population(len(ids), False, start, end, pinned), ids
    filters = bind_request_my_annotations_principal(
        request, list(data.get("filters") or [])
    )
    population = users_service.count_view(
        str(data["project_id"]),
        unit,
        filters,
        search=data.get("search"),
        finish=finish,
    )
    return population, None


def _window(population: rows_service.Population) -> dict:
    return {"from": _iso(population.date_from), "to": _iso(population.date_to)}


# ─── Estimate ────────────────────────────────────────────────────────────────
# A count and a price, open to Viewers: nothing is written or spent.
@workspace_read_only
class InstantEvalEstimateView(InstantEvalLicenseRequired, APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=InstantEvalEstimateRequestSerializer,
        responses={
            200: InstantEvalEstimateResponseSerializer,
            422: ApiErrorResponseSerializer,
            **ERRORS,
        },
    )
    def post(self, request):
        """How many rows a view holds, and what a sample or all of it costs."""
        data = request.validated_data
        project = _project(request, data["project_id"])
        if project is None:
            return self._gm.not_found("Project not found")
        unit = data["unit"]
        try:
            population, ids = _resolve_scope(request, data)
        except rows_service.PopulationUnavailable as exc:
            return self._gm.custom_error_response(
                422, str(exc), code="population_unavailable"
            )
        except Exception:
            logger.exception("instant_eval_estimate_failed")
            return self._gm.custom_error_response(
                503, "The view can't be counted right now. Please retry."
            )
        count, lower = population.count, population.lower_bound
        questions = data.get("questions") or 1
        size = data.get("sample_size")
        org = _org(request)
        chosen_rows = _rows_for(unit, count if size is None else min(size, count))
        # The chosen run, and the most the composer can ask (every row, every
        # question): a refusal of either reports the cap, so each choice and
        # any number of questions can be judged from it.
        try:
            # Read once: every figure below is priced at it.
            rate = credits_per_answer()
            chosen_credits = _credits(chosen_rows, questions, rate)
            refused = check_credits(org, chosen_credits)
            ceiling = check_credits(
                org, _credits(_rows_for(unit, count), MAX_QUESTIONS, rate)
            )
        except CreditCheckUnavailable:
            return credit_check_unavailable(self._gm)
        left = _credits_left(refused, ceiling)
        blocked = _blocked(refused, ceiling)
        fits = None if blocked else _fits(left, unit, questions, count, rate)
        default = DEFAULT_SAMPLE if count > DEFAULT_SAMPLE and not ids else None
        sizes = [s for s in SAMPLE_SIZES if s < count] if not ids else []
        choices = [
            _choice(unit, count, s, questions, rate, left, blocked) for s in sizes
        ] + [_choice(unit, count, None, questions, rate, left, blocked)]
        chosen = _choice(unit, count, size, questions, rate, left, blocked)
        reported = refused or ceiling
        credit_check = {
            "allowed": refused is None,
            "error_code": reported.error_code if reported else None,
            "current_usage": getattr(reported, "current_usage", None),
            "limit": getattr(reported, "limit", None),
            "credits_left": left,
            "blocked": blocked,
            "message": (
                _credit_message(refused, chosen_credits, left, fits, unit)
                if refused
                else None
            ),
        }
        return self._gm.success_response(
            {
                "population": count,
                "population_is_lower_bound": lower,
                "rows_to_read": chosen["rows_to_read"],
                "questions": questions,
                "credits_estimate": chosen["credits_estimate"],
                "credits_per_answer": rate,
                "seconds_estimate": chosen["seconds_estimate"],
                "needs_confirm": chosen["needs_confirm"],
                "max_rows": max_rows(),
                "confirm_rows": confirm_rows(),
                "default_sample_size": default,
                "sessions_per_user": (
                    rows_service.SESSIONS_PER_USER if unit == "users" else None
                ),
                "choices": choices,
                "credit_check": credit_check,
                "credit_max_sample": fits,
                "window": _window(population),
                "sample_error": (
                    None if ids else _sample_error(unit, size, count, lower)
                ),
            }
        )


# ─── Start ───────────────────────────────────────────────────────────────────
class InstantEvalStartView(InstantEvalLicenseRequired, APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=InstantEvalStartRequestSerializer,
        responses={200: InstantEvalStartResponseSerializer, **LIMIT_ERRORS},
    )
    def post(self, request):
        """Start a run: the server resolves the view's rows (or reads the rows
        handed over), enforces the size and concurrency limits, and starts the
        eval task. The rows are picked by the task's first step."""
        data = request.validated_data
        project = _project(request, data["project_id"])
        if project is None:
            return self._gm.not_found("Project not found")
        org = _org(request)
        unit = data["unit"]
        wanted = [str(c) for c in data["config_ids"]]
        configs = {
            str(c.id): c
            for c in CustomEvalConfig.objects.filter(
                id__in=wanted, project=project, deleted=False
            ).select_related("eval_template")
        }
        if len(configs) != len(set(wanted)) or not all(
            (c.config or {}).get("instant_eval") for c in configs.values()
        ):
            return self._gm.bad_request("Those questions aren't saved in this project.")
        if missing := _jev_key_missing(self._gm, project, configs.values()):
            return missing
        active = _active_runs(org)
        if len(active) >= max_active():
            return _busy_response(self._gm, active)
        try:
            # Every user the view lists is counted before a run starts.
            population, ids = _resolve_scope(request, data, finish=True)
        except users_service.UsersCountIncomplete as exc:
            # What was counted is kept: starting again carries on from there.
            return self._gm.custom_error_response(
                503, str(exc), code="users_still_counting"
            )
        except rows_service.PopulationUnavailable as exc:
            return self._gm.custom_error_response(
                422, str(exc), code="population_unavailable"
            )
        except Exception:
            logger.exception("instant_eval_start_count_failed")
            return self._gm.custom_error_response(
                503, "The view can't be read right now. Please retry."
            )
        count, lower = population.count, population.lower_bound
        size = None if ids else data.get("sample_size")
        requested = count if size is None else min(size, count)
        questions = len(wanted)
        rows = _rows_for(unit, requested)
        if requested <= 0:
            return self._gm.bad_request(
                f"There are no {unit} in this view to ask about."
            )
        if refused := _sample_error(unit, size, count):
            return self._gm.custom_error_response(
                400, refused["message"], code=SAMPLE_TOO_LARGE.lower()
            )
        if rows > max_rows():
            return self._gm.custom_error_response(
                400,
                f"A run reads at most {max_rows():,} rows and this one would read {rows:,}. "
                "Read a sample, or narrow the view.",
                code="instant_eval_too_large",
            )
        try:
            rate = credits_per_answer()
            credits = _credits(rows, questions, rate)
            refused = check_credits(org, credits)
        except CreditCheckUnavailable:
            return credit_check_unavailable(self._gm)
        if refused is not None:
            return _credit_refusal(
                self._gm, refused, credits, unit, questions, count, rate
            )
        confirmed = data.get("confirmed_rows")
        if rows > confirm_rows() and (
            not data.get("confirm") or (confirmed is not None and rows > confirmed)
        ):
            return _confirm_response(self._gm, f"Reading {rows:,} rows", rows, credits)
        ordered = [configs[c] for c in dict.fromkeys(wanted)]
        first = _question(ordered[0])["question"]
        filters = {
            "project_id": str(project.id),
            rows_service.RUN_MARKER: True,
            # The questions in the order they were asked.
            "instant_config_ids": [str(c.id) for c in ordered],
        }
        if ids:
            filters["instant_ids"] = ids
        with transaction.atomic():
            # Two starts at once both passed the check above. The organization's
            # row lock serializes them, and the count is taken again under it.
            from accounts.models.organization import Organization

            Organization.objects.select_for_update().filter(id=org.id).first()
            active = _active_runs(org)
            if len(active) >= max_active():
                return _busy_response(self._gm, active)
            task = EvalTask.objects.create(
                project=project,
                name=f"{INSTANT_TASK_PREFIX} · {first}"[:255],
                filters=filters,
                sampling_rate=100,
                spans_limit=rows,
                run_type=RunType.HISTORICAL,
                row_type=rows_service.ROW_TYPE[unit],
                status=EvalTaskStatus.PENDING,
                last_run=timezone.now(),
            )
            task.evals.set(ordered)
            run = InstantEvalRun.objects.create(
                eval_task=task,
                project=project,
                organization=org,
                created_by=request.user if request.user.is_authenticated else None,
                unit=unit,
                row_type=rows_service.ROW_TYPE[unit],
                filters=population.filters,
                date_from=population.date_from,
                date_to=population.date_to,
                date_preset=data.get("date_preset"),
                label=(data.get("label") or "").strip() or None,
                search=(data.get("search") or "").strip(),
                population=count,
                population_is_lower_bound=lower,
                sample_size=size,
                rows_requested=requested,
                questions=questions,
                credits_estimate=credits or 0,
                # Its answers are recorded as they land.
                answers_synced=True,
            )
            _start_workflow_on_commit(task, replace=False)
        return self._gm.success_response(
            {
                "task_id": str(task.id),
                "unit": unit,
                "population": count,
                "rows_requested": requested,
                "is_sample": requested < count,
                "credits_estimate": credits,
                "date_preset": run.date_preset,
                "label": run.label,
            }
        )


# ─── Reading a run ───────────────────────────────────────────────────────────
def _load(request, params, gm):
    project = _project(request, params["project_id"])
    if project is None:
        return None, gm.not_found("Project not found")
    task = (
        EvalTask.objects.filter(
            id=params["eval_task_id"], project=project, deleted=False
        )
        .prefetch_related("evals")
        .first()
    )
    if task is None or not _is_instant_task(task):
        # Only an Instant eval run is read (and given a run record) here: a
        # read of any other eval task would write rows and a run for it, and
        # a continuous one would then count against the org's runs forever.
        return None, gm.not_found("Eval task not found")
    return task, None


def _is_instant_task(task) -> bool:
    """A task the Ask page started (it carries RUN_MARKER), or a one-off task
    whose questions are all Instant eval questions (asked through the plain
    eval-task API)."""
    if (task.filters or {}).get(rows_service.RUN_MARKER):
        return True
    if task.run_type != RunType.HISTORICAL:
        return False
    configs = list(task.evals.all())
    return bool(configs) and all((c.config or {}).get("instant_eval") for c in configs)


def ensure_rows(task, unit: str) -> None:
    """A run started before rows were stored (or by the plain eval-task API)
    gets them on its first read: the ids it named, or the rows it answered."""
    if InstantEvalRow.objects.filter(task_id=task.id).exists():
        return
    row_type = rows_service.ROW_TYPE.get(unit, "traces")
    ids = _task_ids(task, unit)
    if not ids:
        key = {"traces": "trace_id", "spans": "observation_span_id"}.get(
            row_type, "trace_session_id"
        )
        ids = [
            str(v)
            for v in EvalLogger.objects.filter(eval_task_id=str(task.id), deleted=False)
            .exclude(**{f"{key}__isnull": True})
            .order_by()
            .values_list(key, flat=True)
            .distinct()[:MAX_ANSWER_ROWS]
        ]
    if not ids:
        return
    facets = rows_service.read_facets(str(task.project_id), row_type, ids)
    rows_service.write_rows(
        task.id, unit, [(rid, i) for i, rid in enumerate(ids)], facets
    )


# A rate the caller hasn't read: read only if it's needed.
_UNREAD = object()


def _prepared_run(task, params, rate=_UNREAD) -> InstantEvalRun:
    """The run record, its rows and their answers in place. A task asked
    before runs were recorded (or through the plain eval-task API) gets them
    on its first read, its credits priced at ``rate``."""
    run = _run_of(task)
    if run is None:
        if rate is _UNREAD:
            rate = shown_credits_per_answer()
        unit = _unit_of(task, None, params)
        ensure_rows(task, unit)
        stored = InstantEvalRow.objects.filter(task_id=task.id).count()
        run, _ = InstantEvalRun.objects.get_or_create(
            eval_task=task,
            defaults={
                "project_id": task.project_id,
                "organization_id": task.project.organization_id,
                "unit": unit,
                "row_type": rows_service.ROW_TYPE.get(unit, "traces"),
                "population": stored,
                "rows_requested": stored,
                "questions": task.evals.count() or 1,
                "credits_estimate": _credits(stored, task.evals.count() or 1, rate)
                or 0,
            },
        )
        run = _run_of(task)
    return run


def _ensure_answers(run) -> None:
    from tracer.services.instant_eval_answers import ensure_synced

    ensure_synced(run, check=False)


def _unanswered_rows(run) -> int:
    """Rows of a run still missing an answer to one of its questions."""
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM tracer_instant_eval_row r LEFT JOIN ("
            "SELECT row_id, count(*) AS c FROM tracer_instant_eval_answer "
            "WHERE task_id = %(task)s GROUP BY row_id) a ON a.row_id = r.id "
            "WHERE r.task_id = %(task)s AND COALESCE(a.c, 0) < %(k)s",
            {"task": str(run.eval_task_id), "k": max(1, run.questions)},
        )
        return int(cur.fetchone()[0])


def _rows_left(task, run) -> int:
    """The rows restarting a stopped run reads: those still missing an
    answer. Its first step picks the rows, so a run that stopped before
    storing any (it failed, was paused or lost its workflow first) reads
    every row it asked for, counted at the most that can be (a user's five
    sessions each) rather than read from ClickHouse here. A finished run may
    legitimately hold fewer rows, so its requested size is not treated as
    unfinished work."""
    if (
        task.status != EvalTaskStatus.COMPLETED
        and not InstantEvalRow.objects.filter(task_id=task.id).exists()
    ):
        return _rows_for(run.unit, run.rows_requested)
    return _unanswered_rows(run)


# Hundreds of KB of counts every 2 s compress about tenfold.
@method_decorator(gzip_page, name="dispatch")
class InstantEvalSummaryView(InstantEvalLicenseRequired, APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=InstantEvalRunQuerySerializer,
        responses={200: InstantEvalSummaryResponseSerializer, **ERRORS},
    )
    def get(self, request):
        """Everything the Ask page's charts draw for one run, as counts:
        progress, answer totals, a timeline in the viewer's time zone, and the
        counts per day, hour, user, model, turns and error."""
        params = request.validated_query_data
        task, error = _load(request, params, self._gm)
        if error:
            return error
        # Read once per poll: the run's credits and what it has used so far.
        price = shown_credits_per_answer()
        try:
            run = _prepared_run(task, params, price)
            view, questions = _view(task, run, params)
        except summary_service.BadFilter as exc:
            return self._gm.bad_request(str(exc))
        except Exception:
            logger.exception(
                "instant_eval_summary_prepare_failed", task_id=str(task.id)
            )
            return self._gm.custom_error_response(
                503, "The run's answers can't be read right now. Please retry."
            )
        if run.last_tz != view.tz.key:
            InstantEvalRun.objects.filter(id=run.id).update(last_tz=view.tz.key)
        status = _status(task, run)
        live = status in ACTIVE
        # A finished run's summary never changes. A live run is counted again
        # at most every few seconds, shared by everyone watching it, so
        # polling costs one count per window, not one per poll.
        max_age = _live_summary_age(run.rows_requested) if live else None
        stale = None
        for kept in (status, STALLED):
            cached = cache.get(_summary_key(task, run, kept, view))
            if cached is None:
                continue
            age = time.time() - cached["at"]
            if kept == STALLED:
                fresh = age < STALL_CHECK_TTL
            else:
                fresh = not live or age < max_age
            if fresh:
                return self._etagged(request, cached["payload"])
            stale = stale or cached
        # One request counts at a time (per run, time zone and questions);
        # the others show the last count rather than count it again.
        lock = "ie-summary-lock:" + _summary_key(task, run, status, view)
        counting = cache.add(lock, 1, 30)
        if not counting and stale is not None:
            return self._etagged(request, stale["payload"])
        try:
            payload = compute_summary(task, run, view, questions, price)
        except summary_service.BadFilter as exc:
            return self._gm.bad_request(str(exc))
        except Exception:
            logger.exception("instant_eval_summary_failed", task_id=str(task.id))
            return self._gm.custom_error_response(
                503, "The run's answers can't be read right now. Please retry."
            )
        finally:
            if counting:
                cache.delete(lock)
        return self._etagged(request, payload)

    def _etagged(self, request, payload):
        body = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
        etag = '"' + hashlib.sha1(body.encode()).hexdigest() + '"'
        if request.headers.get("If-None-Match") == etag:
            response = Response(status=304)
        else:
            response = self._gm.success_response(payload)
        response["ETag"] = etag
        response["Cache-Control"] = "private, no-cache"
        return response


def _row_filter(params) -> summary_service.RowFilter:
    answers: list[tuple[str | None, str]] = []
    if params.get("answer"):
        answers.append(
            (
                str(params["config_id"]) if params.get("config_id") else None,
                params["answer"],
            )
        )
    if params.get("answers"):
        try:
            extra = json.loads(params["answers"])
        except ValueError:
            raise summary_service.BadFilter("Unreadable answers filter") from None
        if not isinstance(extra, list):
            raise summary_service.BadFilter("Unreadable answers filter")
        for item in extra[:10]:
            if isinstance(item, dict) and item.get("answer"):
                answers.append(
                    (str(item.get("config_id") or "") or None, str(item["answer"]))
                )
    groups = [str(g) for g in params.get("group") or [] if g]
    return summary_service.RowFilter(
        answers=answers,
        unsure=bool(params.get("unsure")),
        unsure_config=str(params["config_id"]) if params.get("config_id") else None,
        time_from=summary_service.parse_time(params.get("time_from")),
        time_to=summary_service.parse_time(params.get("time_to")),
        groups=groups,
        user=params.get("user") or None,
        model=params.get("model"),
        error=params.get("error"),
        q=(params.get("q") or "").strip() or None,
    )


class _InstantEvalSelectionView(InstantEvalLicenseRequired, APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def handle_exception(self, exc):
        if isinstance(exc, TooManyFieldsSent):
            return self._gm.bad_request(
                "Too many selection parameters. Send this query using POST "
                "with a JSON body instead."
            )
        return super().handle_exception(exc)


@method_decorator(gzip_page, name="dispatch")
class InstantEvalRowsView(_InstantEvalSelectionView):
    @validated_request(
        read_post=True,
        query_serializer=InstantEvalRowsQuerySerializer,
        responses={200: InstantEvalRowsResponseSerializer, **ERRORS},
    )
    def get(self, request):
        """One page of a run's rows with their answers, filtered by what the
        charts select: an answer, a time range, a group, unsure answers or text."""
        params = request.validated_query_data
        task, error = _load(request, params, self._gm)
        if error:
            return error
        try:
            run = _prepared_run(task, params)
            _ensure_answers(run)
            view, _ = _view(task, run, params)
            limit = MAX_COMPACT_PAGE if params.get("compact") else MAX_PAGE_SIZE
            page = summary_service.rows_page(
                view,
                _row_filter(params),
                cursor=params.get("cursor") or None,
                page_size=min(params["page_size"], limit),
                compact=bool(params.get("compact")),
                sort=params["sort"],
                sort_config=(
                    str(params["config_id"]) if params.get("config_id") else None
                ),
            )
        except summary_service.BadFilter as exc:
            return self._gm.bad_request(str(exc))
        except Exception:
            logger.exception("instant_eval_rows_failed", task_id=str(task.id))
            return self._gm.custom_error_response(
                503, "The run's rows can't be read right now. Please retry."
            )
        return self._gm.success_response(page)

    # Large chart selections carry the same query in JSON, without a URL limit.
    post = get


class InstantEvalExportView(_InstantEvalSelectionView):
    @validated_request(
        read_post=True,
        query_serializer=InstantEvalExportQuerySerializer,
        responses={
            200: openapi.Response(
                description="Every selected row with its answers, as CSV.",
                schema=openapi.Schema(type=openapi.TYPE_STRING),
            ),
            **ERRORS,
        },
    )
    def get(self, request):
        """Every row the filter selects, with each question's answer,
        confidence and explanation, streamed as CSV. A user question writes
        a line per user (their rolled-up answers and the sessions behind
        them), or with ``sessions`` a line per session of those users."""
        params = request.validated_query_data
        task, error = _load(request, params, self._gm)
        if error:
            return error
        try:
            run = _prepared_run(task, params)
            _ensure_answers(run)
            view, questions = _view(task, run, params)
            row_filter = _row_filter(params)
        except summary_service.BadFilter as exc:
            return self._gm.bad_request(str(exc))
        sessions = bool(params.get("sessions")) and view.by_user
        response = StreamingHttpResponse(
            summary_service.marked_export(
                summary_service.iter_export(
                    view,
                    row_filter,
                    questions,
                    sessions=sessions,
                    sort=params["sort"],
                    sort_config=(
                        str(params["config_id"]) if params.get("config_id") else None
                    ),
                )
            ),
            content_type="text/csv",
        )
        part = "-sessions" if sessions else "-users" if view.by_user else ""
        response["Content-Type"] = "text/csv; charset=utf-8"
        response["Content-Disposition"] = (
            f'attachment; filename="instant-eval-{task.id}{part}.csv"'
        )
        return response

    post = get


# ─── Control ─────────────────────────────────────────────────────────────────
class InstantEvalControlView(InstantEvalLicenseRequired, APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=InstantEvalControlRequestSerializer,
        responses={200: InstantEvalControlResponseSerializer, **LIMIT_ERRORS},
    )
    def post(self, request):
        """Pause, resume or cancel a run, retry the rows Jev failed on, or
        read more of a sample. Resuming, retrying and reading more count
        against the organization's runs in flight."""
        data = request.validated_data
        task, error = _load(request, data, self._gm)
        if error:
            return error
        run = _run_of(task)
        action = data["action"]
        handler = getattr(self, f"_{action}")
        try:
            return handler(request, task, run, data)
        except CreditCheckUnavailable:
            return credit_check_unavailable(self._gm)

    def _ok(self, task, message, **extra):
        task.refresh_from_db(fields=["status"])
        run = _run_of(task)
        return self._gm.success_response(
            {"status": _status(task, run), "message": message, **extra}
        )

    def _restarted(self, task, message, **extra):
        """The run started again, or (its workflow couldn't start, and the
        task was marked failed) a 503 that says so: never a success that
        reports the run failed."""
        task.refresh_from_db(fields=["status"])
        if task.status == EvalTaskStatus.FAILED:
            return self._gm.custom_error_response(
                503,
                "The run couldn't be started again right now. Resume it to carry on.",
                code="instant_eval_restart_failed",
            )
        return self._ok(task, message, **extra)

    def _cancelled(self):
        return self._gm.custom_error_response(
            409,
            {
                "message": "This run was cancelled. Use Run again to ask these "
                "questions again.",
                "error_code": "INSTANT_EVAL_CANCELLED",
            },
            code="instant_eval_cancelled",
        )

    def _busy(self, request, task):
        others = [r for r in _active_runs(_org(request)) if r.eval_task_id != task.id]
        return _busy_response(self._gm, others) if len(others) >= max_active() else None

    def _busy_locked(self, request, task):
        """``_busy`` counted again in the transaction that restarts the run,
        under the organization's row lock: two restarts (or a restart and a
        start) at once both passed the first count, and the lock serializes
        them as it does starts."""
        from accounts.models.organization import Organization

        Organization.objects.select_for_update().filter(id=_org(request).id).first()
        return self._busy(request, task)

    def _pause(self, request, task, run, data):
        from tfc.temporal.eval_tasks.client import signal_pause_eval_task_workflow

        if task.status not in ACTIVE:
            return self._gm.bad_request(
                f"The run is {task.status}; only a running run can be paused."
            )
        EvalTask.objects.filter(id=task.id).update(status=EvalTaskStatus.PAUSED)
        if run is not None:
            InstantEvalRun.objects.filter(id=run.id).update(
                answers_version=F("answers_version") + 1
            )
        try:
            signal_pause_eval_task_workflow(task.id)
        except Exception:
            # The paused status is what the workflow checks at every batch.
            logger.warning("instant_eval_pause_signal_failed", task_id=str(task.id))
        return self._ok(task, "Paused. Answers already being read will still land.")

    def _restart(self, task):
        EvalTask.objects.filter(id=task.id).update(status=EvalTaskStatus.PENDING)
        task.status = EvalTaskStatus.PENDING
        _start_workflow_on_commit(task, replace=True)

    def _resume(self, request, task, run, data):
        if run is not None and run.cancelled_at:
            return self._cancelled()
        resumable = task.status in RESUMABLE_TASK_STATUSES
        if not resumable and run is not None:
            # A run that stopped before finishing: its workflow is gone while
            # the task says running, or it finished with rows unanswered.
            if task.status in ACTIVE:
                cache.delete(f"ie-wf-gone:{task.id}")
                resumable = _workflow_gone(task)
            elif task.status == EvalTaskStatus.COMPLETED:
                resumable = _unanswered_rows(run) > 0
        if not resumable:
            return self._gm.bad_request(
                f"The run is {task.status}; only a paused or stopped run can be resumed."
            )
        if busy := self._busy(request, task):
            return busy
        # Checked again: the key may have gone since the run started.
        if missing := _jev_key_missing(self._gm, task.project, _questions(task)):
            return missing
        if run is not None:
            rate = credits_per_answer()
            left = _rows_left(task, run)
            credits = _credits(left, run.questions, rate)
            if refused := check_credits(_org(request), credits):
                return _credit_refusal(
                    self._gm, refused, credits, run.unit, run.questions, left, rate
                )
        with transaction.atomic():
            if busy := self._busy_locked(request, task):
                return busy
            if run is not None:
                InstantEvalRun.objects.filter(id=run.id).update(
                    answers_version=F("answers_version") + 1
                )
            # The fresh run reads only the rows still without an answer.
            self._restart(task)
        cache.delete(f"ie-wf-gone:{task.id}")
        return self._restarted(task, "Resumed.")

    def _cancel(self, request, task, run, data):
        from tfc.temporal.eval_tasks.client import signal_pause_eval_task_workflow

        if run is None:
            return self._gm.bad_request(
                "Only a run started from the Ask page can be cancelled."
            )
        if run.cancelled_at:
            return self._ok(task, "Already cancelled.")
        if task.status == EvalTaskStatus.COMPLETED:
            return self._gm.bad_request("The run has finished.")
        with transaction.atomic():
            InstantEvalRun.objects.filter(id=run.id).update(
                cancelled_at=timezone.now(), answers_version=F("answers_version") + 1
            )
            EvalTask.objects.filter(id=task.id).update(status=EvalTaskStatus.PAUSED)
        try:
            signal_pause_eval_task_workflow(task.id)
        except Exception:
            logger.warning("instant_eval_cancel_signal_failed", task_id=str(task.id))
        return self._ok(
            task, "Cancelled. Rows not read yet won't be read; the answers so far stay."
        )

    def _retry_failed(self, request, task, run, data):
        if run is not None and run.cancelled_at:
            return self._cancelled()
        failed = EvalLogger.objects.filter(
            eval_task_id=str(task.id),
            deleted=False,
            status__in=[EvalEntryStatus.ERRORED, EvalEntryStatus.SKIPPED],
        )
        if not failed.exists():
            return self._gm.bad_request("No rows failed.")
        # A stalled run (it says running, but no workflow drains it) is
        # restarted, or nothing would answer the requeued rows.
        running = task.status in ACTIVE and not _workflow_gone(task)
        if not running and (stopped := self._stopped_with_rows_left(task, run)):
            return stopped
        if not running and (busy := self._busy(request, task)):
            return busy
        # Checked again: the key may have gone since the run started.
        if missing := _jev_key_missing(self._gm, task.project, _questions(task)):
            return missing
        if run is not None:
            # Each failed entry is one answer to read again.
            rate = credits_per_answer()
            credits = _credits(failed.count(), 1, rate)
            if refused := check_credits(_org(request), credits):
                return _credit_refusal(self._gm, refused, credits, run.unit, 1, 0, rate)
        from tracer.services.instant_eval_answers import forget_answers

        with transaction.atomic():
            if not running and (busy := self._busy_locked(request, task)):
                return busy
            ids = list(failed.values_list("id", flat=True))
            retried = failed.update(
                status=EvalEntryStatus.PENDING,
                error=False,
                error_message=None,
                skipped_reason=None,
                attempts=0,
            )
            if not retried:
                return self._gm.bad_request("No rows failed.")
            # Their rows wait for an answer again.
            forget_answers(task.id, ids)
            if run is not None:
                InstantEvalRun.objects.filter(id=run.id).update(
                    answers_version=F("answers_version") + 1
                )
            # A run still draining claims the requeued rows itself.
            if not running:
                self._restart(task)
        return self._restarted(task, f"Retrying {retried:,} answers.", retried=retried)

    def _stopped_with_rows_left(self, task, run):
        """A 409 for a run that stopped with rows still to read (every row,
        if it stopped before picking them): paused, or stalled (its task
        ended, or its workflow is gone). Restarting it for a retry or "read
        more" would read all of them, past what that action checked and
        confirmed. Resuming is the way on: it checks them all."""
        if run is None or not _rows_left(task, run):
            return None
        message = (
            "This run is paused. Resume it to read the rest first."
            if task.status == EvalTaskStatus.PAUSED
            else "This run stopped before reading every row. Resume it to read "
            "the rest first."
        )
        return self._gm.custom_error_response(
            409,
            # The code the Ask page reads as "resume first", stalled or paused.
            {"message": message, "error_code": "INSTANT_EVAL_PAUSED"},
            code="instant_eval_paused",
        )

    def _read_more(self, request, task, run, data):
        # Refused before any work: a run still answering (or one paused with
        # answers still landing) reads more once it is done.
        if run is not None and run.cancelled_at:
            return self._cancelled()
        if task.status in ACTIVE or _in_flight(task):
            return self._gm.custom_error_response(
                409,
                {
                    "message": "This run is still answering; read more once it finishes.",
                    "error_code": "INSTANT_EVAL_STILL_ANSWERING",
                },
                code="instant_eval_still_answering",
            )
        if run is None or run.rows_requested >= run.population:
            return self._gm.bad_request("This run already reads every row of its view.")
        if stopped := self._stopped_with_rows_left(task, run):
            return stopped
        if busy := self._busy(request, task):
            return busy
        if missing := _jev_key_missing(self._gm, task.project, _questions(task)):
            return missing
        more = data.get("more")
        target = (
            run.population
            if more is None
            else min(run.population, run.rows_requested + more)
        )
        added = target - run.rows_requested
        rows = _rows_for(run.unit, target)
        if rows > max_rows():
            return self._gm.custom_error_response(
                400,
                f"A run reads at most {max_rows():,} rows; this would read {rows:,}.",
                code="instant_eval_too_large",
            )
        rate = credits_per_answer()
        credits = _credits(_rows_for(run.unit, added), run.questions, rate)
        if refused := check_credits(_org(request), credits):
            return _credit_refusal(
                self._gm, refused, credits, run.unit, run.questions, added, rate
            )
        if _rows_for(run.unit, added) > confirm_rows() and not data.get("confirm"):
            return _confirm_response(
                self._gm,
                f"Reading {added:,} more rows",
                _rows_for(run.unit, added),
                credits,
            )
        with transaction.atomic():
            if busy := self._busy_locked(request, task):
                return busy
            InstantEvalRun.objects.filter(id=run.id).update(
                rows_requested=target,
                # The sample's size stays the size of each "Read more".
                sample_size=None if target >= run.population else run.sample_size,
                credits_estimate=run.credits_estimate + (credits or 0),
                answers_version=F("answers_version") + 1,
            )
            EvalTask.objects.filter(id=task.id).update(spans_limit=rows)
            # The restart sizes its batches from the new limit.
            task.spans_limit = rows
            self._restart(task)
        return self._restarted(task, f"Reading {added:,} more.", rows_requested=target)
