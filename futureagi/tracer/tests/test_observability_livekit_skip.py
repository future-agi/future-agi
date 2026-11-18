"""Voice fetcher skips push-based providers (LiveKit): returns [] instead of raising."""

from types import SimpleNamespace

import pytest

from tracer.models.observability_provider import ProviderChoices
from tracer.services.observability_providers import ObservabilityService


@pytest.mark.unit
def test_get_call_logs_skips_livekit_push_based():
    provider = SimpleNamespace(provider=ProviderChoices.LIVEKIT)
    assert ObservabilityService.get_call_logs(provider) == []


@pytest.mark.unit
def test_get_call_logs_still_raises_for_unimplemented_provider():
    # Unknown provider (no fetch path, not in the no-pull set) still raises.
    provider = SimpleNamespace(provider="not-a-real-provider")
    with pytest.raises(NotImplementedError):
        ObservabilityService.get_call_logs(provider)
