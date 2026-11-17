"""Documentation-backed synthetic loopback servers, not live vendor captures."""

import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import jsonschema
import pytest
from django.utils import timezone

from agentcc.models.mcp_vendor import AgentccMCPVendorConnection as Connection
from agentcc.services.credential_manager import encrypt_token
from agentcc.services.mcp_safe_fetch import FetchResponse
from agentcc.services.mcp_vendor_http import VendorUnavailable
from agentcc.tests.mcp_oauth_helpers import member, new_org
from agentcc.tests.test_mcp_vendor_lifecycle import binding
from agentcc.tests.test_mcp_vendor_lifecycle import vendor_settings as vendor_settings

pytestmark = pytest.mark.django_db


@pytest.fixture
def partner_server(monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
            )
            seen.append((self.path, body))
            if self.path == "/v1/oauth/token":
                result = {"access_token": "fixture-loopback-bearer-not-real"}
            elif (
                self.path.endswith("/tokens")
                and self.headers.get("Authorization")
                == "Bearer fixture-loopback-bearer-not-real"
            ):
                result = {
                    "connect_link_url": "https://pipedream.com/connect.html?token=ctok_fixture-not-real"
                }
            else:
                self.send_error(403)
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        def do_GET(self):
            if (
                self.headers.get("Authorization")
                != "Bearer fixture-loopback-bearer-not-real"
            ):
                self.send_error(403)
                return
            query = parse_qs(urlsplit(self.path).query)
            result = {
                "data": [
                    {
                        "id": "apn_fixture",
                        "healthy": True,
                        "dead": False,
                        "external_id": query["external_user_id"][0],
                        "app": {"name_slug": "gmail"},
                    }
                ]
            }
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv("AGENTCC_MCP_EGRESS_PRIVATE_ALLOW", "127.0.0.1")
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_mint_cached_encrypted_and_reminted_before_expiry(partner_server):
    from agentcc.services.mcp_vendor_pipedream import Pipedream

    base, seen = partner_server
    row = binding(new_org())

    def api():
        return Pipedream(
            "fixture-client",
            "fixture-secret-not-real",
            "proj_fixture",
            "development",
            base=base,
            binding=row,
        )

    assert api().account("fau_one", "gmail") == "apn_fixture"
    assert api().account("fau_two", "gmail") == "apn_fixture"
    assert len([path for path, _ in seen if path.endswith("/oauth/token")]) == 1
    row.refresh_from_db()
    assert row.encrypted_bearer.startswith("enc::")
    assert "fixture-loopback" not in row.encrypted_bearer
    row.bearer_expires_at = timezone.now() - timedelta(seconds=1)
    row.save(update_fields=["bearer_expires_at"])
    api().bearer()
    assert len([path for path, _ in seen if path.endswith("/oauth/token")]) == 2


def test_two_members_execute_with_distinct_vendor_subjects(partner_server):
    from agentcc.services import mcp_vendor_credentials as credentials
    from agentcc.services.mcp_vendor_pipedream import Pipedream
    from agentcc.tests.test_mcp_vendor_lifecycle import request_body

    base, _ = partner_server
    org = new_org()
    row = binding(org)
    first, _ = member(org)
    second, _ = member(org)
    for user in (first, second):
        Connection.objects.create(
            binding=row,
            source="member",
            user=user,
            end_user_key=credentials.subject_key(org.pk, "member", str(user.pk)),
        )

    def client(binding):
        return Pipedream(
            "fixture-client",
            "fixture-secret-not-real",
            binding.project_id,
            binding.environment,
            base=base,
            binding=binding,
        )

    with patch.object(credentials, "vendor_client", client):
        a = credentials.answer_request(request_body(org, first))
        b = credentials.answer_request(request_body(org, second))
    assert a["external_user_id"] != b["external_user_id"]
    assert a["vendor"] == b["vendor"] == "pipedream"
    assert a["external_user_id"].startswith("fau_")
    assert str(first.pk) not in json.dumps(a)
    foreign = new_org()
    assert credentials.answer_request(request_body(foreign, first)) == {
        "error": "no_credential"
    }


def test_reconnect_blocked_until_purge_confirmed():
    from agentcc.services import mcp_vendor_connections as lifecycle
    from agentcc.services.mcp_vendor_credentials import subject_key

    org = new_org()
    row = binding(org)
    user, _ = member(org)
    Connection.objects.create(
        binding=row,
        source="member",
        user=user,
        status="revoked",
        gateway_purge_pending=True,
        end_user_key=subject_key(org.pk, "member", str(user.pk)),
    )
    with pytest.raises(lifecycle.ConnectionRefused, match="cleanup_pending"):
        lifecycle.start(row.pk, {"source": "member", "id": str(user.pk)})


def test_composio_direct_tools_preset_and_minted_origin():
    from agentcc.services.mcp_vendor_composio import Composio

    seen = []

    def fetch(url, **kwargs):
        seen.append((url, kwargs))
        body = {
            "session_id": "trs_fixture",
            "mcp": {"url": "https://backend.composio.dev/tool_router/fixture/mcp"},
        }
        if "/tools?" in url:
            body = {"items": [{"slug": "GMAIL_FETCH_EMAILS"}], "next_cursor": None}
        return FetchResponse(
            201 if kwargs["method"] == "POST" else 200,
            {},
            json.dumps(body).encode(),
            url,
        )

    with patch("agentcc.services.mcp_safe_fetch.fetch", fetch):
        session, endpoint = Composio("fixture-project-not-real").create(
            "fau_fixture", ["gmail"], {"gmail": ["GMAIL_FETCH_EMAILS"]}
        )
    body = seen[0][1]["json_body"]
    assert "mcp" not in body  # SDK option, not a REST create field.
    assert body["user_id"] == "fau_fixture"
    assert body["preload"] == {"tools": "all"}
    for name in ("manage_connections", "search", "workbench"):
        assert body[name] == {"enable": False}
    assert body["execute"] == {"enable_multi_execute": False}
    assert session == "trs_fixture" and endpoint.endswith("/fixture/mcp")


def test_composio_off_origin_refused_and_session_removed():
    from agentcc.services.mcp_vendor_composio import Composio

    methods = []

    def fetch(url, **kwargs):
        methods.append(kwargs["method"])
        return FetchResponse(
            201 if kwargs["method"] == "POST" else 200,
            {},
            b'{"session_id":"trs_fixture","mcp":{"url":"https://other.example/mcp"}}',
            url,
        )

    with (
        patch("agentcc.services.mcp_safe_fetch.fetch", fetch),
        pytest.raises(VendorUnavailable),
    ):
        Composio("fixture-project-not-real").create(
            "fau_fixture", ["gmail"], {"gmail": ["GMAIL_FETCH_EMAILS"]}
        )
    assert methods == ["POST", "DELETE"]


@pytest.mark.django_db(transaction=True)
def test_member_removal_offboards_vendor_identities():
    from accounts.models.organization_membership import OrganizationMembership
    from agentcc.services import mcp_vendor_connections as lifecycle
    from agentcc.services.mcp_revocation import Revocation

    org = new_org()
    row = binding(org)
    user, _ = member(org)
    connection = Connection.objects.create(
        binding=row, source="member", user=user, end_user_key="fau_fixture"
    )
    with (
        patch.object(lifecycle, "purge_gateway", return_value=True),
        patch.object(
            lifecycle, "revoke_vendor", return_value=Revocation("confirmed")
        ) as revoke,
    ):
        membership = OrganizationMembership.all_objects.get(organization=org, user=user)
        membership.is_active = False
        membership.save(update_fields=["is_active"])
    connection.refresh_from_db()
    assert connection.status == "revoked"
    assert revoke.call_args.kwargs["offboard"] is True


def test_vendor_contract_example_validates():
    root = Path(__file__).resolve().parents[3] / "api_contracts/gateway/examples"
    schema = json.loads((root / "mcp-vendor-credential-v1.schema.json").read_text())
    example = json.loads((root / "mcp-vendor-credential-v1.json").read_text())
    jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    ).validate(example)


def test_session_fields_refuse_plaintext():
    from django.core.exceptions import ValidationError

    row = binding(new_org())
    with pytest.raises(ValidationError):
        Connection.objects.create(
            binding=row,
            source="asserted",
            end_user_key="fau_fixture",
            encrypted_session="trs_plaintext",
        )
    assert encrypt_token("trs_fixture").startswith("enc::")
