"""A voice call whose provider setup cannot be simulated shows why.

prepare_call stops such a call before a phone number is taken and gives the
sentence the call shows as its reason. Every key here is a made-up string.
"""

from unittest.mock import patch

import pytest

from ee.voice.tests.test_bland_service import agent_definition as agent_definition
from ee.voice.tests.test_bland_service import bland_web_call as bland_web_call
from ee.voice.tests.test_bland_service import call_execution as call_execution
from ee.voice.tests.test_bland_service import scenario as scenario
from ee.voice.tests.test_bland_service import simulator_agent as simulator_agent
from ee.voice.tests.test_bland_service import (
    unsupported_sip_call as unsupported_sip_call,
)

SIP_NOT_SUPPORTED = (
    "Outbound simulation is not supported for the provider 'eleven_labs' "
    "over a phone or SIP connection."
)
NO_WEB_CONNECTOR = (
    "The provider 'bland' has no web connector. Add a phone number to simulate it."
)


async def _prepare(call, workspace):
    from ee.voice.temporal.activities.voice_small import prepare_call
    from simulate.temporal.types.activities import PrepareCallInput

    with (
        patch("temporalio.activity.info"),
        patch(
            "simulate.services.agent_definition.resolve_api_key_for_version",
            return_value="made-up-key-3e7a",
        ),
    ):
        return await prepare_call(
            PrepareCallInput(call_id=str(call.id), workspace_id=str(workspace.id))
        )


@pytest.mark.django_db(transaction=True)
async def test_call_to_a_provider_with_no_phone_path_shows_why(
    unsupported_sip_call, workspace
):
    result = await _prepare(unsupported_sip_call, workspace)

    assert result.is_outbound is True
    assert result.error == SIP_NOT_SUPPORTED
    assert result.reason == SIP_NOT_SUPPORTED


@pytest.mark.django_db(transaction=True)
async def test_bland_call_without_a_phone_number_shows_why(bland_web_call, workspace):
    result = await _prepare(bland_web_call, workspace)

    assert result.is_outbound is True
    assert result.error == NO_WEB_CONNECTOR
    assert result.reason == NO_WEB_CONNECTOR
