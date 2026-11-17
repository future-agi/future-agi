"""Read new row activity, newest first, within the authorized dataset scope."""

from django.core import signing
from django.db.models import Q
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_aware
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from model_hub.models.row_activity import RowActivity
from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
    ModelHubErrorResponseSerializer,
)
from model_hub.serializers.row_activity import (
    RowActivityEventSerializer,
    RowActivityQuerySerializer,
    RowActivityResponseSerializer,
)
from model_hub.views.develop_dataset import (
    _request_dataset_queryset,
    _request_row_queryset,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

_CURSOR_SALT = "model-hub.row-activity.v1"


def _cursor(event, dataset_id, row_id):
    return signing.dumps({
        "dataset": str(dataset_id), "row": str(row_id),
        "at": event.at.isoformat(), "id": event.pk,
    }, salt=_CURSOR_SALT)


def _position(cursor, dataset_id, row_id):
    data = signing.loads(cursor, salt=_CURSOR_SALT, max_age=86400)
    if data["dataset"] != str(dataset_id) or data["row"] != str(row_id):
        raise ValueError("Cursor belongs to another row")
    at = parse_datetime(data["at"])
    event_id = data["id"]
    if at is None or not is_aware(at) or type(event_id) is not int or event_id < 1:
        raise ValueError("Invalid cursor position")
    return at, event_id


def _event(event):
    row_types = {"create": "created", "delete": "deleted", "restore": "restored"}
    verb = {"create": "created", "edit": "changed", "delete": "deleted", "restore": "restored"}
    target = "this row" if event.subject == "row" else (event.column_name or "a field")
    return {
        "id": str(event.pk),
        "type": row_types[event.action] if event.subject == "row" else "edit",
        "subject": event.subject,
        "action": event.action,
        "at": event.at,
        "actor_id": event.actor_id,
        "actor": event.actor,
        "origin": event.origin,
        "cell_id": event.cell_id,
        "column_id": event.column_id,
        "column": event.column_name or None,
        "before": event.before_value,
        "after": event.after_value,
        "before_truncated": event.before_truncated,
        "after_truncated": event.after_truncated,
        "text": f"{event.actor} {verb[event.action]} {target}",
        "source": {"type": "upload"} if event.origin == "import" else None,
    }


class RowActivityView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=RowActivityQuerySerializer,
        responses={
            200: RowActivityResponseSerializer,
            401: ModelHubErrorResponseSerializer,
            405: ModelHubErrorResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
    )
    def get(self, request, dataset_id, row_id):
        dataset = _request_dataset_queryset(request).filter(pk=dataset_id).first()
        if dataset is None:
            return self._gm.not_found("Dataset not found.")
        row = _request_row_queryset(request, dataset).filter(pk=row_id).first()
        if row is None:
            return self._gm.not_found("Dataset row not found.")

        params = request.validated_query_data
        events = RowActivity.objects.filter(row=row).order_by("-at", "-id")
        if params.get("cursor"):
            try:
                at, event_id = _position(params["cursor"], dataset.id, row.id)
            except (signing.BadSignature, KeyError, TypeError, ValueError):
                return self._gm.bad_request({
                    "cursor": ["This activity cursor is invalid or expired. Reload the activity."],
                })
            events = events.filter(Q(at__lt=at) | Q(at=at, id__lt=event_id))

        size = params["page_size"]
        page = list(events[:size + 1])
        has_more = len(page) > size
        page = page[:size]
        return self._gm.success_response({
            "events": RowActivityEventSerializer([_event(e) for e in page], many=True).data,
            "next_cursor": _cursor(page[-1], dataset.id, row.id) if has_more else None,
            "has_more": has_more,
        })
