"""Copy one dataset into another workspace, with a write-free preview.

A move is refused (services/dataset_transfer.py explains why)."""

from django.conf import settings
from django.db import transaction
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.serializers.contracts import MODEL_HUB_ERROR_RESPONSES
from model_hub.serializers.dataset_transfer import (
    DatasetTransferRequestSerializer,
    DatasetTransferResponseSerializer,
)
from model_hub.services import dataset_transfer as transfers
from model_hub.views.utils.dataset_limit import dataset_add_refusal
from model_hub.views.utils.dataset_readiness import dataset_import_refusal
from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import DatasetLimitCheckFailedErrorSerializer
from tfc.utils.general_methods import GeneralMethods

try:
    from ee.usage.utils.usage_entries import (
        ROW_LIMIT_REACHED_MESSAGE,
        log_and_deduct_cost_for_resource_request,
    )
except ImportError:
    ROW_LIMIT_REACHED_MESSAGE = None
    log_and_deduct_cost_for_resource_request = None


def _copy_limit_refusal(request, transfer):
    """Apply the same dataset and row limits as Duplicate, charged to the target."""
    if log_and_deduct_cost_for_resource_request is None:
        return None
    organization = transfer.dataset.organization
    entry = log_and_deduct_cost_for_resource_request(
        organization=organization,
        api_call_type=APICallTypeChoices.DATASET_ADD.value,
        workspace=transfer.target,
    )
    refusal = dataset_add_refusal(entry)
    if refusal is not None:
        return refusal
    entry.status = APICallStatusChoices.SUCCESS.value
    entry.save()
    rows = log_and_deduct_cost_for_resource_request(
        organization,
        api_call_type=APICallTypeChoices.ROW_ADD.value,
        config={"total_rows": transfer.plan["rows"]},
        workspace=transfer.target,
    )
    if rows is None or rows.status == APICallStatusChoices.RESOURCE_LIMIT.value:
        return GeneralMethods().too_many_requests(ROW_LIMIT_REACHED_MESSAGE)
    rows.status = APICallStatusChoices.SUCCESS.value
    rows.save()
    return None


def transfer_enabled():
    """The launch switch, read per request; only an actual True is ON."""
    return getattr(settings, "DSX_DATASET_TRANSFER_ENABLED", False) is True


class DatasetTransferView(APIView):
    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        # Default OFF: 404 for every caller, method and body, like a route that
        # does not exist. APIView.dispatch() calls initial() first, and the
        # check precedes super().initial(): DRF's authentication runs there
        # (APIKeyAuthentication refuses a viewer's POST before any permission
        # check), and the validated_request body checks and the transaction
        # around post() come later still.
        if not transfer_enabled():
            raise NotFound()
        return super().initial(request, *args, **kwargs)

    @validated_request(
        request_serializer=DatasetTransferRequestSerializer,
        responses={
            200: DatasetTransferResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
            503: DatasetLimitCheckFailedErrorSerializer,
        },
        reject_unknown_fields=True,
    )
    @transaction.atomic
    def post(self, request, dataset_id):
        data = request.validated_data
        # prepare() refuses a move (409) once the caller is authorized.
        transfer = transfers.prepare(
            request,
            dataset_id,
            data["to_workspace_id"],
            data["mode"],
            data.get("new_dataset_name"),
        )
        refusal = dataset_import_refusal(transfer.dataset)
        if refusal is not None:
            return refusal
        if data["dry_run"]:
            result = transfers.describe(transfer, dry_run=True)
            return Response({"status": True, "result": result})
        refusal = _copy_limit_refusal(request, transfer)
        if refusal is not None:
            transaction.set_rollback(True)
            return refusal
        copy = transfers.copy_dataset(request, transfer)
        result = transfers.describe(transfer, dry_run=False, dataset_id=copy.id)
        return Response({"status": True, "result": result})
