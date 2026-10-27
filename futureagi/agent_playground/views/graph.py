import structlog
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import (
    Case,
    IntegerField,
    Prefetch,
    Value,
    When,
)
from django.utils.decorators import method_decorator
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from agent_playground.models.choices import GraphVersionStatus
from agent_playground.models.graph import Graph
from agent_playground.models.graph_dataset import GraphDataset
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node import Node
from agent_playground.serializers.contracts import AGENT_PLAYGROUND_ERROR_RESPONSES
from agent_playground.serializers.graph import (
    BulkDeleteSerializer,
    GraphCreateResponseSerializer,
    GraphCreateSerializer,
    GraphDetailSerializer,
    GraphListSerializer,
    GraphUpdateSerializer,
    ReferenceableGraphSerializer,
)
from agent_playground.serializers.graph_version import (
    GraphVersionDetailSerializer,
    GraphVersionListSerializer,
    VersionCreateSerializer,
    VersionMetadataUpdateSerializer,
    prefetch_version_detail,
)
from agent_playground.services.dataset_bridge import activate_version_and_sync
from agent_playground.utils.cascade_delete import (
    cascade_soft_delete_graph,
    cascade_soft_delete_version_content,
)
from agent_playground.utils.graph import (
    annotate_graph_list_fields,
    get_exposed_ports_for_versions,
    get_global_variable_names_for_versions,
)
from agent_playground.utils.graph_validation import would_create_graph_reference_cycle
from agent_playground.utils.version_content import update_version_content
from common.utils.pagination import paginate_queryset
from model_hub.models.choices import DatasetSourceChoices
from model_hub.models.develop_dataset import Dataset
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

agent_playground_errors = swagger_auto_schema(
    responses=AGENT_PLAYGROUND_ERROR_RESPONSES
)


@method_decorator(name="list", decorator=agent_playground_errors)
@method_decorator(name="create", decorator=agent_playground_errors)
@method_decorator(name="retrieve", decorator=agent_playground_errors)
@method_decorator(name="update", decorator=agent_playground_errors)
@method_decorator(name="partial_update", decorator=agent_playground_errors)
@method_decorator(name="destroy", decorator=agent_playground_errors)
@method_decorator(name="bulk_delete", decorator=agent_playground_errors)
@method_decorator(name="list_versions", decorator=agent_playground_errors)
@method_decorator(name="create_version", decorator=agent_playground_errors)
@method_decorator(name="retrieve_version", decorator=agent_playground_errors)
@method_decorator(name="update_version", decorator=agent_playground_errors)
@method_decorator(name="delete_version", decorator=agent_playground_errors)
@method_decorator(name="activate_version", decorator=agent_playground_errors)
@method_decorator(name="referenceable_graphs", decorator=agent_playground_errors)
class GraphViewSet(ModelViewSet):
    """
    ViewSet for Graph CRUD and version management.
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def get_queryset(self):
        """
        Get graphs filtered by organization and workspace, or templates.

        If is_template=true query param is passed, returns system-wide templates
        (no org/workspace filter). Otherwise returns user's graphs filtered by
        org/workspace.
        """
        is_template = self.request.query_params.get("is_template")
        is_template_bool = (
            is_template and is_template.lower() == "true"
        )  # only strings can be passed via query params

        # Templates are system-wide, no org/workspace filter
        if is_template_bool:
            return Graph.no_workspace_objects.filter(is_template=True)

        organization = self.request.organization
        workspace = self.request.workspace

        queryset = (
            Graph.no_workspace_objects.filter(
                organization=organization, is_template=False
            )
            .select_related("created_by")
            .prefetch_related("collaborators")
        )

        if workspace:
            queryset = queryset.filter(workspace=workspace)

        return queryset.order_by("-created_at")

    def get_serializer_class(self):
        """Return appropriate serializer based on action."""
        if self.action == "list":
            return GraphListSerializer
        if self.action == "retrieve":
            return GraphDetailSerializer
        if self.action == "create":
            return GraphCreateSerializer
        if self.action in ("update", "partial_update"):
            return GraphUpdateSerializer
        return GraphListSerializer

    def get_object(self):
        queryset = self.filter_queryset(self.get_queryset())
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
        filter_kwargs = {self.lookup_field: self.kwargs[lookup_url_kwarg]}
        obj = queryset.get(**filter_kwargs)
        self.check_object_permissions(self.request, obj)
        return obj

    def list(self, request, *args, **kwargs):
        """
        List all graphs for the user's org/workspace.
        """
        try:
            queryset = self.get_queryset()
            search = request.query_params.get("search")
            if search:
                queryset = queryset.filter(name__icontains=search)

            # Pin specific IDs to top of results
            pinned_ids_param = request.query_params.get("pinned_ids", "")
            pinned_ids = (
                [i.strip() for i in pinned_ids_param.split(",") if i.strip()]
                if pinned_ids_param
                else []
            )
            if pinned_ids:
                queryset = queryset.annotate(
                    is_pinned=Case(
                        When(id__in=pinned_ids, then=Value(0)),
                        default=Value(1),
                        output_field=IntegerField(),
                    )
                ).order_by("is_pinned", "-created_at")

            queryset = annotate_graph_list_fields(queryset)
            page, metadata = paginate_queryset(queryset, request)
            serializer = GraphListSerializer(page, many=True)
            return self._gm.success_response(
                {"graphs": serializer.data, "metadata": metadata}
            )
        except Exception as e:
            logger.exception("Error listing graphs", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_LIST_GRAPHS")
            )

    def create(self, request, *args, **kwargs):
        """
        Create a new graph with an empty draft version (v1).

        Request body: {name, description (optional)}
        Returns: Graph object with empty draft version.
        """
        try:
            serializer = GraphCreateSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            user = request.user
            organization = request.organization
            workspace = request.workspace

            with transaction.atomic():
                graph = Graph.no_workspace_objects.create(
                    name=serializer.validated_data["name"],
                    description=serializer.validated_data.get("description"),
                    organization=organization,
                    workspace=workspace,
                    created_by=user,
                    is_template=False,  # we are not allowing user to create templates for now
                )

                # Create empty draft version
                GraphVersion.no_workspace_objects.create(
                    graph=graph, version_number=1, status=GraphVersionStatus.DRAFT
                )

                # Auto-create linked Dataset for batch execution
                dataset = Dataset.no_workspace_objects.create(
                    name=graph.name,
                    source=DatasetSourceChoices.GRAPH.value,
                    organization=organization,
                    workspace=workspace,
                    user=user,
                )
                GraphDataset.no_workspace_objects.create(
                    graph=graph,
                    dataset=dataset,
                )

            # Return graph with version (without nodes/edges)
            response_serializer = GraphCreateResponseSerializer(graph)
            return self._gm.create_response(response_serializer.data)

        except Exception as e:
            logger.exception("Error creating graph", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_CREATE_GRAPH")
            )

    def retrieve(self, request, *args, **kwargs):
        """
        Get graph detail with the active version expanded (or latest draft if no active).

        Returns full nested structure: nodes→ports, edges.
        """
        try:
            instance = self.get_object()
            serializer = GraphDetailSerializer(instance)
            return self._gm.success_response(serializer.data)
        except Graph.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_NOT_FOUND"))
        except Exception as e:
            logger.exception("Error retrieving graph", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_RETRIEVE_GRAPH")
            )

    def _blocking_reference_message(self, graphs_to_delete):
        version_ids = GraphVersion.no_workspace_objects.filter(
            graph__in=graphs_to_delete
        ).values_list("id", flat=True)

        blocking_nodes = (
            Node.no_workspace_objects.filter(ref_graph_version_id__in=version_ids)
            .exclude(graph_version__graph__in=graphs_to_delete)
            .select_related("graph_version__graph", "ref_graph_version__graph")
        )

        if blocking_nodes.exists():
            node = blocking_nodes.first()
            deleted_name = node.ref_graph_version.graph.name
            referencing_name = node.graph_version.graph.name
            return f"Unable to delete {deleted_name}: referenced by {referencing_name}"

        return None

    def _update_graph_metadata(self, request):
        """
        Update graph metadata only (name, description).

        Does NOT touch versions.
        """
        try:
            instance = self.get_object()
            serializer = GraphUpdateSerializer(data=request.data, partial=True)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            # Update only provided fields
            for field, value in serializer.validated_data.items():
                setattr(instance, field, value)
            instance.save()

            # Re-fetch with annotations for GraphListSerializer
            instance = annotate_graph_list_fields(
                Graph.no_workspace_objects.filter(pk=instance.pk)
                .select_related("created_by")
                .prefetch_related("collaborators")
            ).get()

            response_serializer = GraphListSerializer(instance)
            return self._gm.success_response(response_serializer.data)
        except Graph.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_NOT_FOUND"))
        except Exception as e:
            logger.exception("Error updating graph", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_UPDATE_GRAPH")
            )

    def update(self, request, *args, **kwargs):
        return self._update_graph_metadata(request)

    def partial_update(self, request, *args, **kwargs):
        return self._update_graph_metadata(request)

    def destroy(self, request, *args, **kwargs):
        """
        Soft-delete a graph through the router detail route with cascade validation.
        """
        try:
            graph = self.get_object()
            graphs_to_delete = self.get_queryset().filter(id=graph.id)

            blocking_message = self._blocking_reference_message(graphs_to_delete)
            if blocking_message:
                return self._gm.bad_request(blocking_message)

            with transaction.atomic():
                cascade_soft_delete_graph(graph)

            return self._gm.success_response({"message": "Graph deleted successfully"})
        except Graph.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_NOT_FOUND"))
        except Exception as e:
            logger.exception("Error deleting graph", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_DELETE_GRAPH")
            )

    @action(detail=False, methods=["post"], url_path="delete")
    def bulk_delete(self, request):
        """
        Bulk soft-delete graphs with reference validation.

        Accepts a list of graph IDs. Before deleting, checks if any graph version
        being deleted is referenced by nodes in graphs outside the deletion set.
        If all referencing graphs are also being deleted, it's allowed; otherwise blocked.
        """
        try:
            serializer = BulkDeleteSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            select_all = serializer.validated_data.get("select_all", False)
            exclude_ids = serializer.validated_data.get("exclude_ids", [])
            requested_ids = serializer.validated_data.get("ids", [])

            if select_all:
                graphs_to_delete = self.get_queryset()
                if exclude_ids:
                    graphs_to_delete = graphs_to_delete.exclude(id__in=exclude_ids)
            else:
                graphs_to_delete = self.get_queryset().filter(id__in=requested_ids)
                found_ids = set(graphs_to_delete.values_list("id", flat=True))
                missing_ids = [
                    str(rid) for rid in requested_ids if rid not in found_ids
                ]

                if missing_ids:
                    return self._gm.not_found(
                        {
                            "message": "Some graphs were not found.",
                            "missing_ids": missing_ids,
                        }
                    )

            blocking_message = self._blocking_reference_message(graphs_to_delete)
            if blocking_message:
                return self._gm.bad_request(blocking_message)

            with transaction.atomic():
                for graph in graphs_to_delete:
                    cascade_soft_delete_graph(graph)

            return self._gm.success_response({"message": "Graphs deleted successfully"})
        except Exception as e:
            logger.exception("Error deleting graphs", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_DELETE_GRAPH")
            )

    def list_versions(self, request, pk=None):
        """
        List all versions for a graph.

        Returns lightweight: id, version_number, status, commit_message, created_at.
        """
        try:
            graph = self.get_object()
            versions = GraphVersion.no_workspace_objects.filter(graph=graph).order_by(
                "-version_number"
            )

            search = request.query_params.get("search", "").strip()
            if search:
                # Support searching by "v1", "V1", or just "1"
                numeric = search.lstrip("vV")
                if numeric.isdigit():
                    versions = versions.filter(version_number=int(numeric))

            page, metadata = paginate_queryset(versions, request)
            version_ids = [v.id for v in page]
            global_variables_map = get_global_variable_names_for_versions(version_ids)
            serializer = GraphVersionListSerializer(
                page,
                many=True,
                context={"global_variables_map": global_variables_map},
            )
            return self._gm.success_response(
                {"versions": serializer.data, "metadata": metadata}
            )
        except Graph.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_NOT_FOUND"))
        except Exception as e:
            logger.exception("Error listing versions", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_LIST_VERSIONS")
            )

    def create_version(self, request, pk=None):
        """
        Create a new draft version (version_number = max + 1) with optional nodes and edges.
        """
        try:
            graph = self.get_object()
            requested_status = request.data.get("status", GraphVersionStatus.DRAFT)
            serializer = VersionCreateSerializer(
                data=request.data,
                context={"version_status": requested_status},
            )
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            with transaction.atomic():
                max_version = (
                    GraphVersion.no_workspace_objects.filter(graph=graph)
                    .order_by("-version_number")
                    .values_list("version_number", flat=True)
                    .first()
                    or 0
                )

                version = GraphVersion.no_workspace_objects.create(
                    graph=graph,
                    version_number=max_version + 1,
                    status=GraphVersionStatus.DRAFT,  # Always DRAFT; promotion handled by update_version_content
                    commit_message=serializer.validated_data.get("commit_message"),
                )

                nodes_data = serializer.validated_data.get("nodes", [])
                node_connections_data = serializer.validated_data.get(
                    "node_connections", []
                )
                requested_status = serializer.validated_data.get(
                    "status", GraphVersionStatus.DRAFT
                )

                if (
                    nodes_data
                    or node_connections_data
                    or requested_status == GraphVersionStatus.ACTIVE
                ):
                    update_version_content(
                        graph=graph,
                        version=version,
                        nodes_data=nodes_data,
                        new_status=requested_status,
                        commit_message=serializer.validated_data.get("commit_message"),
                        node_connections_data=node_connections_data,
                        user=request.user,
                        organization=request.organization,
                        workspace=request.workspace,
                    )

            # Refresh and return
            version.refresh_from_db()
            version = prefetch_version_detail(version)
            response_serializer = GraphVersionDetailSerializer(version)
            return self._gm.create_response(response_serializer.data)
        except Graph.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_NOT_FOUND"))
        except ValidationError as e:
            return self._gm.bad_request(str(e))
        except Exception as e:
            logger.exception("Error creating version", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_CREATE_VERSION")
            )

    def retrieve_version(self, request, pk=None, version_id=None):
        """
        Get a specific version with full nested structure (nodes→ports, edges).
        """
        try:
            graph = self.get_object()
            version = GraphVersion.no_workspace_objects.get(id=version_id, graph=graph)
            version = prefetch_version_detail(version)
            serializer = GraphVersionDetailSerializer(version)
            return self._gm.success_response(serializer.data)
        except Graph.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_NOT_FOUND"))
        except GraphVersion.DoesNotExist:
            return self._gm.not_found(get_error_message("VERSION_NOT_FOUND"))
        except Exception as e:
            logger.exception("Error retrieving version", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_RETRIEVE_VERSION")
            )

    def update_version(self, request, pk=None, version_id=None):
        """
        Metadata-only update endpoint (PUT/PATCH).

        Updates commit_message and/or promotes draft → active.
        Content changes (nodes, ports, edges) are done via granular CRUD or create_version.
        """
        try:
            graph = self.get_object()
            version = GraphVersion.no_workspace_objects.get(id=version_id, graph=graph)

            if version.status != GraphVersionStatus.DRAFT:
                return self._gm.bad_request(
                    get_error_message("ONLY_DRAFT_VERSIONS_UPDATABLE")
                )

            serializer = VersionMetadataUpdateSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            new_status = serializer.validated_data.get(
                "status", GraphVersionStatus.DRAFT
            )
            commit_message = serializer.validated_data.get("commit_message")

            with transaction.atomic():
                if new_status == GraphVersionStatus.ACTIVE:
                    activate_version_and_sync(graph, version, commit_message)
                elif commit_message:
                    version.commit_message = commit_message
                    version.save()

            version.refresh_from_db()
            version = prefetch_version_detail(version)
            response_serializer = GraphVersionDetailSerializer(version)
            return self._gm.success_response(response_serializer.data)

        except Graph.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_NOT_FOUND"))
        except GraphVersion.DoesNotExist:
            return self._gm.not_found(get_error_message("VERSION_NOT_FOUND"))
        except ValidationError as e:
            return self._gm.bad_request(str(e))
        except Exception as e:
            logger.exception("Error updating version", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_UPDATE_VERSION")
            )

    def delete_version(self, request, pk=None, version_id=None):
        """
        Soft-delete a specific version and its content (nodes, ports, edges).

        Cannot delete if this is the only version for the graph.
        Can delete active version - graph will then have no active version.
        """
        try:
            graph = self.get_object()
            version = GraphVersion.no_workspace_objects.get(id=version_id, graph=graph)

            # Check if this is the only version
            version_count = GraphVersion.no_workspace_objects.filter(
                graph=graph
            ).count()
            if version_count <= 1:
                return self._gm.bad_request(
                    get_error_message("CANNOT_DELETE_ONLY_VERSION")
                )

            with transaction.atomic():
                cascade_soft_delete_version_content(version)

            return self._gm.success_response(
                {"message": "Version deleted successfully"}
            )
        except Graph.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_NOT_FOUND"))
        except GraphVersion.DoesNotExist:
            return self._gm.not_found(get_error_message("VERSION_NOT_FOUND"))
        except Exception as e:
            logger.exception("Error deleting version", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_DELETE_VERSION")
            )

    def activate_version(self, request, pk=None, version_id=None):
        """
        POST /graphs/{graph_id}/versions/{version_id}/activate/

        Promote an inactive version to active.
        The currently active version (if any) is set to inactive.
        """
        try:
            graph = self.get_object()
            version = GraphVersion.no_workspace_objects.get(id=version_id, graph=graph)

            if version.status != GraphVersionStatus.INACTIVE:
                return self._gm.bad_request(
                    get_error_message("ONLY_INACTIVE_VERSIONS_ACTIVATABLE")
                )

            activate_version_and_sync(graph, version)

            prefetched = prefetch_version_detail(version)
            return self._gm.success_response(
                GraphVersionDetailSerializer(prefetched).data
            )
        except Graph.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_NOT_FOUND"))
        except GraphVersion.DoesNotExist:
            return self._gm.not_found(get_error_message("VERSION_NOT_FOUND"))
        except ValidationError as e:
            return self._gm.bad_request(str(e))
        except Exception as e:
            logger.exception("Failed to activate version", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_ACTIVATE_VERSION")
            )

    @action(detail=True, methods=["get"], url_path="referenceable-graphs")
    def referenceable_graphs(self, request, pk=None):
        """
        Returns non-template graphs whose non-draft versions (active or inactive)
        can be used as `ref_graph_version` without creating a cycle.

        Filters:
        - Same organization (and workspace, if present)
        - Excludes the current graph
        - Excludes graphs with only draft versions
        - Excludes graphs that would create a reference cycle
        """
        try:
            current_graph = self.get_object()
            organization = request.organization
            workspace = request.workspace

            candidate_graphs = (
                Graph.no_workspace_objects.filter(
                    organization=organization, is_template=False
                )
                .exclude(id=current_graph.id)
                .prefetch_related(
                    Prefetch(
                        "versions",
                        queryset=GraphVersion.no_workspace_objects.filter(deleted=False)
                        .exclude(status=GraphVersionStatus.DRAFT)
                        .order_by("-version_number"),
                        to_attr="non_draft_versions",
                    )
                )
            )

            if workspace:
                candidate_graphs = candidate_graphs.filter(workspace=workspace)

            result = []
            valid_candidates = []

            for candidate in candidate_graphs:
                # Skip if no non-draft versions
                if (
                    not hasattr(candidate, "non_draft_versions")
                    or not candidate.non_draft_versions
                ):
                    continue

                # Skip if would create cycle
                if would_create_graph_reference_cycle(
                    source_graph_id=current_graph.id,
                    target_graph_id=candidate.id,
                ):
                    continue

                valid_candidates.append(candidate)

            # Collect all version IDs for batch exposed ports query
            all_version_ids = []
            for candidate in valid_candidates:
                all_version_ids.extend([v.id for v in candidate.non_draft_versions])

            # Batch-fetch exposed ports for all versions
            exposed_ports_map = get_exposed_ports_for_versions(all_version_ids)

            # Build response with all versions
            for candidate in valid_candidates:
                versions_data = []
                for version in candidate.non_draft_versions:
                    versions_data.append(
                        {
                            "id": version.id,
                            "version_number": version.version_number,
                            "status": version.status,
                            "exposed_ports": exposed_ports_map.get(version.id, []),
                        }
                    )

                result.append(
                    {
                        "id": candidate.id,
                        "name": candidate.name,
                        "description": candidate.description,
                        "is_template": False,
                        "versions": versions_data,
                    }
                )

            serializer = ReferenceableGraphSerializer(result, many=True)
            return self._gm.success_response({"graphs": serializer.data})

        except Graph.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_NOT_FOUND"))
        except Exception as e:
            logger.exception("Error getting referenceable graphs", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_GET_REFERENCEABLE_GRAPHS")
            )
