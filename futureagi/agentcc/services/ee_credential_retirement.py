"""Durable, purpose-based retirement with retryable gateway readback."""

from django.db import transaction
from django.utils import timezone

from agentcc.models import AgentccAPIKey
from agentcc.models.mcp_service_provisioning import (
    AgentccEEAgentKey,
    AgentccEEServiceCredential,
)
from agentcc.services.ee_provisioning_availability import provisioning_enabled
from agentcc.services.gateway_client import GatewayClientError, get_gateway_client


def retire_ee_credentials():
    """Repeat after missed delivery or restart; never infer purpose from user nulls.

    Binding rows remain the durable retry set. A successful DELETE alone is not
    confirmation: readback must show this same key revoked or absent. No raw
    credential is read, created, returned, or persisted by this operation.
    """
    if provisioning_enabled():
        raise RuntimeError(
            "Disable Enterprise provisioning before retiring credentials"
        )
    now = timezone.now()
    with transaction.atomic():
        credentials = AgentccEEServiceCredential.objects.exclude(status="revoked")
        service_count = credentials.update(status="revoked", revoked_at=now)
        bindings = AgentccEEAgentKey.objects.values_list("api_key_id", flat=True)
        keys = AgentccAPIKey._base_manager.filter(pk__in=bindings)
        agent_count = keys.exclude(status=AgentccAPIKey.REVOKED).update(
            status=AgentccAPIKey.REVOKED, updated_at=now
        )
    client = get_gateway_client()
    confirmed, pending = 0, 0
    # Include already retired, expired and deleted rows: a missed publication is
    # independent of the database state. Restart cannot lose this retry set.
    for key_id, org_id in keys.values_list(
        "gateway_key_id", "organization_id"
    ).iterator():
        try:
            try:
                client.revoke_key(key_id)
            except GatewayClientError as exc:
                if exc.status_code != 404:
                    raise
            absent = False
            try:
                observed = client.get_key(key_id)
            except GatewayClientError as exc:
                if exc.status_code != 404:
                    raise
                observed, absent = None, True
            matches = absent or (
                isinstance(observed, dict)
                and observed.get("id") == key_id
                and observed.get("status") == "revoked"
                and isinstance(observed.get("metadata"), dict)
                and observed["metadata"].get("org_id") == str(org_id)
            )
        except GatewayClientError:
            matches = False
        if matches:
            confirmed += 1
        else:
            pending += 1
    return {
        "service_credentials_retired": service_count,
        "agent_keys_retired": agent_count,
        "gateway_confirmed": confirmed,
        "gateway_pending": pending,
    }
