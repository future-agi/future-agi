from unittest.mock import Mock, patch

import pytest
import requests

from simulate.services.harness_credential_probes import (
    LIVEKIT_AGENT_JOIN_TIMEOUT_SECONDS,
    probe_provider_target,
)
from simulate.services.harness_provider import _preflight_credential_probe

LIVEKIT_VALUES = {
    "LIVEKIT_URL": "wss://demo.livekit.cloud",
    "LIVEKIT_API_KEY": "APIdemo",
    "LIVEKIT_API_SECRET": "s" * 32,
}


@patch("simulate.services.harness_credential_probes.requests.get")
def test_retell_target_probe_reports_unknown_agent_id(get):
    get.return_value = Mock(status_code=404)

    result = probe_provider_target(
        "retell", "not-a-real-agent", {"RETELL_API_KEY": "valid-key"}
    )

    assert result is not None
    assert result.ok is False
    assert result.provider == "retell_target"
    assert result.message == (
        "Retell voice agent ID was not found or is not accessible with RETELL_API_KEY"
    )
    assert get.call_args.args[0].endswith("/get-agent/not-a-real-agent")


@patch("simulate.services.harness_credential_probes.requests.get")
def test_retell_chat_target_probe_uses_chat_agent_endpoint(get):
    get.return_value = Mock(status_code=200)

    result = probe_provider_target(
        "retell_chat", "chat-agent", {"RETELL_API_KEY": "valid-key"}
    )

    assert result is not None and result.ok is True
    assert get.call_args.args[0].endswith("/get-chat-agent/chat-agent")


@patch("simulate.services.harness_credential_probes.requests.get")
def test_vapi_target_probe_reports_unknown_assistant_id(get):
    get.return_value = Mock(status_code=404)

    result = probe_provider_target(
        "vapi", "missing/assistant", {"VAPI_API_KEY": "valid-key"}
    )

    assert result is not None and result.ok is False
    assert "Vapi assistant ID was not found" in result.message
    assert get.call_args.args[0].endswith("/assistant/missing%2Fassistant")


def test_target_probe_waits_for_the_dedicated_provider_key():
    assert probe_provider_target("retell", "agent", {}) is None


TARGET_IDENTITY = "agent-AJ_target"
UNRELATED_AGENT = {"identity": "agent-default-worker", "kind": "AGENT"}


def _dispatch_listing(identities, camel=False):
    dispatches, identity = (
        ("agentDispatches", "participantIdentity")
        if camel
        else ("agent_dispatches", "participant_identity")
    )
    return {
        dispatches: [
            {
                "id": "AD_1",
                "state": {
                    "jobs": [
                        {"id": f"AJ_{index}", "state": {identity: value}}
                        for index, value in enumerate(identities)
                    ]
                },
            }
        ]
    }


def _livekit_server(
    jobs=(), participants=(), status=200, error=None, dispatch_id="AD_1", camel=False
):
    calls = []
    job_polls = iter(jobs)
    participant_polls = iter(participants)

    def post(url, headers, json, timeout):
        method = url.rsplit("/", 1)[-1]
        calls.append((method, json))
        if error is not None:
            raise error
        response = Mock(status_code=status, content=b"{}")
        if status >= 400:
            response.raise_for_status.side_effect = requests.HTTPError(
                response=response
            )
            return response
        response.raise_for_status.return_value = None
        responses = {
            "CreateDispatch": lambda: {"id": dispatch_id} if dispatch_id else {},
            "ListDispatch": lambda: _dispatch_listing(next(job_polls, []), camel),
            "ListParticipants": lambda: {"participants": next(participant_polls, [])},
        }
        response.json.return_value = responses.get(method, dict)()
        return response

    return post, calls


@pytest.mark.parametrize("camel", [False, True])
@patch("simulate.services.harness_credential_probes.time.sleep")
def test_livekit_target_probe_passes_once_the_dispatched_agent_joins(sleep, camel):
    post, calls = _livekit_server(
        jobs=[[], [TARGET_IDENTITY]],
        participants=[[UNRELATED_AGENT, {"identity": TARGET_IDENTITY}]],
        camel=camel,
    )

    with patch("simulate.services.harness_credential_probes.requests.post", post):
        result = probe_provider_target("livekit", " returns-agent ", LIVEKIT_VALUES)

    assert result is not None and result.ok is True
    assert result.provider == "livekit_target"
    assert result.target_name == "returns-agent"
    assert result.message == "LiveKit agent 'returns-agent' joined a test room"
    methods = [method for method, _ in calls]
    assert methods == [
        "CreateRoom",
        "CreateDispatch",
        "ListDispatch",
        "ListDispatch",
        "ListParticipants",
        "DeleteRoom",
    ]
    room = calls[0][1]["name"]
    assert calls[0][1]["empty_timeout"] > LIVEKIT_AGENT_JOIN_TIMEOUT_SECONDS
    assert calls[1][1] == {"agent_name": "returns-agent", "room": room}
    assert calls[2][1] == {"dispatch_id": "AD_1", "room": room}
    assert calls[-1][1] == {"room": room}


def _probe_until_deadline(post, ticks):
    with (
        patch("simulate.services.harness_credential_probes.requests.post", post),
        patch("simulate.services.harness_credential_probes.time.sleep"),
        patch(
            "simulate.services.harness_credential_probes.time.monotonic",
            side_effect=ticks,
        ),
    ):
        return probe_provider_target("livekit", "typo-agent", LIVEKIT_VALUES)


def test_livekit_target_probe_ignores_an_unrelated_agent_while_unassigned():
    post, calls = _livekit_server(participants=[[UNRELATED_AGENT]])

    result = _probe_until_deadline(post, [0.0, 0.0, 9.0])

    assert result is not None and result.ok is False
    assert "ListParticipants" not in [method for method, _ in calls]


def test_livekit_target_probe_waits_for_the_assigned_identity_to_join():
    post, calls = _livekit_server(
        jobs=[[TARGET_IDENTITY]], participants=[[UNRELATED_AGENT]]
    )

    result = _probe_until_deadline(post, [0.0, 0.0, 9.0])

    assert result is not None and result.ok is False
    assert [method for method, _ in calls].count("ListParticipants") == 1


def test_livekit_target_probe_fails_without_a_dispatch_id():
    post, calls = _livekit_server(dispatch_id="")

    with patch("simulate.services.harness_credential_probes.requests.post", post):
        result = probe_provider_target("livekit", "returns-agent", LIVEKIT_VALUES)

    assert result is not None and result.ok is False
    assert [method for method, _ in calls] == [
        "CreateRoom",
        "CreateDispatch",
        "DeleteRoom",
    ]


def test_livekit_target_probe_fails_when_no_agent_joins_in_time():
    post, calls = _livekit_server()

    with (
        patch("simulate.services.harness_credential_probes.requests.post", post),
        patch("simulate.services.harness_credential_probes.time.sleep"),
        patch(
            "simulate.services.harness_credential_probes.time.monotonic",
            side_effect=[0.0, 0.0, 4.0, 8.0],
        ),
    ):
        result = probe_provider_target("livekit", "returns-agent", LIVEKIT_VALUES)

    assert result is not None and result.ok is False
    assert result.target_name == ""
    assert result.message == (
        "No agent named 'returns-agent' joined a test room within "
        f"{LIVEKIT_AGENT_JOIN_TIMEOUT_SECONDS}s; check the agent name and that "
        "the agent is running on this LiveKit project"
    )
    assert [method for method, _ in calls].count("ListDispatch") == 2
    assert calls[-1][0] == "DeleteRoom"


@pytest.mark.parametrize("status", [401, 403])
def test_livekit_target_probe_reports_rejected_credentials(status):
    post, calls = _livekit_server(status=status)

    with patch("simulate.services.harness_credential_probes.requests.post", post):
        result = probe_provider_target("livekit", "returns-agent", LIVEKIT_VALUES)

    assert result is not None and result.ok is False
    assert result.message == (
        "LiveKit rejected LIVEKIT_API_KEY + LIVEKIT_API_SECRET " f"(HTTP {status})"
    )
    assert [method for method, _ in calls] == ["CreateRoom"]


def test_livekit_target_probe_reports_other_http_errors():
    post, _calls = _livekit_server(status=500)

    with patch("simulate.services.harness_credential_probes.requests.post", post):
        result = probe_provider_target("livekit", "returns-agent", LIVEKIT_VALUES)

    assert result is not None and result.ok is False
    assert result.message == (
        "LiveKit returned HTTP 500 while checking agent 'returns-agent'"
    )


def test_livekit_target_probe_reports_an_unreachable_server():
    post, _calls = _livekit_server(error=requests.ConnectionError("refused"))

    with patch("simulate.services.harness_credential_probes.requests.post", post):
        result = probe_provider_target("livekit", "returns-agent", LIVEKIT_VALUES)

    assert result is not None and result.ok is False
    assert result.message == (
        "LiveKit is unreachable (ConnectionError); retry Preflight"
    )


@patch("simulate.services.harness_credential_probes.time.sleep")
def test_livekit_target_probe_still_reports_the_join_when_cleanup_fails(sleep):
    post, calls = _livekit_server(
        jobs=[[TARGET_IDENTITY]], participants=[[{"identity": TARGET_IDENTITY}]]
    )

    def post_with_failing_delete(url, headers, json, timeout):
        if url.endswith("/DeleteRoom"):
            calls.append(("DeleteRoom", json))
            raise requests.ConnectionError("gone")
        return post(url, headers, json, timeout)

    with patch(
        "simulate.services.harness_credential_probes.requests.post",
        post_with_failing_delete,
    ):
        result = probe_provider_target("livekit", "returns-agent", LIVEKIT_VALUES)

    assert result is not None and result.ok is True
    assert calls[-1][0] == "DeleteRoom"


@patch(
    "simulate.services.harness_credential_probes.load_extra",
    side_effect=ImportError("install the voice extra"),
)
@patch("simulate.services.harness_credential_probes.requests.post")
def test_livekit_target_probe_reports_a_missing_voice_extra(post, _load_extra):
    result = probe_provider_target("livekit", "returns-agent", LIVEKIT_VALUES)

    assert result is not None and result.ok is False
    assert result.message == "install the voice extra"
    post.assert_not_called()


@pytest.mark.parametrize(
    "agent_name, values",
    [
        ("", LIVEKIT_VALUES),
        ("returns-agent", {**LIVEKIT_VALUES, "LIVEKIT_API_SECRET": ""}),
        ("returns-agent", {"LIVEKIT_URL": "wss://demo.livekit.cloud"}),
    ],
)
@patch("simulate.services.harness_credential_probes.requests.post")
def test_livekit_target_probe_skips_without_a_name_or_full_credentials(
    post, agent_name, values
):
    assert probe_provider_target("livekit", agent_name, values) is None
    post.assert_not_called()


def _livekit_preflight_payload(mode="connect_only"):
    return {
        "source": {"kind": "provider"},
        "agent": {
            "connector": "livekit",
            "mode": mode,
            "config": {
                "agent_name": "returns-agent",
                "livekit_url": "wss://demo.livekit.cloud",
            },
        },
        "credential_values": {
            "LIVEKIT_API_KEY": "APIdemo",
            "LIVEKIT_API_SECRET": "s" * 32,
        },
    }


@patch("simulate.services.harness_credential_probes.probe_all", return_value=[])
@patch("simulate.services.harness_credential_probes.probe_livekit_agent")
def test_preflight_checks_the_hosted_livekit_agent_by_name(probe_agent, _probe_all):
    probe_agent.return_value = Mock(as_dict=lambda: {"provider": "livekit_target"})

    results = _preflight_credential_probe(_livekit_preflight_payload())

    assert results == [{"provider": "livekit_target"}]
    agent_name, values = probe_agent.call_args.args
    assert agent_name == "returns-agent"
    assert values == LIVEKIT_VALUES


@patch("simulate.services.harness_credential_probes.probe_all", return_value=[])
@patch("simulate.services.harness_credential_probes.probe_livekit_agent")
def test_preflight_does_not_dispatch_a_livekit_agent_built_from_source(
    probe_agent, _probe_all
):
    assert _preflight_credential_probe(_livekit_preflight_payload(mode=None)) == []
    probe_agent.assert_not_called()
