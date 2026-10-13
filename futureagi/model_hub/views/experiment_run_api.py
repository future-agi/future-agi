"""Authenticated API adapters for externally executed experiment runs."""

from uuid import UUID

from django.core.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.models.dataset_versions import DatasetVersion
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.models.experiments import ExperimentRun, ExperimentsTable
from model_hub.serializers.contracts import MODEL_HUB_ERROR_RESPONSES
from model_hub.serializers.experiment_run_api import (
    ExternalRunCreateRequestSerializer,
    ExternalRunCreateResponseSerializer,
    ExternalRunHeaderSerializer,
    ExternalRunResultsRequestSerializer,
    ExternalRunResultsResponseSerializer,
    ExternalRunResultsResultSerializer,
)
from model_hub.services.experiment_run_creation import create_experiment_run
from model_hub.services.experiment_run_ingestion import (
    ExternalRunResultInput,
    ingest_external_run_results,
)
from model_hub.services.experiment_run_writes import _RUN_KEY, RunWriteError
from model_hub.utils.json_parser import BoundedJSONParser
from model_hub.utils.workspace_scope import scoped_dataset_queryset
from tfc.routers import primary_required
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.general_methods import GeneralMethods

_gm = GeneralMethods()


def _write_access(request: Request) -> Response | None:
    if getattr(request, "organization", None) is None:
        return _gm.not_found("Dataset version not found")
    workspace = getattr(request, "workspace", None)
    if workspace is None or not request.user.can_write_to_workspace(workspace):
        return Response(
            build_error_envelope("Workspace write access required", status_code=403),
            status=403,
        )
    return None


def _header(run: ExperimentRun) -> dict:
    metadata = run.metadata
    return {
        "id": run.id,
        "dataset_version_id": run.dataset_version_id,
        "experiment_id": run.experiment_id,
        "task_kind": run.task_kind,
        "status": run.status,
        "expected_items": metadata[_RUN_KEY]["plan"]["identities"],
        "evaluator_bindings": run.evaluator_versions,
        "model_config": run.model_config,
        "metadata": {key: value for key, value in metadata.items() if key != _RUN_KEY},
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def _conflict(exc: RunWriteError) -> Response:
    return Response(build_error_envelope(str(exc), status_code=409), status=409)


class ExternalRunCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [BoundedJSONParser]

    @validated_request(
        request_serializer=ExternalRunCreateRequestSerializer,
        responses={
            200: ExternalRunCreateResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
        operation_id="external_experiment_runs_create",
        operation_description="Create or replay a pinned external-task run. Both first creation and identical UUID replay return 200; no task is executed. Tenant, workspace and actor come from authentication. Creation body is limited to 10485760 bytes; JSON config/metadata fields to 262144 bytes. Configuration and code revision metadata are caller-supplied provenance, not executable-code attestation.",
    )
    @primary_required
    def post(self, request: Request) -> Response:
        denied = _write_access(request)
        if denied is not None:
            return denied
        data = request.validated_data
        version = DatasetVersion.no_workspace_objects.filter(
            id=data["dataset_version_id"],
            deleted=False,
            dataset__in=scoped_dataset_queryset(request),
        ).first()
        if version is None:
            return _gm.not_found("Dataset version not found")
        experiment = None
        if data["experiment_id"] is not None:
            experiment = ExperimentsTable.no_workspace_objects.filter(
                id=data["experiment_id"], dataset_id=version.dataset_id, deleted=False
            ).first()
            if experiment is None:
                return _gm.not_found("Experiment not found")
        metrics = list(
            UserEvalMetric.no_workspace_objects.filter(
                id__in=data["evaluator_metric_ids"],
                dataset_id=version.dataset_id,
                deleted=False,
            )
        )
        if len(metrics) != len(data["evaluator_metric_ids"]):
            return _gm.not_found("Evaluator binding not found")
        try:
            run = create_experiment_run(
                run_id=data["run_id"],
                dataset_version=version,
                created_by=request.user,
                task_kind="external",
                expected_items=[
                    (item["example_id"], item["trial_number"])
                    for item in data["expected_items"]
                ],
                experiment=experiment,
                evaluator_metrics=metrics,
                model_config=data["model_config"],
                metadata=data["metadata"],
            )
        except RunWriteError as exc:
            return _conflict(exc)
        return _gm.success_response(ExternalRunHeaderSerializer(_header(run)).data)


class ExternalRunResultsJSONParser(BoundedJSONParser):
    max_body_bytes = 2 * 1024 * 1024


class ExternalRunResultsView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [ExternalRunResultsJSONParser]

    @validated_request(
        request_serializer=ExternalRunResultsRequestSerializer,
        responses={
            200: ExternalRunResultsResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
        operation_id="external_experiment_run_results_ingest",
        operation_description="Atomically ingest 1–100 caller-executed results for a pinned external run. Identical receipt/payload replay is a no-op, including after completion. Conflicts roll back the whole batch. Finalization is derived only when every planned identity is terminal. Body is limited to 2097152 bytes, each item to 1048576 bytes and each output/error/usage JSON field to 262144 bytes. This endpoint does not execute tasks or attest trace/evaluator provenance.",
    )
    @primary_required
    def post(self, request: Request, run_id: UUID) -> Response:
        denied = _write_access(request)
        if denied is not None:
            return denied
        run = (
            ExperimentRun.no_workspace_objects.select_related(
                "dataset_version__dataset"
            )
            .filter(
                id=run_id,
                deleted=False,
                dataset_version__deleted=False,
                dataset_version__dataset__in=scoped_dataset_queryset(request),
            )
            .first()
        )
        if run is None:
            return _gm.not_found("Experiment run not found")
        results = []
        for value in request.validated_data["items"]:
            results.append(
                ExternalRunResultInput(
                    **{
                        **value,
                        "score_refs": [
                            {
                                **ref,
                                "metric_id": str(ref["metric_id"]),
                                "evaluator_version_id": str(
                                    ref["evaluator_version_id"]
                                ),
                            }
                            for ref in value["score_refs"]
                        ],
                    }
                )
            )
        try:
            result = ingest_external_run_results(
                run_id=run.id,
                organization_id=request.organization.id,
                workspace_id=run.dataset_version.dataset.workspace_id,
                results=results,
            )
        except RunWriteError as exc:
            return _conflict(exc)
        except ValidationError as exc:
            return _gm.bad_request(str(exc))
        items = [
            {
                "id": item.id,
                "example_id": item.example_id,
                "trial_number": item.trial_number,
                "receipt_key": value.receipt_key,
                "status": item.status,
                "trace_id": item.trace_id,
                "root_span_id": item.root_span_id,
                "score_refs": item.score_refs,
            }
            for item, value in zip(result.items, results, strict=True)
        ]
        return _gm.success_response(
            ExternalRunResultsResultSerializer(
                {"run": _header(result.run), "items": items}
            ).data
        )
