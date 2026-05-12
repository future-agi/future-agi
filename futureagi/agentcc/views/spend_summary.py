from datetime import datetime, timedelta
from datetime import timezone as dt_tz

import structlog
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView

from agentcc.models import AgentccRequestLog
from agentcc.permissions import IsAdminToken
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


def _period_start(period):
    """Calculate the start of the current budget period (UTC)."""
    now = datetime.now(dt_tz.utc)
    if period == "daily":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "weekly":
        days_since_monday = now.weekday()
        start = now - timedelta(days=days_since_monday)
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "monthly":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == "total":
        return datetime.min.replace(tzinfo=dt_tz.utc)
    # default monthly
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class SpendSummaryView(APIView):
    """
    Returns aggregated spend data so the gateway can seed budget counters
    on startup.  Authenticated by admin token (not user JWT).

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
            "per_key": {"key-abc": 40.0, ...},
            "per_user": {"alice": 30.0, ...},
            "per_model": {"gpt-4o": 50.0, ...}
          }
        }
      }
    }
    """

    authentication_classes = []
    permission_classes = [IsAdminToken]
    renderer_classes = [JSONRenderer]
    _gm = GeneralMethods()

    def get(self, request):
        try:
            period = request.query_params.get("period", "monthly")
            start = _period_start(period)

            logs = AgentccRequestLog.no_workspace_objects.filter(
                started_at__gte=start,
                deleted=False,
            )

            cost_sum = Coalesce(Sum("cost"), Value(0), output_field=DecimalField())

            # Per-org total spend
            org_rows = (
                logs.values("organization_id")
                .annotate(total=cost_sum)
                .filter(total__gt=0)
            )

            result = {}
            for row in org_rows:
                org_str = str(row["organization_id"])
                result[org_str] = {
                    "total_spend": float(row["total"]),
                    "per_key": {},
                    "per_user": {},
                    "per_model": {},
                }

            # Per-org, per-key spend
            for row in (
                logs.exclude(api_key_id__isnull=True)
                .exclude(api_key_id="")
                .values("organization_id", "api_key_id")
                .annotate(total=cost_sum)
                .filter(total__gt=0)
            ):
                org_str = str(row["organization_id"])
                if org_str in result:
                    result[org_str]["per_key"][row["api_key_id"]] = float(row["total"])

            # Per-org, per-user spend
            for row in (
                logs.exclude(user_id__isnull=True)
                .exclude(user_id="")
                .values("organization_id", "user_id")
                .annotate(total=cost_sum)
                .filter(total__gt=0)
            ):
                org_str = str(row["organization_id"])
                if org_str in result:
                    result[org_str]["per_user"][row["user_id"]] = float(row["total"])

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
