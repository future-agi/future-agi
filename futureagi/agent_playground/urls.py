from django.urls import path
from rest_framework.routers import DefaultRouter

from agent_playground.views.dataset_link import GraphDatasetViewSet
from agent_playground.views.graph import GraphViewSet
from agent_playground.views.graph_execution import GraphExecutionViewSet
from agent_playground.views.node import NodeCrudViewSet
from agent_playground.views.node_connection import NodeConnectionCrudViewSet
from agent_playground.views.node_template import NodeTemplateViewSet
from agent_playground.views.port import PortCrudViewSet
from agent_playground.views.trace_import import TraceToGraphView

router = DefaultRouter()

router.register(r"graphs", GraphViewSet, basename="graph")
router.register(r"node-templates", NodeTemplateViewSet, basename="node-template")

urlpatterns = router.urls


# ── Version endpoints (manually routed, same pattern as before) ───

version_list_create = GraphViewSet.as_view(
    {
        "get": "list_versions",
        "post": "create_version",
    }
)

version_detail = GraphViewSet.as_view(
    {
        "get": "retrieve_version",
        "put": "update_version",
        "patch": "update_version",
        "delete": "delete_version",
    }
)

# ── Dataset endpoints ─────────────────────────────────────────────

dataset_detail = GraphDatasetViewSet.as_view({"get": "retrieve"})
dataset_row_create = GraphDatasetViewSet.as_view({"post": "create_row"})
dataset_rows_delete = GraphDatasetViewSet.as_view({"delete": "delete_rows"})
dataset_cell_update = GraphDatasetViewSet.as_view({"put": "update_cell"})
dataset_execute = GraphDatasetViewSet.as_view({"post": "execute"})

# ── Execution result endpoints ────────────────────────────────────

execution_list = GraphExecutionViewSet.as_view({"get": "list"})
execution_detail = GraphExecutionViewSet.as_view({"get": "retrieve"})
node_execution_detail = GraphExecutionViewSet.as_view({"get": "node_detail"})

version_activate = GraphViewSet.as_view(
    {
        "post": "activate_version",
    }
)

# ── Granular Node / Port / NodeConnection endpoints ───────────────

node_create = NodeCrudViewSet.as_view({"post": "create"})
node_detail = NodeCrudViewSet.as_view(
    {
        "get": "retrieve",
        "patch": "partial_update",
        "delete": "destroy",
    }
)
node_possible_mappings = NodeCrudViewSet.as_view({"get": "possible_edge_mappings"})
node_test_execution = NodeCrudViewSet.as_view({"post": "test_execution"})
port_update = PortCrudViewSet.as_view({"patch": "partial_update"})
nc_create = NodeConnectionCrudViewSet.as_view({"post": "create"})
nc_delete = NodeConnectionCrudViewSet.as_view({"delete": "destroy"})

urlpatterns = [
    # Trace → Graph import
    path(
        "graphs/from-trace/",
        TraceToGraphView.as_view(),
        name="graph-from-trace",
    ),
    # Version endpoints
    path(
        "graphs/<uuid:pk>/versions/",
        version_list_create,
        name="graph-versions",
    ),
    path(
        "graphs/<uuid:pk>/versions/<uuid:version_id>/",
        version_detail,
        name="graph-version-detail",
    ),
    path(
        "graphs/<uuid:pk>/versions/<uuid:version_id>/activate/",
        version_activate,
        name="graph-version-activate",
    ),
    # Dataset endpoints
    path(
        "graphs/<uuid:graph_id>/dataset/",
        dataset_detail,
        name="graph-dataset-detail",
    ),
    path(
        "graphs/<uuid:graph_id>/dataset/rows/",
        dataset_row_create,
        name="graph-dataset-row-create",
    ),
    path(
        "graphs/<uuid:graph_id>/dataset/rows/delete/",
        dataset_rows_delete,
        name="graph-dataset-rows-delete",
    ),
    path(
        "graphs/<uuid:graph_id>/dataset/cells/<uuid:cell_id>/",
        dataset_cell_update,
        name="graph-dataset-cell-update",
    ),
    path(
        "graphs/<uuid:graph_id>/dataset/execute/",
        dataset_execute,
        name="graph-dataset-execute",
    ),
    # Execution result endpoints
    path(
        "graphs/<uuid:graph_id>/executions/",
        execution_list,
        name="graph-execution-list",
    ),
    path(
        "graphs/<uuid:graph_id>/executions/<uuid:execution_id>/",
        execution_detail,
        name="graph-execution-detail",
    ),
    path(
        "executions/<uuid:execution_id>/nodes/<uuid:node_execution_id>/",
        node_execution_detail,
        name="graph-node-execution-detail",
    ),
    # ── Granular Node CRUD ───────────────────────────────────────────
    path(
        "graphs/<uuid:pk>/versions/<uuid:version_id>/nodes/",
        node_create,
        name="graph-version-node-create",
    ),
    path(
        "graphs/<uuid:pk>/versions/<uuid:version_id>/nodes/<uuid:node_id>/",
        node_detail,
        name="graph-version-node-detail",
    ),
    path(
        "graphs/<uuid:pk>/versions/<uuid:version_id>/nodes/<uuid:node_id>/possible-edge-mappings/",
        node_possible_mappings,
        name="node-possible-edge-mappings",
    ),
    path(
        "graphs/<uuid:pk>/versions/<uuid:version_id>/nodes/<uuid:node_id>/test-execution/",
        node_test_execution,
        name="node-test-execution",
    ),
    # ── Port update ──────────────────────────────────────────────────
    path(
        "graphs/<uuid:pk>/versions/<uuid:version_id>/ports/<uuid:port_id>/",
        port_update,
        name="graph-version-port-update",
    ),
    # ── NodeConnection CRUD ──────────────────────────────────────────
    path(
        "graphs/<uuid:pk>/versions/<uuid:version_id>/node-connections/",
        nc_create,
        name="graph-version-nc-create",
    ),
    path(
        "graphs/<uuid:pk>/versions/<uuid:version_id>/node-connections/<uuid:nc_id>/",
        nc_delete,
        name="graph-version-nc-delete",
    ),
] + urlpatterns
