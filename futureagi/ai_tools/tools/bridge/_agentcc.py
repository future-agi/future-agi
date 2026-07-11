"""Bridge registration for agentcc ViewSets — secrets, blocklists, routing
policies, webhook outbound, guardrails, prompt templates, custom properties,
analytics, etc. All are clean ModelViewSets with serializers; the bridge
auto-generates CRUD tool names.
"""

from agentcc.views.api_key import AgentccAPIKeyViewSet
from agentcc.views.blocklist import AgentccBlocklistViewSet
from agentcc.views.custom_property import AgentccCustomPropertySchemaViewSet
from agentcc.views.email_alert import AgentccEmailAlertViewSet
from agentcc.views.guardrail_feedback import AgentccGuardrailFeedbackViewSet
from agentcc.views.guardrail_policy import AgentccGuardrailPolicyViewSet
from agentcc.views.org_config import AgentccOrgConfigViewSet
from agentcc.views.prompt_template import AgentccPromptTemplateViewSet
from agentcc.views.provider_credential import AgentccProviderCredentialViewSet
from agentcc.views.request_log import AgentccRequestLogViewSet
from agentcc.views.routing_policy import AgentccRoutingPolicyViewSet
from agentcc.views.session import AgentccSessionViewSet
from agentcc.views.shadow_experiments import (
    AgentccShadowExperimentViewSet,
    AgentccShadowResultViewSet,
)
from agentcc.views.webhook_outbound import (
    AgentccWebhookEventViewSet,
    AgentccWebhookViewSet,
)
from ai_tools.drf_bridge import expose_to_mcp

# entity stripping removes 'Agentcc' prefix via the regex CamelCase split,
# so AgentccBlocklistViewSet -> "agentcc_blocklist" by default. That's
# fine — agentcc IS its own product area.

# AgentccAnalyticsViewSet has no standard list/retrieve (custom analytics
# actions only) — not a CRUD ViewSet, so not bridged.
expose_to_mcp(category="agentcc")(AgentccAPIKeyViewSet)
expose_to_mcp(category="agentcc")(AgentccBlocklistViewSet)
expose_to_mcp(category="agentcc")(AgentccCustomPropertySchemaViewSet)
expose_to_mcp(category="agentcc")(AgentccEmailAlertViewSet)
expose_to_mcp(category="agentcc")(AgentccGuardrailFeedbackViewSet)
expose_to_mcp(category="agentcc")(AgentccGuardrailPolicyViewSet)
expose_to_mcp(category="agentcc")(AgentccOrgConfigViewSet)
expose_to_mcp(category="agentcc")(AgentccPromptTemplateViewSet)
expose_to_mcp(category="agentcc")(AgentccProviderCredentialViewSet)
expose_to_mcp(category="agentcc")(AgentccRequestLogViewSet)
expose_to_mcp(category="agentcc")(AgentccRoutingPolicyViewSet)
expose_to_mcp(category="agentcc")(AgentccSessionViewSet)
expose_to_mcp(category="agentcc")(AgentccShadowExperimentViewSet)
expose_to_mcp(category="agentcc")(AgentccShadowResultViewSet)
expose_to_mcp(category="agentcc")(AgentccWebhookViewSet)
expose_to_mcp(category="agentcc")(AgentccWebhookEventViewSet)


# ---------------------------------------------------------------------------
# Phase 3A — destructive @actions (confirmation-gated; see PHASES.md 3A and
# ai_tools/confirmations.py) plus their paired add-side actions (mutate)
# where one-click Undo needs them.
#
# Deliberately NOT bridged: AgentccGatewayViewSet.update_provider — the
# paired add-side of remove_provider takes raw provider credentials
# (api_key/secret_key); flowing secrets through chat is the exact risk the
# cluster-7 deferral exists for (UX_UI 7.6 secret masking is a
# prerequisite). remove_gateway_provider therefore ships with an undo_note
# only. AgentccAPIKeyViewSet.revoke stays excluded (cluster-7 sign-off, 3B).
# ---------------------------------------------------------------------------

from agentcc.views.gateway import AgentccGatewayViewSet  # noqa: E402

_GATEWAY_ID_DOC = (
    "Gateway id — there is a single virtual gateway; always pass 'default'."
)


def _preview_remove_blocklist_words(params: dict, context) -> str:
    from agentcc.models.blocklist import AgentccBlocklist

    blocklist = (
        AgentccBlocklist.no_workspace_objects.filter(
            id=params.get("id"), organization=context.organization, deleted=False
        )
        .only("id", "name", "words")
        .first()
    )
    words = params.get("words") or []
    if blocklist is None:
        return (
            f"Blocklist `{params.get('id')}` was not found in this "
            "organization — nothing will be removed."
        )
    present = [w for w in words if w in (blocklist.words or [])]
    shown = ", ".join(f"'{w}'" for w in present[:15]) or "(none currently present)"
    more = f" … and {len(present) - 15} more" if len(present) > 15 else ""
    return (
        f"Will remove **{len(present)} word(s)** (of {len(words)} requested) "
        f"from blocklist **'{blocklist.name}'**: {shown}{more}. Guardrails "
        "using this blocklist stop matching the removed words.\n\n"
        "Undo: re-add them with `add_blocklist_words`."
    )


def _preview_remove_gateway_provider(params: dict, context) -> str:
    from agentcc.models.provider_credential import AgentccProviderCredential

    name = params.get("name")
    cred = (
        AgentccProviderCredential.no_workspace_objects.filter(
            organization=context.organization, provider_name=name, deleted=False
        )
        .only("id", "provider_name", "display_name", "models_list")
        .first()
    )
    if cred is None:
        return (
            f"Provider '{name}' is not configured on this organization's "
            "gateway — nothing will be removed."
        )
    model_count = len(cred.models_list or [])
    return (
        f"Will remove provider **'{cred.display_name or cred.provider_name}'** "
        f"({model_count} model(s)) from the AgentCC gateway and push the "
        "updated config. Requests routed to this provider will start "
        "failing.\n\n"
        "Undo requires re-adding the provider WITH its credentials (via the "
        "AgentCC Gateway settings UI) — credentials are not recoverable "
        "from chat. Treat this as hard to reverse."
    )


def _preview_remove_gateway_budget(params: dict, context) -> str:
    level = params.get("level")
    return (
        f"Will remove the gateway budget configured at level **'{level}'** "
        "and push the updated config. Spend at this level becomes "
        "UNCAPPED until a new budget is set.\n\n"
        "Undo: re-create it with `set_gateway_budget` (you need the "
        "previous limit values)."
    )


def _preview_remove_gateway_mcp_server(params: dict, context) -> str:
    server_id = params.get("server_id")
    return (
        f"Will remove MCP server **'{server_id}'** from the AgentCC gateway "
        "config and push the update. Tools served by it become unavailable "
        "to gateway consumers.\n\n"
        "Undo: re-add it with `update_gateway_mcp_server` (you need its "
        "previous config)."
    )


# Blocklist word management (remove = destructive, add = mutate undo-pair).
expose_to_mcp(
    category="agentcc",
    tools={
        "remove_words": {
            "name": "remove_blocklist_words",
            "entity": "agentcc_blocklist",
            "execution_policy": "destructive",
            "confirm_preview": _preview_remove_blocklist_words,
            "undo_note": (
                "Undo: re-add the removed words with `add_blocklist_words`."
            ),
            "undo_prompt": (
                "Undo the blocklist edit: call add_blocklist_words on "
                "blocklist {id} with words={words}."
            ),
            "query_params": {
                "words": {
                    "type": list[str],
                    "required": True,
                    "description": "Words to remove from the blocklist.",
                },
            },
            "description": (
                "Remove words from a named blocklist (guardrails stop "
                "matching them). DESTRUCTIVE: requires user confirmation "
                "(preview first, then re-call with confirm=true)."
            ),
        },
        "add_words": {
            "name": "add_blocklist_words",
            "entity": "agentcc_blocklist",
            "execution_policy": "mutate",
            "query_params": {
                "words": {
                    "type": list[str],
                    "required": True,
                    "description": "Words to add to the blocklist (deduplicated).",
                },
            },
            "description": (
                "Add words to a named blocklist (deduplicates against the "
                "existing list)."
            ),
        },
    },
)(AgentccBlocklistViewSet)

# Gateway config management. The gateway is a virtual singleton — detail
# actions accept any pk; tools document that gateway_id is always 'default'.
expose_to_mcp(
    category="agentcc",
    tools={
        "remove_provider": {
            "name": "remove_gateway_provider",
            "entity": "gateway",
            "pk_field": "gateway_id",
            "execution_policy": "destructive",
            "confirm_preview": _preview_remove_gateway_provider,
            "undo_note": (
                "Undo requires re-adding the provider with its credentials "
                "via the AgentCC Gateway settings UI (credentials cannot "
                "flow through chat)."
            ),
            "description": (
                "Remove (soft-delete) a provider credential from the AgentCC "
                "gateway by provider name and push the updated config. "
                f"{_GATEWAY_ID_DOC} DESTRUCTIVE: requires user confirmation "
                "(preview first, then re-call with confirm=true)."
            ),
        },
        "remove_budget": {
            "name": "remove_gateway_budget",
            "entity": "gateway",
            "pk_field": "gateway_id",
            "execution_policy": "destructive",
            "confirm_preview": _preview_remove_gateway_budget,
            "undo_note": (
                "Undo: re-create the budget with `set_gateway_budget` "
                "(needs the previous limit values)."
            ),
            "undo_prompt": (
                "Undo the budget removal: call set_gateway_budget for level "
                "'{level}' with the budget config that was in place before "
                "the removal (check the gateway config history if needed)."
            ),
            "description": (
                "Remove the budget configured at a level (org/team/user "
                "path) from the AgentCC gateway — spend at that level "
                f"becomes uncapped. {_GATEWAY_ID_DOC} DESTRUCTIVE: requires "
                "user confirmation (preview first, then re-call with "
                "confirm=true)."
            ),
        },
        "set_budget": {
            "name": "set_gateway_budget",
            "entity": "gateway",
            "pk_field": "gateway_id",
            "execution_policy": "mutate",
            "description": (
                "Set (create or replace) the budget config at a level on "
                f"the AgentCC gateway. {_GATEWAY_ID_DOC}"
            ),
        },
        "remove_mcp_server": {
            "name": "remove_gateway_mcp_server",
            "entity": "gateway",
            "pk_field": "gateway_id",
            "execution_policy": "destructive",
            "confirm_preview": _preview_remove_gateway_mcp_server,
            "undo_note": (
                "Undo: re-add the server with `update_gateway_mcp_server` "
                "(needs its previous config)."
            ),
            "undo_prompt": (
                "Undo the MCP server removal: call update_gateway_mcp_server "
                "with server_id='{server_id}' and the config that was in "
                "place before the removal."
            ),
            "description": (
                "Remove an MCP server from the AgentCC gateway config and "
                f"push the update. {_GATEWAY_ID_DOC} DESTRUCTIVE: requires "
                "user confirmation (preview first, then re-call with "
                "confirm=true)."
            ),
        },
        "update_mcp_server": {
            "name": "update_gateway_mcp_server",
            "entity": "gateway",
            "pk_field": "gateway_id",
            "execution_policy": "mutate",
            "description": (
                "Add or update an MCP server entry in the AgentCC gateway "
                f"config and push the update. {_GATEWAY_ID_DOC}"
            ),
        },
    },
)(AgentccGatewayViewSet)
