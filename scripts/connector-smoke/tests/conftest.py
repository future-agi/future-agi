"""Shared fixtures: a trusted local TLS certificate, the fake vendor server,
a network that routes only fixture hosts to it, and a guard that fails any
test reaching a real address.

The test credentials below are fixture words, not credentials: no live
account exists anywhere in this suite, and no request leaves 127.0.0.1.
"""

from __future__ import annotations

import io
import json
import shutil
import ssl
import subprocess
from pathlib import Path

import pytest
from connector_smoke import cli, transport
from fakemcp import Fake, Host

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
CATALOG = FIXTURES / "catalog"
LIVE = FIXTURES / "live"

LINEAR = "mcp.linear.app"
CLOSE = "mcp.close.com"
GMAIL = "gmail.googleapis.com"
ELSEWHERE = "elsewhere.example.com"
HOSTS = (LINEAR, CLOSE, GMAIL, ELSEWHERE)

LINEAR_BEARER = "fixture-bearer-for-the-linear-tests"
CLOSE_KEY = "fixture-close-api-key-for-tests"
GMAIL_BEARER = "fixture-bearer-for-the-gmail-tests"
SPEC_PATH = "/$discovery/rest?version=v1"
PROFILE_PATH = "/gmail/v1/users/me/profile"


def _openssl() -> str:
    path = shutil.which("openssl")
    if path is None:
        pytest.fail(
            "the tests need the openssl command to make a local test certificate"
        )
    return path


@pytest.fixture(scope="session")
def tls(tmp_path_factory):
    """(server context, client context) for a self-signed certificate that
    names every fixture host."""
    d = tmp_path_factory.mktemp("tls")
    cert, key = d / "cert.pem", d / "key.pem"
    san = ",".join(f"DNS:{h}" for h in HOSTS)
    subprocess.run(
        [
            _openssl(),
            "req",
            "-x509",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:prime256v1",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "2",
            "-subj",
            "/CN=connector-smoke-tests",
            "-addext",
            f"subjectAltName={san}",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,digitalSignature,keyCertSign",
        ],
        check=True,
        capture_output=True,
    )
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert, key)
    client = ssl.create_default_context(cafile=str(cert))
    return server, client


@pytest.fixture(scope="session")
def _server(tls):
    fake = Fake(tls[0])
    yield fake
    fake.close()


@pytest.fixture
def fake(_server):
    _server.reset()
    yield _server
    _server.reset()


class Route:
    """Routes the fixture hosts to the fake; any other host is refused and
    remembered, so a test can prove nothing else was dialled."""

    def __init__(self, port: int):
        self.port = port
        self.refused = []

    def __call__(self, host: str, port: int):
        if host not in HOSTS or port != 443:
            self.refused.append((host, port))
            raise transport.TransportError("unrouted_in_test")
        return "127.0.0.1", self.port


@pytest.fixture
def route(fake):
    return Route(fake.port)


@pytest.fixture
def net(tls, route):
    return transport.Net(ssl_context=tls[1], route=route)


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """A test that forgets its Net fails instead of dialling out."""

    def refuse(host, port, deadline):
        raise AssertionError(f"a test tried to reach a real address for {host}")

    monkeypatch.setattr(transport, "_dial_public", refuse)


def live_tools() -> list:
    return json.loads((LIVE / "linear-tools.json").read_text())["tools"]


def issues_result(issue: dict) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps({"issues": [issue]})}],
        "isError": False,
    }


FULL_ISSUE = {
    "id": "ISS-42",
    "title": "Fix the login page",
    "state": {"name": "Todo", "type": "unstarted"},
    "assignee": {"email": "someone@vendor.example"},
    "labels": [{"name": "bug"}],
    "priority": 2,
}


def linear_host(**overrides) -> Host:
    host = Host(
        tools=live_tools(),
        results={
            "list_issues": issues_result(FULL_ISSUE),
            "create_issue": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"issue": {"id": "ISS-43", "title": "x"}}),
                    }
                ]
            },
            "delete_issue": {
                "content": [{"type": "text", "text": json.dumps({"deleted": True})}]
            },
            "archive_issue": {"content": [{"type": "text", "text": "archived"}]},
        },
        require={"Authorization": f"Bearer {LINEAR_BEARER}"},
    )
    for k, v in overrides.items():
        setattr(host, k, v)
    return host


def close_host() -> Host:
    return Host(
        tools=[{"name": "search_leads", "inputSchema": {"type": "object"}}],
        require={"Close-API-Key": CLOSE_KEY, "Close-Scope": "mcp.read"},
    )


def gmail_host(spec: bytes | None = None) -> Host:
    body = (LIVE / "gmail-discovery.json").read_bytes() if spec is None else spec
    return Host(
        get={
            SPEC_PATH: (200, {"Content-Type": "application/json"}, body),
            PROFILE_PATH: (
                200,
                {"Content-Type": "application/json"},
                b'{"emailAddress": "someone@vendor.example"}',
            ),
        },
        require={},
    )


def entry(account: str = "fa-ci-linear", **fields) -> dict:
    return {"testAccount": account, **fields}


def keys_doc(providers: dict) -> str:
    """A keys document for {"<app>/<provider>": entry}."""
    return json.dumps({"format": "connector-smoke-keys/v1", "providers": providers})


def env_with(providers: dict, live: bool = True) -> dict:
    env = {"CONNECTOR_SMOKE_KEYS": keys_doc(providers)}
    if live:
        env["CONNECTOR_SMOKE_LIVE"] = "1"
    return env


def argv(out: Path, *extra) -> list:
    return ["--out", str(out), "--catalog", str(CATALOG), *extra]


LIVE_ENV = {"CONNECTOR_SMOKE_LIVE": "1"}


def run(argv, env: dict, net=None):
    """Run the command in-process: (exit code, its output)."""
    out = io.StringIO()
    code = cli.main(argv, environ=env, net=net, out=out)
    return code, out.getvalue()


def report_of(out_dir: Path) -> dict:
    return json.loads((out_dir / "drift-report.json").read_text())


def provider_of(report: dict, key: str) -> dict:
    app, prov = key.split("/")
    return next(
        p for p in report["providers"] if p["app"] == app and p["provider"] == prov
    )


def checks(provider: dict, name: str) -> list:
    return [c for c in provider.get("checks", []) if c["check"] == name]
