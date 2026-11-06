"""Connect-only Microsoft agents: Copilot Studio and Foundry.

Both are read through one Entra app (tenant ID, client ID, client secret). Only the client
secret is a secret; the tenant, client ID, URLs and agent ID are public config. The URL a token
is sent to is pinned to Microsoft hosts, so a pasted URL can never receive the customer's token.

Kept free of ``simulate`` imports so the serializer, the preflight probes and the gateway can
all read the same rules. ALK (``fi.alk.harness.job``) enforces the same rules in the sandbox.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlsplit

COPILOT_STUDIO = "copilot_studio"
FOUNDRY = "foundry"
MICROSOFT_CONNECTORS = (COPILOT_STUDIO, FOUNDRY)

MICROSOFT_CLIENT_SECRET = "AZURE_CLIENT_SECRET"
# Only needed to chat with a published Copilot Studio agent. Checked when present; never required
# for a build, because a trial licence cannot publish.
COPILOT_DIRECTLINE_SECRET = "COPILOT_DIRECTLINE_SECRET"

# Kept apart from ``CONNECTOR_ALIASES``: for an ``auto`` job any complete family there satisfies
# the credential check, and ``AZURE_CLIENT_SECRET`` is common in customers' own repositories.
MICROSOFT_CONNECTOR_ALIASES: dict[str, tuple[str, ...]] = {
    COPILOT_STUDIO: (MICROSOFT_CLIENT_SECRET,),
    FOUNDRY: (MICROSOFT_CLIENT_SECRET,),
}

MICROSOFT_REQUIRED_CONFIG: dict[str, tuple[str, ...]] = {
    COPILOT_STUDIO: ("tenant_id", "client_id", "dataverse_url", "agent_id"),
    FOUNDRY: ("tenant_id", "client_id", "project_endpoint", "agent_name"),
}

# The config field whose host receives the Entra token, and the host suffixes it may have.
MICROSOFT_ENDPOINT_HOSTS: dict[str, tuple[str, tuple[str, ...]]] = {
    COPILOT_STUDIO: ("dataverse_url", (".dynamics.com",)),
    FOUNDRY: (
        "project_endpoint",
        (".services.ai.azure.com", ".cognitiveservices.azure.com"),
    ),
}

MICROSOFT_LOGIN_HOST = "login.microsoftonline.com"
DIRECTLINE_HOST = "directline.botframework.com"
DATAVERSE_API_PATH = "api/data/v9.2"
FOUNDRY_TOKEN_SCOPE = "https://ai.azure.com/.default"
FOUNDRY_DEFAULT_API_VERSION = "v1"

# Human labels for the fields, used in validation messages the form shows.
_FIELD_LABELS = {
    "tenant_id": "Tenant ID",
    "client_id": "Client ID",
    "dataverse_url": "Dataverse environment URL",
    "agent_id": "Agent ID",
    "project_endpoint": "Project endpoint",
    "agent_name": "Agent name",
}


def is_microsoft_connector(connector: Any) -> bool:
    return str(connector or "").strip().lower() in MICROSOFT_CONNECTORS


def endpoint_host(connector: str, config: Mapping[str, Any]) -> str:
    field, _suffixes = MICROSOFT_ENDPOINT_HOSTS[connector]
    return (urlsplit(str(config.get(field) or "").strip()).hostname or "").lower()


def microsoft_config_error(connector: str, config: Mapping[str, Any]) -> str | None:
    """Why this config cannot reach the agent, or None when it can."""
    missing = [
        _FIELD_LABELS.get(key, key)
        for key in MICROSOFT_REQUIRED_CONFIG[connector]
        if not str(config.get(key) or "").strip()
    ]
    if missing:
        return f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} required"
    field, suffixes = MICROSOFT_ENDPOINT_HOSTS[connector]
    parsed = urlsplit(str(config.get(field) or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host.endswith(suffixes):
        return f"{_FIELD_LABELS[field]} must be an https URL on " + " or ".join(
            f"*{suffix}" for suffix in suffixes
        )
    return None


def microsoft_egress_domains(connector: str, config: Mapping[str, Any]) -> list[str]:
    """Hosts the build sandbox must reach to read this agent: Entra, then the agent's own host."""
    domains = [MICROSOFT_LOGIN_HOST]
    host = endpoint_host(connector, config)
    if host:
        domains.append(host)
    return domains


def token_url(tenant_id: str) -> str:
    return (
        f"https://{MICROSOFT_LOGIN_HOST}/{quote(str(tenant_id).strip(), safe='')}"
        "/oauth2/v2.0/token"
    )


def token_scope(connector: str, config: Mapping[str, Any]) -> str:
    if connector == COPILOT_STUDIO:
        return f"https://{endpoint_host(connector, config)}/.default"
    return FOUNDRY_TOKEN_SCOPE
