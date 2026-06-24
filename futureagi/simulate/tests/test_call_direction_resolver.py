"""Unit tests for the call-direction resolver (TH-5642).

Pins that the audit's silent direction bugs now fail loudly: typo'd direction and
an outbound request on a provider that hasn't wired outbound.
"""

import pytest

from simulate.providers.direction import (
    FIRST_MESSAGE_MODE_SPEAKS_FIRST,
    FIRST_MESSAGE_MODE_WAITS,
    UnsupportedCallDirectionError,
    first_message_mode_for,
    resolve_call_direction,
    resolve_is_outbound,
)
from simulate.providers.registry import Direction


@pytest.mark.unit
def test_missing_defaults_to_inbound():
    # Preserves the historical default for the common unspecified case.
    assert resolve_call_direction(None) is Direction.INBOUND
    assert resolve_call_direction("") is Direction.INBOUND
    assert resolve_call_direction("   ") is Direction.INBOUND


@pytest.mark.unit
def test_explicit_values_case_insensitive():
    assert resolve_call_direction("inbound") is Direction.INBOUND
    assert resolve_call_direction("OUTBOUND") is Direction.OUTBOUND
    assert resolve_is_outbound("outbound") is True
    assert resolve_is_outbound("inbound") is False


@pytest.mark.unit
def test_typo_raises_instead_of_silent_inbound():
    # The exact bug: "outbond" previously became is_outbound=False (inbound) silently.
    with pytest.raises(UnsupportedCallDirectionError):
        resolve_call_direction("outbond")
    with pytest.raises(UnsupportedCallDirectionError):
        resolve_is_outbound("oubound")


@pytest.mark.unit
def test_unimplemented_outbound_raises_for_known_provider():
    # Deepgram supports outbound but hasn't wired it → loud refusal, not a silent
    # inbound run. (Retell/Vapi DO wire outbound via the dialer registry.)
    with pytest.raises(UnsupportedCallDirectionError):
        resolve_call_direction("outbound", "deepgram")
    # Inbound is implemented for deepgram → fine.
    assert resolve_call_direction("inbound", "deepgram") is Direction.INBOUND


@pytest.mark.unit
def test_vapi_outbound_is_allowed():
    # Vapi is the one provider with outbound wired.
    assert resolve_call_direction("outbound", "vapi") is Direction.OUTBOUND
    assert resolve_is_outbound("outbound", "vapi") is True


@pytest.mark.unit
def test_unknown_provider_skips_implemented_check():
    # Custom / free-form providers are not in the registry → don't break them.
    assert resolve_call_direction("outbound", "my_custom_provider") is Direction.OUTBOUND


@pytest.mark.unit
def test_first_message_mode_is_direction_keyed():
    # Outbound (agent calls us) → simulator waits; inbound → simulator speaks first.
    assert first_message_mode_for(True) == FIRST_MESSAGE_MODE_WAITS
    assert first_message_mode_for(False) == FIRST_MESSAGE_MODE_SPEAKS_FIRST
    # Matches the existing LiveKit-bridge values so lifting it to all transports is
    # behaviour-preserving for the bridge.
    assert FIRST_MESSAGE_MODE_WAITS == "assistant-waits-for-user"
    assert FIRST_MESSAGE_MODE_SPEAKS_FIRST == "assistant-speaks-first"


@pytest.mark.unit
def test_enforce_implemented_can_be_disabled():
    # For callers that only want normalisation/typo-catching, not the wiring gate.
    assert (
        resolve_call_direction("outbound", "retell", enforce_implemented=False)
        is Direction.OUTBOUND
    )
