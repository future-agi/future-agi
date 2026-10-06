"""Response envelopes for Workbench graph-version and node reads.

Declaration-only: the views wrap serializer output with
``GeneralMethods.success_response``/``create_response`` as
``{"status": true, "result": ...}``; these serializers describe that body for
swagger.json. Graph version pages use ``common.utils.pagination``
(page_number/page_size), not DRF page/limit.
"""

from drf_yasg import openapi
from rest_framework import serializers

from agent_playground.serializers.graph_version import (
    GraphVersionDetailSerializer,
    GraphVersionListSerializer,
)
from agent_playground.serializers.node import NodeReadSerializer


class GraphVersionPageMetadataSerializer(serializers.Serializer):
    total_count = serializers.IntegerField()
    page_number = serializers.IntegerField(
        help_text="Page actually returned; out-of-range requests get the last page."
    )
    page_size = serializers.IntegerField()
    total_pages = serializers.IntegerField()
    next_page = serializers.IntegerField(allow_null=True)
    previous_page = serializers.IntegerField(allow_null=True)


class GraphVersionListResultSerializer(serializers.Serializer):
    versions = GraphVersionListSerializer(many=True)
    metadata = GraphVersionPageMetadataSerializer()


class GraphVersionListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GraphVersionListResultSerializer()


class GraphVersionDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GraphVersionDetailSerializer()


class NodeReadResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = NodeReadSerializer()


IS_TEMPLATE_QUERY_PARAMETER = openapi.Parameter(
    "is_template",
    openapi.IN_QUERY,
    description=(
        "'true' (any case) resolves the graph among system graph templates "
        "instead of the caller's own graphs; other values are ignored."
    ),
    type=openapi.TYPE_STRING,
)

GRAPH_VERSION_LIST_QUERY_PARAMETERS = [
    openapi.Parameter(
        "page_number",
        openapi.IN_QUERY,
        description="1-based page number (default 1).",
        type=openapi.TYPE_INTEGER,
    ),
    openapi.Parameter(
        "page_size",
        openapi.IN_QUERY,
        description="Versions per page (default 10).",
        type=openapi.TYPE_INTEGER,
    ),
    openapi.Parameter(
        "search",
        openapi.IN_QUERY,
        description="Version number filter: 'v3', 'V3' or '3'; other text is ignored.",
        type=openapi.TYPE_STRING,
    ),
    IS_TEMPLATE_QUERY_PARAMETER,
]
