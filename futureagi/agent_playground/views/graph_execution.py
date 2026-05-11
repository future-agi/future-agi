import structlog
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import GenericViewSet

from agent_playground.models.choices import GraphExecutionStatus, NodeType
from agent_playground.models.execution_data import ExecutionData
from agent_playground.models.graph_execution import GraphExecution
from agent_playground.models.node_execution import NodeExecution
from agent_playground.serializers.graph_execution import (
    GraphExecutionListSerializer,
    GraphExecutionSerializer,
    NodeExecutionBriefSerializer,
)
from agent_playground.serializers.graph_version import (
    GraphVersionDetailSerializer,
    prefetch_version_detail,
)
from agent_playground.services.evaluation import (
    coerce_threshold,
    run_agent_evaluation_batch,
)
from common.utils.pagination import paginate_queryset
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class GraphExecutionViewSet(GenericViewSet):
    """
    ViewSet for Graph Execution results.

    Provides execution detail and node execution detail.
    """

    permission_classes = [IsAuthenticated]
    lookup_url_kwarg = "execution_id"
    _gm = GeneralMethods()

    def get_queryset(self, exclude_children=True):
        """GraphExecution filtered by user's organization and workspace."""
        organization = self.request.organization
        workspace = self.request.workspace

        queryset = GraphExecution.no_workspace_objects.filter(
            graph_version__graph__organization=organization,
            graph_version__graph__is_template=False,
        )

        if exclude_children:
            queryset = queryset.filter(parent_node_execution__isnull=True)

        if workspace:
            queryset = queryset.filter(graph_version__graph__workspace=workspace)

        return queryset

    def get_object(self):
        """Get GraphExecution for the execution_id in the URL."""
        queryset = self.get_queryset()
        return queryset.get(
            id=self.kwargs["execution_id"],
            graph_version__graph_id=self.kwargs["graph_id"],
        )

    def list(self, request, graph_id=None):
        """List all executions for a graph, with optional status filter."""
        try:
            queryset = (
                self.get_queryset()
                .filter(graph_version__graph_id=graph_id)
                .order_by("-created_at")
            )

            page, metadata = paginate_queryset(queryset, request)
            serializer = GraphExecutionListSerializer(page, many=True)

            return self._gm.success_response(
                {"executions": serializer.data, "metadata": metadata}
            )
        except Exception as e:
            logger.exception("Error listing executions", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_LIST_EXECUTIONS")
            )

    def retrieve(self, request, *args, **kwargs):
        """
        Returns the full Graph execution detail.

        Response includes basic execution data, the graph version DAG
        (nodes with ports, edges), and each node's execution status.
        Subgraph nodes include a nested ``sub_graph`` with their inner
        graph version and execution details (recursive).
        """
        try:
            graph_execution = self.get_object()
            data = self._build_execution_detail(graph_execution)
            return self._gm.success_response(data)
        except GraphExecution.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_EXECUTION_NOT_FOUND"))
        except Exception as e:
            logger.exception("Error getting execution detail", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_GET_EXECUTION_DETAIL")
            )

    def evaluate(self, request, graph_id=None, execution_id=None):
        """Run one or more evals against outputs from a completed graph execution."""
        try:
            graph_execution = self.get_object()
            if graph_execution.status != GraphExecutionStatus.SUCCESS:
                raise ValueError("Agent evaluation requires a successful execution")

            evaluators = request.data.get("evaluators") or []
            mappings = request.data.get("mappings") or {}
            threshold = coerce_threshold(request.data.get("threshold", 0.5))

            summary = run_agent_evaluation_batch(
                evaluators=evaluators,
                graph_execution=graph_execution,
                execution_context={
                    "organization_id": getattr(self.request.organization, "id", None),
                    "workspace_id": getattr(self.request.workspace, "id", None),
                },
                threshold=threshold,
                fallback_mappings=mappings,
            )
            summary["mappings"] = mappings

            output_payload = graph_execution.output_payload or {}
            agent_evaluations = list(output_payload.get("agent_evaluations") or [])
            agent_evaluations.append(summary)
            graph_execution.output_payload = {
                **output_payload,
                "agent_evaluations": agent_evaluations[-20:],
            }
            graph_execution.save(update_fields=["output_payload", "updated_at"])

            return self._gm.success_response(
                {
                    **summary,
                    "history": self._agent_evaluation_history(graph_execution),
                }
            )
        except GraphExecution.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_EXECUTION_NOT_FOUND"))
        except ValueError as e:
            return self._gm.bad_request(str(e))
        except Exception as e:
            logger.exception("Error evaluating graph execution", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_GET_EXECUTION_DETAIL")
            )

    def _build_execution_detail(self, graph_execution):
        """Build hierarchical execution detail for a graph execution.

        Returns a dict with:
        - Basic execution fields (id, status, timing, payloads)
        - ``nodes`` with ports and attached ``node_execution`` details
        - Subgraph nodes augmented with ``sub_graph`` containing the
          inner execution detail (recursive)
        """
        data = GraphExecutionSerializer(graph_execution).data

        version = prefetch_version_detail(graph_execution.graph_version)
        version_data = GraphVersionDetailSerializer(version).data

        node_executions = (
            NodeExecution.no_workspace_objects.filter(
                graph_execution=graph_execution,
            )
            .select_related("node")
            .prefetch_related("child_graph_executions")
        )
        exec_map = {str(ne.node_id): ne for ne in node_executions}
        eval_result_map = {
            str(ed.node_execution_id): ed.payload
            for ed in ExecutionData.no_workspace_objects.filter(
                node_execution__in=node_executions,
                port__key="evaluation_result",
                port__direction="output",
            ).select_related("port")
        }

        for node_data in version_data.get("nodes", []):
            node_id = str(node_data["id"])
            node_exec = exec_map.get(node_id)

            node_execution_data = (
                NodeExecutionBriefSerializer(node_exec).data if node_exec else None
            )
            if node_execution_data and str(node_exec.id) in eval_result_map:
                node_execution_data["evaluation_result"] = eval_result_map[
                    str(node_exec.id)
                ]
            node_data["node_execution"] = node_execution_data

            # For subgraph nodes, recursively attach inner graph
            if node_data["type"] == NodeType.SUBGRAPH and node_exec:
                child_exec = node_exec.child_graph_executions.first()
                node_data["sub_graph"] = (
                    self._build_execution_detail(child_exec) if child_exec else None
                )
            else:
                node_data["sub_graph"] = None

        data["nodes"] = version_data.get("nodes", [])
        data["node_connections"] = version_data.get("node_connections", [])
        return data

    def _agent_evaluation_history(self, graph_execution, limit=20):
        executions = (
            GraphExecution.no_workspace_objects.filter(
                graph_version__graph=graph_execution.graph_version.graph,
            )
            .exclude(output_payload__isnull=True)
            .order_by("-created_at")[:limit]
        )
        history = []
        for execution in executions:
            agent_evaluations = (execution.output_payload or {}).get(
                "agent_evaluations"
            ) or []
            if not agent_evaluations:
                continue
            history.append(
                {
                    **agent_evaluations[-1],
                    "execution_id": str(execution.id),
                    "created_at": execution.created_at.isoformat(),
                }
            )
        return list(reversed(history))

    def node_detail(self, request, execution_id=None, node_execution_id=None):
        """
        Returns detailed results for a specific node execution.

        All execution data (inputs/outputs) organized by port.
        """
        try:
            graph_execution = self.get_queryset(exclude_children=False).get(
                id=execution_id
            )

            node_execution = NodeExecution.no_workspace_objects.get(
                id=node_execution_id,
                graph_execution=graph_execution,
            )

            node = node_execution.node

            # Duration
            duration = None
            if node_execution.started_at and node_execution.completed_at:
                duration = (
                    node_execution.completed_at - node_execution.started_at
                ).total_seconds()

            # Get all execution data, grouped by port direction
            exec_data_qs = (
                ExecutionData.no_workspace_objects.filter(node_execution=node_execution)
                .select_related("port")
                .order_by("port__direction", "port__key")
            )

            inputs = []
            outputs = []
            for ed in exec_data_qs:
                entry = {
                    "port_id": ed.port_id,
                    "port_key": ed.port.key,
                    "port_direction": ed.port.direction,
                    "payload": ed.payload,
                    "is_valid": ed.is_valid,
                    "validation_errors": ed.validation_errors,
                }
                if ed.port.direction == "input":
                    inputs.append(entry)
                else:
                    outputs.append(entry)

            return self._gm.success_response(
                {
                    "node_execution_id": node_execution.id,
                    "node_id": node.id,
                    "node_name": node.name,
                    "node_type": node.type,
                    "status": node_execution.status,
                    "started_at": node_execution.started_at,
                    "completed_at": node_execution.completed_at,
                    "duration_seconds": duration,
                    "error_message": node_execution.error_message,
                    "inputs": inputs,
                    "outputs": outputs,
                }
            )
        except GraphExecution.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_EXECUTION_NOT_FOUND"))
        except NodeExecution.DoesNotExist:
            return self._gm.not_found(get_error_message("NODE_EXECUTION_NOT_FOUND"))
        except Exception as e:
            logger.exception("Error getting node execution detail", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_GET_NODE_EXECUTION_DETAIL")
            )
