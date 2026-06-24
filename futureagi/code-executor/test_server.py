"""Behavioral tests for the code-executor HTTP service.

These drive the real Falcon call-path (request -> AuthMiddleware -> resource), not
config flags. Each test below goes RED if the corresponding hardening is reverted:
  - drop AuthMiddleware  -> the auth tests stop returning 401
  - restore the unsandboxed fallback -> the no-nsjail refusal test starts executing
"""

from unittest import mock

import falcon
import server
from falcon import testing

KEY = "test-internal-key"


def make_client(key=KEY):
    app = falcon.App(middleware=[server.AuthMiddleware(key=key)])
    app.add_route("/execute", server.ExecuteResource())
    app.add_route("/health", server.HealthResource())
    return testing.TestClient(app)


CODE = {"code": "def evaluate(**k):\n    return True", "language": "python"}


class TestAuth:
    def test_missing_key_rejected(self):
        resp = make_client().simulate_post("/execute", json=CODE)
        assert resp.status_code == 401

    def test_wrong_key_rejected(self):
        resp = make_client().simulate_post(
            "/execute", json=CODE, headers={"X-Internal-Api-Key": "nope"}
        )
        assert resp.status_code == 401

    def test_correct_key_passes_middleware(self):
        # Auth passes -> request reaches the resource (not a 401).
        resp = make_client().simulate_post(
            "/execute", json=CODE, headers={"X-Internal-Api-Key": KEY}
        )
        assert resp.status_code != 401

    def test_unset_key_fails_closed(self):
        # A misconfigured executor with no key must reject everything, not run open.
        resp = make_client(key="").simulate_post(
            "/execute", json=CODE, headers={"X-Internal-Api-Key": ""}
        )
        assert resp.status_code == 401

    def test_health_is_open(self):
        resp = make_client().simulate_get("/health")
        assert resp.status_code == 200


class TestNoNsjailFailsClosed:
    def test_execute_refuses_without_nsjail(self):
        with mock.patch.object(server, "NSJAIL_AVAILABLE", False):
            resp = make_client().simulate_post(
                "/execute", json=CODE, headers={"X-Internal-Api-Key": KEY}
            )
        assert resp.json["status"] == "error"
        assert "nsjail" in resp.json["data"].lower()
        assert "refusing" in resp.json["data"].lower()

    def test_health_unhealthy_without_nsjail(self):
        with mock.patch.object(server, "NSJAIL_AVAILABLE", False):
            resp = make_client().simulate_get("/health")
        assert resp.json["status"] == "unhealthy"
        assert resp.json["nsjail"] is False


class TestRequestValidation:
    def test_unsupported_language_rejected(self):
        with mock.patch.object(server, "NSJAIL_AVAILABLE", True):
            resp = make_client().simulate_post(
                "/execute",
                json={"code": "x=1", "language": "ruby"},
                headers={"X-Internal-Api-Key": KEY},
            )
        assert resp.json["status"] == "error"
        assert "unsupported language" in resp.json["data"].lower()

    def test_empty_code_rejected(self):
        with mock.patch.object(server, "NSJAIL_AVAILABLE", True):
            resp = make_client().simulate_post(
                "/execute",
                json={"code": "   ", "language": "python"},
                headers={"X-Internal-Api-Key": KEY},
            )
        assert resp.json["status"] == "error"
        assert "no code" in resp.json["data"].lower()
