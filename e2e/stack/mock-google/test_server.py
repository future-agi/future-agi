"""Real HTTPS self-tests for the synthetic provider, without Django startup."""

import base64
import hashlib
import json
import ssl
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt

from server import CALLBACK, CLIENT, IdentityServer, tls_context


def verify(id_token, keys, access_token):
    """Verify like the backend: the published key named by kid, RS256 only,
    the fixture's audience and issuer, and at_hash against the access token."""
    kid = jwt.get_unverified_header(id_token).get("kid")
    try:
        key = jwt.PyJWKSet.from_dict(keys)[kid]
    except KeyError:
        raise jwt.PyJWTError("No published key for kid") from None
    claims = jwt.decode(
        id_token,
        key,
        algorithms=["RS256"],
        audience=CLIENT,
        issuer="https://accounts.google.com",
    )
    digest = hashlib.sha256(access_token.encode()).digest()
    at_hash = base64.urlsafe_b64encode(digest[:16]).rstrip(b"=").decode()
    if claims.get("at_hash") != at_hash:
        raise jwt.PyJWTError("at_hash does not match the access token")
    return claims


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args):
        return None


class ProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.server = IdentityServer(("127.0.0.1", 0))
        cls.server.socket = tls_context(cls.directory.name).wrap_socket(
            cls.server.socket, server_side=True
        )
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"https://localhost:{cls.server.server_port}"
        cls.http = urllib.request.build_opener(
            NoRedirect(),
            urllib.request.HTTPSHandler(
                context=ssl.create_default_context(
                    cafile=cls.directory.name + "/ca.pem"
                )
            ),
        )

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.directory.cleanup()

    def request(self, path, data=None, headers=None):
        request = urllib.request.Request(
            self.base + path,
            data=None if data is None else json.dumps(data).encode(),
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            return self.http.open(request, timeout=5)
        except urllib.error.HTTPError as error:
            return error

    def test_real_authorization_and_verification_cases(self):
        for mode in (
            "valid",
            "signature",
            "issuer",
            "audience",
            "expired",
            "unknown",
            "rotated",
        ):
            with self.subTest(mode=mode):
                response = self.request(
                    "/scenario",
                    {"email": "e2e-provider-selftest@futureagi.com", "mode": mode},
                )
                self.assertEqual(response.status, 201)
                scenario = json.load(response)["scenario"]
                response = self.request(
                    "/auth?"
                    + urlencode(
                        {
                            "client_id": CLIENT,
                            "redirect_uri": CALLBACK,
                            "response_type": "code",
                        }
                    ),
                    headers={"Cookie": "e2e_google_scenario=" + scenario},
                )
                self.assertEqual(response.status, 200)
                self.assertIn(b"Authorize synthetic identity", response.read())
                approved = self.request("/approve", {"scenario": scenario})
                self.assertEqual(approved.status, 302)
                code = parse_qs(urlsplit(approved.headers["Location"]).query)["code"][0]
                payload = {
                    "code": code,
                    "client_id": CLIENT,
                    "client_secret": "e2e-google-secret",
                    "redirect_uri": CALLBACK,
                }
                tokens = json.load(self.request("/token", payload))
                keys = json.load(self.request("/certs"))

                token = tokens["id_token"]
                if mode in ("valid", "rotated"):
                    claims = verify(token, keys, tokens["access_token"])
                    self.assertEqual(
                        claims["email"], "e2e-provider-selftest@futureagi.com"
                    )
                else:
                    with self.assertRaises(jwt.PyJWTError):
                        verify(token, keys, tokens["access_token"])
                self.assertEqual(self.request("/token", payload).status, 400)

    def test_refuses_non_fixture_inputs(self):
        for data in (
            {"email": "person@example.test", "mode": "valid"},
            {"email": "e2e-provider-selftest@futureagi.com", "mode": "arbitrary"},
        ):
            self.assertEqual(self.request("/scenario", data).status, 400)
        self.assertEqual(
            self.request(
                "/auth?" + urlencode({"redirect_uri": "https://example.test"})
            ).status,
            400,
        )
        self.assertEqual(self.request("/approve", {"scenario": "absent"}).status, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
