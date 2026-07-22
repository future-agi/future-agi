import structlog
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.decorators import method_decorator
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from agent_playground.models.graph import Graph
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.port import Port
from agent_playground.serializers.contracts import AGENT_PLAYGROUND_ERROR_RESPONSES
from agent_playground.serializers.port import PortReadSerializer, UpdatePortSerializer
from agent_playground.services.dataset_bridge import sync_dataset_columns
from agent_playground.utils.graph import get_graph_and_version, require_draft
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

agent_playground_errors = swagger_auto_schema(
    responses=AGENT_PLAYGROUND_ERROR_RESPONSES
)


@method_decorator(name="partial_update", decorator=agent_playground_errors)
class PortCrudViewSet(ModelViewSet):
    """Granular CRUD for ports within a graph version."""

    permission_classes = [IsAuthenticated]
    lookup_url_kwarg = "port_id"
    _gm = GeneralMethods()

    def get_queryset(self):
        return Port.no_workspace_objects.select_related("ref_port")

    def get_serializer_class(self):
        if self.action == "partial_update":
            return UpdatePortSerializer
        return PortReadSerializer

    def partial_update(self, request, pk=None, version_id=None, port_id=None):
        """PATCH /graphs/{pk}/versions/{version_id}/ports/{port_id}/"""
        try:
            graph, version = get_graph_and_version(request, pk, version_id)
            if require_draft(version):
                return self._gm.bad_request(
                    get_error_message("ONLY_DRAFT_VERSIONS_UPDATABLE")
                )

            port = Port.no_workspace_objects.select_related(
                "node", "node__node_template", "ref_port", "ref_port__node"
            ).get(id=port_id, node__graph_version=version)

            serializer = UpdatePortSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            with transaction.atomic():
                port.display_name = serializer.validated_data["display_name"]
                port.save()

            try:
                sync_dataset_columns(graph, version)
            except Exception:
                logger.warning("Column sync failed after port update", exc_info=True)

            port = self.get_queryset().get(id=port.id)
            response_serializer = PortReadSerializer(port)
            return self._gm.success_response(response_serializer.data)

        except Graph.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_NOT_FOUND"))
        except GraphVersion.DoesNotExist:
            return self._gm.not_found(get_error_message("VERSION_NOT_FOUND"))
        except Port.DoesNotExist:
            return self._gm.not_found(get_error_message("PORT_NOT_FOUND"))
        except ValidationError as e:
            return self._gm.bad_request(e.message)
        except IntegrityError:
            return self._gm.bad_request(
                "A port with this display_name already exists on this node."
            )
        except Exception as e:
            logger.exception("Error updating port", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_UPDATE_PORT")
            )
