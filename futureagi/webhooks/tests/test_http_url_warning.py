"""HTTP remains accepted under the existing profile; warn without changing envelopes."""

import pytest

from webhooks import crypto
from webhooks.models import Endpoint
from webhooks.tests.api_support import EP

pytestmark = pytest.mark.django_db
MESSAGE = (
    "This URL uses HTTP, so deliveries are not encrypted. Webhook payloads can include "
    "event data from your organization. Use an HTTPS URL to protect payloads in transit."
)
WARNING = {"code": "webhook_url_unencrypted", "message": MESSAGE}


def create(owner, url):
    response = owner.post(
        EP,
        {
            "name": "Transport warning",
            "url": url,
            "subscriptions": [{"event_type": "prompt.*"}],
        },
        format="json",
    )
    assert response.status_code == 201
    assert set(response.json()) == {"status", "result"}
    return response.json()["result"]


@pytest.mark.parametrize("scheme", ["http", "HTTP", "https"])
def test_create_transport_warning_and_persistence(owner, env, scheme):
    port = ":8443" if scheme.lower() == "http" else ""
    url = f"{scheme}://hooks.acme.ai{port}/events"
    result = create(owner, url)
    assert set(result) == {"endpoint", "secret", "warnings"}
    endpoint = result["endpoint"]
    assert endpoint["url_scheme"] == scheme.lower()
    assert result["warnings"] == ([WARNING] if scheme.lower() == "http" else [])
    if scheme.lower() == "http":
        assert endpoint["warnings"] == [WARNING]
    else:
        assert "warnings" not in endpoint
    stored = Endpoint.objects.get(id=endpoint["id"])
    assert crypto.decrypt(stored.url_encrypted) == url


@pytest.mark.parametrize("scheme", ["http", "HTTP", "https"])
def test_update_transport_warning_and_persistence(owner, env, scheme):
    before = "https" if scheme.lower() == "http" else "http"
    before_port = ":8443" if before == "http" else ""
    result = create(owner, f"{before}://hooks.acme.ai{before_port}/before")
    endpoint_id = result["endpoint"]["id"]
    port = ":8443" if scheme.lower() == "http" else ""
    url = f"{scheme}://hooks.acme.ai{port}/after"
    response = owner.patch(f"{EP}{endpoint_id}/", {"url": url}, format="json")
    assert response.status_code == 200
    endpoint = response.json()["result"]
    assert endpoint["id"] == endpoint_id
    if scheme.lower() == "http":
        assert endpoint["warnings"] == [WARNING]
    else:
        assert "warnings" not in endpoint
    stored = Endpoint.objects.get(id=endpoint_id)
    assert crypto.decrypt(stored.url_encrypted) == url


def test_list_and_unchanged_url_update_keep_warning(owner, env):
    result = create(owner, "http://hooks.acme.ai:8443/events")
    endpoint_id = result["endpoint"]["id"]
    response = owner.patch(
        f"{EP}{endpoint_id}/", {"description": "Updated"}, format="json"
    )
    assert response.status_code == 200
    assert response.json()["result"]["warnings"] == [WARNING]
    rows = owner.get(EP).json()["result"]["results"]
    assert next(row for row in rows if row["id"] == endpoint_id)["warnings"] == [
        WARNING
    ]


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.acme.ai",
        "http://hooks.acme.ai:80",
        "http://hooks.acme.ai:5432",
        "http://localhost:8443",
    ],
)
def test_rejected_create_and_update_have_no_warning(owner, env, url):
    result = create(owner, "https://hooks.acme.ai/events")
    endpoint_id = result["endpoint"]["id"]
    responses = [
        owner.post(
            EP,
            {
                "name": "Rejected",
                "url": url,
                "subscriptions": [{"event_type": "prompt.*"}],
            },
            format="json",
        ),
        owner.patch(f"{EP}{endpoint_id}/", {"url": url}, format="json"),
    ]
    for response in responses:
        assert response.status_code == 400
        assert response.json()["status"] is False
        assert "webhook_url_unencrypted" not in response.content.decode()
    stored = Endpoint.objects.get(id=endpoint_id)
    assert crypto.decrypt(stored.url_encrypted) == "https://hooks.acme.ai/events"


def test_masked_and_non_admin_list_keep_safe_scheme(
    owner, env, organization, workspace
):
    from tfc.constants.levels import Level
    from webhooks.tests.api_support import client_for, person

    result = create(owner, "HtTp://hooks.acme.ai:8443/events?token=not-a-real-secret")
    assert result["endpoint"]["url_scheme"] == "http"
    assert "not-a-real-secret" not in result["endpoint"]["url"]
    assert WARNING in result["warnings"]
    assert any(w["code"] == "WEBHOOK_URL_QUERY_SECRET" for w in result["warnings"])
    member = client_for(
        person(organization, workspace, Level.MEMBER, Level.MEMBER),
        organization,
        workspace,
    )
    rows = member.get(EP).json()["result"]["results"]
    row = next(row for row in rows if row["id"] == result["endpoint"]["id"])
    assert row["url"] is None
    assert row["header_names"] is None
    assert row["url_scheme"] == "http"
    assert row["warnings"] == [WARNING]
    assert "not-a-real-secret" not in str(row)


@pytest.mark.parametrize(
    "hosted,allow_http", [(True, True), (False, False), (False, True)]
)
def test_scheme_error_tracks_effective_profile(
    owner, env, monkeypatch, hosted, allow_http
):
    from webhooks import config

    monkeypatch.setattr(config, "is_hosted", lambda: hosted)
    monkeypatch.setenv("FI_WEBHOOKS_ALLOW_HTTP", "true" if allow_http else "false")
    allowed = allow_http and not hosted
    response = owner.post(
        EP,
        {
            "name": "Scheme",
            "url": "ftp://hooks.acme.ai:8443",
            "subscriptions": [{"event_type": "prompt.*"}],
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["result"] == (
        "Webhook URLs must use HTTPS or HTTP."
        if allowed
        else "Webhook URLs must use HTTPS."
    )
    assert "webhook_url_unencrypted" not in str(response.json())
    if not allowed:
        response = owner.post(
            EP,
            {
                "name": "HTTP refused",
                "url": "http://hooks.acme.ai:8443",
                "subscriptions": [{"event_type": "prompt.*"}],
            },
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["result"] == "Webhook URLs must use HTTPS."


@pytest.mark.parametrize(
    "hosted,allow_http,private",
    [
        (True, True, True),
        (False, False, True),
        (False, True, False),
        (False, False, False),
    ],
)
def test_policy_exposes_only_safe_effective_url_flags(
    owner, env, monkeypatch, hosted, allow_http, private
):
    from webhooks import config

    monkeypatch.setattr(config, "is_hosted", lambda: hosted)
    monkeypatch.setenv("FI_WEBHOOKS_ALLOW_HTTP", "true" if allow_http else "false")
    monkeypatch.setenv("FI_WEBHOOKS_PRIVATE_CIDRS", "10.0.0.0/8" if private else "")
    monkeypatch.setenv(
        "FI_WEBHOOKS_ALLOWED_PRIVATE_HOSTS", "internal.acme.ai" if private else ""
    )
    response = owner.get("/webhooks/policy/")
    assert response.status_code == 200
    assert response.json()["result"]["url_policy"] == {
        "http_allowed": allow_http and not hosted,
        "private_addresses_blocked": hosted or not private,
    }


def test_explicit_same_http_url_patch_retains_warning(owner, env):
    url = "HtTp://hooks.acme.ai:443/events"
    result = create(owner, url)
    response = owner.patch(
        f"{EP}{result['endpoint']['id']}/", {"url": url}, format="json"
    )
    assert response.status_code == 200
    assert response.json()["result"]["warnings"] == [WARNING]
    assert response.json()["result"]["url_scheme"] == "http"
