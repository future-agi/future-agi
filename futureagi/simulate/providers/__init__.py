"""Simulation provider registry (TH-5642) — the ProviderSpec single source of truth.

See internal-docs/multi-provider-simulation/DESIGN.md.
"""

from simulate.providers.registry import (
    PROVIDER_REGISTRY,
    CredentialShape,
    Direction,
    ProviderSpec,
    Role,
    Status,
    Transport,
    agent_platform_keys,
    connector_key_for,
    get_spec,
    implemented_directions_for,
    implements_direction,
    is_agent_platform,
    provider_choices,
    supports_direction,
)

__all__ = [
    "PROVIDER_REGISTRY",
    "CredentialShape",
    "Direction",
    "ProviderSpec",
    "Role",
    "Status",
    "Transport",
    "agent_platform_keys",
    "connector_key_for",
    "get_spec",
    "implemented_directions_for",
    "implements_direction",
    "is_agent_platform",
    "provider_choices",
    "supports_direction",
]
