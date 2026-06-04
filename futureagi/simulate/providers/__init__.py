"""Simulation provider registry (TH-5642) — the ProviderSpec single source of truth.

See internal-docs/multi-provider-simulation/DESIGN.md.
"""

from simulate.providers.registry import (
    PROVIDER_REGISTRY,
    CredentialShape,
    ProviderSpec,
    Role,
    Status,
    Transport,
    agent_platform_keys,
    connector_key_for,
    get_spec,
    is_agent_platform,
    provider_choices,
)

__all__ = [
    "PROVIDER_REGISTRY",
    "CredentialShape",
    "ProviderSpec",
    "Role",
    "Status",
    "Transport",
    "agent_platform_keys",
    "connector_key_for",
    "get_spec",
    "is_agent_platform",
    "provider_choices",
]
