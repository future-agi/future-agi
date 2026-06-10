"""Voice observability fetcher must skip push-based providers (TH-5642 step 1).

LiveKit agents are instrumented with traceai-livekit and PUSH spans, so the
log-fetcher has nothing to pull and must skip gracefully (return []) rather than
raising NotImplementedError every poll cycle.
"""

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
    # A provider with no fetch path and not in the deliberate no-pull set still
    # raises, so we notice genuinely-unwired providers. (agora/deepgram/pipecat
    # moved to the graceful no-pull set — pull is impossible or gated TH-5682 —
    # covered in test_pull_observability_bland_twilio.py.)
    provider = SimpleNamespace(provider="not-a-real-provider")
    with pytest.raises(NotImplementedError):
        ObservabilityService.get_call_logs(provider)
