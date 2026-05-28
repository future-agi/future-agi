"""Bridge registration for PromptTemplateViewSet.

Applies @expose_to_mcp programmatically to avoid editing the legacy
model_hub/views/prompt_template.py file (which has pre-existing lint debt).
This file does the same thing as adding @expose_to_mcp directly to the
ViewSet class — the decorator is just a callable.
"""

from ai_tools.drf_bridge import expose_to_mcp
from model_hub.views.prompt_template import PromptTemplateViewSet

expose_to_mcp(
    category="prompts",
    tools={
        "list": {
            "name": "list_prompt_templates",
            "description": (
                "List prompt templates in the workspace. Prompt templates are "
                "reusable, versioned LLM prompt definitions. Returns template "
                "id, name, folder, and modality. Filter by name (search) or "
                "modality."
            ),
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
        "retrieve": {
            "name": "get_prompt_template",
            "description": (
                "Get full details of a single prompt template by its UUID, "
                "including its name, description, variables, placeholders, and "
                "folder. Use this after list_prompt_templates to inspect a "
                "specific template."
            ),
        },
        "create": {
            "name": "create_prompt_template",
            "description": (
                "Create a new prompt template in the current workspace. "
                "Requires a unique name. variable_names and placeholders are "
                "optional. Returns the created template's id."
            ),
            "include_fields": [
                "name",
                "description",
                "variable_names",
                "placeholders",
                "prompt_folder",
            ],
        },
        "update": {
            "name": "update_prompt_template",
            "description": (
                "Update an existing prompt template's metadata (name, "
                "description, variables, placeholders, folder). Requires the "
                "template UUID — call list_prompt_templates first."
            ),
            "include_fields": [
                "name",
                "description",
                "variable_names",
                "placeholders",
                "prompt_folder",
            ],
        },
        "destroy": {
            "name": "delete_prompt_template",
            "description": (
                "Delete a prompt template by its UUID (soft delete). Requires "
                "the template UUID — call list_prompt_templates first. This "
                "also removes its associated versions."
            ),
        },
    },
)(PromptTemplateViewSet)
