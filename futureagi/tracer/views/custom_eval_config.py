import json
import traceback

import structlog
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

logger = structlog.get_logger(__name__)
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.serializers.develop_optimisation import EvalTemplateSerializer
from tfc.routers import uses_db
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.general_methods import GeneralMethods
from tracer.db_routing import DATABASE_FOR_CUSTOM_EVAL_CONFIG_LIST
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.serializers.custom_eval_config import (
    CustomEvalConfigSerializer,
    GetCustomEvalTemplateSerializer,
    RunEvaluationSerializer,
)
from tracer.utils.eval import evaluate_observation_span


class CustomEvalConfigView(BaseModelViewSetMixin, ModelViewSet):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()
    serializer_class = CustomEvalConfigSerializer

    def get_queryset(self):
        custom_eval_config_id = self.kwargs.get("pk")
        # Get base queryset with automatic filtering from mixin
        queryset = super().get_queryset()

        if custom_eval_config_id:
            queryset = queryset.filter(id=custom_eval_config_id)

        eval_template_id = self.request.query_params.get("eval_template_id")
        if eval_template_id:
            queryset = queryset.filter(eval_template_id=eval_template_id)

        name = self.request.query_params.get("name")
        if name:
            queryset = queryset.filter(name__icontains=name)

        project_id = self.request.query_params.get("project_id")
        if project_id:
            queryset = queryset.filter(project_id=project_id)

        return queryset

    def create(self, request, *args, **kwargs):
        try:
            data = request.data

            eval_template_id = str(data.get("eval_template"))
            try:
                eval_template = EvalTemplate.no_workspace_objects.get(
                    id=eval_template_id
                )
            except EvalTemplate.DoesNotExist:
                return self._gm.bad_request(
                    f"Eval template with id {eval_template_id} does not exist."
                )

            if eval_template.name == "tone":
                choices = eval_template.choices
                data["config"]["choices"] = choices

            serializer = self.get_serializer(data=data)
            serializer.is_valid(raise_exception=True)

            mapping = serializer.validated_data.get("mapping", {})

            optional_keys = eval_template.config.get("optional_keys", [])
            if len(optional_keys) > 0:
                for key in optional_keys:
                    if key in mapping and (mapping[key] is None or mapping[key] == ""):
                        mapping.pop(key)

            serializer.validated_data["mapping"] = mapping
            custom_eval_config = serializer.save()

            return self._gm.success_response({"id": str(custom_eval_config.id)})

        except Exception as e:
            traceback.print_exc()
            logger.exception(f"Error in creating custom eval config: {str(e)}")
            return self._gm.bad_request(
                f"Error in creating custom eval config: {str(e)}"
            )

    def partial_update(self, request, *args, **kwargs):
        try:
            custom_eval_config_id = kwargs.get("pk")
            try:
                custom_eval_config = CustomEvalConfig.objects.get(
                    id=custom_eval_config_id,
                    project__organization=getattr(request, "organization", None)
                    or request.user.organization,
                    deleted=False,
                )
            except CustomEvalConfig.DoesNotExist:
                return self._gm.bad_request(
                    f"Custom eval config with id {custom_eval_config_id} does not exist."
                )

            data = request.data

            eval_template_id = str(data.get("eval_template", "")) or None
            if eval_template_id:
                try:
                    eval_template = EvalTemplate.no_workspace_objects.get(
                        id=eval_template_id
                    )
                except EvalTemplate.DoesNotExist:
                    return self._gm.bad_request(
                        f"Eval template with id {eval_template_id} does not exist."
                    )

                if eval_template.name == "tone" and "config" in data:
                    choices = eval_template.choices
                    data["config"]["choices"] = choices

            serializer = self.get_serializer(
                custom_eval_config, data=data, partial=True
            )
            serializer.is_valid(raise_exception=True)

            if "mapping" in serializer.validated_data:
                mapping = serializer.validated_data["mapping"]
                resolved_template = (
                    eval_template
                    if eval_template_id
                    else custom_eval_config.eval_template
                )
                optional_keys = resolved_template.config.get("optional_keys", [])
                for key in optional_keys:
                    if key in mapping and (mapping[key] is None or mapping[key] == ""):
                        mapping.pop(key)
                serializer.validated_data["mapping"] = mapping

            serializer.save()

            return self._gm.success_response({"id": str(custom_eval_config.id)})

        except Exception as e:
            traceback.print_exc()
            logger.exception(f"Error in updating custom eval config: {str(e)}")
            return self._gm.bad_request(
                f"Error in updating custom eval config: {str(e)}"
            )

    @action(detail=False, methods=["post"])
    def check_exists(self, request, *args, **kwargs):
        try:
            project_name = request.data.get("project_name")
            project_type = request.data.get("project_type", "experiment")
            eval_tags = request.data.get("eval_tags")

            if not project_name or not eval_tags:
                return self._gm.bad_request(
                    "Both project_name and eval_tags are required"
                )

            if not isinstance(eval_tags, list):
                return self._gm.bad_request("eval_tags must be a list")

            try:
                Project.objects.get(
                    name=project_name,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                    trace_type=project_type,
                )

            except Project.DoesNotExist:
                return self._gm.success_response(
                    {
                        "exists": False,
                        "message": f"Project {project_name} does not exist",  # this means that it is a new project which is not created yet
                    }
                )

            query = Q()
            for eval_tag in eval_tags:
                try:
                    custom_eval_name = eval_tag.get("custom_eval_name")
                    eval_name = eval_tag.get("eval_name")
                    mapping_data = eval_tag.get("mapping", {})
                    mapping_normalized = json.dumps(mapping_data, sort_keys=True)
                    json_mapping = json.loads(mapping_normalized)

                    eval_query = Q(
                        project__name=project_name,
                        project__organization=getattr(request, "organization", None)
                        or request.user.organization,
                        name=custom_eval_name,
                    ) & (
                        ~Q(mapping__exact=json_mapping)
                        | ~Q(eval_template__name=eval_name)
                    )

                    query |= eval_query

                except json.JSONDecodeError as e:
                    return self._gm.bad_request(
                        f"Invalid JSON in eval_tag configuration: {str(e)}"
                    )

            conflicting_config = CustomEvalConfig.objects.filter(
                query & Q(deleted=False)
            ).first()

            if conflicting_config:
                return self._gm.success_response(
                    {
                        "exists": True,
                        "message": f"Custom eval '{conflicting_config.name}' already exists in project '{project_name}' with different configuration or mapping",
                    }
                )

            return self._gm.success_response(
                {"exists": False, "message": "All custom eval configurations are valid"}
            )

        except Exception as e:
            logger.exception(
                f"Error in checking custom eval config existence: {str(e)}"
            )
            return self._gm.internal_server_error_response(
                f"Error in checking custom eval config existence: {str(e)}"
            )

    @action(detail=False, methods=["get"])
    @uses_db(
        DATABASE_FOR_CUSTOM_EVAL_CONFIG_LIST,
        feature_key="feature:custom_eval_config_list",
    )
    def list_custom_eval_configs(self, request, *args, **kwargs):
        """
        List CustomEvalConfigs filtered by the filters provided in the request body.
        """
        try:
            filters = request.query_params.get("filters", {})
            task_id = request.query_params.get(
                "task_id", None
            ) or request.query_params.get("taskId", None)
            if filters:
                filters = json.loads(filters)
            if not isinstance(filters, dict):
                return self._gm.bad_request("Filters must be a dictionary")

            # Pure-routing change: only the alias is different from the
            # original query. CustomEvalConfigSerializer.get_eval_group()
            # does `obj.eval_group.name` per row — that per-row FK fetch
            # still goes through the router for EvalGroup (likely landing
            # on `default`) and remains a pre-existing N+1. We do NOT fix
            # that here — fixing the serializer is a separate refactor.
            queryset = CustomEvalConfig.objects.db_manager(
                DATABASE_FOR_CUSTOM_EVAL_CONFIG_LIST
            ).filter(
                deleted=False,
                project__organization=getattr(request, "organization", None)
                or request.user.organization,
            ).all()
            project_id = request.query_params.get(
                "project_id"
            ) or request.query_params.get("projectId")
            if project_id:
                queryset = queryset.filter(project_id=project_id)

            if task_id:
                queryset = queryset.filter(eval_tasks__id=task_id)
            # for key, value in filters.items():
            #     filter_key = f"filters__{key}"
            #     queryset = queryset.filter(**{filter_key: value})

            serializer = self.get_serializer(queryset, many=True)
            return self._gm.success_response(serializer.data)

        except Exception as e:
            logger.exception(f"Error in listing custom eval configs: {str(e)}")
            return self._gm.internal_server_error_response(
                f"Error in listing custom eval configs: {str(e)}"
            )

    @action(detail=False, methods=["post"])
    def run_evaluation(self, request, *args, **kwargs):
        try:
            serializer = RunEvaluationSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            try:
                custom_eval_config = CustomEvalConfig.objects.get(
                    id=serializer.validated_data.get("custom_eval_config_id"),
                    project__organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
            except CustomEvalConfig.DoesNotExist:
                return self._gm.bad_request(
                    f"Custom eval config with id {serializer.validated_data.get('custom_eval_config_id')} does not exist"
                )

            try:
                project_version = ProjectVersion.objects.get(
                    id=serializer.validated_data.get("project_version_id"),
                    project__organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
            except ProjectVersion.DoesNotExist:
                return self._gm.bad_request(
                    f"Project version with id {serializer.validated_data.get('project_version_id')} does not exist"
                )

            eval_tags = project_version.eval_tags

            if isinstance(eval_tags, str):
                try:
                    eval_tags = json.loads(eval_tags)
                except json.JSONDecodeError:
                    eval_tags = None
                    logger.warning("eval_tags JSON decode failed, defaulting to None")

            if eval_tags is None or len(eval_tags) == 0:
                return self._gm.bad_request("No eval tags found in the project version")

            req_eval_tag = next(
                (
                    eval_tag
                    for eval_tag in eval_tags
                    if str(eval_tag.get("custom_eval_config_id"))
                    == str(custom_eval_config.id)
                ),
                None,
            )

            if req_eval_tag is None:
                return self._gm.bad_request(
                    f"Custom eval config with id {custom_eval_config.id} not found in the project version"
                )

            observation_spans = ObservationSpan.objects.filter(
                project_version_id=project_version.id,
                observation_type=req_eval_tag.get("value").lower(),
                project__organization=getattr(request, "organization", None)
                or request.user.organization,
            )

            if observation_spans.count() == 0:
                return self._gm.bad_request(
                    f"No observation spans found for the custom eval config with id {custom_eval_config.id}"
                )

            # mark all previous eval_logger as deleted
            EvalLogger.objects.filter(
                observation_span__in=observation_spans,
                custom_eval_config=custom_eval_config,
            ).update(deleted=True, deleted_at=timezone.now())

            for observation_span in observation_spans:
                evaluate_observation_span.delay(
                    observation_span.id,
                    custom_eval_config.id,
                )

            return self._gm.success_response({"message": "Evaluation Ran Successfully"})
        except Exception as e:
            logger.exception(f"Error in running evaluation: {str(e)}")
            return self._gm.internal_server_error_response(
                f"Error in running evaluation: {str(e)}"
            )
        finally:
            close_old_connections()

    @action(detail=False, methods=["post"])
    def get_custom_eval_by_name(self, request, *args, **kwargs):
        try:
            serializer = GetCustomEvalTemplateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            eval_template_name = serializer.validated_data.get("eval_template_name")

            try:
                eval_template = EvalTemplate.objects.get(
                    name=eval_template_name,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                    owner=OwnerChoices.USER.value,
                )
                serializer = EvalTemplateSerializer(eval_template)

            except EvalTemplate.DoesNotExist:
                result = {"is_user_eval_template": False, "eval_template": None}
                return self._gm.success_response(result)

            result = {"is_user_eval_template": True, "eval_template": serializer.data}
            return self._gm.success_response(result)
        except Exception as e:
            logger.exception(f"Error in getting custom eval template by id: {str(e)}")
            return self._gm.internal_server_error_response(
                f"Error in getting custom eval template by id: {str(e)}"
            )
