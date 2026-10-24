"""Gateway admin calls for managed credits.

``put_granted`` sends ``credits-granted-push-v1``: the gateway applies the
granted total only when ``version`` is newer than the one it holds, and always
raises its spent counter to the floor. ``get_credits`` reads the gateway's
account state (``credits-v1``, admin form). Both raise ``GatewayClientError``.
"""

from typing import Any

from agentcc.services.gateway_admin.base import GatewayAdminClient


def put_granted(
    org_id: str,
    *,
    granted_total_micros: int,
    version: int,
    settled_spend_floor_micros: int,
) -> dict[str, Any]:
    """Push the ledger's granted total; returns ``{applied, ...account state}``."""
    return GatewayAdminClient().request(
        "PUT",
        f"/-/orgs/{org_id}/credits/granted",
        json={
            "granted_total_micros": granted_total_micros,
            "version": version,
            "settled_spend_floor_micros": settled_spend_floor_micros,
        },
    )


def get_credits(org_id: str) -> dict[str, Any]:
    """The gateway's hot-path account: granted, spent, held, outbox and ledger kind."""
    return GatewayAdminClient().request("GET", f"/-/orgs/{org_id}/credits")
