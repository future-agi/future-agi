"""
Views for Prompt-based Simulations in Prompt Workbench.

These views provide API endpoints for creating and managing simulations
that use prompts as the agent source instead of SDK-based agent definitions.
"""

from functools import wraps

import structlog
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.utils import get_request_organization
from common.utils.page_limits import enforce_page_size, page_bounds
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.utils.scoring import (
    ChoiceScoreOverrideError,
    validate_choice_score_override,
)
from simulate.models import RunTest, Scenarios, SimulateEvalConfig
from simulate.serializers.prompt_simulation import (
    ExecutePromptSimulationRequestSerializer,
    ExecutePromptSimulationResponseSerializer,
    PromptSimulationListResponseSerializer,
    PromptSimulationRunResponseSerializer,
    PromptSimulationScenariosResponseSerializer,
    PromptSimulationUpdateRequestSerializer,
)
from simulate.serializers.requests.run_test import (
    CreatePromptSimulationRequestSerializer,
    CreatePromptSimulationSerializer,
    PromptSimulationListQuerySerializer,
)
from simulate.serializers.run_test import (
    RunTestSerializer,
    harness_run_tests_context,
)
from simulate.services.eval_config_edit import unknown_eval_group_ids
from simulate.services.harness_provider import (
    request_workspace as resolve_request_workspace,
)
from simulate.services.test_executor import TestExecutor
from simulate.utils.owned_relations import owned_scenario_graphs
from simulate.utils.scenario_completeness import check_scenarios_incomplete
from simulate.utils.scenario_ownership import (
    all_attached_to_run_test,
    scenarios_not_in_run_test_errors,
)
from simulate.views.run_test import (
    _bounded_run_test_list_read,
    _run_test_read_queryset,
    _scope_run_test_agent_roots,
)
from tfc.middleware.workspace_context import (
    get_current_organization,
    get_current_user,
    get_current_workspace,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiTextErrorResponseSerializer
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


def _prompt_simulation_workspace(request):
    # Prefer auth-populated request context; the canonical header fallback checks
    # organization, active state and membership. A rejected header never falls back.
    try:
        workspace = resolve_request_workspace(request)
    except (DjangoValidationError, ValueError) as exc:
        raise PermissionDenied("Access denied to this workspace") from exc
    if workspace is None:
        # Legacy session callers carry middleware context rather than an attribute.
        # An ambient workspace alone (or user.workspace) is not authorization.
        user = getattr(request, "user", None)
        organization = getattr(request, "organization", None) or getattr(
            user, "organization", None
        )
        candidate = get_current_workspace()
        if (
            user is not None
            and getattr(user, "is_authenticated", False)
            and get_current_user() == user
            and organization is not None
            and get_current_organization() == organization
            and candidate is not None
            and candidate.organization_id == organization.pk
            and candidate.is_active
            and user.can_access_workspace(candidate)
        ):
            workspace = candidate
    if workspace is None:
        raise PermissionDenied(
            {"code": "workspace_required", "message": "Workspace is required."},
            code="workspace_required",
        )
    request.workspace = workspace
    return workspace


def _require_prompt_simulation_workspace(handler):
    @wraps(handler)
    def scoped(self, request, *args, **kwargs):
        _prompt_simulation_workspace(request)
        return handler(self, request, *args, **kwargs)

    return scoped


def _scope_prompt_scenario_queryset(queryset, organization, workspace):
    """Filter before evaluation, including nullable nested-reference scope.

    Kept local to avoid importing another API module during view initialization.
    This predicate matches scenarios._scope_scenario_queryset.
    """
    queryset = queryset.filter(
        organization=organization, workspace=workspace, deleted=False
    )
    for relation in (
        "dataset",
        "simulator_agent",
        "agent_definition",
        "prompt_template",
    ):
        queryset = queryset.filter(
            Q(**{f"{relation}__isnull": True})
            | Q(
                **{
                    f"{relation}__organization": organization,
                    f"{relation}__workspace": workspace,
                }
            )
        )
    return queryset.filter(
        Q(prompt_version__isnull=True)
        | Q(
            prompt_version__original_template__organization=organization,
            prompt_version__original_template__workspace=workspace,
            prompt_version__original_template__deleted=False,
            prompt_version__deleted=False,
        )
    )


def _prompt_scenario_read_queryset(queryset, organization, workspace):
    """Replace the shared graph prefetch before it can hydrate foreign graphs.

    Graphs have an independent organization but inherit workspace from scenario.
    Keep annotations, joins and non-graph prefetches on the scenario queryset.
    """
    other_prefetches = []
    for lookup in queryset._prefetch_related_lookups:
        path = lookup.prefetch_through if isinstance(lookup, Prefetch) else lookup
        if path != "graphs":
            other_prefetches.append(lookup)
    graphs = Prefetch(
        "graphs",
        queryset=owned_scenario_graphs(
            organization_id=organization.pk,
            workspace_id=workspace.pk if workspace is not None else None,
        )
        .filter(is_active=True)
        .order_by("-created_at"),
        to_attr="_active_graphs",
    )
    return (
        _scope_prompt_scenario_queryset(queryset, organization, workspace)
        .prefetch_related(None)
        .prefetch_related(graphs, *other_prefetches)
    )


def _prompt_simulation_read_queryset(queryset, organization, workspace):
    """Scope direct versions and scenario Prefetches before any row is loaded.

    Preserve its annotations, nested prefetches and joins by cloning/filtering
    the existing scenario queryset. Install that root prefetch first, so deeper
    lookups cannot implicitly fetch an unscoped scenarios relation.
    """
    # A local run/template does not authorize the independently linked version.
    # Keep NULL versions, but require any set version's own parent tenant keys
    # before the shared helper installs select_related("prompt_version").
    queryset = queryset.filter(
        organization=organization, workspace=workspace, deleted=False
    ).filter(
        Q(prompt_version__isnull=True)
        | Q(
            prompt_version__original_template__organization=organization,
            prompt_version__original_template__workspace=workspace,
        )
    )
    queryset = _run_test_read_queryset(queryset)
    scenario_queryset = None
    other_prefetches = []
    for lookup in queryset._prefetch_related_lookups:
        path = lookup.prefetch_through if isinstance(lookup, Prefetch) else lookup
        if path == "scenarios":
            if isinstance(lookup, Prefetch) and lookup.to_attr is None:
                scenario_queryset = lookup.queryset
            elif isinstance(lookup, Prefetch):
                # An alternate cache must be scoped as well, not silently dropped.
                other_prefetches.append(
                    Prefetch(
                        "scenarios",
                        queryset=_prompt_scenario_read_queryset(
                            lookup.queryset
                            if lookup.queryset is not None
                            else Scenarios.no_workspace_objects.all(),
                            organization,
                            workspace,
                        ),
                        to_attr=lookup.to_attr,
                    )
                )
        else:
            other_prefetches.append(lookup)
    if scenario_queryset is None:
        scenario_queryset = Scenarios.no_workspace_objects.select_related(
            "dataset",
            "simulator_agent",
            "agent_definition",
            "prompt_template",
            "prompt_version",
        )
    scoped_scenarios = Prefetch(
        "scenarios",
        queryset=_prompt_scenario_read_queryset(
            scenario_queryset, organization, workspace
        ),
    )
    return queryset.prefetch_related(None).prefetch_related(
        scoped_scenarios, *other_prefetches
    )


def _prompt_simulation_response_data(run_test, organization, workspace):
    """Use the same query-scoped scenario representation for successful writes."""
    run_test = get_object_or_404(
        _prompt_simulation_read_queryset(
            RunTest.no_workspace_objects, organization, workspace
        ),
        id=run_test.id,
    )
    return RunTestSerializer(run_test).data


class PromptSimulationListCreateView(APIView):
    """
    API View to list and create prompt-based simulation runs for a prompt template.

    GET  - List paginated simulation runs
    POST - Create a new simulation run
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    # The deadline opens its transaction lazily; validation and the workspace
    # guard still precede tenant queries, but their reads share the action budget.
    @_bounded_run_test_list_read
    @validated_request(
        query_serializer=PromptSimulationListQuerySerializer,
        responses={
            200: PromptSimulationListResponseSerializer,
            400: ApiTextErrorResponseSerializer,
            403: ApiTextErrorResponseSerializer,
            404: ApiTextErrorResponseSerializer,
            503: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        },
    )
    @_require_prompt_simulation_workspace
    def get(self, request, prompt_template_id, *args, **kwargs):
        """
        Get paginated list of simulation runs for a specific prompt template.

        Query Parameters:
        - limit: number of items per page (default: 10)
        - page: page number (default: 1)
        - version_id: filter by specific prompt version
        """
        request_workspace = _prompt_simulation_workspace(request)
        try:
            user_organization = get_request_organization(request)

            if not user_organization:
                return self.gm.not_found("Organization not found for the user.")

            # Require the template's organization and exact request workspace.
            prompt_template = get_object_or_404(
                PromptTemplate,
                id=prompt_template_id,
                organization=user_organization,
                workspace=request_workspace,
                deleted=False,
            )

            # Get validated query parameters
            query_data = request.validated_query_data
            version_id = query_data.get("version_id")
            limit = query_data["limit"]
            page = query_data["page"]

            # Filter run tests by prompt_template and source_type='prompt'
            run_tests = _prompt_simulation_read_queryset(
                RunTest.objects.filter(
                    prompt_template=prompt_template,
                    source_type="prompt",
                    organization=user_organization,
                    workspace=request_workspace,
                    deleted=False,
                ),
                user_organization,
                request_workspace,
            ).order_by("-created_at")

            # Filter by version if specified
            if version_id:
                run_tests = run_tests.filter(prompt_version_id=version_id)

            # Pagination
            total_count = run_tests.count()
            offset = (page - 1) * limit
            run_tests = list(run_tests[offset : offset + limit])

            # Serialize
            serializer = RunTestSerializer(
                run_tests,
                many=True,
                context=harness_run_tests_context(run_tests),
            )

            return self.gm.success_response(
                {
                    "count": total_count,
                    "page": page,
                    "limit": limit,
                    "results": serializer.data,
                    "prompt_template": {
                        "id": str(prompt_template.id),
                        "name": prompt_template.name,
                    },
                }
            )

        except Http404:
            return self.gm.not_found("Simulation or related resource not found")
        except ValueError as e:
            return self.gm.bad_request(str(e))
        except Exception as e:
            logger.exception("Error listing prompt simulations", error=str(e))
            return self.gm.internal_server_error_response("Failed to list simulations")

    @_require_prompt_simulation_workspace
    @validated_request(
        request_serializer=CreatePromptSimulationRequestSerializer,
        responses={
            201: PromptSimulationRunResponseSerializer,
            400: ApiTextErrorResponseSerializer,
            403: ApiTextErrorResponseSerializer,
            404: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, prompt_template_id, *args, **kwargs):
        """
        Create a new prompt-based simulation run.

        Request Body:
        - name: Name of the simulation run
        - description: Optional description
        - prompt_version_id: The prompt version to use
        - scenario_ids: List of scenario IDs to run
        - dataset_row_ids: Optional list of specific row IDs
        - evaluations_config: Optional evaluation configurations
        - enable_tool_evaluation: Optional boolean to enable tool evaluation
        """
        request_workspace = _prompt_simulation_workspace(request)
        try:
            user_organization = get_request_organization(request)

            if not user_organization:
                return self.gm.not_found("Organization not found for the user.")

            # Require the template's organization and exact request workspace.
            prompt_template = get_object_or_404(
                PromptTemplate,
                id=prompt_template_id,
                organization=user_organization,
                workspace=request_workspace,
                deleted=False,
            )

            # The template id is path-owned. Keep the public body contract
            # focused on simulation fields and inject the path id only for
            # cross-field business validation.
            data = request.validated_data.copy()
            data["prompt_template_id"] = str(prompt_template_id)

            # Check the whole set before the internal serializer or any writes.
            # A foreign ID and an absent ID must have the same response.
            scenarios = list(
                _scope_prompt_scenario_queryset(
                    Scenarios.objects.filter(id__in=data["scenario_ids"]),
                    user_organization,
                    request_workspace,
                )
            )
            if {scenario.id for scenario in scenarios} != set(data["scenario_ids"]):
                raise Http404

            logger.info(
                "create_prompt_simulation_request",
                request_data=dict(data),
                prompt_template_id=str(prompt_template_id),
            )

            # Validate request data
            serializer = CreatePromptSimulationSerializer(
                data=data, context={"request": request}
            )
            if not serializer.is_valid():
                logger.warning(
                    "create_prompt_simulation_validation_failed",
                    errors=serializer.errors,
                    request_data=dict(data),
                )
                return self.gm.bad_request(serializer.errors)

            validated_data = serializer.validated_data

            # Get the prompt version
            prompt_version = get_object_or_404(
                PromptVersion,
                id=validated_data["prompt_version_id"],
                original_template=prompt_template,
                deleted=False,
            )

            # Eval groups are checked against the same resolved request workspace.
            if unknown_eval_group_ids(
                [
                    eval_config_data.get("eval_group")
                    for eval_config_data in validated_data.get("evaluations_config", [])
                ],
                user_organization,
                request_workspace,
            ):
                return self.gm.bad_request("Eval group not found")

            # Create the simulation run
            with transaction.atomic():
                run_test = RunTest.objects.create(
                    name=validated_data["name"],
                    description=validated_data.get("description", ""),
                    source_type="prompt",
                    prompt_template=prompt_template,
                    prompt_version=prompt_version,
                    agent_definition=None,  # Not used for prompt-based simulations
                    agent_version=None,
                    simulator_agent=None,
                    dataset_row_ids=validated_data.get("dataset_row_ids", []),
                    organization=user_organization,
                    workspace=request_workspace,
                    enable_tool_evaluation=validated_data.get(
                        "enable_tool_evaluation", False
                    ),
                )

                # Attach exactly the fully validated, scoped scenario set.
                run_test.scenarios.set(scenarios)

                # Handle evaluations - create SimulateEvalConfig instances
                evaluations_config = validated_data.get("evaluations_config", [])

                if evaluations_config:
                    for eval_config_data in evaluations_config:
                        template_id = eval_config_data.get("template_id")

                        if template_id:
                            try:
                                eval_template = EvalTemplate.no_workspace_objects.get(
                                    # System rows remain public; private templates
                                    # use the exact workspace, not default widening.
                                    Q(organization__isnull=True)
                                    | Q(
                                        organization=user_organization,
                                        workspace=request_workspace,
                                    ),
                                    id=template_id,
                                    deleted=False,
                                )

                                validate_choice_score_override(
                                    eval_template, eval_config_data.get("config", {})
                                )
                                SimulateEvalConfig.objects.create(
                                    eval_template=eval_template,
                                    name=eval_config_data.get(
                                        "name", f"Eval-{template_id}"
                                    ),
                                    config=eval_config_data.get("config", {}),
                                    mapping=eval_config_data.get("mapping", {}),
                                    run_test=run_test,
                                    filters=eval_config_data.get("filters", []),
                                    error_localizer=eval_config_data.get(
                                        "error_localizer", False
                                    ),
                                    model=eval_config_data.get("model", None),
                                    eval_group_id=eval_config_data.get(
                                        "eval_group", None
                                    ),
                                )
                            except EvalTemplate.DoesNotExist:
                                continue

                logger.info(
                    "prompt_simulation_created",
                    run_test_id=str(run_test.id),
                    prompt_template_id=str(prompt_template_id),
                    prompt_version_id=str(prompt_version.id),
                    scenario_count=len(scenarios),
                )

                # Serialize and return
                return self.gm.success_response(
                    _prompt_simulation_response_data(
                        run_test, user_organization, request_workspace
                    ),
                    status=status.HTTP_201_CREATED,
                )

        except Http404:
            return self.gm.not_found("Simulation or related resource not found")
        except ChoiceScoreOverrideError as e:
            # Refused before any write commits (the run test is rolled back).
            return self.gm.bad_request(str(e))
        except Exception as e:
            logger.exception("Error creating prompt simulation", error=str(e))
            return self.gm.internal_server_error_response("Failed to create simulation")


class PromptSimulationDetailView(APIView):
    """
    API View to retrieve, update, or delete a specific prompt simulation run.
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    @swagger_auto_schema(
        responses={
            200: PromptSimulationRunResponseSerializer,
            403: ApiTextErrorResponseSerializer,
            404: ApiTextErrorResponseSerializer,
            503: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        },
    )
    @_bounded_run_test_list_read
    @_require_prompt_simulation_workspace
    def get(self, request, prompt_template_id, run_test_id, *args, **kwargs):
        """Retrieve a specific prompt simulation run."""
        request_workspace = _prompt_simulation_workspace(request)
        try:
            user_organization = get_request_organization(request)
            if not user_organization:
                return self.gm.not_found("Organization not found for the user.")

            # Verify prompt template
            prompt_template = get_object_or_404(
                PromptTemplate,
                id=prompt_template_id,
                organization=user_organization,
                workspace=request_workspace,
                deleted=False,
            )

            # Get the run test
            run_test = get_object_or_404(
                _prompt_simulation_read_queryset(
                    RunTest.objects, user_organization, request_workspace
                ),
                id=run_test_id,
                prompt_template=prompt_template,
                source_type="prompt",
                organization=user_organization,
                workspace=request_workspace,
                deleted=False,
            )

            serializer = RunTestSerializer(run_test)
            return self.gm.success_response(serializer.data)

        except Http404:
            return self.gm.not_found("Simulation not found")
        except Exception as e:
            logger.exception("Error retrieving prompt simulation", error=str(e))
            return self.gm.internal_server_error_response(
                "Failed to retrieve simulation"
            )

    @_require_prompt_simulation_workspace
    @validated_request(
        request_serializer=PromptSimulationUpdateRequestSerializer,
        responses={
            200: PromptSimulationRunResponseSerializer,
            400: ApiTextErrorResponseSerializer,
            403: ApiTextErrorResponseSerializer,
            404: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        },
        reject_unknown_fields=True,
        partial_request_validation=True,
    )
    def patch(self, request, prompt_template_id, run_test_id, *args, **kwargs):
        """Update a prompt simulation run (version, scenarios, etc.)."""
        request_workspace = _prompt_simulation_workspace(request)
        try:
            user_organization = get_request_organization(request)
            if not user_organization:
                return self.gm.not_found("Organization not found for the user.")

            # Verify prompt template
            prompt_template = get_object_or_404(
                PromptTemplate,
                id=prompt_template_id,
                organization=user_organization,
                workspace=request_workspace,
                deleted=False,
            )

            # Reject mixed agent roots before any write, not only on reload.
            run_test = get_object_or_404(
                _scope_run_test_agent_roots(RunTest.objects),
                id=run_test_id,
                prompt_template=prompt_template,
                source_type="prompt",
                organization=user_organization,
                workspace=request_workspace,
                deleted=False,
            )

            validated = request.validated_data

            # Update prompt version if provided
            prompt_version_id = validated.get("prompt_version_id")
            if prompt_version_id:
                prompt_version = get_object_or_404(
                    PromptVersion,
                    id=prompt_version_id,
                    original_template=prompt_template,
                    deleted=False,
                )
                run_test.prompt_version = prompt_version

            # Update scenarios if provided
            scenario_ids = validated.get("scenario_ids")
            if scenario_ids is not None:
                scenarios = list(
                    _scope_prompt_scenario_queryset(
                        Scenarios.objects.filter(id__in=scenario_ids),
                        user_organization,
                        request_workspace,
                    )
                )
                if {scenario.id for scenario in scenarios} != set(scenario_ids):
                    raise Http404
                run_test.scenarios.set(scenarios)

            # Update name if provided
            name = validated.get("name")
            if name:
                run_test.name = name

            # Update description if provided
            description = validated.get("description")
            if description is not None:
                run_test.description = description

            # Update enable_tool_evaluation if provided
            enable_tool_evaluation = validated.get("enable_tool_evaluation")
            if enable_tool_evaluation is not None:
                run_test.enable_tool_evaluation = enable_tool_evaluation

            run_test.save()

            logger.info(
                "prompt_simulation_updated",
                run_test_id=str(run_test_id),
                prompt_template_id=str(prompt_template_id),
            )

            return self.gm.success_response(
                _prompt_simulation_response_data(
                    run_test, user_organization, request_workspace
                )
            )

        except Http404:
            return self.gm.not_found("Simulation or related resource not found")
        except Exception as e:
            logger.exception("Error updating prompt simulation", error=str(e))
            return self.gm.internal_server_error_response("Failed to update simulation")

    @swagger_auto_schema(
        responses={
            404: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        },
    )
    @_require_prompt_simulation_workspace
    def delete(self, request, prompt_template_id, run_test_id, *args, **kwargs):
        """Soft delete a prompt simulation run."""
        request_workspace = _prompt_simulation_workspace(request)
        try:
            user_organization = get_request_organization(request)
            if not user_organization:
                return self.gm.not_found("Organization not found for the user.")

            # Verify prompt template
            prompt_template = get_object_or_404(
                PromptTemplate,
                id=prompt_template_id,
                organization=user_organization,
                workspace=request_workspace,
                deleted=False,
            )

            # Get the run test
            run_test = get_object_or_404(
                RunTest,
                id=run_test_id,
                prompt_template=prompt_template,
                source_type="prompt",
                organization=user_organization,
                workspace=request_workspace,
                deleted=False,
            )

            # Soft delete
            run_test.deleted = True
            run_test.save(update_fields=["deleted", "updated_at"])

            logger.info(
                "prompt_simulation_deleted",
                run_test_id=str(run_test_id),
                prompt_template_id=str(prompt_template_id),
            )

            return self.gm.success_response("Simulation run deleted successfully")

        except Http404:
            return self.gm.not_found("Simulation not found")
        except Exception as e:
            logger.exception("Error deleting prompt simulation", error=str(e))
            return self.gm.internal_server_error_response("Failed to delete simulation")


def _execute_prompt_serializer_context(request, prompt_template_id, run_test_id):
    """Who is executing, and which run test: what
    ExecutePromptSimulationRequestSerializer needs to check that every selected
    scenario is the run test's own (T6 #7e)."""
    return {
        "request": request,
        "prompt_template_id": prompt_template_id,
        "run_test_id": run_test_id,
    }


class _WorkspaceScopedExecutePromptSimulationRequestSerializer(
    ExecutePromptSimulationRequestSerializer
):
    """Keep field errors first, but guard before DB-backed ownership validation."""

    class Meta:
        # Workspace validation does not change the public request body shape.
        ref_name = "ExecutePromptSimulationRequest"

    def validate(self, attrs):
        if attrs.get("select_all") or not attrs.get("scenario_ids"):
            # No ownership read: the view's guard handles these requests.
            return attrs

        request = self.context["request"]
        try:
            workspace = _prompt_simulation_workspace(request)
        except PermissionDenied as exc:
            if (
                isinstance(exc.detail, dict)
                and exc.detail.get("code") == "workspace_required"
            ):
                # Protected IR [unbound] contract: a fixed 400, independent of
                # root/selection existence. No tenant lookup has happened.
                raise ValidationError(scenarios_not_in_run_test_errors()) from exc
            # A rejected header/membership is not a legacy unbound request.
            raise

        organization = get_request_organization(request)
        run_test_id = self.context.get("run_test_id")
        template_id = self.context.get("prompt_template_id")
        if organization is None or run_test_id is None:
            raise ValidationError(scenarios_not_in_run_test_errors())

        # A manager can widen a default workspace. Authorize both path roots
        # exactly before asking whether any scenario is attached to this run.
        template_visible = (
            PromptTemplate.objects.filter(
                id=template_id,
                organization=organization,
                workspace=workspace,
                deleted=False,
            )
            .values_list("id", flat=True)
            .first()
        )
        if template_visible is None:
            return attrs  # Preserve the view's normal missing-resource 404 body.
        run_visible = (
            RunTest.objects.filter(
                id=run_test_id,
                prompt_template_id=template_id,
                source_type="prompt",
                organization=organization,
                workspace=workspace,
                deleted=False,
            )
            .values_list("id", flat=True)
            .first()
        )
        if run_visible is None:
            return attrs
        if not all_attached_to_run_test(
            attrs["scenario_ids"], organization=organization, run_test_id=run_test_id
        ):
            raise ValidationError(scenarios_not_in_run_test_errors())
        return attrs


class ExecutePromptSimulationView(APIView):
    """
    API View to execute a prompt-based simulation run.
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    @validated_request(
        request_serializer=_WorkspaceScopedExecutePromptSimulationRequestSerializer,
        responses={
            200: ExecutePromptSimulationResponseSerializer,
            400: ApiTextErrorResponseSerializer,
            404: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        },
        reject_unknown_fields=True,
        serializer_context=_execute_prompt_serializer_context,
    )
    @_require_prompt_simulation_workspace
    def post(self, request, prompt_template_id, run_test_id, *args, **kwargs):
        """
        Execute a prompt-based simulation run.

        Request Body (optional):
        - scenario_ids: List of specific scenario IDs to run (default: all scenarios)
        - select_all: If true, run all scenarios except ones in scenario_ids
        """
        request_workspace = _prompt_simulation_workspace(request)
        try:
            user_organization = get_request_organization(request)

            if not user_organization:
                return self.gm.not_found("Organization not found for the user.")

            # Verify prompt template
            prompt_template = get_object_or_404(
                PromptTemplate,
                id=prompt_template_id,
                organization=user_organization,
                workspace=request_workspace,
                deleted=False,
            )

            # Get the run test
            run_test = get_object_or_404(
                RunTest,
                id=run_test_id,
                prompt_template=prompt_template,
                source_type="prompt",
                organization=user_organization,
                workspace=request_workspace,
                deleted=False,
            )

            # Inspect the FK id, not a possibly foreign hydrated version. Preserve
            # the existing 400 contract for null/deleted/unavailable versions.
            if not run_test.prompt_version_id or not (
                PromptVersion.no_workspace_objects.filter(
                    id=run_test.prompt_version_id,
                    original_template=prompt_template,
                    original_template__organization=user_organization,
                    original_template__workspace=request_workspace,
                    deleted=False,
                ).exists()
            ):
                return self.gm.bad_request(
                    "Prompt version has been deleted. Please update the simulation with a valid version."
                )

            validated = request.validated_data
            scenario_ids = [
                str(scenario_id) for scenario_id in validated.get("scenario_ids", [])
            ]
            select_all = validated.get("select_all", False)

            # Only scoped linked scenarios can reach completeness checks/execution.
            # The request serializer has already refused ids the run test does not
            # include (T6 #7e, 400); an attached id outside the request workspace
            # scope is missing here, also when select_all names it for exclusion.
            all_scenario_ids = list(
                _scope_prompt_scenario_queryset(
                    run_test.scenarios.all(), user_organization, request_workspace
                ).values_list("id", flat=True)
            )
            if not set(scenario_ids).issubset({str(pk) for pk in all_scenario_ids}):
                raise Http404

            # Determine which scenarios to execute
            if select_all:
                if scenario_ids:
                    # Exclude the provided scenario_ids from all scenarios
                    final_scenario_ids = [
                        str(scenario_id)
                        for scenario_id in all_scenario_ids
                        if str(scenario_id) not in scenario_ids
                    ]
                else:
                    # Run on all scenarios
                    final_scenario_ids = [
                        str(scenario_id) for scenario_id in all_scenario_ids
                    ]
            else:
                if scenario_ids:
                    final_scenario_ids = scenario_ids
                else:
                    # Run on all linked scenarios
                    final_scenario_ids = [
                        str(scenario_id) for scenario_id in all_scenario_ids
                    ]

            # Do not let an empty scoped set become an executor's "all" fallback.
            if not final_scenario_ids:
                return self.gm.bad_request("No scenarios available for execution.")

            gate_response = check_scenarios_incomplete(final_scenario_ids, run_test)
            if gate_response is not None:
                return gate_response

            # Use the existing TestExecutor
            test_executor = TestExecutor()

            result = test_executor.execute_test(
                run_test_id=str(run_test.id),
                user_id=str(request.user.id),
                scenario_ids=final_scenario_ids,
                simulator_id=None,
            )

            if result["success"]:
                logger.info(
                    "prompt_simulation_execution_started",
                    run_test_id=str(run_test_id),
                    execution_id=result["execution_id"],
                    prompt_template_id=str(prompt_template_id),
                )

                return self.gm.success_response(
                    {
                        "message": "Simulation execution started successfully",
                        "execution_id": result["execution_id"],
                        "run_test_id": result["run_test_id"],
                        "status": result["status"],
                        "total_scenarios": result["total_scenarios"],
                        "total_calls": result["total_calls"],
                        "scenario_ids": final_scenario_ids,
                    }
                )
            else:
                return self.gm.bad_request(result["error"])

        except Http404:
            return self.gm.not_found("Simulation or related resource not found")
        except Exception as e:
            logger.exception("Error executing prompt simulation", error=str(e))
            return self.gm.internal_server_error_response(
                "Failed to execute simulation"
            )


class PromptSimulationScenariosView(APIView):
    """
    API View to list available scenarios for prompt simulations.

    Returns scenarios that can be used with prompt-based simulations,
    filtered by organization.
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    @swagger_auto_schema(
        responses={
            200: PromptSimulationScenariosResponseSerializer,
            404: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        },
    )
    def get(self, request, *args, **kwargs):
        """
        Get list of scenarios available for prompt simulations.

        Query Parameters:
        - limit: number of items per page (default: 20)
        - page: page number (default: 1)
        - search: search string to filter scenarios by name
        """
        # Preserve base parsing-before-DB ordering and its invalid-input response.
        # The page-size cap stays first: an oversized read is refused before any
        # data or workspace lookup (tfc/tests/test_page_cap_round3.py).
        try:
            enforce_page_size(request.query_params.get("limit"))
            search_query = request.query_params.get("search", "").strip()
            limit = int(request.query_params.get("limit", 20))
            page = int(request.query_params.get("page", 1))
        except (TypeError, ValueError):
            return self.gm.internal_server_error_response("Failed to list scenarios")

        # Outside the broad handler exception boundary: keep the DRF 403 shape.
        request_workspace = _prompt_simulation_workspace(request)
        try:
            user_organization = get_request_organization(request)

            if not user_organization:
                return self.gm.not_found("Organization not found for the user.")

            # Narrow the organization's live scenarios to the request workspace
            # and in-scope nested references before any row is loaded.
            scenarios = _scope_prompt_scenario_queryset(
                Scenarios.objects.filter(
                    organization=user_organization,
                    deleted=False,
                ),
                user_organization,
                request_workspace,
            ).order_by("-created_at")

            # Apply search filter
            if search_query:
                scenarios = scenarios.filter(name__icontains=search_query)

            # Pagination
            total_count = scenarios.count()
            offset, end = page_bounds(page, limit, total_count, first_page=1)
            scenarios = scenarios[offset:end]

            # Serialize with minimal fields for selection
            scenarios_data = [
                {
                    "id": str(s.id),
                    "name": s.name,
                    "description": s.description,
                    "scenario_type": s.scenario_type,
                    "dataset_id": str(s.dataset_id) if s.dataset_id else None,
                    "created_at": s.created_at.isoformat(),
                }
                for s in scenarios
            ]

            return self.gm.success_response(
                {
                    "count": total_count,
                    "page": page,
                    "limit": limit,
                    "results": scenarios_data,
                }
            )

        except APIException:
            raise
        except Exception as e:
            logger.exception("Error listing scenarios", error=str(e))
            return self.gm.internal_server_error_response("Failed to list scenarios")
