"""Bridge registration for PersonaViewSet (simulate)."""

from ai_tools.drf_bridge import expose_to_mcp
from simulate.views.persona import PersonaViewSet

# list_personas exposes the same filters the Personas UI offers (TH-5387):
# - type: 'prebuilt' (Future AGI built / system) vs 'custom' (your workspace)
# - simulation_type: 'voice' vs 'text'
# - search: name / description / keywords
expose_to_mcp(
    category="simulation",
    tools={
        "list": {
            "query_params": {
                "type": {
                    "type": str,
                    "description": (
                        "Filter by persona source: 'prebuilt' (Future AGI built / "
                        "system personas) or 'custom' (personas created in your "
                        "workspace)."
                    ),
                    "required": False,
                },
                "simulation_type": {
                    "type": str,
                    "description": (
                        "Filter by agent/simulation type: 'voice' or 'text'."
                    ),
                    "required": False,
                },
                "search": {
                    "type": str,
                    "description": (
                        "Case-insensitive search across persona name, description, "
                        "and keywords."
                    ),
                    "required": False,
                },
                "page": {
                    "type": int,
                    "description": "Page number for pagination.",
                    "required": False,
                },
                "page_size": {
                    "type": int,
                    "description": "Number of personas per page.",
                    "required": False,
                    # TH-4667: the paginator reads `limit`; without this
                    # remap page_size was silently ignored (always 10 rows).
                    "actual": "limit",
                },
            }
        }
    },
)(PersonaViewSet)

# ===========================================================================
# Phase 2A Packet C — persona CRUD writes + @actions (cluster 8 part).
# PersonaViewSet has NO `serializer_class` attribute (only
# get_serializer_class), so create/update declare their request serializers
# explicitly; @action method/detail auto-derive from the DRF decorator (A1).
# All eight tools are NET-NEW (only list_personas existed before).
# ===========================================================================

expose_to_mcp(
    category="simulation",
    tools={
        "retrieve": {
            "name": "get_persona",
            "id_source": "list_personas",
            "entity": "persona",
            "description": (
                "Get one persona's full profile by id — demographics, "
                "personality, communication style, scenario keywords. Get "
                "the id from list_personas."
            ),
        },
        "create": {
            "name": "create_persona",
            "serializer": "PersonaCreateSerializer",
            "entity": "persona",
            "description": (
                "Create a custom workspace-level persona (the simulated "
                "'customer' profile used to drive simulation calls). name "
                "and description are required; attribute fields (gender, "
                "age_group, location, profession, personality, ...) accept "
                "lists of values. Use get_persona_field_options to discover "
                "the valid choices."
            ),
        },
        "update": {
            "name": "update_persona",
            "serializer": "PersonaCreateSerializer",
            "id_source": "list_personas",
            "entity": "persona",
            "description": (
                "Update a workspace-level persona (system/prebuilt personas "
                "cannot be modified). Provide the persona id (from "
                "list_personas with type='custom') and the fields to change."
            ),
        },
        "destroy": {
            "name": "delete_persona",
            "id_source": "list_personas",
            "entity": "persona",
            "description": (
                "Delete a workspace-level persona by id (system/prebuilt "
                "personas cannot be deleted). Get the id from list_personas "
                "with type='custom'."
            ),
        },
    },
)(PersonaViewSet)

# Persona @actions — method/detail derived from the @action decorators.
expose_to_mcp(
    category="simulation",
    tools={
        "system_personas": {
            "name": "list_system_personas",
            "entity": "persona",
            "description": (
                "List only the system-level (Future AGI prebuilt) personas. "
                "No pagination — returns the full set."
            ),
        },
        "workspace_personas": {
            "name": "list_workspace_personas",
            "entity": "persona",
            "description": (
                "List only the custom personas created in your workspace. "
                "No pagination — returns the full set."
            ),
        },
        "field_options": {
            "name": "get_persona_field_options",
            "entity": "persona",
            "description": (
                "Get the valid choices for persona attribute fields (gender, "
                "age_group, profession, personality, communication_style, "
                "accent, ...) — call before create_persona / update_persona."
            ),
        },
        "duplicate": {
            "name": "duplicate_persona",
            "pk_field": "persona_id",
            "pk_kwarg": "id",
            "id_source": "list_personas",
            "entity": "persona",
            "description": (
                "Duplicate an existing persona (system or workspace) into a "
                "new workspace-level persona. Provide persona_id (from "
                "list_personas) and the new unique name."
            ),
        },
    },
)(PersonaViewSet)
