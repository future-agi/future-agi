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
                },
            }
        }
    },
)(PersonaViewSet)
