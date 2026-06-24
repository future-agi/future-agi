"""Call-direction resolution + guards (TH-5642).

Fixes the audit's silent direction bugs at the point direction is decided:
- a typo'd ``call_direction`` (e.g. "outbond") previously became INBOUND silently;
- an OUTBOUND test on a provider that hasn't WIRED outbound silently ran the
  simulator-initiated INBOUND path (a green result that tested the wrong direction).

Both now fail LOUDLY. This module is Django-free (it only touches the pure
ProviderSpec registry) so the Temporal voice activities can import it.
"""

from __future__ import annotations

from simulate.providers.registry import (
    Direction,
    get_spec,
    implements_direction,
)


class UnsupportedCallDirectionError(ValueError):
    """A call_direction is malformed, or not wired for the chosen provider."""


# Who speaks first, from the SIMULATOR's perspective (LiveKit first_message_mode).
FIRST_MESSAGE_MODE_SPEAKS_FIRST = "assistant-speaks-first"
FIRST_MESSAGE_MODE_WAITS = "assistant-waits-for-user"


def first_message_mode_for(is_outbound: bool) -> str:
    """The simulator's speaking order for a call direction.

    Inbound (FutureAGI calls the agent → agent receives) → the simulator (our
    caller) speaks first. Outbound (the agent calls us) → the simulator waits; the
    agent speaks first. Previously this was set ONLY inside the LiveKit-bridge
    branch, so DIRECT_WS (ElevenLabs/Deepgram) and SIP providers silently produced a
    simulator-speaks-first transcript on outbound (audit gap). This resolver is
    applied to every transport.
    """
    return (
        FIRST_MESSAGE_MODE_WAITS if is_outbound else FIRST_MESSAGE_MODE_SPEAKS_FIRST
    )


def resolve_call_direction(
    call_direction: str | None,
    provider: str | None = None,
    *,
    enforce_implemented: bool = True,
) -> Direction:
    """Resolve a ``call_direction`` string to a :class:`Direction`, failing loudly.

    - missing / None / "" → INBOUND (preserves the historical default: the common
      case where direction is simply not specified is an inbound test);
    - "inbound" / "outbound" (any case) → that Direction;
    - any other non-empty value → raise (catches typos that previously became
      INBOUND silently);
    - if ``provider`` is a known agent platform that does NOT implement the resolved
      direction (and ``enforce_implemented``) → raise, instead of silently running
      the inbound path. Unknown / free-form providers skip this check so custom
      integrations are not broken.
    """
    raw = (call_direction or "").strip().lower()
    if raw == "":
        direction = Direction.INBOUND
    elif raw == Direction.INBOUND.value:
        direction = Direction.INBOUND
    elif raw == Direction.OUTBOUND.value:
        direction = Direction.OUTBOUND
    else:
        raise UnsupportedCallDirectionError(
            f"Unknown call_direction {call_direction!r} "
            f"(expected 'inbound' or 'outbound')"
        )

    if enforce_implemented and provider:
        spec = get_spec(provider)
        if (
            spec is not None
            and spec.is_agent_platform
            and not implements_direction(provider, direction)
        ):
            raise UnsupportedCallDirectionError(
                f"Provider {provider!r} does not yet implement {direction.value} "
                f"calls (supported={sorted(d.value for d in spec.supported_directions)}, "
                f"implemented={sorted(d.value for d in spec.implemented_directions)}). "
                f"Refusing to silently run the inbound path."
            )
    return direction


def resolve_is_outbound(
    call_direction: str | None,
    provider: str | None = None,
    *,
    enforce_implemented: bool = True,
) -> bool:
    """Boolean form of :func:`resolve_call_direction` (True iff OUTBOUND)."""
    return (
        resolve_call_direction(
            call_direction, provider, enforce_implemented=enforce_implemented
        )
        is Direction.OUTBOUND
    )
