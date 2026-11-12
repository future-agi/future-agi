"""End-to-end boundary: real Django session exchange -> real Omega gateway -> stub EE.

The gateway (services/omega-gateway) runs as a Node subprocess in local mode,
pointed at this test's live Django server and a recording stub EE. The checks
prove what EE actually receives: only Django-minted customer assertions, a
fresh one per call, never the FutureAGI browser token, and nothing at all for
operator routes, viewers, expired or forged assertions.
"""

import base64
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.cache import cache
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone

from accounts.authentication import generate_encrypted_message
from accounts.models.auth_token import AuthToken
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from omega_rollout.testing import admit_for_tests
from tfc.middleware.workspace_context import clear_workspace_context

REPO_ROOT = Path(__file__).resolve().parents[3]
GATEWAY = REPO_ROOT / "services" / "omega-gateway" / "src" / "server.mjs"
NODE = shutil.which("node")
ISSUER = "https://host.example/omega"
KEY_ID = "bridge-test-key"


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _claims(assertion):
    payload = assertion.removeprefix("omega_delegate_").split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


class _StubEE:
    """Records every request that reaches 'EE' and answers 200 JSON."""

    def __init__(self):
        self.requests = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def _record(self):
                length = int(self.headers.get("content-length") or 0)
                stub.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": self.rfile.read(length) if length else b"",
                    }
                )
                body = b'{"ok":true}'
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _record

            def log_message(self, *args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@unittest.skipUnless(NODE, "node is required to run the Omega gateway")
# STATIC_URL: the live server wraps the WSGI app in a static-files handler.
@override_settings(
    ROOT_URLCONF="accounts.tests.omega_session_test_urls", STATIC_URL="/static/"
)
class OmegaGatewayBridgeTests(LiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        cls.key_file = root / "issuer.pem"
        cls.key_file.write_bytes(
            cls.key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        cls.key_file.chmod(0o600)
        numbers = cls.key.public_key().public_numbers()
        jwks = {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": KEY_ID,
                    "alg": "RS256",
                    "use": "sig",
                    "n": _b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8)),
                    "e": _b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8)),
                }
            ]
        }
        (root / "jwks.json").write_text(json.dumps(jwks))
        (root / "home").mkdir()
        cls.environment = patch.dict(
            os.environ,
            {
                "OMEGA_SQUAD_DELEGATION_ISSUER": ISSUER,
                "OMEGA_SQUAD_DELEGATION_KEY_ID": KEY_ID,
                "OMEGA_SQUAD_DELEGATION_PRIVATE_KEY_FILE": str(cls.key_file),
            },
        )
        cls.environment.start()
        cls.ee = _StubEE()
        cls.gateway = subprocess.Popen(
            [NODE, str(GATEWAY)],
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(root / "home"),
                "OMEGA_GATEWAY_MODE": "local",
                "PORT": "0",
                "OMEGA_GATEWAY_BASE_PATH": "/omega/api",
                "OMEGA_REMOTE_BASE_URL": cls.ee.url,
                "OMEGA_SESSION_EXCHANGE_URL": f"{cls.live_server_url}/accounts/omega/session-exchange/",
                "OMEGA_ASSERTION_JWKS_FILE": str(root / "jwks.json"),
                "OMEGA_ASSERTION_ISSUER": ISSUER,
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        lines = queue.Queue()
        threading.Thread(
            target=lambda: [lines.put(line) for line in cls.gateway.stdout],
            daemon=True,
        ).start()
        try:
            line = lines.get(timeout=20)
        except queue.Empty:
            line = ""
        try:
            started = json.loads(line)
        except ValueError:
            started = {}
        if started.get("event") != "omega-gateway.listening":
            cls._stop_services()
            raise RuntimeError(f"gateway did not start: {line!r}")
        cls.gateway_url = started["url"] + "/omega/api"

    @classmethod
    def _stop_services(cls):
        cls.gateway.terminate()
        try:
            cls.gateway.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.gateway.kill()
        cls.ee.close()
        cls.environment.stop()
        cls.directory.cleanup()

    @classmethod
    def tearDownClass(cls):
        cls._stop_services()
        super().tearDownClass()

    def setUp(self):
        clear_workspace_context()
        cache.clear()
        self.addCleanup(clear_workspace_context)
        self.ee.requests.clear()
        self.organization = Organization.objects.create(name="Bridge organization")
        # The products ship dark (READINESS 3.13); this suite tests the bridge, not the rollout.
        admit_for_tests(self.organization)
        self.user = User.objects.create_user(
            email="bridge@example.invalid",
            password="disposable",
            organization=self.organization,
        )
        self.membership = OrganizationMembership.no_workspace_objects.create(
            user=self.user, organization=self.organization, role="Member", level=3
        )
        self.workspace = Workspace.no_workspace_objects.create(
            name="Bridge workspace", organization=self.organization, created_by=self.user
        )
        WorkspaceMembership.no_workspace_objects.create(
            user=self.user,
            workspace=self.workspace,
            organization_membership=self.membership,
            role="workspace_member",
            level=3,
        )
        token = AuthToken.no_workspace_objects.create(
            user=self.user, auth_type="access", is_active=True, last_used_at=timezone.now()
        )
        self.browser_token = generate_encrypted_message(
            {"id": str(token.pk), "user_id": str(self.user.pk)}
        )
        self.selection = {
            "X-Organization-Id": str(self.organization.pk),
            "X-Workspace-Id": str(self.workspace.pk),
        }

    def _request(self, url, method="GET", headers=None, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    def _gateway(self, path, bearer, method="GET", headers=None):
        return self._request(
            self.gateway_url + path,
            method=method,
            headers={"Authorization": f"Bearer {bearer}", **(headers or {})},
        )

    def _mint(self):
        status, body = self._request(
            f"{self.live_server_url}/accounts/omega/session-exchange/",
            method="POST",
            headers={"Authorization": f"Bearer {self.browser_token}", **self.selection},
            body={"audience": "omega-squad"},
        )
        self.assertEqual(status, 200, body)
        return body["accessToken"]

    def test_ee_receives_only_fresh_django_assertions_never_the_browser_token(self):
        for _ in range(2):
            status, body = self._gateway(
                "/workspace/sandboxes",
                self.browser_token,
                headers={**self.selection, "X-Omega-Principal-Roles": "operator"},
            )
            self.assertEqual((status, body), (200, {"ok": True}))
        self.assertEqual(len(self.ee.requests), 2)
        presented = [r["headers"]["authorization"] for r in self.ee.requests]
        for request, value in zip(self.ee.requests, presented):
            self.assertEqual(request["path"], "/workspace/sandboxes")
            self.assertTrue(value.startswith("Bearer omega_delegate_"))
            self.assertNotIn(self.browser_token, json.dumps(request["headers"]))
            for header in ("x-workspace-id", "x-organization-id", "x-omega-principal-roles", "cookie"):
                self.assertNotIn(header, request["headers"])
            claims = _claims(value.removeprefix("Bearer "))
            self.assertEqual(claims["sub"], str(self.user.pk))
            self.assertEqual(claims["workspace_id"], str(self.workspace.pk))
            self.assertEqual(claims["role"], "member")
            self.assertEqual(claims["exp"] - claims["iat"], 45)
        # Single use: each upstream call carried its own assertion and replay ids.
        first, second = (_claims(v.removeprefix("Bearer ")) for v in presented)
        self.assertNotEqual(presented[0], presented[1])
        self.assertNotEqual(first["jti"], second["jti"])
        self.assertNotEqual(first["nonce"], second["nonce"])

    def test_browser_minted_assertion_is_forwarded_unchanged_and_scope_bound(self):
        assertion = self._mint()
        status, _ = self._gateway(
            "/workspace/deployments/dep-0123456789abcdef/health",
            assertion,
            headers=self.selection,
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.ee.requests[-1]["headers"]["authorization"], f"Bearer {assertion}")
        other = Workspace.no_workspace_objects.create(
            name="Other", organization=self.organization, created_by=self.user
        )
        status, body = self._gateway(
            "/workspace/deployments", self._mint(), headers={"X-Workspace-Id": str(other.pk)}
        )
        self.assertEqual((status, body["error"]), (403, "omega_scope_mismatch"))
        self.assertEqual(len(self.ee.requests), 1)

    def test_operator_routes_are_refused_before_ee(self):
        for path in ("/os/sandboxes", "/os/agents/agent-1/deployments", "/os/audit"):
            for bearer in (self.browser_token, self._mint()):
                status, body = self._gateway(path, bearer)
                self.assertEqual((status, body["error"]), (403, "route_not_allowed"), path)
                self.assertRegex(body["requestId"], r"^[0-9a-f]{32}$")
        self.assertEqual(self.ee.requests, [])

    def test_viewer_gets_no_assertion_and_reaches_nothing(self):
        OrganizationMembership.no_workspace_objects.filter(pk=self.membership.pk).update(level=1)
        status, _ = self._request(
            f"{self.live_server_url}/accounts/omega/session-exchange/",
            method="POST",
            headers={"Authorization": f"Bearer {self.browser_token}", **self.selection},
            body={"audience": "omega-squad"},
        )
        self.assertEqual(status, 403)
        # The gateway passes the viewer reason on (CP P10), so the host can say why.
        status, body = self._gateway("/workspace/sandboxes", self.browser_token, headers=self.selection)
        self.assertEqual((status, body["error"]), (403, "omega_viewer_not_admitted"))
        self.assertEqual(self.ee.requests, [])

    def test_expired_and_forged_assertions_are_refused_before_ee(self):
        with patch("accounts.omega_session.time.time", return_value=time.time() - 60):
            expired = self._mint()
        status, body = self._gateway("/workspace/sandboxes", expired)
        self.assertEqual((status, body["reason"]), (401, "expired_assertion"))
        genuine = self._mint()
        prefix_header, payload, signature = genuine.split(".")
        claims = _claims(genuine)
        claims["role"] = "owner"
        forged = ".".join([prefix_header, _b64(json.dumps(claims).encode()), signature])
        status, body = self._gateway("/workspace/sandboxes", forged)
        self.assertEqual((status, body["reason"]), (401, "bad_signature"))
        self.assertEqual(self.ee.requests, [])

    def test_rollout_claims_drive_the_real_gateway_wall(self):
        """READINESS 3.13 end to end: Django mints the `products` claim, the real gateway reads it.
        A workspace deny is 404 for that product only, a pause keeps reads and refuses new work with
        503, an org admitted to nothing gets no key and a 404, and an edited claim fails the
        signature. Nothing refused reaches EE."""
        from omega_rollout.models import ProductAccess, ProductRollout

        ProductAccess.objects.create(
            product="sandboxes",
            organization=self.organization,
            workspace=self.workspace,
            effect="deny",
            cohort="partner",
        )
        status, body = self._gateway(
            "/workspace/sandboxes", self.browser_token, headers=self.selection
        )
        self.assertEqual((status, body["error"]), (404, "product_not_admitted"))
        status, _ = self._gateway(
            "/workspace/deployments", self.browser_token, headers=self.selection
        )
        self.assertEqual(status, 200)
        ProductRollout.objects.filter(product="deployments").update(paused=True)
        status, _ = self._gateway(
            "/workspace/deployments", self.browser_token, headers=self.selection
        )
        self.assertEqual(status, 200)
        status, body = self._gateway(
            "/workspace/deployments",
            self.browser_token,
            method="POST",
            headers={**self.selection, "Content-Type": "application/json"},
        )
        self.assertEqual((status, body["error"]), (503, "product_paused"))
        # B edits its key to name sandboxes: the signature no longer matches.
        prefix_header, _, signature = self._mint().split(".")
        claims = _claims(self._mint())
        claims["products"]["sandboxes"] = "active"
        forged = ".".join([prefix_header, _b64(json.dumps(claims).encode()), signature])
        status, body = self._gateway("/workspace/sandboxes", forged)
        self.assertEqual((status, body["reason"]), (401, "bad_signature"))
        ProductAccess.objects.filter(organization=self.organization).delete()
        status, body = self._gateway(
            "/workspace/conversations", self.browser_token, headers=self.selection
        )
        self.assertEqual((status, body["error"]), (404, "product_not_admitted"))
        self.assertEqual([r["method"] for r in self.ee.requests], ["GET", "GET"])
