"""Fixed proof-state bounds shared by catalog writers and readers.

These values constrain one versioned proof format and are not deployment
capacity knobs. Operational row, byte, and time budgets live in Django settings.
"""

MAX_DELIVERIES_PER_REVISION = 100_000
MAX_DELIVERY_REPLAYS = 8
MAX_LOGICAL_STATE_VARIANTS = 32
MAX_PROOF_BYTES = 64 << 20
