"""WH-2 endpoint management over FA's real authentication (DESIGN.md sections 4, 5.3, 6.1, 6.3, 9.2 and 10).

Rows: WT-05 (refused at create, before any lookup), WT-05b and WT-05c (hosted settings), WT-27 (ciphertext under
``WEBHOOKS_ENCRYPTION_KEYS``; after delete the secret rows are gone), the K2 key gate, the section 9.2 audit entries
(never a full URL, a secret or a header value), the section 7.8 limits, and the error codes of section 10.
"""

from __future__ import annotations

import json
import os
import socket

import pytest
from cryptography.fernet import Fernet

from accounts.models.audit_log import AuditLog
from webhooks.models import Endpoint, Secret, Subscription
from webhooks.tests.api_support import client_for
from webhooks.tests.factories import use_test_keys

pytestmark = pytest.mark.django_db
URL = "/webhooks/endpoints/"
# Canaries are assembled at run time, so no credential-shaped literal sits in the source.
SK = "sk" + "-live-"


@pytest.fixture
def keys(monkeypatch):
    use_test_keys(monkeypatch)


@pytest.fixture
def owner(user, organization, workspace):
    return client_for(user, organization, workspace)


def body(**kw):
    out = {
        "name": "release hook",
        "url": "https://hooks.acme.ai/omega?token=canary2canary2",
        "subscriptions": [{"event_type": "prompt.*"}],
    }
    out.update(kw)
    return out


def code_of(r):
    return r.json().get("code")


def test_create_returns_the_secret_once_and_stores_only_ciphertext(
    owner, keys, organization
):
    r = owner.post(
        URL, body(headers={"Authorization": "Bearer " + SK + "canary1"}), format="json"
    )
    assert r.status_code == 201, r.content
    res = r.json()["result"]
    secret = res["secret"]
    assert secret.startswith("whsec_") and len(secret) == 50
    assert res["warnings"][0]["code"] == "WEBHOOK_URL_QUERY_SECRET"
    assert res["warnings"][0]["params"] == ["token"]
    ep = Endpoint.objects.get(id=res["endpoint"]["id"])
    fernet = Fernet(os.environ["WEBHOOKS_ENCRYPTION_KEYS"].encode())
    # WT-27: Fernet ciphertext under the webhooks key, never the plaintext.
    assert b"hooks.acme.ai" not in bytes(ep.url_encrypted)
    assert (
        fernet.decrypt(bytes(ep.url_encrypted))
        .decode()
        .endswith("token=canary2canary2")
    )
    s = Secret.objects.get(endpoint=ep)
    assert secret.encode() not in bytes(s.secret_encrypted)
    assert fernet.decrypt(bytes(s.secret_encrypted)).decode() == secret
    assert b"canary1" not in bytes(ep.headers_encrypted)
    assert ep.url_display == "https://hooks.acme.ai/omega?token=…"
    assert ep.header_names == ["Authorization"] and ep.owner_id == ep.created_by_id
    assert list(
        Subscription.objects.filter(endpoint=ep).values_list("event_type", flat=True)
    ) == ["prompt.*"]
    # Never shown again: not on read, not in the list.
    for got in (owner.get(f"{URL}{ep.id}/"), owner.get(URL)):
        dumped = json.dumps(got.json())
        assert got.status_code == 200
        for needle in (secret, "canary1", "canary2"):
            assert needle not in dumped
    detail = owner.get(f"{URL}{ep.id}/").json()["result"]
    assert detail["secrets"] == [
        {
            "id": s.id,
            "tail": secret[-4:],
            "state": "active",
            "created_at": detail["secrets"][0]["created_at"],
            "expires_at": None,
        }
    ]
    # Audit (section 9.2): created + secret.created, never the URL's token, the header value or the secret.
    entries = AuditLog.objects.filter(organization=organization, scope="webhooks")
    assert {"webhook.endpoint.created", "webhook.secret.created"} <= set(
        entries.values_list("action", flat=True)
    )
    for e in entries:
        dumped = json.dumps([e.changes, e.metadata], default=str)
        for needle in (secret, "canary1", "canary2"):
            assert needle not in dumped
    created = entries.get(action="webhook.endpoint.created")
    assert created.changes["after"]["url_display"] == ep.url_display
    assert created.changes["after"]["url_sha256"] == ep.url_sha256


WT05 = [
    (
        "https://169.254.169.254/",
        "WEBHOOK_URL_UNSAFE_ADDRESS",
        ("169.254.169.254", 443),
    ),
    (
        "https://[::ffff:169.254.169.254]/",
        "WEBHOOK_URL_UNSAFE_ADDRESS",
        ("::ffff:169.254.169.254", 443),
    ),
    ("https://localhost/", "WEBHOOK_URL_UNSAFE_ADDRESS", ("localhost", 443)),
    (
        "https://metadata.google.internal/",
        "WEBHOOK_URL_UNSAFE_ADDRESS",
        ("metadata.google.internal", 443),
    ),
    ("https://10.0.0.5/", "WEBHOOK_URL_UNSAFE_ADDRESS", ("10.0.0.5", 443)),
    ("https://100.64.0.1/", "WEBHOOK_URL_UNSAFE_ADDRESS", ("100.64.0.1", 443)),
    ("https://[fd00::1]/", "WEBHOOK_URL_UNSAFE_ADDRESS", ("fd00::1", 443)),
    ("https://[fe80::1]/", "WEBHOOK_URL_UNSAFE_ADDRESS", ("fe80::1", 443)),
    ("https://user:pw@hooks.test/", "WEBHOOK_URL_INVALID", ("hooks.test", 443)),
    ("https://hooks.acme.ai:6379/", "WEBHOOK_URL_INVALID", ("hooks.acme.ai", 6379)),
    ("https://hooks.acme.ai:6443/", "WEBHOOK_URL_INVALID", ("hooks.acme.ai", 6443)),
    ("https://2852039166/", "WEBHOOK_URL_INVALID", ("2852039166", 443)),
    ("https://127.1/", "WEBHOOK_URL_INVALID", ("127.1", 443)),
    ("https://0x7f.1/", "WEBHOOK_URL_INVALID", ("0x7f.1", 443)),
    (
        "https://0251.0376.0251.0376/",
        "WEBHOOK_URL_INVALID",
        ("0251.0376.0251.0376", 443),
    ),
]


@pytest.fixture
def network_log(monkeypatch):
    """Every name lookup and connect the process makes while the API runs."""
    seen: list[tuple] = []
    real_gai, real_connect = socket.getaddrinfo, socket.socket.connect

    def gai(host, port, *a, **kw):
        seen.append(("lookup", host, port))
        return real_gai(host, port, *a, **kw)

    def connect(self, address):
        seen.append(
            (
                "connect",
                *(address[:2] if isinstance(address, tuple) else (address, None)),
            )
        )
        return real_connect(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", gai)
    monkeypatch.setattr(socket.socket, "connect", connect)
    return seen


@pytest.mark.parametrize("url,code,target", WT05, ids=[u for u, _c, _t in WT05])
def test_wt05_refused_at_create_before_any_lookup(
    owner, keys, url, code, target, network_log
):
    r = owner.post(URL, body(url=url), format="json")
    assert r.status_code == 400, r.content
    assert code_of(r) == code
    assert r.json()["attr"] == "url"
    host, port = target
    # 0 lookups or connections toward the target: the URL is judged on its text (section 6.1).
    toward = [
        x
        for x in network_log
        if x[1] in (host, host.strip("[]")) and str(x[2]) in (str(port), "None")
    ]
    assert toward == []
    assert not Endpoint.objects.exists()


@pytest.fixture
def hosted(monkeypatch):
    from webhooks import config

    monkeypatch.setattr(config, "is_hosted", lambda: True)
    monkeypatch.setenv(
        "WEBHOOKS_DENY_HOSTS", "ee.platform.example.net,gateway.platform.example.net"
    )
    monkeypatch.setenv("FI_WEBHOOKS_ALLOW_HTTP", "true")  # ignored on hosted


@pytest.mark.parametrize(
    "url,code,rule",
    [
        ("http://hooks.acme.ai/omega", "WEBHOOK_URL_NOT_HTTPS", "url.scheme"),
        (
            "https://api.futureagi.com/hook",
            "WEBHOOK_URL_UNSAFE_ADDRESS",
            "url.host_own",
        ),
        (
            "https://ee.platform.example.net/x",
            "WEBHOOK_URL_UNSAFE_ADDRESS",
            "url.host_own",
        ),
        (
            "https://gateway.platform.example.net/x",
            "WEBHOOK_URL_UNSAFE_ADDRESS",
            "url.host_own",
        ),
        (
            "https://redis.default.svc/",
            "WEBHOOK_URL_UNSAFE_ADDRESS",
            "url.host_internal_name",
        ),
        ("https://kubernetes/", "WEBHOOK_URL_UNSAFE_ADDRESS", "url.host_no_dot"),
        ("https://intranet/", "WEBHOOK_URL_UNSAFE_ADDRESS", "url.host_no_dot"),
    ],
)
def test_wt05b_wt05c_hosted_rules(owner, keys, hosted, url, code, rule, network_log):
    r = owner.post(URL, body(url=url), format="json")
    assert r.status_code == 400, r.content
    assert (code_of(r), r.json()["rule"]) == (code, rule)
    host = url.split("/")[2]
    assert [
        x for x in network_log if x[1] == host and str(x[2]) in ("443", "80", "None")
    ] == []
    # No tenant-visible field carries an address it was not given.
    assert "10." not in json.dumps(r.json()) and "169.254" not in json.dumps(r.json())


@pytest.mark.parametrize(
    "headers,code",
    [
        ({"webhook-id": "x"}, "WEBHOOK_HEADER_RESERVED"),
        ({"X-FutureAGI-Event": "x"}, "WEBHOOK_HEADER_RESERVED"),
        ({"Host": "evil.example"}, "WEBHOOK_HEADER_RESERVED"),
        ({"Proxy-Authorization": "x"}, "WEBHOOK_HEADER_RESERVED"),
        ({"X-Forwarded-For": "1.2.3.4"}, "WEBHOOK_HEADER_RESERVED"),
        ({"Bad Name": "x"}, "WEBHOOK_HEADER_INVALID"),
        ({"X-Token": "a\r\nInjected: 1"}, "WEBHOOK_HEADER_INVALID"),
    ],
)
def test_custom_header_rules(owner, keys, headers, code):
    r = owner.post(URL, body(headers=headers), format="json")
    assert (r.status_code, code_of(r)) == (400, code), r.content


@pytest.mark.parametrize(
    "subs,code",
    [
        ([{"event_type": "prompt.deleted"}], "WEBHOOK_EVENT_TYPE_UNKNOWN"),
        ([], "WEBHOOK_EVENT_TYPE_UNKNOWN"),
        (
            [
                {
                    "event_type": "prompt.version.created",
                    "filters": {"label_names": ["x"]},
                }
            ],
            "WEBHOOK_FILTER_INVALID",
        ),
        (
            [
                {
                    "event_type": "prompt.label.changed",
                    "filters": {"actions": ["exploded"]},
                }
            ],
            "WEBHOOK_FILTER_INVALID",
        ),
        (
            [
                {
                    "event_type": "deployment.run.failed",
                    "filters": {"statuses": "failed"},
                }
            ],
            "WEBHOOK_FILTER_INVALID",
        ),
        (
            [{"event_type": "prompt.*"}, {"event_type": "prompt.*"}],
            "WEBHOOK_FILTER_INVALID",
        ),
        ([{"event_type": "prompt.*"}] * 51, "WEBHOOK_ENDPOINT_LIMIT"),
    ],
)
def test_subscription_rules(owner, keys, subs, code):
    r = owner.post(URL, body(subscriptions=subs), format="json")
    assert (r.status_code, code_of(r)) == (400, code), r.content


def test_filters_are_stored_normalised(owner, keys):
    subs = [
        {
            "event_type": "prompt.label.changed",
            "filters": {"label_names": ["Production", "Production"], "actions": []},
        },
        {
            "event_type": "deployment.run.failed",
            "filters": {"statuses": ["failed", "cancelled"]},
        },
    ]
    r = owner.post(URL, body(subscriptions=subs), format="json")
    assert r.status_code == 201, r.content
    got = {
        s["event_type"]: s["filters"]
        for s in r.json()["result"]["endpoint"]["subscriptions"]
    }
    assert got == {
        "deployment.run.failed": {"statuses": ["failed", "cancelled"]},
        "prompt.label.changed": {"label_names": ["Production"]},
    }


def test_unknown_fields_name_taken_and_kinds(owner, keys):
    r = owner.post(URL, body(colour="red"), format="json")
    assert r.status_code == 400 and "colour" in json.dumps(r.json())
    assert (
        owner.post(URL, body(kind="github_dispatch"), format="json").status_code == 400
    )
    assert owner.post(URL, body(), format="json").status_code == 201
    r = owner.post(URL, body(), format="json")
    assert (r.status_code, code_of(r)) == (409, "WEBHOOK_NAME_TAKEN")


def test_keys_missing_refuses_before_anything_is_stored(owner, monkeypatch):
    monkeypatch.delenv("WEBHOOKS_ENCRYPTION_KEYS", raising=False)
    r = owner.post(URL, body(), format="json")
    assert (r.status_code, code_of(r)) == (503, "WEBHOOK_KEYS_MISSING")
    assert not Endpoint.objects.exists() and not Secret.objects.exists()


def test_bring_your_own_secret_and_its_format(owner, keys):
    from webhooks.core.signing import secret_from_key

    byo = secret_from_key(
        bytes(range(32))
    )  # the section 5.6 Secret A, built from its bytes
    r = owner.post(URL, body(secret="whsec_short"), format="json")
    assert (r.status_code, code_of(r)) == (400, "WEBHOOK_SECRET_INVALID")
    r = owner.post(URL, body(secret=byo), format="json")
    assert r.status_code == 201 and r.json()["result"]["secret"] == byo
