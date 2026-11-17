"""Legacy storage vocabulary, retained only for retirement and rollback."""

LEGACY_ORG_KEY_TYPE = "system"
RETIRED_KEY_TYPES = frozenset({LEGACY_ORG_KEY_TYPE, "mcp", "ingest"})
RECOVERABLE_TYPES = frozenset({LEGACY_ORG_KEY_TYPE, "ingest"})
RETIREMENT_REASON = "733"
# Human navigation, not the session-only issuance API.
PERSONAL_KEY_CREATE_URL = "/dashboard/keys"
