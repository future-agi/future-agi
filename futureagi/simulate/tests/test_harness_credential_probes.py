import json as jsonlib
from unittest.mock import Mock, patch

import jwt
import pytest
import requests
from google.protobuf import json_format

from simulate.services.harness_credential_probes import (
    LIVEKIT_AGENT_JOIN_TIMEOUT_SECONDS,
    LIVEKIT_MAX_RESPONSE_BYTES,
    _cloud_phone_numbers,
    _phone_routing_verdict,
    probe,
    probe_livekit_phone_routing,
    probe_provider_target,
)
from simulate.services.harness_provider import (
    _preflight_checks,
    _preflight_credential_probe,
)


@pytest.fixture(autouse=True)
def public_hosts():
    with patch(
        "simulate.services.harness_credential_probes.assert_url_host_public"
    ) as guard:
        yield guard


def _answer(response, payload):
    response.raw.read.return_value = (
        jsonlib.dumps(payload).encode() if payload is not None else b""
    )
    return response


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

    def post(url, headers, json, timeout, **kwargs):
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
        return _answer(response, responses.get(method, dict)())

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
        f"LiveKit rejected LIVEKIT_API_KEY + LIVEKIT_API_SECRET (HTTP {status})"
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

    def post_with_failing_delete(url, headers, json, timeout, **kwargs):
        if url.endswith("/DeleteRoom"):
            calls.append(("DeleteRoom", json))
            raise requests.ConnectionError("gone")
        return post(url, headers, json, timeout, **kwargs)

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


NUMBER = "+14844815043"
CALLER = "+13125557682"
ROUTING_VALUES = {**LIVEKIT_VALUES, "LIVEKIT_AGENT_NAME": "Morgan-467"}
CLOUD_NUMBER = {"e164_format": NUMBER, "sip_dispatch_rule_ids": []}


def _rule(
    rule_id="SDR_1", agents=("Morgan-467",), trunks=(), numbers=(), callers=(), pin=""
):
    return {
        "sip_dispatch_rule_id": rule_id,
        "trunk_ids": list(trunks),
        "numbers": list(numbers),
        "inbound_numbers": list(callers),
        "rule": {"dispatch_rule_individual": {"room_prefix": "call-", "pin": pin}},
        "room_config": {"agents": [{"agent_name": name} for name in agents]},
    }


def _trunk(trunk_id="ST_1", numbers=(NUMBER,), allowed=(), addresses=()):
    return {
        "sip_trunk_id": trunk_id,
        "name": trunk_id.lower(),
        "numbers": list(numbers),
        "allowed_numbers": list(allowed),
        "allowed_addresses": list(addresses),
    }


def _parsed(cloud, trunks, rules):
    from livekit import api as livekit_api

    return (
        _cloud_phone_numbers(list(cloud)),
        json_format.ParseDict(
            {"items": list(trunks)},
            livekit_api.ListSIPInboundTrunkResponse(),
            ignore_unknown_fields=True,
        ).items,
        json_format.ParseDict(
            {"items": list(rules)},
            livekit_api.ListSIPDispatchRuleResponse(),
            ignore_unknown_fields=True,
        ).items,
    )


@pytest.mark.parametrize(
    "cloud, trunks, rules, caller, ok, message",
    [
        pytest.param(
            [CLOUD_NUMBER],
            [],
            [_rule()],
            CALLER,
            True,
            "to 'Morgan-467' only",
            id="cloud-number-catch-all-rule",
        ),
        pytest.param(
            [],
            [_trunk()],
            [_rule(trunks=("ST_1",))],
            CALLER,
            True,
            "your carrier must also forward this number",
            id="carrier-number-on-one-trunk",
        ),
        pytest.param(
            [],
            [_trunk(numbers=(), addresses=("1.2.3.4/32",))],
            [_rule(trunks=("ST_1",))],
            CALLER,
            True,
            "to 'Morgan-467' only",
            id="default-trunk-accepts-any-number",
        ),
        pytest.param(
            [CLOUD_NUMBER],
            [_trunk()],
            [_rule()],
            CALLER,
            True,
            "to 'Morgan-467' only",
            id="number-on-cloud-and-trunk",
        ),
        pytest.param(
            [],
            [_trunk()],
            [_rule(trunks=("ST_2",), numbers=(NUMBER,))],
            CALLER,
            True,
            "to 'Morgan-467' only",
            id="rule-lists-the-number-under-another-trunk",
        ),
        pytest.param(
            [],
            [_trunk(allowed=("+15550001111",))],
            [_rule(trunks=("ST_1",))],
            "",
            True,
            "to 'Morgan-467' only",
            id="no-caller-number-configured-skips-the-allow-list",
        ),
        pytest.param(
            [{**CLOUD_NUMBER, "sip_dispatch_rule_ids": ["SDR_1"]}],
            [],
            [_rule(), _rule("SDR_2", agents=("Riley-1",))],
            CALLER,
            True,
            "to 'Morgan-467' only",
            id="assigned-rule-wins-over-catch-all",
        ),
        pytest.param(
            [],
            [_trunk(numbers=("+15550000000",))],
            [_rule()],
            CALLER,
            False,
            "lists or accepts it",
            id="number-not-on-project",
        ),
        pytest.param(
            [],
            [],
            [_rule()],
            CALLER,
            False,
            "lists or accepts it",
            id="empty-project",
        ),
        pytest.param(
            [],
            [_trunk("ST_1"), _trunk("ST_2")],
            [_rule(trunks=("ST_1",))],
            CALLER,
            False,
            "2 inbound trunks list +14844815043 (st_1, st_2)",
            id="number-on-two-trunks",
        ),
        pytest.param(
            [],
            [_trunk("ST_1", numbers=()), _trunk("ST_2", numbers=())],
            [_rule()],
            CALLER,
            False,
            "2 inbound trunks accept any number",
            id="two-default-trunks",
        ),
        pytest.param(
            [],
            [_trunk(allowed=("+15550001111",))],
            [_rule(trunks=("ST_1",))],
            CALLER,
            False,
            "add +13125557682, the number we call from",
            id="trunk-refuses-our-caller",
        ),
        pytest.param(
            [CLOUD_NUMBER],
            [],
            [],
            CALLER,
            False,
            "ring into an empty room",
            id="no-dispatch-rule",
        ),
        pytest.param(
            [CLOUD_NUMBER],
            [],
            [_rule(callers=("+15550001111",))],
            CALLER,
            False,
            "ring into an empty room",
            id="rule-only-for-other-callers",
        ),
        pytest.param(
            [CLOUD_NUMBER],
            [],
            [_rule(pin="1234")],
            CALLER,
            False,
            "asks callers for a PIN",
            id="rule-needs-a-pin",
        ),
        pytest.param(
            [CLOUD_NUMBER],
            [],
            [_rule(agents=())],
            CALLER,
            False,
            "names no agent",
            id="rule-names-no-agent",
        ),
        pytest.param(
            [CLOUD_NUMBER],
            [],
            [_rule(agents=("Riley-1",))],
            CALLER,
            False,
            "go to Riley-1, not 'Morgan-467'",
            id="rule-sends-to-another-agent",
        ),
        pytest.param(
            [CLOUD_NUMBER],
            [],
            [_rule(agents=("Morgan-467", "Riley-1"))],
            CALLER,
            False,
            "also go to Riley-1",
            id="rule-sends-to-two-agents",
        ),
        pytest.param(
            [],
            [_trunk()],
            [
                _rule(trunks=("ST_1",)),
                _rule("SDR_2", agents=("Riley-1",), numbers=(NUMBER,)),
            ],
            CALLER,
            False,
            "also go to Riley-1",
            id="two-rules-send-to-different-agents",
        ),
    ],
)
def test_phone_routing_verdict(cloud, trunks, rules, caller, ok, message):
    passed, detail = _phone_routing_verdict(
        NUMBER, "Morgan-467", caller, *_parsed(cloud, trunks, rules)
    )

    assert passed is ok
    assert message in detail


def _livekit_sip_server(
    trunks=(), rules=(), cloud=(), cloud_status=200, status=200, error=None
):
    calls = []

    def post(url, headers, json, timeout, **kwargs):
        method = url.rsplit("/", 1)[-1]
        calls.append(method)
        if error is not None:
            raise error
        code = cloud_status if method == "ListPhoneNumbers" else status
        response = Mock(status_code=code, content=b"{}")
        if code >= 400:
            response.raise_for_status.side_effect = requests.HTTPError(
                response=response
            )
            return response
        response.raise_for_status.return_value = None
        listings = {
            "ListSIPInboundTrunk": trunks,
            "ListSIPDispatchRule": rules,
            "ListPhoneNumbers": cloud,
        }
        return _answer(response, {"items": list(listings[method])})

    return post, calls


def test_routing_probe_reads_the_project_and_names_the_agent_it_reaches():
    post, calls = _livekit_sip_server(cloud=[CLOUD_NUMBER], rules=[_rule()])

    with patch("simulate.services.harness_credential_probes.requests.post", post):
        result = probe_livekit_phone_routing(NUMBER, ROUTING_VALUES, CALLER)

    assert result is not None and result.ok is True
    assert result.provider == "livekit_routing"
    assert result.target_name == "Morgan-467"
    assert calls == ["ListSIPInboundTrunk", "ListSIPDispatchRule", "ListPhoneNumbers"]


@pytest.mark.parametrize("cloud_status", [401, 403, 404, 501])
def test_routing_probe_uses_trunks_on_a_server_without_phone_numbers(cloud_status):
    post, _calls = _livekit_sip_server(
        trunks=[_trunk()], rules=[_rule(trunks=("ST_1",))], cloud_status=cloud_status
    )

    with patch("simulate.services.harness_credential_probes.requests.post", post):
        result = probe_livekit_phone_routing(NUMBER, ROUTING_VALUES, CALLER)

    assert result is not None and result.ok is True


@pytest.mark.parametrize("status", [401, 403])
def test_routing_probe_reports_rejected_credentials(status):
    post, calls = _livekit_sip_server(status=status)

    with patch("simulate.services.harness_credential_probes.requests.post", post):
        result = probe_livekit_phone_routing(NUMBER, ROUTING_VALUES, CALLER)

    assert result is not None and result.ok is False
    assert result.message == (
        f"LiveKit rejected LIVEKIT_API_KEY + LIVEKIT_API_SECRET (HTTP {status})"
    )
    assert calls == ["ListSIPInboundTrunk"]


def test_routing_probe_reports_other_http_errors():
    post, _calls = _livekit_sip_server(status=500)

    with patch("simulate.services.harness_credential_probes.requests.post", post):
        result = probe_livekit_phone_routing(NUMBER, ROUTING_VALUES, CALLER)

    assert result is not None and result.ok is False
    assert result.message == f"LiveKit returned HTTP 500 while checking {NUMBER}"


def test_routing_probe_reports_an_unreadable_response():
    post, _calls = _livekit_sip_server()

    def post_with_bad_json(url, headers, json, timeout, **kwargs):
        response = post(url, headers, json, timeout, **kwargs)
        response.raw.read.return_value = b"not json"
        return response

    with patch(
        "simulate.services.harness_credential_probes.requests.post", post_with_bad_json
    ):
        result = probe_livekit_phone_routing(NUMBER, ROUTING_VALUES, CALLER)

    assert result is not None and result.ok is False
    assert result.message == (
        "LiveKit returned an unreadable response (JSONDecodeError); retry Preflight"
    )


@pytest.mark.parametrize(
    "body",
    [
        [{"e164_format": NUMBER}],
        b"{" + b" " * LIVEKIT_MAX_RESPONSE_BYTES + b"}",
    ],
    ids=["a-json-list", "over-the-size-cap"],
)
def test_routing_probe_treats_a_list_or_oversized_reply_as_unreadable(body):
    post, _calls = _livekit_sip_server()

    def post_with_body(url, headers, json, timeout, **kwargs):
        response = post(url, headers, json, timeout, **kwargs)
        if url.endswith("/ListPhoneNumbers"):
            response.raw.read.return_value = (
                body if isinstance(body, bytes) else jsonlib.dumps(body).encode()
            )
        return response

    with patch(
        "simulate.services.harness_credential_probes.requests.post", post_with_body
    ):
        result = probe_livekit_phone_routing(NUMBER, ROUTING_VALUES, CALLER)

    assert result is not None and result.ok is False
    assert result.message == (
        "LiveKit returned an unreadable response (LiveKitUnreadable); retry Preflight"
    )


def test_livekit_requests_never_follow_redirects_and_read_a_capped_body():
    post, _calls = _livekit_sip_server(cloud=[CLOUD_NUMBER], rules=[_rule()])
    seen = []

    def recording(url, headers, json, timeout, **kwargs):
        seen.append((kwargs, headers))
        return post(url, headers, json, timeout, **kwargs)

    with patch("simulate.services.harness_credential_probes.requests.post", recording):
        probe_livekit_phone_routing(NUMBER, ROUTING_VALUES, CALLER)

    assert seen and all(
        kwargs == {"allow_redirects": False, "stream": True} for kwargs, _h in seen
    )
    token = seen[0][1]["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["exp"] - claims["nbf"] <= 60


@pytest.mark.parametrize("status", [301, 302, 307])
def test_a_livekit_redirect_is_a_failure_not_followed(status):
    post, calls = _livekit_sip_server(status=status)

    with patch("simulate.services.harness_credential_probes.requests.post", post):
        result = probe_livekit_phone_routing(NUMBER, ROUTING_VALUES, CALLER)

    assert result is not None and result.ok is False
    assert result.message == f"LiveKit returned HTTP {status} while checking {NUMBER}"
    assert calls == ["ListSIPInboundTrunk"]


@pytest.mark.parametrize(
    "url",
    [
        "http://demo.livekit.cloud",
        "ws://demo.livekit.cloud",
        "https://user:pw@demo.livekit.cloud",
        "wss://[::1",
        "demo.livekit.cloud",
    ],
)
@patch("simulate.services.harness_credential_probes.requests.post")
@patch("simulate.services.harness_credential_probes.requests.request")
def test_every_livekit_probe_refuses_a_url_that_is_not_https_or_wss(request, post, url):
    values = {**ROUTING_VALUES, "LIVEKIT_URL": url}

    results = [
        probe("livekit", values),
        probe_provider_target("livekit", "returns-agent", values),
        probe_livekit_phone_routing(NUMBER, values, CALLER),
    ]

    assert [one.ok for one in results] == [False, False, False]
    assert all("URL refused" in one.message for one in results)
    request.assert_not_called()
    post.assert_not_called()


@patch("simulate.services.harness_credential_probes.requests.post")
@patch("simulate.services.harness_credential_probes.requests.request")
def test_every_livekit_probe_refuses_an_internal_host(request, post, public_hosts):
    from tfc.utils.ssrf_guard import assert_url_host_public

    public_hosts.side_effect = assert_url_host_public
    values = {**ROUTING_VALUES, "LIVEKIT_URL": "wss://127.0.0.1:7880"}

    results = [
        probe("livekit", values),
        probe_provider_target("livekit", "returns-agent", values),
        probe_livekit_phone_routing(NUMBER, values, CALLER),
    ]

    assert [one.ok for one in results] == [False, False, False]
    assert all("private/internal address" in one.message for one in results)
    request.assert_not_called()
    post.assert_not_called()


def test_routing_results_get_their_own_preflight_check():
    probe = [
        {"provider": "livekit", "ok": True, "message": "key ok", "aliases": []},
        {
            "provider": "livekit_routing",
            "ok": False,
            "message": "no dispatch rule routes calls to +14844815043",
            "aliases": ["LIVEKIT_URL"],
        },
    ]

    checks = {
        check["id"]: check
        for check in _preflight_checks("provider", None, 0, [], [], probe, "phone")
    }

    assert checks["provider_target"]["status"] == "skipped"
    assert checks["phone_routing"]["status"] == "failed"
    assert "no dispatch rule" in checks["phone_routing"]["detail"]
    assert checks["phone_routing"]["fix"] == (
        "Fix the number, trunk or dispatch rule named above on your LiveKit project"
    )
    assert "phone_routing" not in {
        check["id"]
        for check in _preflight_checks("provider", None, 0, [], [], probe[:1], "phone")
    }


def test_routing_probe_reports_an_unreachable_server():
    post, _calls = _livekit_sip_server(error=requests.ConnectionError("refused"))

    with patch("simulate.services.harness_credential_probes.requests.post", post):
        result = probe_livekit_phone_routing(NUMBER, ROUTING_VALUES, CALLER)

    assert result is not None and result.ok is False
    assert result.message == "LiveKit is unreachable (ConnectionError); retry Preflight"


@patch(
    "simulate.services.harness_credential_probes.load_extra",
    side_effect=ImportError("install the voice extra"),
)
@patch("simulate.services.harness_credential_probes.requests.post")
def test_routing_probe_reports_a_missing_voice_extra(post, _load_extra):
    result = probe_livekit_phone_routing(NUMBER, ROUTING_VALUES, CALLER)

    assert result is not None and result.ok is False
    assert result.message == "install the voice extra"
    post.assert_not_called()


@pytest.mark.parametrize(
    "number, values",
    [
        ("", ROUTING_VALUES),
        (NUMBER, {**ROUTING_VALUES, "LIVEKIT_AGENT_NAME": " "}),
        (NUMBER, {**ROUTING_VALUES, "LIVEKIT_API_SECRET": ""}),
    ],
)
@patch("simulate.services.harness_credential_probes.requests.post")
def test_routing_probe_skips_without_a_number_agent_or_full_credentials(
    post, number, values
):
    assert probe_livekit_phone_routing(number, values, CALLER) is None
    post.assert_not_called()


@patch(
    "simulate.services.phone_telephony.platform_phone_telephony",
    return_value={"SIP_OUTBOUND_FROM_NUMBER": CALLER},
)
@patch("simulate.services.harness_credential_probes.probe_all", return_value=[])
@patch("simulate.services.harness_credential_probes.probe_livekit_phone_routing")
def test_preflight_checks_a_phone_target_routing_from_our_caller_number(
    probe_routing, _probe_all, _telephony
):
    probe_routing.return_value = Mock(as_dict=lambda: {"provider": "livekit_target"})
    payload = {
        "source": {"kind": "provider"},
        "agent": {
            "connector": "phone",
            "mode": "connect_only",
            "config": {"phone_number": NUMBER, "target_system_prompt": "Help."},
        },
        "credential_values": ROUTING_VALUES,
    }

    assert _preflight_credential_probe(payload) == [{"provider": "livekit_target"}]
    number, values, caller = probe_routing.call_args.args
    assert (number, values, caller) == (NUMBER, ROUTING_VALUES, CALLER)
