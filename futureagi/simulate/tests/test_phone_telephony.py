from types import SimpleNamespace

import jwt
import pytest

from simulate.services.phone_telephony import (
    HostedPhoneRoomCleanupError,
    cleanup_hosted_phone_rooms,
    platform_phone_telephony,
    uses_platform_phone_telephony,
)


def test_alk_phone_uses_existing_agent_definition_telephony(settings, monkeypatch):
    settings.LIVEKIT_URL = "wss://livekit.example.test"
    settings.LIVEKIT_API_KEY = "test-key"
    settings.LIVEKIT_API_SECRET = "test-secret"
    settings.LIVEKIT_OUTBOUND_TRUNK_ID = "ST_existing"
    settings.PSTN_CALLER_NUMBER = "+14155550123"
    monkeypatch.setenv("SIP_OUTBOUND_TRUNK_ID", "ST_legacy")
    monkeypatch.setenv("SIP_OUTBOUND_FROM_NUMBER", "+14155550000")

    assert platform_phone_telephony() == {
        "LIVEKIT_URL": "wss://livekit.example.test",
        "LIVEKIT_API_KEY": "test-key",
        "LIVEKIT_API_SECRET": "test-secret",
        "SIP_OUTBOUND_TRUNK_ID": "ST_existing",
        "SIP_OUTBOUND_FROM_NUMBER": "+14155550123",
    }


def test_alk_phone_preserves_legacy_aliases(settings, monkeypatch):
    settings.LIVEKIT_OUTBOUND_TRUNK_ID = ""
    settings.PSTN_CALLER_NUMBER = ""
    monkeypatch.delenv("LIVEKIT_OUTBOUND_TRUNK_ID", raising=False)
    monkeypatch.delenv("PSTN_CALLER_NUMBER", raising=False)
    monkeypatch.setenv("SIP_OUTBOUND_TRUNK_ID", "ST_legacy")
    monkeypatch.setenv("SIP_OUTBOUND_FROM_NUMBER", "+14155550000")

    values = platform_phone_telephony()
    assert values["SIP_OUTBOUND_TRUNK_ID"] == "ST_legacy"
    assert values["SIP_OUTBOUND_FROM_NUMBER"] == "+14155550000"


@pytest.mark.parametrize(
    ("agent", "expected"),
    [
        (
            {
                "connector": "phone",
                "mode": "connect_only",
                "config": {"phone_number": "+14155550123"},
            },
            True,
        ),
        (
            {
                "connector": "vapi",
                "mode": "connect_only",
                "config": {"phone_number": "+14155550123"},
            },
            True,
        ),
        (
            {
                "connector": "retell",
                "mode": "connect_only",
                "config": {"agent_id": "agent-1"},
            },
            False,
        ),
        ({"connector": "auto", "config": {}}, False),
    ],
)
def test_platform_phone_job_detection(agent, expected):
    assert uses_platform_phone_telephony({"agent": agent}) is expected


def test_cancel_cleanup_deletes_only_execution_rooms_and_verifies(
    settings, monkeypatch
):
    settings.LIVEKIT_URL = "wss://livekit.example.test"
    settings.LIVEKIT_API_KEY = "test-key"
    settings.LIVEKIT_API_SECRET = "test-secret"
    execution_id = "execution-1"
    job_id = "12345678-1234-1234-1234-123456789abc"
    active_rooms = {
        f"hosted-{execution_id}-case-a",
        "harness-12345678-a1-case-b-s1-invocation-case-b",
        "hosted-another-execution-case-a",
        "harness-87654321-a1-case-c-s1-invocation-case-c",
    }

    requests_seen = []

    def post(url, *, headers, json, timeout):
        requests_seen.append((url, headers, json, timeout))
        if url.endswith("/DeleteRoom"):
            active_rooms.remove(json["room"])
            payload = {}
        else:
            payload = {"rooms": [{"name": name} for name in sorted(active_rooms)]}
        return SimpleNamespace(
            content=b"{}",
            json=lambda: payload,
            raise_for_status=lambda: None,
        )

    monkeypatch.setattr("simulate.services.phone_telephony.requests.post", post)
    job = SimpleNamespace(
        payload={
            "agent": {
                "connector": "phone",
                "mode": "connect_only",
                "config": {"phone_number": "+14155550123"},
            }
        },
        id=job_id,
        test_execution_id=execution_id,
    )

    result = cleanup_hosted_phone_rooms(job)

    assert result.matched_rooms == (
        "harness-12345678-a1-case-b-s1-invocation-case-b",
        f"hosted-{execution_id}-case-a",
    )
    assert result.deleted_rooms == result.matched_rooms
    assert active_rooms == {
        "hosted-another-execution-case-a",
        "harness-87654321-a1-case-c-s1-invocation-case-c",
    }
    assert [url.rsplit("/", 1)[-1] for url, *_ in requests_seen] == [
        "ListRooms",
        "DeleteRoom",
        "DeleteRoom",
        "ListRooms",
    ]
    assert all(
        request[0].startswith("https://livekit.example.test/")
        and request[1]["Authorization"].startswith("Bearer ")
        and request[3] == 10
        for request in requests_seen
    )
    grants = [
        jwt.decode(
            request[1]["Authorization"].removeprefix("Bearer "),
            "test-secret",
            algorithms=["HS256"],
        )["video"]
        for request in requests_seen
    ]
    assert grants == [
        {"roomList": True},
        {"roomCreate": True},
        {"roomCreate": True},
        {"roomList": True},
    ]


def test_cancel_cleanup_fails_until_room_absence_is_verified(settings, monkeypatch):
    settings.LIVEKIT_URL = "wss://livekit.example.test"
    settings.LIVEKIT_API_KEY = "test-key"
    settings.LIVEKIT_API_SECRET = "test-secret"
    room_name = "hosted-execution-1-case-a"

    def post(url, *, headers, json, timeout):
        del headers, json, timeout
        if url.endswith("/DeleteRoom"):
            raise RuntimeError("LiveKit temporarily unavailable")
        return SimpleNamespace(
            content=b"{}",
            json=lambda: {"rooms": [{"name": room_name}]},
            raise_for_status=lambda: None,
        )

    monkeypatch.setattr("simulate.services.phone_telephony.requests.post", post)
    job = SimpleNamespace(
        payload={"agent": {"connector": "phone", "config": {}}},
        id="12345678-1234-1234-1234-123456789abc",
        test_execution_id="execution-1",
    )

    with pytest.raises(HostedPhoneRoomCleanupError, match="rooms still active"):
        cleanup_hosted_phone_rooms(job)
