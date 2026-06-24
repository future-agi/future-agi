"""Single source of truth for selecting the **system** voice provider.

There are two distinct provider notions in the simulation stack, and they must
never be conflated:

* **System provider** — the infrastructure FutureAGI runs the *simulator*
  persona on (LiveKit or Vapi). This module resolves it. It is the lever for
  the Vapi→LiveKit migration: LiveKit is the default; Vapi stays registered and
  pluggable behind it.
* **Client provider** — the customer's *own* agent account
  (``AgentDefinition.provider``). It reflects whatever the user configured and
  is resolved separately; do **not** route it through this module.

Every code path that needs to know which engine the ``VoiceServiceManager``
should run MUST call :func:`resolve_system_voice_provider`. Do not read
``SYSTEM_VOICE_PROVIDER`` directly and do not hard-code a provider — doing so
reintroduces the string/enum drift this module exists to remove.

Resolution precedence (highest first):

1. an explicit ``override`` (a per-test / per-project pin),
2. the ``SYSTEM_VOICE_PROVIDER`` environment variable,
3. :data:`DEFAULT_SYSTEM_VOICE_PROVIDER` (LiveKit).

The function always returns a :class:`ProviderChoices` enum, normalising any
string input, so callers can rely on a single comparable type.
"""

from __future__ import annotations

import os

from tracer.models.observability_provider import ProviderChoices

__all__ = [
    "DEFAULT_SYSTEM_VOICE_PROVIDER",
    "SYSTEM_VOICE_PROVIDER_ENV_VAR",
    "resolve_system_voice_provider",
]

#: The simulator runs on LiveKit unless explicitly overridden. This is the
#: migration default — Vapi remains registered in ``ENGINE_REGISTRY`` and
#: selectable via an override or the environment variable.
DEFAULT_SYSTEM_VOICE_PROVIDER: ProviderChoices = ProviderChoices.LIVEKIT

#: Environment variable that pins the system provider when no explicit override
#: is supplied. Accepts any ``ProviderChoices`` value (e.g. ``"vapi"``).
SYSTEM_VOICE_PROVIDER_ENV_VAR: str = "SYSTEM_VOICE_PROVIDER"


def _coerce_provider(value: ProviderChoices | str) -> ProviderChoices:
    """Coerce a provider value to a :class:`ProviderChoices` enum.

    Args:
        value: an existing enum member or a provider string (case-insensitive).

    Returns:
        The matching :class:`ProviderChoices` member.

    Raises:
        ValueError: if ``value`` does not name a recognised provider.
    """
    if isinstance(value, ProviderChoices):
        return value
    try:
        return ProviderChoices(str(value).strip().lower())
    except ValueError as exc:
        valid = ", ".join(choice.value for choice in ProviderChoices)
        raise ValueError(
            f"Unknown system voice provider {value!r}; expected one of: {valid}."
        ) from exc


def resolve_system_voice_provider(
    override: ProviderChoices | str | None = None,
) -> ProviderChoices:
    """Resolve the system (simulator-side) voice provider.

    See the module docstring for the precedence rules. Always returns a
    :class:`ProviderChoices` enum so callers never have to defend against a
    raw string vs. enum.

    Args:
        override: an explicit per-call provider pin. Takes priority over the
            environment variable and the default. ``None`` or an empty string
            falls through to the environment, then the default.

    Returns:
        The resolved :class:`ProviderChoices` enum.

    Raises:
        ValueError: if ``override`` or the ``SYSTEM_VOICE_PROVIDER`` environment
            variable names an unknown provider.
    """
    if override is not None and override != "":
        return _coerce_provider(override)

    env_value = os.getenv(SYSTEM_VOICE_PROVIDER_ENV_VAR)
    if env_value:
        return _coerce_provider(env_value)

    return DEFAULT_SYSTEM_VOICE_PROVIDER
