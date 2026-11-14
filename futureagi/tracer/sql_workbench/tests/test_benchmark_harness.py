"""The opt-in full-scale benchmark client runs end to end against a fake API.

This keeps ``scripts/sql_workbench/benchmark.py`` runnable (``scripts/`` is not
collected by pytest). It is not a performance measurement: the fake server
answers instantly and holds no data.
"""

import importlib.util
import io
import json
import threading
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/sql_workbench/benchmark.py"
TOKEN = "benchmark-test-token"


def _load():
    spec = importlib.util.spec_from_file_location("sql_benchmark_client", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeAPI(BaseHTTPRequestHandler):
    rows = [["locale-value-sentinel", 3, 0.5]]
    calls: list = []

    def log_message(self, *args):
        pass

    def _reply(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(202 if self.command == "POST" else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length))
        self.calls.append(("POST", self.path, body, self.headers["Authorization"]))
        self._reply({"id": "11111111-1111-4111-8111-111111111111", "state": "running"})

    def do_GET(self):
        self.calls.append(("GET", self.path, None, self.headers["Authorization"]))
        self._reply(
            {
                "id": "11111111-1111-4111-8111-111111111111",
                "state": "succeeded",
                "result": {
                    "rows": self.rows,
                    "row_count": len(self.rows),
                    "truncated": False,
                    "elapsed_ms": 7,
                },
            }
        )


@pytest.fixture
def api():
    FakeAPI.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeAPI)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _run(tmp_path, base_url, *, opt_in=True, token=TOKEN, span_count=10):
    project = uuid4()
    profile = json.loads((SCRIPT.parent / "profile.json").read_text())
    profile.update(required_project_spans=10, duration_seconds=1, interval_seconds=10)
    (tmp_path / "profile.json").write_text(json.dumps(profile))
    (tmp_path / "manifest.json").write_text(
        json.dumps({"project_id": str(project), "span_count": span_count})
    )
    argv = [
        "benchmark.py",
        "--base-url",
        base_url,
        "--organization",
        str(uuid4()),
        "--workspace",
        str(uuid4()),
        "--project",
        str(project),
        "--fixture-manifest",
        str(tmp_path / "manifest.json"),
        "--profile",
        str(tmp_path / "profile.json"),
    ] + (["--allow-benchmark-requests"] if opt_in else [])
    out = io.StringIO()
    env = {"SQL_BENCHMARK_TOKEN": token} if token else {}
    with (
        patch("sys.argv", argv),
        patch.dict("os.environ", env, clear=True),
        redirect_stdout(out),
    ):
        code = _load().main()
    return code, [json.loads(line) for line in out.getvalue().splitlines()], profile


def test_benchmark_client_runs_a_sample_and_reports_an_honest_summary(api, tmp_path):
    code, records, profile = _run(tmp_path, api)
    assert code == 0
    sample, summary = records
    assert sample["state"] == "succeeded" and sample["rows"] == 1
    assert sample["sql_ms"] == 7 and sample["wall_ms"] >= 0
    assert summary["summary"] is True and summary["samples"] == 1
    assert summary["failures"] == 0
    assert summary["fixture_count_independently_verified"] is False
    method, path, body, auth = FakeAPI.calls[0]
    assert (method, path) == ("POST", "/tracer/sql-query/run/")
    assert body["sql"] == profile["sql"] and auth == f"Bearer {TOKEN}"
    assert FakeAPI.calls[1][0] == "GET"
    # Output carries timings and status only: no SQL, values or credentials.
    text = json.dumps(records)
    assert profile["sql"] not in text and TOKEN not in text
    assert "locale-value-sentinel" not in text


def test_an_empty_join_is_a_failure_and_the_job_is_cancelled(api, tmp_path):
    with patch.object(FakeAPI, "rows", []):
        code, records, _ = _run(tmp_path, api)
    assert code == 1
    assert records[0]["state"] == "failed"
    assert records[-1]["failures"] == 1
    assert any(path.endswith("/cancel/") for _, path, _, _ in FakeAPI.calls)


@pytest.mark.parametrize(
    "kwargs", [{"opt_in": False}, {"token": ""}, {"span_count": 9}]
)
def test_client_refuses_without_opt_in_token_or_a_large_enough_fixture(
    api, tmp_path, kwargs
):
    with pytest.raises(SystemExit) as refused:
        _run(tmp_path, api, **kwargs)
    assert refused.value.code == 2
    assert FakeAPI.calls == []
