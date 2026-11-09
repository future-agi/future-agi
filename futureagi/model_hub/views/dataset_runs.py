"""A dataset's experiment runs and its baseline pin (SRV-4)."""

from uuid import UUID

from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.serializers.contracts import MODEL_HUB_ERROR_RESPONSES
from model_hub.serializers.dataset_runs import (
    DatasetBaselinePinRequestSerializer,
    DatasetBaselineResponseSerializer,
    DatasetBaselineSerializer,
    DatasetRunsQuerySerializer,
    DatasetRunsResponseSerializer,
    DatasetRunsResultSerializer,
)
from model_hub.services.dataset_runs import (
    BaselineRunNotEligible,
    BaselineRunNotFound,
    baseline_payload,
    get_baseline,
    list_dataset_runs,
    pin_baseline,
    unpin_baseline,
)
from model_hub.utils.json_parser import BoundedJSONParser
from model_hub.utils.workspace_scope import scoped_dataset_queryset
from tfc.routers import primary_required
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.general_methods import GeneralMethods

_gm = GeneralMethods()


def _dataset(request: Request, dataset_id: UUID):
    if getattr(request, "organization", None) is None:
        return None
    return scoped_dataset_queryset(request).filter(id=dataset_id).first()


def _write_denied(request: Request) -> Response | None:
    if getattr(request, "organization", None) is None:
        return _gm.not_found("Dataset not found")
    workspace = getattr(request, "workspace", None)
    if workspace is None or not request.user.can_write_to_workspace(workspace):
        return Response(
            build_error_envelope("Workspace write access required", status_code=403),
            status=403,
        )
    return None


class DatasetExperimentRunsView(APIView):
    """List a dataset's experiment runs for the Experiments UI and the SDK."""

    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=DatasetRunsQuerySerializer,
        responses={
            200: DatasetRunsResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
        operation_id="dataset_experiment_runs_list",
        operation_description="List the experiment runs on one dataset visible in the caller's organization and workspace, newest first. Each run has its ordinal and label (#N: 1, 2, 3 ... per dataset in creation order, never reused), metadata.name, status, task kind, dataset version, item counts per status with not_started, final (false when the caller marked the run non_final, as the SDK does for a sample of the rows), is_baseline, and app_url (a link to the run in the app: the dataset's Experiments tab, built from the deployment's frontend URL, whose run= parameter opens the run's panel with its traces, joined by run id; null when no frontend URL is set). The response also carries the dataset's baseline pin. status filters by a comma-separated set of run statuses; dataset_version_id by one version. limit is 1-200 (default 50). Runs that are deleted or on a deleted version are not listed. A dataset outside the caller's organization or workspace, or deleted, is a 404. Performs no writes.",
    )
    @primary_required
    def get(self, request: Request, dataset_id: UUID) -> Response:
        dataset = _dataset(request, dataset_id)
        if dataset is None:
            return _gm.not_found("Dataset not found")
        query = request.validated_query_data
        pin = get_baseline(dataset)
        page = list_dataset_runs(
            dataset,
            statuses=query["status"],
            dataset_version_id=query["dataset_version_id"],
            limit=query["limit"],
            offset=query["offset"],
            baseline_run_id=pin.run_id if pin else None,
        )
        return _gm.success_response(
            DatasetRunsResultSerializer(
                {
                    "dataset_id": dataset.id,
                    "baseline": baseline_payload(pin),
                    "runs": page.runs,
                    "total": page.total,
                    "limit": page.limit,
                    "offset": page.offset,
                    "has_more": page.has_more,
                }
            ).data
        )


class DatasetBaselineJSONParser(BoundedJSONParser):
    max_body_bytes = 4096


class DatasetBaselineView(APIView):
    """Read, pin or unpin the run a dataset's comparisons and gates default to."""

    permission_classes = [IsAuthenticated]
    parser_classes = [DatasetBaselineJSONParser]

    @validated_request(
        responses={
            200: DatasetBaselineResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        operation_id="dataset_baseline_read",
        operation_description="Read the dataset's baseline pin: baseline_run_id, its label, pinned_by and pinned_at, all null when no run is pinned or the pinned run (or its version) was deleted. This is what futureagi run --gate baseline compares against. Readable by viewers and API keys. A dataset outside the caller's organization or workspace, or deleted, is a 404. Performs no writes.",
    )
    @primary_required
    def get(self, request: Request, dataset_id: UUID) -> Response:
        dataset = _dataset(request, dataset_id)
        if dataset is None:
            return _gm.not_found("Dataset not found")
        return _gm.success_response(
            DatasetBaselineSerializer(baseline_payload(get_baseline(dataset))).data
        )

    @validated_request(
        request_serializer=DatasetBaselinePinRequestSerializer,
        responses={
            200: DatasetBaselineResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
        operation_id="dataset_baseline_pin",
        operation_description="Pin run_id as the dataset's baseline, replacing any earlier pin; pinning the same run again is a no-op that refreshes pinned_by and pinned_at. The run must be on this dataset, not deleted, on a version that is not deleted (else 404), finished as completed or partial, and not marked non_final (else 409). Requires workspace write access. A dataset outside the caller's organization or workspace, or deleted, is a 404.",
    )
    @primary_required
    def put(self, request: Request, dataset_id: UUID) -> Response:
        denied = _write_denied(request)
        if denied is not None:
            return denied
        dataset = _dataset(request, dataset_id)
        if dataset is None:
            return _gm.not_found("Dataset not found")
        try:
            pin = pin_baseline(
                dataset,
                run_id=request.validated_data["run_id"],
                user=request.user,
            )
        except BaselineRunNotFound as exc:
            return _gm.not_found(str(exc))
        except BaselineRunNotEligible as exc:
            return Response(build_error_envelope(str(exc), status_code=409), status=409)
        return _gm.success_response(
            DatasetBaselineSerializer(baseline_payload(pin)).data
        )

    @validated_request(
        responses={
            200: DatasetBaselineResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        operation_id="dataset_baseline_unpin",
        operation_description="Remove the dataset's baseline pin; idempotent, so a dataset with no pin also returns baseline_run_id null. Comparisons and gates then name their baseline run explicitly. Requires workspace write access. A dataset outside the caller's organization or workspace, or deleted, is a 404.",
    )
    @primary_required
    def delete(self, request: Request, dataset_id: UUID) -> Response:
        denied = _write_denied(request)
        if denied is not None:
            return denied
        dataset = _dataset(request, dataset_id)
        if dataset is None:
            return _gm.not_found("Dataset not found")
        unpin_baseline(dataset)
        return _gm.success_response(
            DatasetBaselineSerializer(baseline_payload(None)).data
        )
