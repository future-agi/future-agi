"""CN22d: the EE agent-key mint is idempotent (AM-C27).

A mint whose answer EE lost (a dropped connection, EE's 15 s timeout, a 502
or 504 after the key was made) must never leave a key nobody holds. EE sends
every mint with an Idempotency-Key, a UUIDv7 it stored before the call, and
sends the same one when it retries:

- the first mint makes the key and, in the same transaction under the org
  lock, a record keyed by (org, EE service credential, SHA-256 of the
  Idempotency-Key) that holds only hashes and is kept 24 hours;
- a retry with the same body is 409 ``agent_key_mint_replayed`` naming the
  key it made (never the key itself); the same Idempotency-Key with another
  body (another workspace) is 422 ``idempotency_key_reused``; a missing or
  malformed one is 400 ``idempotency_key_invalid``;
- EE finds the key by its Idempotency-Key
  (``GET .../workspaces/<id>/agent-keys/?mint_idempotency_key=``), revokes
  it and mints with a new one; revoked keys never count toward the cap;
  replays and reconcile revokes are audited with the workspace and EE as
  the actor.

The gateway is faked at its admin client; every key and secret is generated
at run time.
"""

import hashlib
import json
import threading
import time
import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.urls import include, path
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.test import APIClient
from structlog.testing import capture_logs

from accounts.models.audit_log import AuditLog
from agentcc.models import AgentccAPIKey
from agentcc.models.ee_agent_key_mint import AgentccEEAgentKeyMintRecord
from agentcc.models.mcp_service_provisioning import AgentccEEAgentKey
from agentcc.services import ee_agent_key_mint as mint_idempotency
from agentcc.services import ee_agent_key_policy as policy
from agentcc.services import mcp_service_provisioning as provisioning
from agentcc.tests.test_mcp_service_provisioning import (
    CN22_OFF_733,
    KEYS,
    FakeGateway,
    _bearer,
    _client,
    _ee,
    _fixture,
    _member,
    _org,
    _revoke_url,
    _seed,
    _valid,
    _validator,
    _workspace,
    uuid7,
)

urlpatterns = [
    path("agentcc/", include("agentcc.urls_mcp_service_provisioning")),
    path("", include("tfc.urls")),
]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.django_db,
    pytest.mark.urls(__name__),
    CN22_OFF_733,
]

GATEWAY_TARGETS = (
    "agentcc.services.auth_bridge.get_gateway_client",
    "agentcc.services.config_push.get_gateway_client",
    "agentcc.services.mcp_service_provisioning.get_gateway_client",
)
JOIN_TIMEOUT = 30


@pytest.fixture
def gateway(monkeypatch):
    fake = FakeGateway()
    for target in GATEWAY_TARGETS:
        monkeypatch.setattr(target, lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _env(monkeypatch, settings):
    # This file exercises the explicitly enabled compatibility implementation.
    settings.EE_SERVICE_PROVISIONING_ENABLED = True
    monkeypatch.setenv(provisioning.PUBLIC_URL_ENV, "https://gateway.example.com")
    monkeypatch.delenv(provisioning.FALLBACK_URL_ENV, raising=False)
    monkeypatch.delenv(provisioning.MCP_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(policy.CAP_ENV, raising=False)
    monkeypatch.delenv(policy.DEFAULT_BUDGET_ENV, raising=False)


def _keys_url(workspace):
    return f"/agentcc/mcp/service/workspaces/{workspace.id}/agent-keys/"


def _post_mint(client, workspace, key):
    """EE's mint with ``key`` as its Idempotency-Key (None: no header)."""
    headers = {} if key is None else {"HTTP_IDEMPOTENCY_KEY": key}
    return client.post(KEYS, {"workspace": str(workspace.id)}, format="json", **headers)


def _find(client, workspace, key=None):
    params = {} if key is None else {mint_idempotency.QUERY_PARAM: key}
    return client.get(_keys_url(workspace), params)


def _sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _audits(org, action):
    return list(
        AuditLog.objects.filter(organization=org, action=action).order_by("created_at")
    )


def _http(response):
    return {
        "status": response.status_code,
        "headers": {"content-type": response["Content-Type"]},
    }


# --- The header ---------------------------------------------------------------


def test_mint_requires_a_uuidv7_idempotency_key(gateway):
    """No header, or anything but a UUIDv7, is 400 idempotency_key_invalid
    naming the header, and nothing is made. Case does not matter: the
    upper-case form is the same key."""
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    v7 = uuid7()
    wrong_variant = v7[:19] + "c" + v7[20:]
    for key in (
        None,
        "",
        "not-a-uuid",
        str(uuid.uuid4()),
        v7.replace("-", ""),
        "{" + v7 + "}",
        v7[:-1],
        wrong_variant,
        "00000000-0000-0000-0000-000000000000",
    ):
        response = _post_mint(ee, workspace, key)
        assert response.status_code == 400, key
        body = response.json()
        assert (body["code"], body["attr"]) == (
            mint_idempotency.CODE_KEY_INVALID,
            mint_idempotency.HEADER,
        ), key
        _valid("django_error_response", {"http": _http(response), "body": body})
    assert gateway.created == [] and not AgentccEEAgentKey.objects.exists()

    first = _post_mint(ee, workspace, v7.upper())
    assert first.status_code == 201
    again = _post_mint(ee, workspace, v7)
    assert again.status_code == 409
    assert again.json()["agent_key_id"] == first.json()["result"]["agent_key_id"]
    binding = AgentccEEAgentKey.objects.get()
    assert str(binding.mint_idempotency_key) == v7


# --- A lost answer, the retry, and EE's reconcile ----------------------------


def test_a_lost_answer_then_a_retry_is_replayed_with_the_same_id(gateway):
    org, owner, workspace = _org()
    _seed(org)
    ee, issued = _ee(org, owner)
    key = uuid7()
    first = _post_mint(ee, workspace, key)
    assert first.status_code == 201
    lost = first.json()["result"]  # the answer EE never read
    made = len(gateway.created)

    retry = _post_mint(ee, workspace, key)
    assert retry.status_code == 409
    body = retry.json()
    assert (body["type"], body["code"]) == ("conflict", mint_idempotency.CODE_REPLAYED)
    row = AgentccAPIKey.no_workspace_objects.get(gateway_key_id=lost["agent_key_id"])
    assert body["agent_key_id"] == lost["agent_key_id"]
    assert parse_datetime(body["created_at"]) == row.created_at
    assert body["expires_at"] == lost["expires_at"]
    assert body["revoked"] is False
    assert "key" not in body and lost["key"] not in retry.content.decode()
    assert len(gateway.created) == made  # no second key
    assert AgentccEEAgentKey.objects.count() == 1

    replays = _audits(org, mint_idempotency.AUDIT_MINT_REPLAYED)
    assert len(replays) == 1
    audit = replays[0]
    assert audit.actor is None and audit.metadata["actor"] == "omega-ee"
    assert audit.metadata["workspace"] == str(workspace.id)
    assert audit.metadata["agent_key_id"] == lost["agent_key_id"]
    assert audit.metadata["service_credential"] == issued.credential.api_key
    assert audit.metadata["mint_idempotency_key"] == key
    record = AgentccEEAgentKeyMintRecord.objects.get()
    assert record.replays == 1 and record.last_replayed_at is not None

    # EE's reconcile: revoke the key it lost, then mint with a new key.
    assert ee.post(_revoke_url(lost["agent_key_id"])).status_code == 200
    revoke = _audits(org, provisioning.AUDIT_AGENT_KEY_REVOKED)[-1]
    assert revoke.metadata["reconcile"] is True
    assert revoke.metadata["mint_idempotency_key"] == key
    assert revoke.metadata["workspace"] == str(workspace.id)
    assert revoke.actor is None and revoke.metadata["actor"] == "omega-ee"
    again = _post_mint(ee, workspace, key)
    assert (again.status_code, again.json()["revoked"]) == (409, True)
    fresh = _post_mint(ee, workspace, uuid7())
    assert fresh.status_code == 201
    fresh_id = fresh.json()["result"]["agent_key_id"]
    assert fresh_id != lost["agent_key_id"]

    # Revoking a key whose mint was never replayed is not a reconcile.
    assert ee.post(_revoke_url(fresh_id)).status_code == 200
    assert (
        _audits(org, provisioning.AUDIT_AGENT_KEY_REVOKED)[-1].metadata["reconcile"]
        is False
    )


def test_the_same_key_with_another_body_is_422(gateway):
    """Another workspace with the same Idempotency-Key is a reuse: 422,
    nothing made, and the answer names no key of the first workspace."""
    org, owner, ws_a = _org()
    ws_b = _workspace(org, owner, "Support")
    _seed(org)
    ee, _ = _ee(org, owner)
    key = uuid7()
    first = _post_mint(ee, ws_a, key).json()["result"]
    made = len(gateway.created)

    reused = _post_mint(ee, ws_b, key)
    assert reused.status_code == 422
    body = reused.json()
    assert (body["type"], body["code"]) == (
        "client_error",
        mint_idempotency.CODE_REUSED,
    )
    assert first["agent_key_id"] not in reused.content.decode()
    assert len(gateway.created) == made
    _valid("django_error_response", {"http": _http(reused), "body": body})
    # The record is untouched: the first workspace's retry is still a replay.
    assert _post_mint(ee, ws_a, key).status_code == 409
    assert AgentccEEAgentKeyMintRecord.objects.get().replays == 1


def test_after_24_hours_the_key_mints_again(gateway):
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    key, other = uuid7(), uuid7()
    first = _post_mint(ee, workspace, key).json()["result"]["agent_key_id"]
    _post_mint(ee, workspace, other)
    record = AgentccEEAgentKeyMintRecord.objects.get(key_sha256=_sha256(key))
    assert record.expires_at - record.created_at == timedelta(hours=24)

    # Just inside the window it is still a replay.
    AgentccEEAgentKeyMintRecord.objects.filter(pk=record.pk).update(
        expires_at=timezone.now() + timedelta(seconds=30)
    )
    assert _post_mint(ee, workspace, key).status_code == 409

    # Past it, the key is accepted again and mints a new key; the expired
    # record is gone, the other key's live record stays.
    AgentccEEAgentKeyMintRecord.objects.filter(organization=org).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    AgentccEEAgentKeyMintRecord.objects.filter(key_sha256=_sha256(other)).update(
        expires_at=timezone.now() + timedelta(hours=1)
    )
    again = _post_mint(ee, workspace, key)
    assert again.status_code == 201
    second = again.json()["result"]["agent_key_id"]
    assert second != first
    records = {
        r.key_sha256: r.agent_key.api_key.gateway_key_id
        for r in AgentccEEAgentKeyMintRecord.objects.select_related(
            "agent_key__api_key"
        )
    }
    assert records[_sha256(key)] == second
    assert set(records) == {_sha256(key), _sha256(other)}
    # Both keys stay findable by their Idempotency-Key.
    found = _find(ee, workspace, key).json()["result"]["keys"]
    assert {k["agent_key_id"] for k in found} == {first, second}


def test_records_are_isolated_across_workspaces_credentials_and_orgs(gateway):
    org, owner, ws_a = _org()
    ws_b = _workspace(org, owner, "Support")
    _seed(org)
    ee, _ = _ee(org, owner)
    key = uuid7()
    a = _post_mint(ee, ws_a, key).json()["result"]["agent_key_id"]

    # Workspace B's find and list never show workspace A's key.
    assert _find(ee, ws_b, key).json()["result"]["keys"] == []
    assert _find(ee, ws_b).json()["result"]["keys"] == []
    assert [
        k["agent_key_id"] for k in _find(ee, ws_a, key).json()["result"]["keys"]
    ] == [a]

    # Another org with the same Idempotency-Key mints its own key, and its
    # credential cannot read this org's workspace.
    org2, owner2, ws2 = _org("Other org")
    _seed(org2)
    ee2, _ = _ee(org2, owner2)
    other = _post_mint(ee2, ws2, key)
    assert other.status_code == 201
    assert other.json()["result"]["agent_key_id"] != a
    assert _find(ee2, ws_a, key).status_code == 404

    # A rotated credential keeps its own records (the key mints again), and
    # the workspace's find shows both keys, so EE reconciles across a
    # rotation. The rotated-out pair can no longer read.
    rotated = provisioning.rotate_credential(org, owner)
    ee_new = _client(rotated.credential.api_key, rotated.secret)
    b = _post_mint(ee_new, ws_a, key)
    assert b.status_code == 201
    found = _find(ee_new, ws_a, key).json()["result"]["keys"]
    assert {k["agent_key_id"] for k in found} == {a, b.json()["result"]["agent_key_id"]}
    assert _find(ee, ws_a, key).status_code == 401
    assert AgentccEEAgentKeyMintRecord.objects.filter(organization=org).count() == 2


def test_failed_mints_leave_no_record(gateway, monkeypatch):
    """A mint that made nothing (the gateway down, the cap reached) keeps
    no record: the same Idempotency-Key mints once the cause is gone."""
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    key = uuid7()
    gateway.down = True
    assert _post_mint(ee, workspace, key).status_code == 503
    gateway.down = False
    assert not AgentccEEAgentKeyMintRecord.objects.exists()
    assert _post_mint(ee, workspace, key).status_code == 201

    monkeypatch.setenv(policy.CAP_ENV, "1")
    refused_key = uuid7()
    refused = _post_mint(ee, workspace, refused_key)
    assert (refused.status_code, refused.json()["code"]) == (
        409,
        policy.CODE_CAP_REACHED,
    )
    assert AgentccEEAgentKeyMintRecord.objects.count() == 1


def test_the_cap_never_hides_a_replay_and_revoked_keys_never_count(
    gateway, monkeypatch
):
    """A retry after a lost answer whose key filled the cap is a replay,
    not agent_key_cap_reached; once EE revokes that key it leaves room, and
    the mint EE retries with its new Idempotency-Key goes through."""
    monkeypatch.setenv(policy.CAP_ENV, "1")
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    key = uuid7()
    lost = _post_mint(ee, workspace, key).json()["result"]["agent_key_id"]

    retry = _post_mint(ee, workspace, key)
    assert (retry.status_code, retry.json()["code"]) == (
        409,
        mint_idempotency.CODE_REPLAYED,
    )
    assert retry.json()["agent_key_id"] == lost
    new_key = uuid7()
    capped = _post_mint(ee, workspace, new_key)
    assert capped.json()["code"] == policy.CODE_CAP_REACHED

    assert ee.post(_revoke_url(lost)).status_code == 200
    assert policy.live_agent_keys(org).count() == 0
    fresh = _post_mint(ee, workspace, new_key)
    assert fresh.status_code == 201
    assert _post_mint(ee, workspace, key).json()["revoked"] is True


# --- Finding a key -------------------------------------------------------------


def test_find_and_list_carry_the_mint_idempotency_key(gateway):
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    key = uuid7()
    minted = _post_mint(ee, workspace, key).json()["result"]
    older = _post_mint(ee, workspace, uuid7()).json()["result"]["agent_key_id"]
    # A key minted before CN22d has no Idempotency-Key.
    AgentccEEAgentKey.objects.filter(api_key__gateway_key_id=older).update(
        mint_idempotency_key=None
    )

    found = _find(ee, workspace, key)
    assert found.status_code == 200
    assert minted["key"] not in found.content.decode()
    result = found.json()["result"]
    assert result["workspace"] == str(workspace.id)
    [entry] = result["keys"]
    assert entry["agent_key_id"] == minted["agent_key_id"]
    assert entry["mint_idempotency_key"] == key
    assert entry["status"] == "active" and "key" not in entry
    assert _find(ee, workspace, key.upper()).json()["result"]["keys"] == [entry]
    assert _find(ee, workspace, uuid7()).json()["result"]["keys"] == []

    # The whole list carries mint_idempotency_key per key, for EE and for an
    # Owner or Admin alike.
    for client in (ee, _bearer(owner)):
        listed = {
            k["agent_key_id"]: k["mint_idempotency_key"]
            for k in _find(client, workspace).json()["result"]["keys"]
        }
        assert listed == {minted["agent_key_id"]: key, older: None}

    # A malformed filter is 400 naming it; a member still may not read.
    bad = _find(ee, workspace, "not-a-uuid")
    assert (bad.status_code, bad.json()["code"], bad.json()["attr"]) == (
        400,
        "invalid",
        mint_idempotency.QUERY_PARAM,
    )
    assert _find(_bearer(_member(org, workspace)), workspace, key).status_code == 403

    # A caller with no credential gets the platform's own 401 challenge on this
    # route, as before CN22d, not the provisioning API's realm.
    anonymous = APIClient().get(_keys_url(workspace))
    assert anonymous.status_code == 401
    assert anonymous["WWW-Authenticate"] == "ApiKey"


def test_no_secret_in_any_replay_body_log_audit_row_or_record(gateway):
    org, owner, ws_a = _org()
    ws_b = _workspace(org, owner, "Support")
    _seed(org)
    with capture_logs() as logs:
        ee, issued = _ee(org, owner)
        key = uuid7()
        minted = _post_mint(ee, ws_a, key).json()["result"]
        answers = [
            _post_mint(ee, ws_a, key),
            _post_mint(ee, ws_b, key),
            _post_mint(ee, ws_a, None),
            _find(ee, ws_a, key),
            _find(ee, ws_a),
            ee.post(_revoke_url(minted["agent_key_id"])),
            _post_mint(ee, ws_a, key),
        ]
    raw_key = minted["key"]
    for answer in answers:
        assert raw_key not in answer.content.decode()
        assert issued.secret not in answer.content.decode()
    assert raw_key not in json.dumps(logs, default=str)
    audited = json.dumps(
        [[a.metadata, a.changes] for a in AuditLog.objects.filter(organization=org)],
        default=str,
    )
    assert raw_key not in audited and issued.secret not in audited
    stored = json.dumps(list(AgentccEEAgentKeyMintRecord.objects.values()), default=str)
    assert raw_key not in stored and key not in stored  # hashes only
    record = AgentccEEAgentKeyMintRecord.objects.get()
    assert record.key_sha256 == _sha256(key)
    assert record.request_sha256 == mint_idempotency.request_sha256(
        {"workspace": str(ws_a.id)}
    )


# --- Two mints at once ---------------------------------------------------------


def _a_backend_waits_on_this_connection():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_locks"
            " WHERE NOT granted AND pg_backend_pid() = ANY(pg_blocking_pids(pid))"
        )
        return cursor.fetchone()[0] > 0


class _BarrierGateway(FakeGateway):
    """The first key create (inside the first mint's transaction, under the
    org lock) holds until the other mint waits on that lock, so the two
    mints overlap for certain, with no sleeps."""

    def __init__(self):
        super().__init__()
        self.saw_the_other_mint_wait = None
        self._first = threading.Lock()

    def create_key(self, *args, **kwargs):
        if self._first.acquire(blocking=False):
            deadline = time.monotonic() + JOIN_TIMEOUT
            while not _a_backend_waits_on_this_connection():
                if time.monotonic() > deadline:
                    self.saw_the_other_mint_wait = False
                    break
                time.sleep(0.05)
            else:
                self.saw_the_other_mint_wait = True
        return super().create_key(*args, **kwargs)


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_mints_with_one_key_make_exactly_one_key(monkeypatch):
    fake = _BarrierGateway()
    for target in GATEWAY_TARGETS:
        monkeypatch.setattr(target, lambda: fake)
    org, owner, workspace = _org()
    _seed(org)
    issued = provisioning.issue_credential(org, owner)
    key = uuid7()
    results = {}

    def mint(name):
        try:
            response = _post_mint(
                _client(issued.credential.api_key, issued.secret), workspace, key
            )
            results[name] = (response.status_code, response.json())
        except Exception as e:  # recorded for the assertion message
            results[name] = ("error", f"{type(e).__name__}: {e}")
        finally:
            connection.close()

    threads = [threading.Thread(target=mint, args=(n,), name=n) for n in "ab"]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(JOIN_TIMEOUT * 2)
    assert set(results) == {"a", "b"}, results
    by_status = dict(results.values())
    assert sorted(by_status) == [201, 409], results
    assert fake.saw_the_other_mint_wait is True
    assert by_status[409]["code"] == mint_idempotency.CODE_REPLAYED
    assert by_status[409]["agent_key_id"] == by_status[201]["result"]["agent_key_id"]
    assert len(fake.created) == 1 and fake.revoked == []
    assert AgentccEEAgentKey.objects.count() == 1
    assert AgentccEEAgentKeyMintRecord.objects.count() == 1


# --- The contract ---------------------------------------------------------------


def test_matches_omega_ee_attach_fixtures(gateway):
    """The new answers validate against omega-ee-attach/v1 and equal its
    golden fixtures, and EE's find request is accepted as written."""
    org, owner, workspace = _org()
    _seed(org)
    ee, _ = _ee(org, owner)
    mint_request = _fixture("provision/agent-key-mint.request.json")
    _valid("provision_agent_key_mint_request", mint_request)
    key = mint_request["http"]["headers"]["idempotency-key"]
    minted = _post_mint(ee, workspace, key).json()["result"]

    replayed = _post_mint(ee, workspace, key)
    golden = _fixture("provision/error-agent-key-mint-replayed.response.json")
    document = {"http": _http(replayed), "body": replayed.json()}
    _valid("provision_agent_key_mint_replayed_response", document)
    assert document["http"] == golden["http"]
    substituted = {
        **golden["body"],
        "agent_key_id": minted["agent_key_id"],
        "created_at": replayed.json()["created_at"],
        "expires_at": minted["expires_at"],
    }
    assert replayed.json() == substituted

    other = _workspace(org, owner, "Support")
    for name, response in (
        (
            "provision/error-idempotency-key-reused.response.json",
            _post_mint(ee, other, key),
        ),
        (
            "provision/error-idempotency-key-invalid.response.json",
            _post_mint(ee, workspace, None),
        ),
    ):
        golden = _fixture(name)
        assert response.status_code == golden["http"]["status"], name
        assert response.json() == golden["body"], name
        _valid(
            "django_error_response", {"http": _http(response), "body": response.json()}
        )

    find = _fixture("provision/agent-keys-find.request.json")
    _valid("provision_agent_keys_find_request", find)
    assert find["http"]["method"] == "GET" and find["body"] is None
    query = find["http"]["url"].split("?", 1)[1]
    assert query == f"{mint_idempotency.QUERY_PARAM}={key}"
    found = ee.get(f"{_keys_url(workspace)}?{query}")
    document = {"http": _http(found), "body": found.json()}
    _valid("provision_agent_keys_find_response", document)
    golden = _fixture("provision/agent-keys-find.response.json")
    assert set(found.json()["result"]["keys"][0]) == set(
        golden["body"]["result"]["keys"][0]
    )

    # EE's forbidden shapes: a mint without the header, a replay with a key.
    for name, definition in (
        (
            "forbidden/agent-key-mint.request-without-idempotency-key.json",
            "provision_agent_key_mint_request",
        ),
        (
            "forbidden/agent-key-mint.replayed-with-key.json",
            "provision_agent_key_mint_replayed_response",
        ),
    ):
        assert not _validator(definition).is_valid(_fixture(name)), name


def test_answers_match_their_declared_serializers(gateway, settings):
    settings.DEBUG = True
    org, owner, workspace = _org()
    other = _workspace(org, owner, "Support")
    _seed(org)
    ee, _ = _ee(org, owner)
    key = uuid7()
    with capture_logs() as logs:
        assert _post_mint(ee, workspace, key).status_code == 201
        assert _post_mint(ee, workspace, key).status_code == 409
        assert _post_mint(ee, other, key).status_code == 422
        assert _post_mint(ee, workspace, None).status_code == 400
        assert _find(ee, workspace, key).status_code == 200
        assert _find(ee, workspace, "x").status_code == 400
        assert _find(_bearer(owner), workspace).status_code == 200
    drift = [
        event
        for event in logs
        if event.get("event")
        in (
            "API response does not match declared serializer.",
            "API response status is not declared.",
            "API request does not match declared serializer.",
        )
    ]
    assert not drift, drift
