"""CN22 OFF gates: exact persisted-state snapshots without credential values.

Loaded into each Django shell by CW2/CW2c. Every concrete column is included,
not a projection of identifiers/statuses. Hash *every* value, including JSON
subtrees (mcp can contain credentials), so failure diagnostics never disclose
an ees_ identifier, stored secret hash, encrypted credential, or future field.
Rows and field names remain visible for counts and diagnosis. The only digest
normalization is JSON object order; list order, nulls, timestamps (including
microseconds), soft-deleted rows and all other stored values remain significant.
"""

import hashlib
import json


def answer(response):
    try:
        body = json.loads(response.content.decode() or "null")
    except ValueError:
        body = {"unparsed": response.content.decode(errors="replace")[:400]}
    return {"status": response.status_code, "body": body}


def ee_state(org_id):
    from accounts.models.audit_log import AuditLog
    from agentcc.models import AgentccAPIKey, AgentccOrgConfig
    from agentcc.models.ee_agent_key_mint import AgentccEEAgentKeyMintRecord
    from agentcc.models.ee_agent_key_policy import AgentccEEWorkspacePolicy
    from agentcc.models.mcp_service_provisioning import (
        AgentccEEAgentKey,
        AgentccEEServiceCredential,
        AgentccEEVirtualServer,
    )
    from agentcc.models.provider_credential import AgentccProviderCredential

    def rows(model, **filters):
        columns = [field.attname for field in model._meta.concrete_fields]
        return [
            {
                column: hashlib.sha256(
                    json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
                ).hexdigest()
                for column, value in row.items()
            }
            for row in model._base_manager.filter(organization_id=org_id, **filters)
            .order_by("pk")
            .values(*columns)
        ]

    return {
        "credentials": rows(AgentccEEServiceCredential),
        "agent_keys": rows(AgentccEEAgentKey),
        "mint_records": rows(AgentccEEAgentKeyMintRecord),
        "virtual_servers": rows(AgentccEEVirtualServer),
        "workspace_policies": rows(AgentccEEWorkspacePolicy),
        "gateway_keys": rows(AgentccAPIKey),
        "org_configs": rows(AgentccOrgConfig),
        "provider_credentials": rows(AgentccProviderCredential),
        "ee_audit": rows(AuditLog, action__startswith="agentcc.ee_"),
    }
