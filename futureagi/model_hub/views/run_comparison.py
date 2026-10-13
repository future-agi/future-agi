"""Read-only API over the persisted run-comparison service."""

from dataclasses import asdict

from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import workspace_read_only
from model_hub.models.experiments import ExperimentRun
from model_hub.serializers.contracts import MODEL_HUB_ERROR_RESPONSES
from model_hub.serializers.run_comparison import (
    RunComparisonRequestSerializer,
    RunComparisonResponseSerializer,
    RunComparisonResultSerializer,
)
from model_hub.services.experiment_release_gate import (
    GateCaseIdentity,
    ReleaseGatePolicy,
    evaluate_release_gate,
)
from model_hub.services.persisted_run_comparison import (
    RunComparisonError,
    compare_experiment_runs,
)
from model_hub.utils.workspace_scope import scoped_dataset_queryset
from tfc.routers import primary_required
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

_gm = GeneralMethods()


@workspace_read_only
class RunComparisonView(APIView):
    """Compare immutable run evidence; this POST performs no writes."""

    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=RunComparisonRequestSerializer,
        responses={200: RunComparisonResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True,
        operation_id="experiment_runs_compare",
        operation_description="Compare compatible persisted runs. Requires the same dataset version, exact example/trial plan and selected evaluator binding. At most 10,000 planned and recorded items per run; excess evidence is rejected, never truncated. Tenant and workspace scope come from authentication. Optional include_trial_stability returns per-example counts, observed assessed-score population standard deviations and paired candidate-minus-baseline deltas; deviations are null below two assessed trials and imply no statistical significance. An optional release_gate derives a case-level verdict requiring valid evidence for the complete plan on both sides; a failed gate still returns 200. This endpoint does not execute tasks or attest caller-supplied evidence.",
    )
    @primary_required
    def post(self, request: Request) -> Response:
        organization = getattr(request, "organization", None)
        if organization is None:
            return _gm.not_found("Experiment run not found")
        data = dict(request.validated_data)
        gate_policy = data.pop("release_gate", None)
        run_ids = {data["baseline_run_id"], data["candidate_run_id"]}
        # Reuse default-workspace visibility, including authorized legacy nulls.
        visible = dict(
            ExperimentRun.no_workspace_objects.filter(
                id__in=run_ids,
                deleted=False,
                dataset_version__deleted=False,
                dataset_version__dataset__in=scoped_dataset_queryset(request),
            ).values_list("id", "dataset_version__dataset__workspace_id")
        )
        if visible.keys() != run_ids:
            return _gm.not_found("Experiment run not found")
        try:
            result = compare_experiment_runs(
                **data,
                organization_id=organization.id,
                workspace_id=visible[data["baseline_run_id"]],
            )
        except RunComparisonError as exc:
            return _gm.bad_request(str(exc))
        payload = asdict(result)
        if result.trial_stability is None:
            payload.pop("trial_stability")
        if gate_policy is not None:
            try:
                gate = evaluate_release_gate(
                    result.comparison,
                    expected_cases=(
                        GateCaseIdentity(ref.example_id, ref.trial_number)
                        for ref in result.trace_refs
                    ),
                    policy=ReleaseGatePolicy(
                        max_regressions=gate_policy["max_regressions"],
                        critical_cases=tuple(
                            GateCaseIdentity(
                                str(case["example_id"]), case["trial_number"]
                            )
                            for case in gate_policy["critical_cases"]
                        ),
                    ),
                    passing_score=data["passing_score"],
                    higher_is_better=data["higher_is_better"],
                )
            except ValueError as exc:
                return _gm.bad_request(str(exc))
            payload["release_gate"] = asdict(gate)
        payload["comparison"]["comparable_count"] = result.comparison.comparable_count
        payload["missing_by_side"] = {
            side: [
                {"example_id": example_id, "trial_number": trial}
                for example_id, trial in identities
            ]
            for side, identities in result.missing_by_side.items()
        }
        return _gm.success_response(RunComparisonResultSerializer(payload).data)
