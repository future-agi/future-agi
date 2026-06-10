"""Bridge registration for AgentDefinitionOperationsViewSet (simulate)."""

from ai_tools.drf_bridge import expose_to_mcp
from simulate.views.agent_definition import AgentDefinitionOperationsViewSet

# entity override: derived class name would be 'agent_definition_operations' which is ugly.
#
# create/update need the REQUEST serializer, not the default `serializer_class`.
# The viewset's `serializer_class` is AgentDefinitionResponseSerializer (read /
# output only), so the bridge derived an EMPTY input schema for create_agent —
# making it impossible to actually create an agent via MCP (TH-5373). The real
# write serializer is AgentDefinitionSerializer (returned by get_serializer_class
# for create/update/partial_update); point the bridge at it so create_agent /
# update_agent expose the agent fields (name, contact_number, …).
expose_to_mcp(
    category="agents",
    tools={
        "list": {"name": "list_agents"},
        "retrieve": {"name": "get_agent"},
        "create": {
            "name": "create_agent",
            "serializer": "AgentDefinitionSerializer",
        },
        "update": {
            "name": "update_agent",
            "serializer": "AgentDefinitionSerializer",
        },
        "destroy": {"name": "delete_agent"},
    },
)(AgentDefinitionOperationsViewSet)

# ===========================================================================
# Phase 2A Packet C — agent-definition @action (cluster 3 tail).
# method/detail derive from the DRF @action decorator (detail=False POST);
# the request schema resolves from @validated_request
# (FetchAssistantRequestSerializer: provider/api_key/assistant_id).
#
# Adjudicated (one surface per capability, per spec):
# - delete_agent_definition (legacy HW) RETIRED — exact duplicate of the
#   bridged delete_agent (ViewSet destroy -> soft delete agent + versions).
# - duplicate_agent_definition stays hand-written (no DRF endpoint exists;
#   §6.3-style endpoint-first work).
# ===========================================================================
expose_to_mcp(
    category="agents",
    tools={
        "fetch_assistant_from_provider": {
            "name": "fetch_assistant_from_provider",
            "entity": "agent definition",
            "description": (
                "Fetch a voice assistant's configuration (name + system "
                "prompt) from an external provider (vapi or retell) given an "
                "API key and assistant id — WITHOUT creating an agent or "
                "version. Use it to preview/import provider agents before "
                "create_agent."
            ),
        }
    },
)(AgentDefinitionOperationsViewSet)
