"""Tests for code-executor security: auth, sandbox isolation, fallback behaviour.

Runs under code-executor/pytest.ini in the executor's own venv (falcon lives in
the executor image, not the core backend). ``server`` resolves because pytest
inserts the code-executor directory into sys.path.
"""

import os
import shutil

import falcon.testing
import pytest

# Set secret before importing the module-under-test. Confined to this isolated
# harness; the backend process gets the real secret from its environment.
os.environ["INTERNAL_API_SECRET"] = "test-internal-secret-123"

import server as server_module  # noqa: E402

_SECRET = "test-internal-secret-123"


def _client():
    return falcon.testing.TestClient(server_module.app)


VALID_EVAL_CODE = "def evaluate(input):\n    return {'result': 1.0}\n"


def test_health_endpoint_returns_status():
    response = _client().simulate_get("/health")
    assert response.status == falcon.HTTP_200
    body = response.json
    assert body["status"] == "ok"
    assert "nsjail" in body


def test_execute_rejected_without_auth():
    response = _client().simulate_post(
        "/execute", json={"code": "print(1)", "input_data": {}}
    )
    assert response.status == falcon.HTTP_401
    assert "Missing Bearer token" in response.json["data"]


def test_execute_rejected_with_invalid_token():
    response = _client().simulate_post(
        "/execute",
        json={"code": "print(1)", "input_data": {}},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status == falcon.HTTP_401
    assert "Invalid token" in response.json["data"]


def test_execute_rejected_without_authorization_header():
    response = _client().simulate_post(
        "/execute",
        json={"code": "print(1)", "input_data": {}},
        headers={"X-Custom": "value"},
    )
    assert response.status == falcon.HTTP_401


def test_execute_rejected_when_internal_api_secret_not_configured(
    monkeypatch: pytest.MonkeyPatch,
):
    """When INTERNAL_API_SECRET is empty, endpoint rejects all requests."""
    import importlib

    monkeypatch.setenv("INTERNAL_API_SECRET", "")
    importlib.reload(server_module)
    try:
        response = _client().simulate_post(
            "/execute",
            json={"code": "print(1)", "input_data": {}},
            headers={"Authorization": "Bearer anything"},
        )
        assert response.status == falcon.HTTP_401
        assert "not configured" in response.json["data"]
    finally:
        # Restore module state so subsequent tests see the real secret.
        monkeypatch.setenv("INTERNAL_API_SECRET", _SECRET)
        importlib.reload(server_module)


@pytest.mark.skipif(
    not shutil.which("nsjail"), reason="nsjail not installed on this host"
)
def test_execute_accepted_with_valid_key():
    response = _client().simulate_post(
        "/execute",
        json={"code": VALID_EVAL_CODE, "input_data": {}, "timeout": 5},
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status == falcon.HTTP_200
    body = response.json
    assert body.get("status") == "success"
    assert body.get("data", {}).get("result") == 1.0


@pytest.mark.skipif(
    shutil.which("nsjail") is not None,
    reason="test only valid when nsjail is NOT installed",
)
def test_python_execution_rejected_when_nsjail_missing():
    response = _client().simulate_post(
        "/execute",
        json={"code": VALID_EVAL_CODE, "input_data": {}},
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status == falcon.HTTP_503
    assert "sandbox not available" in response.json["data"].lower()


@pytest.mark.skipif(
    shutil.which("nsjail") is not None,
    reason="test only valid when nsjail is NOT installed",
)
def test_javascript_execution_rejected_when_nsjail_missing():
    response = _client().simulate_post(
        "/execute",
        json={"code": "print('hello')", "input_data": {}, "language": "javascript"},
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status == falcon.HTTP_503
    assert "sandbox not available" in response.json["data"].lower()


def _probe_path_code(path, mode="rb"):
    return f"""
def evaluate(input):
    try:
        with open({path!r}, {mode!r}) as f:
            data = f.read()
        return {{"exposed": True, "sample": data[:100].decode(errors="replace")}}
    except PermissionError:
        return {{"exposed": False, "reason": "PermissionError"}}
    except FileNotFoundError:
        return {{"exposed": False, "reason": "FileNotFoundError"}}
    except Exception as e:
        return {{"exposed": False, "reason": str(e)}}
"""


@pytest.mark.skipif(
    not shutil.which("nsjail"), reason="nsjail not installed on this host"
)
def test_sandbox_cannot_read_proc_environ():
    """Host env secrets must not leak into the sandbox.

    nsjail mounts a fresh /proc, so /proc/self/environ exists but holds an empty
    environment — readable-but-empty is still "not exposed".
    """
    response = _client().simulate_post(
        "/execute",
        json={
            "code": _probe_path_code("/proc/self/environ"),
            "input_data": {},
            "timeout": 10,
        },
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status == falcon.HTTP_200
    body = response.json
    assert body.get("status") == "success", body
    result_data = body.get("data", body)
    exposed_content = result_data.get("exposed") and result_data.get("sample")
    assert (
        not exposed_content
    ), f"/proc/self/environ leaked host env inside sandbox: {result_data}"


@pytest.mark.skipif(
    not shutil.which("nsjail"), reason="nsjail not installed on this host"
)
def test_sandbox_cannot_read_etc_passwd():
    response = _client().simulate_post(
        "/execute",
        json={
            "code": _probe_path_code("/etc/passwd", mode="r"),
            "input_data": {},
            "timeout": 10,
        },
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status == falcon.HTTP_200
    body = response.json
    assert body.get("status") == "success", body
    result_data = body.get("data", body)
    assert not result_data.get(
        "exposed", False
    ), f"/etc/passwd was readable inside sandbox: {result_data}"


@pytest.mark.skipif(
    not shutil.which("nsjail"), reason="nsjail not installed on this host"
)
def test_sandbox_dns_still_resolves():
    """-N networking is deliberately kept; the /etc mounts must keep DNS alive."""
    dns_code = """
def evaluate(input):
    import socket
    try:
        socket.getaddrinfo("example.com", 443, socket.AF_INET)
        return {"dns_ok": True}
    except Exception as e:
        return {"dns_ok": False, "reason": str(e)}
"""
    response = _client().simulate_post(
        "/execute",
        json={"code": dns_code, "input_data": {}, "timeout": 10},
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status == falcon.HTTP_200
    body = response.json
    assert body.get("status") == "success", body
    result_data = body.get("data", body)
    assert result_data.get(
        "dns_ok"
    ), f"DNS resolution failed inside sandbox (are /etc mounts present?): {result_data}"
