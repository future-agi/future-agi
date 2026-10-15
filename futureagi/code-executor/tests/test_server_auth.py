import importlib.util
import io
import sys
from pathlib import Path

import falcon


SERVER_PATH = Path(__file__).parents[1] / "server.py"


def _load_server_module():
    spec = importlib.util.spec_from_file_location("code_executor_server_test", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Request:
    def __init__(self, authorization=None, body=b"{}"):  # noqa: D107
        self._authorization = authorization
        self.bounded_stream = io.BytesIO(body)

    def get_header(self, name):
        assert name == "Authorization"
        return self._authorization


class _Response:
    status = None
    media = None


def test_execute_requires_internal_token_before_reading_or_spawning(monkeypatch):
    server = _load_server_module()
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "test-service-token")

    class _UnreadableBody:
        def read(self):
            raise AssertionError("request body was read before auth")

    request = _Request()
    request.bounded_stream = _UnreadableBody()
    response = _Response()
    monkeypatch.setattr(
        server.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("subprocess spawned before auth")
        ),
    )

    server.ExecuteResource().on_post(request, response)

    assert response.status == falcon.HTTP_401
    assert response.media == {"status": "error", "data": "Unauthorized"}


def test_health_does_not_expose_interpreter_paths():
    server = _load_server_module()
    response = _Response()

    server.HealthResource().on_get(None, response)

    assert response.media["status"] == "ok"
    assert response.media["python"] is True
    assert response.media["node"] is bool(server.NODE_PATH)
    assert all(
        path not in str(response.media)
        for path in (server.PYTHON_PATH, server.NODE_PATH)
        if path
    )
