import structlog
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.decorators import method_decorator
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from agent_playground.models.graph import Graph
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node import Node
from agent_playground.models.node_connection import NodeConnection
from agent_playground.serializers.contracts import AGENT_PLAYGROUND_ERROR_RESPONSES
from agent_playground.serializers.node_connection import (
    CreateNodeConnectionSerializer,
    NodeConnectionReadSerializer,
)
from agent_playground.services.dataset_bridge import sync_dataset_columns
from agent_playground.services.node_crud import cascade_soft_delete_node_connection
from agent_playground.utils.graph import get_graph_and_version, require_draft
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

agent_playground_errors = swagger_auto_schema(
    responses=AGENT_PLAYGROUND_ERROR_RESPONSES
)


@method_decorator(name="create", decorator=agent_playground_errors)
@method_decorator(name="destroy", decorator=agent_playground_errors)
class NodeConnectionCrudViewSet(ModelViewSet):
    """Granular CRUD for node connections within a graph version."""

    permission_classes = [IsAuthenticated]
    lookup_url_kwarg = "nc_id"
    _gm = GeneralMethods()

    def get_queryset(self):
        return NodeConnection.no_workspace_objects.select_related(
            "source_node", "target_node"
        )

    def get_serializer_class(self):
        if self.action == "create":
            return CreateNodeConnectionSerializer
        return NodeConnectionReadSerializer

    def create(self, request, pk=None, version_id=None):
        """POST /graphs/{pk}/versions/{version_id}/node-connections/"""
        try:
            graph, version = get_graph_and_version(request, pk, version_id)
            if require_draft(version):
                return self._gm.bad_request(
                    get_error_message("ONLY_DRAFT_VERSIONS_UPDATABLE")
                )

            serializer = CreateNodeConnectionSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            data = serializer.validated_data

            with transaction.atomic():
                nc = NodeConnection(
                    id=data["id"],
                    graph_version=version,
                    source_node_id=data["source_node_id"],
                    target_node_id=data["target_node_id"],
                )
                nc.save()  # runs clean()

            try:
                sync_dataset_columns(graph, version)
            except Exception:
                logger.warning(
                    "Column sync failed after connection create", exc_info=True
                )

            nc = self.get_queryset().get(id=nc.id)
            response_serializer = NodeConnectionReadSerializer(nc)
            return self._gm.create_response(response_serializer.data)

        except Graph.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_NOT_FOUND"))
        except GraphVersion.DoesNotExist:
            return self._gm.not_found(get_error_message("VERSION_NOT_FOUND"))
        except Node.DoesNotExist:
            return self._gm.not_found(get_error_message("NODE_NOT_FOUND"))
        except (ValidationError, IntegrityError) as e:
            return self._gm.bad_request(str(e))
        except Exception as e:
            logger.exception("Error creating node connection", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_CREATE_NODE_CONNECTION")
            )

    def destroy(self, request, pk=None, version_id=None, nc_id=None):
        """DELETE /graphs/{pk}/versions/{version_id}/node-connections/{nc_id}/"""
        try:
            graph, version = get_graph_and_version(request, pk, version_id)
            if require_draft(version):
                return self._gm.bad_request(
                    get_error_message("ONLY_DRAFT_VERSIONS_UPDATABLE")
                )

            nc = NodeConnection.no_workspace_objects.get(
                id=nc_id, graph_version=version
            )

            with transaction.atomic():
                cascade_soft_delete_node_connection(nc)

            try:
                sync_dataset_columns(graph, version)
            except Exception:
                logger.warning(
                    "Column sync failed after connection delete", exc_info=True
                )

            return self._gm.success_response(
                {"message": "Node connection deleted successfully"}
            )

        except Graph.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_NOT_FOUND"))
        except GraphVersion.DoesNotExist:
            return self._gm.not_found(get_error_message("VERSION_NOT_FOUND"))
        except NodeConnection.DoesNotExist:
            return self._gm.not_found(get_error_message("NODE_CONNECTION_NOT_FOUND"))
        except Exception as e:
            logger.exception("Error deleting node connection", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_DELETE_NODE_CONNECTION")
            )
