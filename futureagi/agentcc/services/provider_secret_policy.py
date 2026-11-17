"""Value-free input refusal and non-mutating public provider projections.

No secrets, rejected values or exception text are logged or cached here. The
publisher and discovery owners must call these helpers at their own boundaries.
"""

import math
from collections.abc import Iterable, Mapping

from agentcc.services.provider_url_auth import (
    provider_url_auth_values,
    public_provider_base_url,
    reject_provider_url_userinfo,
)
from agentcc.services.provider_validation import provider_credential_input_names

CREDENTIAL_REDACTION = "****"
EXTRA_CONFIG_SECRET_ERROR = "Credential fields are not allowed in extra_config."

# Explicit scalar-only allowlist: future contract fields are not public by
# default. models is deliberately excluded; use the dedicated models_list and
# safe_model_identifiers with the resolved credentials instead.
_PUBLIC_SETTING_TYPES = {
    "base_url": (str,),
    "api_format": (str,),
    "api_path_prefix": (str,),
    "timeout": (int,),
    "weight": (int, float),
    "enabled": (bool,),
    "max_concurrent": (int,),
    "conn_pool_size": (int,),
    "zdr_attested": (bool,),
    "data_region": (str,),
}


def _objects(value):
    """Walk JSON containers without recursion or retaining them beyond the call."""
    pending = [value]
    seen = set()
    while pending:
        current = pending.pop()
        if isinstance(current, (dict, list, tuple)):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            if isinstance(current, dict):
                yield current
                pending.extend(current.values())
            else:
                pending.extend(current)


def _base_url_names():
    # Keep URL aliases aligned with the provider contract, including legacy JSON.
    from agentcc.contracts.gateway_admin import ProviderConfig
    from agentcc.services.config_push import field_input_names

    return field_input_names(ProviderConfig, "base_url")


def reject_secret_extra_config(value):
    """Return unchanged settings or raise a fixed ValueError before any save.

    Refuse credential names regardless of their values, even inside a legacy
    wrapper/list. Callers must translate ValueError to a 400 without logging the
    submitted object; there is no customer-row cleanup in this policy.
    """
    if not isinstance(value, dict):
        raise ValueError("extra_config must be an object.")
    forbidden = provider_credential_input_names()
    if any(forbidden.intersection(obj) for obj in _objects(value)):
        raise ValueError(EXTRA_CONFIG_SECRET_ERROR)
    url_names = _base_url_names()
    for obj in _objects(value):
        for name in url_names.intersection(obj):
            if obj[name] is not None:
                reject_provider_url_userinfo(obj[name])
    return value


def public_provider_settings(value):
    """Project scalar noncredential settings under their original input names.

    Unknown names, credential aliases, malformed types and embedded containers
    are omitted. A fresh mapping is returned; stored JSON is never changed.
    """
    if not isinstance(value, dict):
        return {}
    # Lazy import avoids a cycle when config_push imports this policy.
    from agentcc.contracts.gateway_admin import ProviderConfig
    from agentcc.services.config_push import field_input_names

    forbidden = provider_credential_input_names()
    public = {}
    for field_name, types in _PUBLIC_SETTING_TYPES.items():
        for name in field_input_names(ProviderConfig, field_name) - forbidden:
            if name not in value:
                continue
            candidate = value[name]
            if candidate is None or type(candidate) in types:
                if isinstance(candidate, float) and not math.isfinite(candidate):
                    continue
                public[name] = (
                    public_provider_base_url(candidate)
                    if field_name == "base_url" and candidate is not None
                    else candidate
                )
    return public


def extra_config_secret_values(value):
    """In-memory legacy credential values for model-ID filtering, never output."""
    forbidden = provider_credential_input_names()
    url_names = _base_url_names()
    secrets = []
    for obj in _objects(value):
        for name, candidate in obj.items():
            if name in forbidden:
                secrets.append(candidate)
            elif name in url_names and candidate is not None:
                secrets.extend(provider_url_auth_values(candidate))
    return secrets


def _secret_strings(values):
    # Handle nested legacy credential values as well as an iterable of the exact
    # strings submitted to a provider. Ignore empty/nonstring scalars; do not
    # coerce them into misleading candidate credentials (e.g. the string None).
    pending = [values]
    seen = set()
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            if value:
                yield value
        elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
            identity = id(value)
            if identity not in seen:
                seen.add(identity)
                pending.extend(value.values() if isinstance(value, Mapping) else value)


def safe_model_identifiers(values, submitted_secrets, *, normalize=None):
    """Drop credential-bearing IDs before AND after optional normalization.

    Pass raw provider IDs, not pre-normalized text. Google discovery can supply
    normalize=lambda value: value.removeprefix("models/"). Identity is the
    default so persisted/non-Google identifiers retain their routing semantics.
    Secrets may be strings, collections or legacy dictionaries; no replacement
    marker becomes a routable model. Order and valid duplicates are preserved.
    """
    if not isinstance(values, (list, tuple)):
        return []
    secrets = tuple(_secret_strings(submitted_secrets))

    def safe(value):
        return (
            isinstance(value, str)
            and bool(value)
            and value != CREDENTIAL_REDACTION
            and not any(secret in value for secret in secrets)
        )

    result = []
    for value in values:
        if not safe(value):
            continue
        if normalize is not None:
            try:
                value = normalize(value)
            except Exception:
                # A normalizer must not turn malformed input into an error echo.
                continue
        if safe(value):
            result.append(value)
    return result
