"""The `products` claim and rollout epoch for a key the session exchange mints (READINESS 3.13).

The exchange is the key batch every tab needs (product `platform`): it answers any workspace admitted
to at least one hosted product, active or paused, and refuses one admitted to none with 404
`product_not_admitted`. An unreadable rollout store mints nothing: 503 `verification_unavailable`
(RC-07); keys already minted expire on their own within 45 s. Each refusal is logged with its reason.
"""

from __future__ import annotations

from omega_rollout.enforce import require_platform


def rollout_claims_for(organization_id, workspace_id) -> tuple[dict[str, str], int]:
    """(`products` claim, epoch) for a key, or the refusal the exchange answers with."""
    claims = require_platform(organization_id, workspace_id, route="key_batch")
    return claims.products, claims.epoch
