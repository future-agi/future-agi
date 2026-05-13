import structlog
from django.db import transaction
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from tfc.routers import uses_db
from tfc.temporal.simulate import start_create_graph_scenario_workflow_sync
from tfc.utils.error_codes import get_error_message
from tfc.utils.errors import format_validation_error
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination
from tracer.db_routing import DATABASE_FOR_REPLAY_SESSION_LIST
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project
from tracer.models.replay_session import ReplaySession, ReplaySessionStep
from tracer.serializers.replay_session import (
    CreateReplaySessionSerializer,
    GenerateScenarioSerializer,
    ReplaySessionListSerializer,
    ReplaySessionResponseSerializer,
    ReplaySessionSerializer,
)
from tracer.utils.replay_session import (
    _build_trace_query,
    _extract_voice_trace_original_config,
    _is_voice_trace_query,
    create_scenario,
    get_agent_suggestions,
    get_or_create_agent_definition,
    get_transcripts,
)

logger = structlog.get_logger(__name__)


class ReplaySessionView(ViewSet):
    """
    Provides APIs for stateful replay session management.

    Endpoints:
        GET /replay-session/ - List replay sessions for a project
        GET /replay-session/{id}/ - Get single replay session
        POST /replay-session/ - Create new replay session (INIT state)
        POST /replay-session/{id}/generate-scenario/ - Generate scenario (GENERATING state)
        GET /replay-session/eval-configs/ - Get eval configs for a project
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    serializer_classes = {
        "list": ReplaySessionListSerializer,
        "retrieve": ReplaySessionSerializer,
        "create": CreateReplaySessionSerializer,
        "generate_scenario": GenerateScenarioSerializer,
    }

    def get_serializer(self, *args, **kwargs):
        """Return serializer instance based on action with request context."""
        serializer_class = self.serializer_classes.get(
            self.action, ReplaySessionSerializer
        )
        kwargs.setdefault("context", {"request": self.request})
        return serializer_class(*args, **kwargs)

    def get_queryset(self):
        """Return base queryset filtered by user's organization and workspace."""
        queryset = ReplaySession.objects.filter(
            project__organization=self.request.user.organization
        )

        if hasattr(self.request.user, "workspace") and self.request.user.workspace:
            queryset = queryset.filter(project__workspace=self.request.user.workspace)
        return queryset

    @uses_db(DATABASE_FOR_REPLAY_SESSION_LIST, feature_key="feature:replay_session_list")
    def list(self, request: Request) -> Response:
        """
        List replay sessions for a project with pagination.

        Query params:
            project_id: uuid (optional) - filter by project
            page: int (optional) - page number
            limit: int (optional) - items per page

        Returns:
            Paginated list of replay sessions with basic info.
        """
        project_id = request.query_params.get("project_id")

        try:
            queryset = (
                self.get_queryset()
                .select_related("project")
                .order_by("-created_at")
                .using(DATABASE_FOR_REPLAY_SESSION_LIST)
            )

            if project_id:
                queryset = queryset.filter(project_id=project_id)

            paginator = ExtendedPageNumberPagination()
            result_page = paginator.paginate_queryset(queryset, request)
            serializer = self.get_serializer(result_page, many=True)
            return paginator.get_paginated_response(serializer.data)

        except NotFound:
            raise
        except Exception as e:
            logger.exception("Error listing replay sessions", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_LIST_REPLAY_SESSIONS")
            )

    def retrieve(self, request: Request, pk=None) -> Response:
        """
        Get a single replay session with all related data.

        URL params:
            pk: replay session uuid

        Returns:
            Replay session with nested agent_definition, scenario, run_test.
        """
        try:
            replay_session = (
                self.get_queryset()
                .select_related(
                    "project",
                    "agent_definition",
                    "scenario",
                    "run_test",
                )
                .get(id=pk)
            )

            serializer = self.get_serializer(replay_session)
            return self._gm.success_response(serializer.data)

        except ReplaySession.DoesNotExist:
            return self._gm.not_found(get_error_message("REPLAY_SESSION_NOT_FOUND"))

        except Exception as e:
            logger.exception("Error retrieving replay session", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_GET_REPLAY_SESSION")
            )

    def create(self, request: Request) -> Response:
        """
        Create a new replay session in INIT state.

        Request body:
            project_id: uuid (required)
            replay_type: "session" or "trace" (default: "session")
            ids: list of uuids (required if select_all=false)
            select_all: bool (default: false)

        Returns:
            Created replay session with suggested agent_name, agent_description, scenario_name.
        """
        serializer = self.get_serializer(data=request.data)

        if not serializer.is_valid():
            return self._gm.bad_request(format_validation_error(serializer.errors))

        validated_data = serializer.validated_data
        replay_type = validated_data["replay_type"]
        ids = validated_data.get("ids", [])
        select_all = validated_data.get("select_all", False)
        # Get project from serializer context (validated and cached in validate_project_id)
        project = serializer.context.get("project")

        try:
            exists, suggestions, agent_def = get_agent_suggestions(
                project=project,
                replay_type=replay_type,
                ids=ids,
                select_all=select_all,
            )

            replay_session = ReplaySession.objects.create(
                project=project,
                replay_type=replay_type,
                ids=[str(id) for id in ids],
                select_all=select_all,
                current_step=ReplaySessionStep.INIT,
                agent_definition=agent_def,
            )

            logger.info(
                "Created replay session",
                replay_session_id=str(replay_session.id),
                project_id=str(project.id),
                exists=exists,
            )

            response_serializer = ReplaySessionResponseSerializer(replay_session)
            response_data = response_serializer.data
            response_data["exists"] = exists
            response_data["suggestions"] = suggestions

            return self._gm.create_response(response_data)

        except Exception as e:
            logger.exception("Error creating replay session", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_CREATE_REPLAY_SESSION")
            )

    @action(detail=True, methods=["post"], url_path="generate-scenario")
    def generate_scenario(self, request: Request, pk=None) -> Response:
        """
        Create agent definition + scenario and start generation workflow.
        Moves replay session to GENERATING state.

        URL params:
            pk: replay session uuid

        Request body:
            agent_name: string (required)
            agent_description: string (optional)
            scenario_name: string (required)
            agent_type: "text" or "voice" (default: "text")
            no_of_rows: int (default: 20)
            personas: list of uuids (optional)
            custom_columns: list of dicts (optional)
            graph: dict (optional)
            generate_graph: bool (default: true)

        Returns:
            Updated replay session with agent_definition and scenario linked.
        """
        serializer = self.get_serializer(data=request.data)

        if not serializer.is_valid():
            return self._gm.bad_request(format_validation_error(serializer.errors))

        validated_data = serializer.validated_data

        try:
            replay_session = self.get_queryset().select_related("project").get(id=pk)

            project = replay_session.project
            agent_name = validated_data["agent_name"]
            agent_description = validated_data.get("agent_description", "")
            agent_type = validated_data.get("agent_type", "text")
            scenario_name = validated_data["scenario_name"]
            no_of_rows = validated_data.get("no_of_rows", 20)
            personas = validated_data.get("personas", [])
            custom_columns = validated_data.get("custom_columns", [])
            graph = validated_data.get("graph")
            generate_graph = validated_data.get("generate_graph", True)

            transcripts = get_transcripts(
                project_id=str(project.id),
                replay_type=replay_session.replay_type,
                ids=replay_session.ids or [],
                select_all=replay_session.select_all,
            )

            # Pass original voice config if available from the replay session's suggestions
            original_voice_config = None
            if agent_type == "voice":
                trace_query = _build_trace_query(
                    str(project.id),
                    replay_session.replay_type,
                    replay_session.ids or [],
                    replay_session.select_all,
                )
                if _is_voice_trace_query(trace_query):
                    original_voice_config = _extract_voice_trace_original_config(
                        trace_query
                    )

            with transaction.atomic():
                agent_def = get_or_create_agent_definition(
                    project=project,
                    agent_name=agent_name,
                    agent_description=agent_description,
                    agent_type=agent_type,
                    original_voice_config=original_voice_config,
                )

                scenario = create_scenario(
                    project=project,
                    agent_def=agent_def,
                    scenario_name=scenario_name,
                    agent_description=agent_description,
                )

                replay_session.agent_definition = agent_def
                replay_session.scenario = scenario
                replay_session.current_step = ReplaySessionStep.GENERATING
                replay_session.save()

            workflow_data = {
                "agent_definition_id": str(agent_def.id),
                "no_of_rows": no_of_rows,
                "generate_graph": generate_graph,
                "graph": graph,
                "personas": [str(p) for p in personas],
                "custom_columns": custom_columns,
                "transcripts": transcripts,
            }

            workflow_id = start_create_graph_scenario_workflow_sync(
                validated_data=workflow_data,
                scenario_id=str(scenario.id),
            )

            logger.info(
                "Started scenario generation for replay session",
                workflow_id=workflow_id,
                replay_session_id=str(replay_session.id),
                agent_definition_id=str(agent_def.id),
                scenario_id=str(scenario.id),
            )

            response_serializer = ReplaySessionResponseSerializer(replay_session)
            return self._gm.success_response(response_serializer.data)

        except ReplaySession.DoesNotExist:
            return self._gm.not_found(get_error_message("REPLAY_SESSION_NOT_FOUND"))

        except ValidationError as e:
            return self._gm.bad_request(format_validation_error(e))

        except Exception as e:
            logger.exception("Error generating scenario", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_GENERATE_SCENARIO")
            )

    @action(detail=False, methods=["get"], url_path="eval-configs")
    def get_eval_configs(self, request: Request) -> Response:
        """
        Get all custom eval configs for a project with available models per eval template.

        Query params:
            project_id: uuid (required)

        Returns:
            eval_configs: list of eval configs with available models
            common_models: list of models available across all eval templates
        """
        project_id = request.query_params.get("project_id")

        if not project_id:
            return self._gm.bad_request(get_error_message("PROJECT_ID_REQUIRED"))

        try:
            project = Project.objects.get(
                id=project_id,
                organization=request.user.organization,
            )

            custom_eval_configs = CustomEvalConfig.objects.filter(
                project=project
            ).select_related("eval_template")

            eval_configs_data = []
            models_sets = []

            for config in custom_eval_configs:
                available_models = config.eval_template.config.get("models", [])

                if available_models:
                    models_sets.append(set(available_models))

                eval_configs_data.append(
                    {
                        "id": str(config.id),
                        "name": config.name,
                        "eval_template": {
                            "id": str(config.eval_template.id),
                            "name": config.eval_template.name,
                            "description": config.eval_template.description,
                            "required_keys": config.eval_template.config.get(
                                "required_keys", []
                            ),
                            "optional_keys": config.eval_template.config.get(
                                "optional_keys", []
                            ),
                            "tags": config.eval_template.eval_tags,
                        },
                        "error_localizer": config.error_localizer,
                        "available_models": available_models,
                        "mapping": config.mapping or {},
                        "params": (config.config or {}).get("params", {}),
                    }
                )

            if models_sets:
                common_models = list(
                    set.intersection(*models_sets)
                    if len(models_sets) > 1
                    else models_sets[0]
                )
            else:
                common_models = []

            return self._gm.success_response(
                {
                    "eval_configs": eval_configs_data,
                    "common_models": common_models,
                }
            )

        except Project.DoesNotExist:
            return self._gm.not_found(get_error_message("PROJECT_NOT_FOUND"))

        except Exception as e:
            logger.exception("Error getting eval configs", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_GET_EVAL_CONFIGS")
            )
