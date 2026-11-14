import datetime

from django.db.models import Count
from django.db.models.functions import TruncHour
from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.models.monitor_alert import MonitorAlert
from model_hub.serializers.ai_model import AIModelSerializer
from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
    OverviewResponseSerializer,
)
from model_hub.utils.clickhouse import get_model_hourly_volume
from model_hub.utils.workspace_scope import scoped_ai_model_queryset


def _hourly_zero_series(now, hours):
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    return [
        {"x": current_hour - datetime.timedelta(hours=hours - index - 1), "y": 0}
        for index in range(hours)
    ]


class OverviewView(APIView):
    serializer_class = AIModelSerializer
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={200: OverviewResponseSerializer, **MODEL_HUB_ERROR_RESPONSES}
    )
    def get(self, request, *args, **kwargs):
        scoped_models = scoped_ai_model_queryset(request)
        scoped_model_ids = [
            str(model_id) for model_id in scoped_models.values_list("id", flat=True)
        ]

        results = {"volume": {}, "issues": {}, "versions": {}}

        now = timezone.now()
        if scoped_model_ids:
            (
                results["volume"]["volume"],
                results["volume"]["total_count"],
            ) = get_model_hourly_volume(model_ids=scoped_model_ids)
            _, volume_48 = get_model_hourly_volume(model_ids=scoped_model_ids, hours=48)
        else:
            results["volume"]["volume"] = _hourly_zero_series(now, 24)
            results["volume"]["total_count"] = 0
            volume_48 = 0
        volume_48_24 = volume_48 - results["volume"]["total_count"]

        if volume_48_24 == 0 and results["volume"]["total_count"] == 0:
            results["volume"]["change"] = 0
        elif volume_48_24 == 0:
            results["volume"]["change"] = None
        else:
            results["volume"]["change"] = (
                (results["volume"]["total_count"] - volume_48_24) * 100 / volume_48_24
            )

        current_hour = now.replace(minute=0, second=0, microsecond=0)
        twenty_four_hours_ago = now - timezone.timedelta(hours=24)
        forty_eight_hours_ago = now - timezone.timedelta(hours=48)

        hours_list = [
            current_hour - datetime.timedelta(hours=23 - i) for i in range(24)
        ]
        scoped_alerts = MonitorAlert.objects.filter(
            monitor__ai_model_id__in=scoped_model_ids
        )

        alerts_data = (
            scoped_alerts.filter(created_at__gte=twenty_four_hours_ago)
            .annotate(hour=TruncHour("created_at"))
            .values("hour")
            .annotate(count=Count("id"))
            .order_by("hour")
        )

        # day before yesterday
        dby_alert_count = scoped_alerts.filter(
            created_at__lt=twenty_four_hours_ago,
            created_at__gte=forty_eight_hours_ago,
        ).count()

        alerts_dict = {alert["hour"]: alert["count"] for alert in alerts_data}

        hourly_alerts = [
            {"x": hour, "y": alerts_dict.get(hour, 0)} for hour in hours_list
        ]

        alert_count = 0
        for row in alerts_data:
            alert_count += row["count"]
        results["issues"]["last_day"] = hourly_alerts
        results["issues"]["total_count"] = alert_count
        if dby_alert_count == 0 and alert_count == 0:
            results["issues"]["change"] = 0
        elif dby_alert_count == 0:
            results["issues"]["change"] = None
        else:
            results["issues"]["change"] = (
                (alert_count - dby_alert_count) * 100 / dby_alert_count
            )

        return Response(results)
