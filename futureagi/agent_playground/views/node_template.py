import structlog
from django.core.exceptions import ValidationError
from django.utils.decorators import method_decorator
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from agent_playground.models.node_template import NodeTemplate
from agent_playground.serializers.contracts import AGENT_PLAYGROUND_ERROR_RESPONSES
from agent_playground.serializers.node_template import (
    NodeTemplateDetailSerializer,
    NodeTemplateListSerializer,
)
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

agent_playground_errors = swagger_auto_schema(
    responses=AGENT_PLAYGROUND_ERROR_RESPONSES
)


@method_decorator(name="list", decorator=agent_playground_errors)
@method_decorator(name="retrieve", decorator=agent_playground_errors)
class NodeTemplateViewSet(ModelViewSet):
    """
    Read-only ViewSet for NodeTemplate.

    Node templates are system-wide and have no org/workspace filtering.
    """

    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "head", "options"]  # Read-only
    _gm = GeneralMethods()

    def get_queryset(self):
        """Get all non-deleted node templates."""
        return NodeTemplate.no_workspace_objects.all().order_by("name")

    def get_serializer_class(self):
        """Return appropriate serializer based on action."""
        if self.action == "list":
            return NodeTemplateListSerializer
        return NodeTemplateDetailSerializer

    def get_object(self):
        queryset = self.filter_queryset(self.get_queryset())
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
        filter_kwargs = {self.lookup_field: self.kwargs[lookup_url_kwarg]}
        obj = queryset.get(**filter_kwargs)
        self.check_object_permissions(self.request, obj)
        return obj

    def list(self, request, *args, **kwargs):
        """
        List all node templates.

        Returns lightweight representation: id, name, display_name, description, icon, categories.
        """
        try:
            queryset = self.get_queryset()
            serializer = self.get_serializer(queryset, many=True)
            return self._gm.success_response({"node_templates": serializer.data})
        except Exception as e:
            logger.exception("Error listing node templates", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_LIST_NODE_TEMPLATES")
            )

    def retrieve(self, request, *args, **kwargs):
        """
        Get a single node template with full details.

        Returns full template detail: + input_definition, output_definition,
        input_mode, output_mode, config_schema.
        """
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance)
            return self._gm.success_response(serializer.data)
        except (NodeTemplate.DoesNotExist, ValidationError):
            return self._gm.not_found(get_error_message("NODE_TEMPLATE_NOT_FOUND"))
        except Exception as e:
            logger.exception("Error retrieving node template", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_RETRIEVE_NODE_TEMPLATE")
            )
