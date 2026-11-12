"""Public transcript routes must not resolve providers for empty polling results."""

import logging

import pytest
from sentry_sdk.integrations.logging import LoggingIntegration

from simulate.models import CallExecution
from simulate.tests.test_transcript_and_branch_analysis_rendering import (
    _create_voice_call_execution,
    _seed_transcripts,
)
from tfc.tests.sentry_capture import sentry_capture

pytestmark = [pytest.mark.integration, pytest.mark.api]


@pytest.fixture
def call(organization, workspace):
    return _create_voice_call_execution(
        organization,
        workspace,
        provider_call_data={},
        call_metadata={},
        call_type=None,
    )


def transcript_path(call, endpoint):
    if endpoint == "call":
        return f"/simulate/call-executions/{call.pk}/transcripts/"
    return f"/simulate/test-executions/{call.test_execution_id}/transcripts/"


@pytest.mark.parametrize("endpoint", ["call", "execution"])
@pytest.mark.parametrize("metadata", [None, {}, {"unknown": {}}, "invalid"])
@pytest.mark.parametrize("state", ["pending", "completed"])
def test_empty_transcripts_are_quiet(
    endpoint, metadata, state, call, auth_client, caplog
):
    call.provider_call_data = metadata
    call.status = state
    call.save(update_fields=["provider_call_data", "status"])
    caplog.clear()
    with sentry_capture(
        LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)
    ) as transport:
        response = auth_client.get(transcript_path(call, endpoint))
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["total_transcripts"] == 0
    data = body if endpoint == "call" else body["calls"][0]
    assert data["status"] == state
    assert data["transcripts"] == []
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not [e for e in transport.events if e.get("level") in ("error", "fatal")]


@pytest.mark.parametrize("endpoint", ["call", "execution"])
@pytest.mark.parametrize(
    "metadata,event",
    [
        (None, None),  # Legacy rows without metadata retain the VAPI default.
        ({}, "speaker_role_resolver_unknown_provider"),
        ({"unknown": {}}, "speaker_role_resolver_unknown_provider"),
        ("invalid", "speaker_role_resolver_invalid_provider_call_data"),
    ],
)
def test_nonempty_provider_diagnostics_are_preserved(
    endpoint, metadata, event, call, auth_client, caplog
):
    call.provider_call_data = metadata
    call.status = CallExecution.CallStatus.COMPLETED
    call.save(update_fields=["provider_call_data", "status"])
    _seed_transcripts(call, [("assistant", "Synthetic transcript", 100)])
    caplog.clear()
    response = auth_client.get(transcript_path(call, endpoint))
    assert response.status_code == 200, response.content
    assert response.json()["total_transcripts"] == 1
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    if event is None:
        assert errors == []
    else:
        assert any(event in r.getMessage() for r in errors)
