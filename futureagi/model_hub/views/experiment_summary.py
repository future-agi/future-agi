from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.models.evals_metric import EvalTemplateVersion
from model_hub.models.experiment_summary import (
    ExperimentSummaryBinding,
    ExperimentSummaryResult,
)
from model_hub.models.experiments import ExperimentRun, ExperimentsTable
from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
    ModelHubErrorResponseSerializer,
)
from model_hub.serializers.experiment_summary import (
    SummaryBindingCreateSerializer,
    SummaryBindingResponseSerializer,
    SummaryBindingSerializer,
    SummaryBindingsResponseSerializer,
    SummaryEvaluateSerializer,
    SummaryScoreResponseSerializer,
    SummaryScoreSerializer,
    SummaryScoresResponseSerializer,
)
from model_hub.services.experiment_summary import (
    SummaryError,
    create_binding,
    request_summary,
)
from model_hub.utils.eval_list import build_eval_list_queryset
from model_hub.utils.workspace_scope import scoped_dataset_queryset
from tfc.routers import primary_required
from tfc.utils.api_contracts import validated_request
from tfc.utils.tenant_scope import caller_organization, caller_workspace


def _experiment(request, experiment_id):
    return get_object_or_404(
        ExperimentsTable.no_workspace_objects.select_related("dataset").filter(
            dataset__in=scoped_dataset_queryset(request),
            deleted=False,
        ),
        pk=experiment_id,
    )


def _write_access(request):
    if not request.user.can_write_to_workspace(caller_workspace(request)):
        raise PermissionDenied("Workspace write access required")


def _run(experiment, run_id):
    return get_object_or_404(
        ExperimentRun.no_workspace_objects.filter(
            experiment=experiment,
            dataset_version__dataset=experiment.dataset,
            deleted=False,
            dataset_version__deleted=False,
        ),
        pk=run_id,
    )


class SummaryBindingView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        responses={
            200: SummaryBindingsResponseSerializer,
            401: ModelHubErrorResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        operation_id="experiment_summary_evaluators_list",
    )
    @primary_required
    def get(self, request, experiment_id):
        experiment = _experiment(request, experiment_id)
        bindings = (
            ExperimentSummaryBinding.objects.select_related("version")
            .filter(
                experiment=experiment,
            )
            .order_by("created_at", "id")
        )
        return Response(
            {
                "status": True,
                "result": {
                    "bindings": SummaryBindingSerializer(bindings, many=True).data,
                },
            }
        )

    @validated_request(
        request_serializer=SummaryBindingCreateSerializer,
        responses={
            201: SummaryBindingResponseSerializer,
            401: ModelHubErrorResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
        operation_id="experiment_summary_evaluators_create",
    )
    @primary_required
    def post(self, request, experiment_id):
        experiment = _experiment(request, experiment_id)
        _write_access(request)
        data = request.validated_data
        template = get_object_or_404(
            build_eval_list_queryset(
                caller_organization(request),
                caller_workspace(request),
            ).filter(deleted=False),
            pk=data["template_id"],
        )
        version = get_object_or_404(
            EvalTemplateVersion.all_objects.filter(
                eval_template=template,
                deleted=False,
            ),
            pk=data["version_id"],
        )
        try:
            binding = create_binding(
                experiment=experiment,
                template=template,
                version=version,
                name=data["name"],
                mapping=data["mapping"],
            )
        except SummaryError as exc:
            raise ValidationError(str(exc)) from exc
        return Response(
            {
                "status": True,
                "result": SummaryBindingSerializer(binding).data,
            },
            status=201,
        )


class SummaryScoresView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        responses={
            200: SummaryScoresResponseSerializer,
            401: ModelHubErrorResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        operation_id="experiment_summary_scores_list",
    )
    @primary_required
    def get(self, request, experiment_id, run_id):
        experiment = _experiment(request, experiment_id)
        run = _run(experiment, run_id)
        scores = (
            ExperimentSummaryResult.objects.select_related(
                "version",
                "binding",
            )
            .filter(run=run, binding__experiment=experiment)
            .order_by("created_at", "id")
        )
        return Response(
            {
                "status": True,
                "result": {
                    "scores": SummaryScoreSerializer(scores, many=True).data,
                },
            }
        )

    @validated_request(
        request_serializer=SummaryEvaluateSerializer,
        responses={
            202: SummaryScoreResponseSerializer,
            401: ModelHubErrorResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
        operation_id="experiment_summary_scores_evaluate",
    )
    @primary_required
    def post(self, request, experiment_id, run_id):
        experiment = _experiment(request, experiment_id)
        run = _run(experiment, run_id)
        _write_access(request)
        data = request.validated_data
        binding = get_object_or_404(
            ExperimentSummaryBinding.objects.filter(experiment=experiment),
            pk=data["binding_id"],
        )
        try:
            result = request_summary(
                run=run,
                binding=binding,
                retry_failed=data["retry_failed"],
            )
        except SummaryError as exc:
            raise ValidationError(str(exc)) from exc
        return Response(
            {
                "status": True,
                "result": SummaryScoreSerializer(result).data,
            },
            status=202,
        )
