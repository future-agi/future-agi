"""733 F9 retirement, with the L1b journal and admission gate behind an interface.

L1b owns the durable model and the OFF switch. Integration supplies
``accounts.services.retirement_733.gateway_adapter()`` with the protocol below.
The adapter must preserve the first prior_state on retries and write its manifest
in the caller's transaction. No credential values or hashes enter that journal.
There is deliberately no open-batch/drain barrier (733 addendum 4(2)).
"""

from importlib import import_module
from typing import Protocol

from django.core.management.base import CommandError
from django.db import transaction
from django.db.models import Q

from agentcc.models import AgentccAPIKey
from agentcc.services.auth_bridge import revoke_keys_with_readback


class GatewayRetirementAdapter(Protocol):
    def issuance_is_off(self) -> bool:
        """True only when L1b's actual legacy issuance paths are disabled."""

    def record(self, *, key, prior_state: dict, confirmed: bool) -> None:
        """Idempotently write/update L1b's durable retirement manifest row."""


def load_adapter() -> GatewayRetirementAdapter:
    try:
        module = import_module("accounts.services.retirement_733")
        adapter = module.gateway_adapter()
    except (ImportError, AttributeError) as exc:
        raise CommandError(
            "L1b OFF-switch/manifest adapter is not installed; gateway retirement "
            "requires accounts.services.retirement_733.gateway_adapter()."
        ) from exc
    return adapter


def selected_keys():
    """Purpose/owner selector, NOT a blanket user-is-null retirement.

    Older sync adoption persisted no hash and no person. Later importers may
    explicitly label source=sync. EE's binding is authoritative purpose evidence
    even when the row predates purpose metadata or still has a person attached.
    """
    purpose = (
        Q(name__startswith="console-batch-")
        | Q(
            metadata__purpose__in=[
                "console-batch",
                "playground-testing",
                "ee-agent-key",
                "omega-ee-agent",
            ]
        )
        | Q(owner__in=["playground", "omega-ee"])
        | Q(ee_agent_key__isnull=False)
    )
    adopted = Q(user__isnull=True) & (
        Q(key_hash="")
        | Q(metadata__source="sync")
        | Q(metadata__purpose="sync-adopted")
    )
    return AgentccAPIKey._base_manager.filter(purpose | adopted).order_by("pk")


def retire_selected_keys(adapter: GatewayRetirementAdapter, *, dry_run=False):
    if adapter.issuance_is_off() is not True:
        raise CommandError("L1b's OFF switch is not set; no gateway key was retired")
    report = {"selected": 0, "revoked": [], "pending": []}
    # Stable IDs, then lock each row so prior_state and its manifest agree.
    for key_id in selected_keys().values_list("pk", flat=True).iterator():
        report["selected"] += 1
        if dry_run:
            continue
        with transaction.atomic():
            key = AgentccAPIKey._base_manager.select_for_update().get(pk=key_id)
            prior = {
                "status": key.status,
                "expires_at": key.expires_at.isoformat() if key.expires_at else None,
            }
            result = revoke_keys_with_readback([key])
            adapter.record(key=key, prior_state=prior, confirmed=not result["pending"])
            report["revoked"].extend(result["revoked"])
            report["pending"].extend(result["pending"])
    return report
