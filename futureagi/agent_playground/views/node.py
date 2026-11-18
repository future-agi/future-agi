import structlog
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.decorators import method_decorator
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from agent_playground.models.choices import PortDirection
from agent_playground.models.graph import Graph
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node import Node
from agent_playground.models.node_connection import NodeConnection
from agent_playground.models.port import Port
from agent_playground.serializers.contracts import AGENT_PLAYGROUND_ERROR_RESPONSES
from agent_playground.serializers.edge import SourceNodeOutputPortsSerializer
from agent_playground.serializers.node import (
    CreateNodeSerializer,
    NodeReadSerializer,
    UpdateNodeSerializer,
)
from agent_playground.services.dataset_bridge import sync_dataset_columns
from agent_playground.services.node_crud import (
    cascade_soft_delete_node,
    create_node,
    update_node,
)
from agent_playground.utils.graph import get_graph_and_version, require_draft
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

agent_playground_errors = swagger_auto_schema(
    responses=AGENT_PLAYGROUND_ERROR_RESPONSES
)


@method_decorator(name="create", decorator=agent_playground_errors)
@method_decorator(name="retrieve", decorator=agent_playground_errors)
@method_decorator(name="partial_update", decorator=agent_playground_errors)
@method_decorator(name="destroy", decorator=agent_playground_errors)
@method_decorator(name="possible_edge_mappings", decorator=agent_playground_errors)
class NodeCrudViewSet(ModelViewSet):
    """Granular CRUD for nodes within a graph version."""

    permission_classes = [IsAuthenticated]
    lookup_url_kwarg = "node_id"
    _gm = GeneralMethods()

    def get_queryset(self):
        return Node.no_workspace_objects.select_related(
            "node_template",
            "ref_graph_version__graph",
            "prompt_template_node__prompt_template",
            "prompt_template_node__prompt_version",
        ).prefetch_related("ports__ref_port")

    def get_serializer_class(self):
        if self.action == "create":
            return CreateNodeSerializer
        if self.action == "partial_update":
            return UpdateNodeSerializer
        return NodeReadSerializer

    def create(self, request, pk=None, version_id=None):
        """POST /graphs/{pk}/versions/{version_id}/nodes/"""
        try:
            graph, version = get_graph_and_version(request, pk, version_id)
            if require_draft(version):
                return self._gm.bad_request(
                    get_error_message("ONLY_DRAFT_VERSIONS_UPDATABLE")
                )

            serializer = CreateNodeSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            user = request.user
            organization = request.organization
            workspace = request.workspace

            with transaction.atomic():
                node, nc = create_node(
                    version=version,
                    data=serializer.validated_data,
                    user=user,
                    organization=organization,
                    workspace=workspace,
                )

            try:
                sync_dataset_columns(graph, version)
            except Exception:
                logger.warning("Column sync failed after node create", exc_info=True)

            node = self.get_queryset().filter(pk=node.pk).first() or node
            response_serializer = NodeReadSerializer(
                node, context={"node_connection": nc}
            )
            return self._gm.create_response(response_serializer.data)

        except Graph.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_NOT_FOUND"))
        except GraphVersion.DoesNotExist:
            return self._gm.not_found(get_error_message("VERSION_NOT_FOUND"))
        except (ValidationError, IntegrityError) as e:
            return self._gm.bad_request(str(e))
        except Exception as e:
            logger.exception("Error creating node", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_CREATE_NODE")
            )

    def retrieve(self, request, pk=None, version_id=None, node_id=None):
        """GET /graphs/{pk}/versions/{version_id}/nodes/{node_id}/"""
        try:
            graph, version = get_graph_and_version(request, pk, version_id)

            node = self.get_queryset().get(id=node_id, graph_version=version)
            serializer = NodeReadSerializer(node)
            return self._gm.success_response(serializer.data)

        except Graph.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_NOT_FOUND"))
        except GraphVersion.DoesNotExist:
            return self._gm.not_found(get_error_message("VERSION_NOT_FOUND"))
        except Node.DoesNotExist:
            return self._gm.not_found(get_error_message("NODE_NOT_FOUND"))
        except Exception as e:
            logger.exception("Error retrieving node", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_RETRIEVE_NODE")
            )

    def partial_update(self, request, pk=None, version_id=None, node_id=None):
        """PATCH /graphs/{pk}/versions/{version_id}/nodes/{node_id}/"""
        try:
            graph, version = get_graph_and_version(request, pk, version_id)
            if require_draft(version):
                return self._gm.bad_request(
                    get_error_message("ONLY_DRAFT_VERSIONS_UPDATABLE")
                )

            node = Node.no_workspace_objects.get(id=node_id, graph_version=version)

            serializer = UpdateNodeSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            user = request.user
            organization = request.organization
            workspace = request.workspace

            with transaction.atomic():
                node = update_node(
                    node=node,
                    data=serializer.validated_data,
                    user=user,
                    organization=organization,
                    workspace=workspace,
                )

            try:
                sync_dataset_columns(graph, version)
            except Exception:
                logger.warning("Column sync failed after node update", exc_info=True)

            node = self.get_queryset().filter(pk=node.pk).first() or node
            response_serializer = NodeReadSerializer(node)
            return self._gm.success_response(response_serializer.data)

        except Graph.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_NOT_FOUND"))
        except GraphVersion.DoesNotExist:
            return self._gm.not_found(get_error_message("VERSION_NOT_FOUND"))
        except Node.DoesNotExist:
            return self._gm.not_found(get_error_message("NODE_NOT_FOUND"))
        except (ValidationError, IntegrityError) as e:
            return self._gm.bad_request(str(e))
        except Exception as e:
            logger.exception("Error updating node", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_UPDATE_NODE")
            )

    def destroy(self, request, pk=None, version_id=None, node_id=None):
        """DELETE /graphs/{pk}/versions/{version_id}/nodes/{node_id}/"""
        try:
            graph, version = get_graph_and_version(request, pk, version_id)
            if require_draft(version):
                return self._gm.bad_request(
                    get_error_message("ONLY_DRAFT_VERSIONS_UPDATABLE")
                )

            node = Node.no_workspace_objects.get(id=node_id, graph_version=version)

            with transaction.atomic():
                cascade_soft_delete_node(node)

            try:
                sync_dataset_columns(graph, version)
            except Exception:
                logger.warning("Column sync failed after node delete", exc_info=True)

            return self._gm.success_response({"message": "Node deleted successfully"})

        except Graph.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_NOT_FOUND"))
        except GraphVersion.DoesNotExist:
            return self._gm.not_found(get_error_message("VERSION_NOT_FOUND"))
        except Node.DoesNotExist:
            return self._gm.not_found(get_error_message("NODE_NOT_FOUND"))
        except Exception as e:
            logger.exception("Error deleting node", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_DELETE_NODE")
            )

    def possible_edge_mappings(self, request, pk=None, version_id=None, node_id=None):
        """
        GET /graphs/{pk}/versions/{version_id}/nodes/{node_id}/possible-edge-mappings/

        Returns all source nodes that have NodeConnections targeting this node,
        along with their output ports. This helps the frontend build UI for
        creating edges between specific ports.

        Response: Array of source nodes with their output ports.
        """
        try:
            graph, version = get_graph_and_version(request, pk, version_id)

            # Get the target node
            node = Node.no_workspace_objects.select_related("graph_version").get(
                id=node_id, graph_version=version
            )

            # Find all incoming NodeConnections
            incoming_connections = NodeConnection.no_workspace_objects.filter(
                graph_version=version, target_node=node
            ).select_related("source_node")

            result = []
            for nc in incoming_connections:
                # Get all output ports from source node
                output_ports = Port.no_workspace_objects.filter(
                    node=nc.source_node, direction=PortDirection.OUTPUT
                ).order_by("display_name")

                result.append(
                    {
                        "source_node_id": nc.source_node.id,
                        "source_node_name": nc.source_node.name,
                        "node_connection_id": nc.id,
                        "output_ports": output_ports,
                    }
                )

            serializer = SourceNodeOutputPortsSerializer(result, many=True)
            return self._gm.success_response(serializer.data)

        except Graph.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_NOT_FOUND"))
        except GraphVersion.DoesNotExist:
            return self._gm.not_found(get_error_message("VERSION_NOT_FOUND"))
        except Node.DoesNotExist:
            return self._gm.not_found(get_error_message("NODE_NOT_FOUND"))
        except Exception as e:
            logger.exception("Error getting possible edge mappings", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_GET_EDGE_MAPPINGS")
            )
