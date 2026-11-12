"""The hosted Omega products under controlled rollout (READINESS 3.13, OD-01), and their vocabulary.

Pure constants: no Django imports, so the gateway contract test and the capability registry can read
them without a database.
"""

from __future__ import annotations

# The five hosted products. Studio (local) and Omega Agents run on the user's machine and are gated by
# the private package channel (OD-24), not by this app.
CONSOLE = "console"
SANDBOXES = "sandboxes"
DEPLOYMENTS = "deployments"
STUDIO_TAB = "studio_tab"
HUB = "hub"

HOSTED_PRODUCTS: tuple[str, ...] = (CONSOLE, SANDBOXES, DEPLOYMENTS, STUDIO_TAB, HUB)

# Routes every tab needs (the key batch, readiness, capabilities) carry this value in the route
# manifest. It is not a product an org is admitted to: it answers any org admitted to at least one.
PLATFORM = "platform"
# What a platform refusal records as its product; its 404 and 503 messages name no product.
PLATFORM_LABEL_PRODUCT = CONSOLE

PRODUCT_LABELS: dict[str, str] = {
    CONSOLE: "Omega Console",
    SANDBOXES: "Sandboxes",
    DEPLOYMENTS: "Deployments",
    STUDIO_TAB: "Studio",
    HUB: "Omega Hub",
}

# The capability registry id of each product (tfc/capabilities/registry.py), read by the UI.
CAPABILITY_IDS: dict[str, str] = {
    product: f"omega_{product}" for product in HOSTED_PRODUCTS
}

# Stages (READINESS 3.13). Each later stage admits every cohort an earlier stage admits.
S0, S1, S2, S3 = "S0", "S1", "S2", "S3"
STAGES: tuple[str, ...] = (S0, S1, S2, S3)
STAGE_RANK: dict[str, int] = {stage: rank for rank, stage in enumerate(STAGES)}

# Who an allow entry admits, and the first stage that admits that cohort.
COHORT_STAFF = "staff"  # S0: Future AGI staff orgs on the named list
COHORT_PARTNER = "partner"  # S1: design partners added by a named operator
COHORT_WAITLIST = "waitlist"  # S2: invited from the Early access waitlist
COHORTS: tuple[str, ...] = (COHORT_STAFF, COHORT_PARTNER, COHORT_WAITLIST)
COHORT_FIRST_STAGE: dict[str, str] = {
    COHORT_STAFF: S0,
    COHORT_PARTNER: S1,
    COHORT_WAITLIST: S2,
}

EFFECT_ALLOW = "allow"
EFFECT_DENY = "deny"

# The state an admitted product carries in the `products` claim.
STATE_ACTIVE = "active"
STATE_PAUSED = "paused"

# Stable reason codes (READINESS L07). A product an org is not admitted to answers 404 with the first
# one, so a dark product does not reveal itself; an admitted org whose product is paused gets 503 with
# the second, never a 404; a rollout store that cannot be read answers 503 with the third.
REASON_NOT_ADMITTED = "product_not_admitted"
REASON_PAUSED = "product_paused"
REASON_UNAVAILABLE = "verification_unavailable"


def is_product(value: object) -> bool:
    return isinstance(value, str) and value in HOSTED_PRODUCTS
