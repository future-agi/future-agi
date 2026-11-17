from datetime import UTC, datetime, timedelta

import structlog
from django.db import connection
from django.db.models import DecimalField, F, OuterRef, Q, Subquery, Sum, TextField, Value
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Coalesce, NullIf
from rest_framework.permissions import BasePermission
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView

from accounts.authentication import APIKeyAuthentication
from agentcc.models import AgentccAPIKey, AgentccRequestLog
from agentcc.permissions import IsAdminToken
from agentcc.serializers.contracts import (
    AgentccErrorResponseSerializer,
    SpendSummaryQuerySerializer,
    SpendSummaryResponseSerializer,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


_EMPTY = Value("", output_field=TextField())


def _logged(name):
    """A metadata entry the gateway logged with the request, or NULL."""
    return NullIf(KeyTextTransform(name, "metadata"), _EMPTY, output_field=TextField())


def _of_the_key(field):
    """A field of the request's key (by its gateway id, in its org): the
    fallback for a log that does not carry the key's own fields."""
    return NullIf(
        Subquery(
            AgentccAPIKey.unscoped_objects.filter(
                gateway_key_id=OuterRef("api_key_id"),
                organization_id=OuterRef("organization_id"),
            ).values(field)[:1],
            output_field=TextField(),
        ),
        _EMPTY,
        output_field=TextField(),
    )


# The levels the gateway's budgets count a request under (plugins/budget
# extractIdentity), so a restart seeds the counters they read:
# - a key by its name (auth_key_name), two keys of one name summed;
# - the request's user (the log's user_id, which is the key's owner when the
#   request names no user), and the key's owner when that is another user;
# - a team: the key's team, else the request's RBAC team.
_KEY_NAME = Coalesce(_logged("auth_key_name"), _of_the_key("name"), output_field=TextField())
_KEY_OWNER = Coalesce(_logged("auth_key_owner"), _of_the_key("owner"), output_field=TextField())
_REQUEST_USER = Coalesce(
    NullIf(F("user_id"), _EMPTY, output_field=TextField()), _KEY_OWNER, output_field=TextField()
)
_REQUEST_TEAM = Coalesce(_logged("key_team"), _logged("rbac_team"), output_field=TextField())

# A key the platform pays for itself (billing: platform_funded, logged with
# each of its requests as key_billing): no budget counts its calls
# (plugins/budget, logspool), so the summary leaves them out of every level.
_PLATFORM_FUNDED = "platform_funded"
_KEY_BILLING = Coalesce(_logged("key_billing"), _EMPTY, output_field=TextField())

# Per-org spend by tag: each tag:<name> entry of a log's metadata, keyed
# <name>:<value> as the gateway's tag budgets are (plugins/budget
# extractTags). A log whose metadata is not a JSON object has no tags.
_PER_TAG_SQL = (
    "SELECT l.organization_id, substr(kv.key, 5) || ':' || kv.value, SUM(l.cost)"
    " FROM agentcc_request_log l CROSS JOIN LATERAL jsonb_each_text("
    "CASE WHEN jsonb_typeof(l.metadata) = 'object' THEN l.metadata"
    " ELSE '{}'::jsonb END) AS kv(key, value)"
    " WHERE l.started_at >= %s AND l.deleted = false"
    " AND (CASE WHEN jsonb_typeof(l.metadata) = 'object'"
    " THEN l.metadata ->> 'key_billing' END) IS DISTINCT FROM %s"
    " AND kv.key LIKE 'tag:%%' AND length(kv.key) > 4"
)
_PER_TAG_ORG_SQL = " AND l.organization_id = %s"
_PER_TAG_GROUP_SQL = " GROUP BY 1, 2 HAVING SUM(l.cost) > 0"


def _per_tag_spend(start, organization_id=None):
    """(org id, tag key, spend) for the period from start, one org when given."""
    sql, params = _PER_TAG_SQL, [start, _PLATFORM_FUNDED]
    if organization_id is not None:
        sql += _PER_TAG_ORG_SQL
        params.append(organization_id)
    with connection.cursor() as cursor:
        cursor.execute(sql + _PER_TAG_GROUP_SQL, params)
        return cursor.fetchall()


def _period_start(period):
    """Calculate the start of the current budget period (UTC)."""
    now = datetime.now(UTC)
    if period == "daily":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "weekly":
        days_since_monday = now.weekday()
        start = now - timedelta(days=days_since_monday)
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "monthly":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == "total":
        return datetime.min.replace(tzinfo=UTC)
    # default monthly
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class _AdminTokenOrSessionAuthentication(APIKeyAuthentication):
    """The gateway's admin token is left to IsAdminToken and never decoded as
    a user token; any other credential goes through the console's own
    authenticator (which resolves and checks the request's organization)."""

    def authenticate(self, request):
        if IsAdminToken().has_permission(request, None):
            return None
        return super().authenticate(request)

    def authenticate_header(self, request):
        # No WWW-Authenticate challenge: a refused caller gets 403, as before.
        return None


class _OrganizationSession(BasePermission):
    """A signed-in console user acting on an organization they belong to."""

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        return bool(
            user is not None
            and user.is_authenticated
            and getattr(request, "organization", None) is not None
        )


class SpendSummaryView(APIView):
    """
    Returns aggregated spend data: the gateway seeds its budget counters
    from it on startup (admin token: every org), and the console's Budgets
    page reads its own organization's spend for each budget's period (a
    signed-in user: that organization only, present even at $0). The calls
    of a platform-funded key are in no part of it: no budget counts them.

    GET /agentcc/spend-summary/?period=monthly

    Response:
    {
      "status": true,
      "result": {
        "period": "monthly",
        "period_start": "2026-03-01T00:00:00+00:00",
        "orgs": {
          "<org_id>": {
            "total_spend": 82.50,
            "per_key": {"svc-prod": 40.0, ...},   # by the key's name
            "per_user": {"alice": 30.0, ...},     # request user and key owner
            "per_model": {"gpt-4o": 50.0, ...},
            "per_team": {"eng": 20.0, ...},
            "per_tag": {"project:atlas": 10.0, ...}
          }
        }
      }
    }
    """

    authentication_classes = [_AdminTokenOrSessionAuthentication]
    permission_classes = [IsAdminToken | _OrganizationSession]
    renderer_classes = [JSONRenderer]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=SpendSummaryQuerySerializer,
        responses={
            200: SpendSummaryResponseSerializer,
            500: AgentccErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def get(self, request):
        try:
            period = request.validated_query_data.get("period", "monthly")
            start = _period_start(period)

            logs = (
                AgentccRequestLog.no_workspace_objects.filter(
                    started_at__gte=start,
                    deleted=False,
                )
                .alias(key_billing=_KEY_BILLING)
                .exclude(key_billing=_PLATFORM_FUNDED)
            )
            # A console session reads its own organization only.
            session_org = (
                None
                if IsAdminToken().has_permission(request, self)
                else getattr(request, "organization", None)
            )
            if session_org is not None:
                logs = logs.filter(organization_id=session_org.id)

            cost_sum = Coalesce(Sum("cost"), Value(0), output_field=DecimalField())

            # Per-org total spend
            org_rows = (
                logs.values("organization_id")
                .annotate(total=cost_sum)
                .filter(total__gt=0)
            )

            result = {}
            if session_org is not None:
                result[str(session_org.id)] = {
                    "total_spend": 0.0,
                    "per_key": {},
                    "per_user": {},
                    "per_model": {},
                    "per_team": {},
                    "per_tag": {},
                }
            for row in org_rows:
                org_str = str(row["organization_id"])
                result[org_str] = {
                    "total_spend": float(row["total"]),
                    "per_key": {},
                    "per_user": {},
                    "per_model": {},
                    "per_team": {},
                    "per_tag": {},
                }

            # Per-org, per-key spend, by the key's name
            for row in (
                logs.annotate(key_name=_KEY_NAME)
                .exclude(key_name__isnull=True)
                .values("organization_id", "key_name")
                .annotate(total=cost_sum)
                .filter(total__gt=0)
            ):
                org_str = str(row["organization_id"])
                if org_str in result:
                    result[org_str]["per_key"][row["key_name"]] = float(row["total"])

            # Per-org, per-user spend: the request's user, then the key's
            # owner when that is another user.
            with_users = logs.annotate(request_user=_REQUEST_USER, key_owner=_KEY_OWNER)
            for users in (
                with_users.exclude(request_user__isnull=True).values(
                    "organization_id", user=F("request_user")
                ),
                with_users.exclude(key_owner__isnull=True)
                .exclude(Q(request_user=F("key_owner")))
                .values("organization_id", user=F("key_owner")),
            ):
                for row in users.annotate(total=cost_sum).filter(total__gt=0):
                    org_str = str(row["organization_id"])
                    if org_str in result:
                        per_user = result[org_str]["per_user"]
                        per_user[row["user"]] = per_user.get(row["user"], 0.0) + float(
                            row["total"]
                        )

            # Per-org, per-model spend
            for row in (
                logs.exclude(model__isnull=True)
                .exclude(model="")
                .values("organization_id", "model")
                .annotate(total=cost_sum)
                .filter(total__gt=0)
            ):
                org_str = str(row["organization_id"])
                if org_str in result:
                    result[org_str]["per_model"][row["model"]] = float(row["total"])

            # Per-org, per-team spend
            for row in (
                logs.annotate(team=_REQUEST_TEAM)
                .exclude(team__isnull=True)
                .values("organization_id", "team")
                .annotate(total=cost_sum)
                .filter(total__gt=0)
            ):
                org_str = str(row["organization_id"])
                if org_str in result:
                    result[org_str]["per_team"][row["team"]] = float(row["total"])

            # Per-org, per-tag spend
            for org_id, tag, total in _per_tag_spend(
                start, session_org.id if session_org is not None else None
            ):
                org_str = str(org_id)
                if org_str in result:
                    result[org_str]["per_tag"][tag] = float(total)

            return self._gm.success_response(
                {
                    "period": period,
                    "period_start": start.isoformat(),
                    "orgs": result,
                }
            )
        except Exception as e:
            logger.exception("spend_summary_error", error=str(e))
            return self._gm.internal_server_error_response("Internal server error")
