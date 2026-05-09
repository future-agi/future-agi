"""
Template discovery and instantiation endpoints.

Templates are Graph objects with is_template=True.  Two scopes:
- System templates (organization=None): visible to all orgs.
- Org-scoped templates (organization=request.organization): visible within org.

Instantiation creates a subgraph Node in a caller-supplied GraphVersion,
pinned to the template's current active GraphVersion.
"""

import structlog
from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from agent_playground.models.choices import GraphVersionStatus, NodeType, PortDirection
from agent_playground.models.graph import Graph
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node import Node
from agent_playground.models.port import Port
from agent_playground.services.template_composition import (
    check_interface_compatibility,
    detect_cross_graph_cycle,
    build_cross_graph_adjacency,
)
from agent_playground.utils.graph_validation import would_create_graph_reference_cycle
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)
_gm = GeneralMethods()


class TemplateListSerializer(serializers.ModelSerializer):
    active_version_id = serializers.SerializerMethodField()

    class Meta:
        model = Graph
        fields = [
            "id", "name", "description", "tags",
            "is_template", "organization",
            "active_version_id", "created_at", "updated_at",
        ]

    def get_active_version_id(self, obj):
        v = obj.versions.filter(status=GraphVersionStatus.ACTIVE, deleted=False).first()
        return str(v.id) if v else None


class TemplateCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Graph
        fields = ["name", "description", "tags"]

    def validate_tags(self, tags):
        cleaned = [t.strip().lower() for t in tags]
        bad = [t for t in cleaned if not t.isidentifier() and "-" not in t]
        if bad:
            raise serializers.ValidationError(
                f"Tags must be lowercase identifiers or hyphenated: {bad}"
            )
        return cleaned


class TemplateViewSet(ViewSet):
    """
    /agent-playground/templates/

    list   GET  — browse system + org templates, filter by ?tags=rag,safety
    create POST — publish current graph as an org-scoped template
    retrieve GET /<id>/ — template detail
    instantiate POST /<id>/instantiate/ — embed template as subgraph node
    """

    permission_classes = [IsAuthenticated]

    def _accessible_templates(self, request):
        """Templates visible to this request: system-wide + org-scoped."""
        from django.db.models import Q
        return Graph.objects.filter(
            is_template=True,
            deleted=False,
        ).filter(
            Q(organization__isnull=True) | Q(organization=request.organization)
        )

    def list(self, request):
        qs = self._accessible_templates(request).prefetch_related("versions")
        tags = request.query_params.get("tags")
        if tags:
            tag_list = [t.strip().lower() for t in tags.split(",") if t.strip()]
            qs = qs.filter(tags__contains=tag_list)
        search = request.query_params.get("q")
        if search:
            qs = qs.filter(name__icontains=search)

        data = TemplateListSerializer(qs, many=True).data
        return _gm.success_response(data)

    def retrieve(self, request, pk=None):
        try:
            graph = self._accessible_templates(request).get(pk=pk)
        except Graph.DoesNotExist:
            return _gm.error_response("Template not found", status_code=status.HTTP_404_NOT_FOUND)
        return _gm.success_response(TemplateListSerializer(graph).data)

    def create(self, request):
        """
        Publish an existing graph as an org-scoped template.
        Body: { "graph_id": "<uuid>", "name": "...", "description": "...", "tags": [...] }
        """
        graph_id = request.data.get("graph_id")
        if not graph_id:
            return _gm.error_response("graph_id is required", status_code=status.HTTP_400_BAD_REQUEST)

        try:
            source = Graph.objects.get(
                id=graph_id,
                organization=request.organization,
                deleted=False,
                is_template=False,
            )
        except Graph.DoesNotExist:
            return _gm.error_response("Graph not found", status_code=status.HTTP_404_NOT_FOUND)

        serializer = TemplateCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            with transaction.atomic():
                source.is_template = True
                source.name = serializer.validated_data.get("name", source.name)
                source.description = serializer.validated_data.get("description", source.description)
                source.tags = serializer.validated_data.get("tags", source.tags)
                source.save()
        except ValidationError as e:
            return _gm.error_response(str(e), status_code=status.HTTP_400_BAD_REQUEST)

        logger.info("template_published", graph_id=str(source.id), org=str(request.organization.id))
        return _gm.success_response(
            TemplateListSerializer(source).data,
            status_code=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="instantiate")
    def instantiate(self, request, pk=None):
        """
        Embed this template as a subgraph node in the caller's graph version.

        Body:
          target_version_id: UUID   — the GraphVersion to add the node to
          node_name: str            — display name for the new subgraph node
          position: {x, y}         — optional UI coordinates

        Returns the created Node.
        """
        try:
            template = self._accessible_templates(request).get(pk=pk)
        except Graph.DoesNotExist:
            return _gm.error_response("Template not found", status_code=status.HTTP_404_NOT_FOUND)

        target_version_id = request.data.get("target_version_id")
        node_name = request.data.get("node_name", template.name)
        position = request.data.get("position", {"x": 0, "y": 0})

        if not target_version_id:
            return _gm.error_response(
                "target_version_id is required", status_code=status.HTTP_400_BAD_REQUEST
            )

        try:
            target_version = GraphVersion.no_workspace_objects.get(
                id=target_version_id,
                graph__organization=request.organization,
                deleted=False,
            )
        except GraphVersion.DoesNotExist:
            return _gm.error_response(
                "Target graph version not found", status_code=status.HTTP_404_NOT_FOUND
            )

        if target_version.status != GraphVersionStatus.DRAFT:
            return _gm.error_response(
                "Can only add nodes to a draft version", status_code=status.HTTP_400_BAD_REQUEST
            )

        # Find the active version of the template to pin to
        active_version = (
            template.versions.filter(status=GraphVersionStatus.ACTIVE, deleted=False)
            .first()
        )
        if not active_version:
            return _gm.error_response(
                "Template has no active version — publish a version first",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Cross-graph cycle check
        if would_create_graph_reference_cycle(
            source_graph_id=target_version.graph_id,
            target_graph_id=template.id,
        ):
            return _gm.error_response(
                "Instantiating this template would create a circular graph dependency",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                node = Node(
                    graph_version=target_version,
                    type=NodeType.SUBGRAPH,
                    name=node_name,
                    ref_graph_version=active_version,
                    config={},
                    position=position,
                )
                node.clean()
                node.save(skip_validation=True)  # already called clean()

                # Mirror the template's exposed ports onto the new subgraph node
                template_ports = Port.no_workspace_objects.filter(
                    node__graph_version=active_version,
                    deleted=False,
                ).select_related("node")

                for tpl_port in template_ports:
                    if tpl_port.node.graph_version_id != active_version.id:
                        continue
                    Port.objects.create(
                        node=node,
                        key="custom",
                        display_name=tpl_port.display_name,
                        direction=tpl_port.direction,
                        data_schema=tpl_port.data_schema,
                        required=tpl_port.required,
                        default_value=tpl_port.default_value,
                        ref_port=tpl_port,
                    )

        except ValidationError as e:
            return _gm.error_response(str(e.message), status_code=status.HTTP_400_BAD_REQUEST)

        logger.info(
            "template_instantiated",
            template_id=str(template.id),
            target_version_id=str(target_version.id),
            node_id=str(node.id),
        )
        return _gm.success_response(
            {
                "node_id": str(node.id),
                "node_name": node.name,
                "ref_graph_version_id": str(active_version.id),
                "template_id": str(template.id),
            },
            status_code=status.HTTP_201_CREATED,
        )
