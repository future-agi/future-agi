"""Tests for code-executor security: auth, sandbox isolation, fallback behaviour."""

import json
import os
import shutil

import falcon.testing
import pytest

# Set secret before importing module-under-test
os.environ["INTERNAL_API_SECRET"] = "test-internal-secret-123"

import code_executor.server as server_module  # noqa: E402

_SECRET = "test-internal-secret-123"


def test_health_endpoint_returns_status():
    client = falcon.testing.TestClient(server_module.app)
    response = client.simulate_get("/health")
    assert response.status == falcon.HTTP_200
    body = response.json
    assert body["status"] == "ok"
    assert "nsjail" in body


def test_execute_rejected_without_auth():
    client = falcon.testing.TestClient(server_module.app)
    response = client.simulate_post(
        "/execute", json={"code": "print(1)", "input_data": {}}
    )
    assert response.status == falcon.HTTP_401
    assert "Missing Bearer token" in response.json["data"]


def test_execute_rejected_with_invalid_token():
    client = falcon.testing.TestClient(server_module.app)
    response = client.simulate_post(
        "/execute",
        json={"code": "print(1)", "input_data": {}},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status == falcon.HTTP_401
    assert "Invalid token" in response.json["data"]


def test_execute_rejected_without_authorization_header():
    client = falcon.testing.TestClient(server_module.app)
    response = client.simulate_post(
        "/execute",
        json={"code": "print(1)", "input_data": {}},
        headers={"X-Custom": "value"},
    )
    assert response.status == falcon.HTTP_401


def test_execute_rejected_when_internal_api_secret_not_configured(
    monkeypatch: pytest.MonkeyPatch,
):
    """When INTERNAL_API_SECRET is empty, endpoint rejects all requests."""
    monkeypatch.setenv("INTERNAL_API_SECRET", "")

    # Reload module to pick up empty secret
    os.environ["INTERNAL_API_SECRET"] = ""
    import importlib

    importlib.reload(server_module)

    client = falcon.testing.TestClient(server_module.app)
    response = client.simulate_post(
        "/execute",
        json={"code": "print(1)", "input_data": {}},
        headers={"Authorization": "Bearer anything"},
    )
    assert response.status == falcon.HTTP_401
    assert "not configured" in response.json["data"]

    # Restore for subsequent tests
    os.environ["INTERNAL_API_SECRET"] = "test-internal-secret-123"
    importlib.reload(server_module)


@pytest.mark.skipif(
    not shutil.which("nsjail"), reason="nsjail not installed on this host"
)
def test_execute_accepted_with_valid_key():
    client = falcon.testing.TestClient(server_module.app)
    response = client.simulate_post(
        "/execute",
        json={"code": "print('hello')", "input_data": {}, "timeout": 5},
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status == falcon.HTTP_200
    body = response.json
    assert "status" in body


@pytest.mark.skipif(
    shutil.which("nsjail"), reason="test only valid when nsjail is NOT installed"
)
def test_python_execution_rejected_when_nsjail_missing():
    client = falcon.testing.TestClient(server_module.app)
    response = client.simulate_post(
        "/execute",
        json={"code": "print('hello')", "input_data": {}},
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status == falcon.HTTP_503
    assert "sandbox not available" in response.json["data"].lower()


@pytest.mark.skipif(
    shutil.which("nsjail"), reason="test only valid when nsjail is NOT installed"
)
def test_javascript_execution_rejected_when_nsjail_missing():
    client = falcon.testing.TestClient(server_module.app)
    response = client.simulate_post(
        "/execute",
        json={"code": "print('hello')", "input_data": {}, "language": "javascript"},
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status == falcon.HTTP_503
    assert "sandbox not available" in response.json["data"].lower()


@pytest.mark.skipif(
    not shutil.which("nsjail"), reason="nsjail not installed on this host"
)
def test_sandbox_cannot_read_proc_environ():
    client = falcon.testing.TestClient(server_module.app)
    read_environ_code = """
import json as _json
try:
    with open("/proc/self/environ", "rb") as f:
        data = f.read()
    result = {"exposed": True, "environ_sample": data[:100].decode(errors="replace")}
except PermissionError:
    result = {"exposed": False, "reason": "PermissionError"}
except FileNotFoundError:
    result = {"exposed": False, "reason": "FileNotFoundError"}
except Exception as e:
    result = {"exposed": False, "reason": str(e)}
print(_json.dumps({"status": "success", "data": result}))
"""
    response = client.simulate_post(
        "/execute",
        json={"code": read_environ_code, "input_data": {}, "timeout": 10},
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status == falcon.HTTP_200
    body = response.json
    result_data = body.get("data", body)
    assert not result_data.get(
        "exposed", False
    ), f"/proc/self/environ was readable inside sandbox: {result_data}"


@pytest.mark.skipif(
    not shutil.which("nsjail"), reason="nsjail not installed on this host"
)
def test_sandbox_cannot_read_etc_passwd():
    client = falcon.testing.TestClient(server_module.app)
    read_passwd_code = """
import json as _json
try:
    with open("/etc/passwd", "r") as f:
        data = f.read()
    result = {"exposed": True, "passwd_sample": data[:100]}
except PermissionError:
    result = {"exposed": False, "reason": "PermissionError"}
except FileNotFoundError:
    result = {"exposed": False, "reason": "FileNotFoundError"}
except Exception as e:
    result = {"exposed": False, "reason": str(e)}
print(_json.dumps({"status": "success", "data": result}))
"""
    response = client.simulate_post(
        "/execute",
        json={"code": read_passwd_code, "input_data": {}, "timeout": 10},
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status == falcon.HTTP_200
    body = response.json
    result_data = body.get("data", body)
    assert not result_data.get(
        "exposed", False
    ), f"/etc/passwd was readable inside sandbox: {result_data}"
