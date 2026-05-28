"""Bridge registration for PromptTemplateViewSet.

Applies @expose_to_mcp programmatically to avoid editing the legacy
model_hub/views/prompt_template.py file (which has pre-existing lint debt).
Descriptions live on the PromptTemplateSerializer's docstring — the bridge
auto-derives the tool description from there, so this file only declares
tool names, query params, and field allowlists.
"""

from ai_tools.drf_bridge import expose_to_mcp
from model_hub.views.prompt_template import PromptTemplateViewSet

expose_to_mcp(
    category="prompts",
    tools={
        "list": {
            "name": "list_prompt_templates",
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
                    "description": "Page number, 1-indexed. Default 1.",
                    "required": False,
                },
                "page_size": {
                    "type": int,
                    "default": 20,
                    "description": (
                        "Number of templates per page. Range 1-100. Default 20."
                    ),
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
        "retrieve": {"name": "get_prompt_template"},
        "create": {"name": "create_prompt_template"},
        "update": {"name": "update_prompt_template"},
        "destroy": {"name": "delete_prompt_template"},
    },
)(PromptTemplateViewSet)
