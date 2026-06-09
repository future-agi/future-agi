"""Bridge registration for PromptTemplateViewSet.

All metadata (descriptions, parameter schemas, field allowlists) lives in
the serializer — PromptTemplateSerializer.__doc__ for the entity description
and field-level help_text for parameter descriptions. Standard CRUD tool
names are auto-generated as list/get/create/update/delete + entity. No
config needed here beyond the category.

TODO: when PromptTemplateViewSet.list grows a @validated_request(
query_serializer=PromptTemplateListRequestSerializer), the bridge will
auto-discover the search/page/page_size/ordering params and this file can
remove the list query_params block too.
"""

from ai_tools.drf_bridge import expose_to_mcp
from model_hub.views.prompt_template import PromptTemplateViewSet

expose_to_mcp(
    category="prompts",
    tools={
        "list": {
            "query_params": {
                "search": {
                    "type": str,
                    "description": (
                        "Filter by template name (case-insensitive substring "
                        "match). Example: 'summari' matches 'summarization-v3'."
                    ),
                    "required": False,
                },
                "page": {
                    "type": int,
                    "default": 1,
                    "description": "Page number, 1-indexed.",
                    "required": False,
                },
                "page_size": {
                    "type": int,
                    "default": 20,
                    "description": "Number of templates per page. Range 1-100.",
                    "required": False,
                },
                "ordering": {
                    "type": str,
                    "description": (
                        "Sort order. One of: 'name', '-name', 'created_at', "
                        "'-created_at'. Prefix with '-' for descending."
                    ),
                    "required": False,
                },
            },
        },
        "retrieve": {},
        "create": {},
        "update": {},
        "destroy": {},
    },
)(PromptTemplateViewSet)
