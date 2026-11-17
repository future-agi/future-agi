"""Tests for ``GET /api/setup-checks/`` — the OSS first-run infrastructure probe.

Probe results are injected by patching ``_run_probes`` rather than by patching
the individual probe functions. ``CHECKS`` captures each probe as a function
object at import time, so rebinding a module-level name like ``_clickhouse_up``
would not reach the reference the tuple already holds. Patching the one function
that produces the id -> bool mapping controls every check by construction, and
keeps these tests about reporting rather than about socket behaviour.

The view caches a snapshot for a few seconds, so every test clears the cache
first — otherwise a second request in the same test returns the first one's
verdict and the assertions pass for the wrong reason.

The code sandbox's probe is the exception that is tested end to end: it sends
a test evaluation through the client real runs use, so those tests run it
against a loopback executor and hand the verdict it returns to the view.
"""

import ast
import errno
import json
import re
import socket
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from django.core.cache import cache
from redis.exceptions import ConnectionError as RedisConnectionError
from rest_framework import status
from rest_framework.test import APIClient

from agentic_eval.core.embeddings import serving_client
from agentic_eval.core_evals.fi_utils import sandbox as code_sandbox
from tfc.views import setup_checks
from tfc.views.setup_checks import (
    ABSENT,
    CHECKS,
    DISTRIBUTED,
    EXPERIMENT,
    FAILED,
    HELM,
    LIMITED,
    LIVE,
    PASSED,
    PROBE_TIMEOUT_SECONDS,
    SKIPPED,
    STANDALONE,
    WARNING,
    _code_executor_up,
    _is_local_host,
    _model_serving_up,
    _object_storage_up,
    _safe,
    _setup,
    _tls_up,
)

REPO = Path(__file__).resolve().parents[3]
INSTALLATION = REPO / "INSTALLATION.md"
# The setup screen's client for this endpoint, and its request timeout.
SETUP_SCREEN_CLIENT = REPO / "frontend" / "src" / "api" / "ossSetup" / "oss-setup.js"
EXECUTOR_SOURCE = REPO / "futureagi" / "code-executor" / "server.py"


def _executor_constant(name):
    """A string constant of code-executor/server.py, read without importing it."""
    tree = ast.parse(EXECUTOR_SOURCE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is not in {EXECUTOR_SOURCE}")


# What the executor answers when nsjail cannot build a run's jail.
SANDBOX_CANNOT_START = _executor_constant("SANDBOX_UNAVAILABLE_MESSAGE")
TEST_EVALUATION = "def evaluate():\n    return True\n"
NO_ANSWER = "The test evaluation got no answer from the sandbox."
EXECUTOR_SUCCESS = {
    "status": "success",
    "data": {"result": 1.0, "reason": "bool"},
    "execution_time": 0.02,
}

# Bound at import, before the conftest fixture swaps in its stand-in.
REAL_SERVING_PROBE = serving_client._probe


def _github_anchor(heading):
    """GitHub's heading slug: lowercase, drop every character that is not a word
    character, a space or a hyphen, then spaces to hyphens."""
    return re.sub(r"[^\w\- ]", "", heading.lower()).strip().replace(" ", "-")


SETUP_CHECKS_URL = "/api/setup-checks/"

ALL_IDS = [c["id"] for c in CHECKS]


def all_up():
    return dict.fromkeys(ALL_IDS, True)


def all_down():
    return dict.fromkeys(ALL_IDS, False)


def down_only(*check_ids):
    results = all_up()
    for check_id in check_ids:
        results[check_id] = False
    return results


def get_checks(client, mode=None, probe_results=None, setup=DISTRIBUTED, **extra):
    """Request a snapshot with probe results and the setup forced, and return
    the result payload. ``extra`` goes to the test client (e.g. HTTP_HOST)."""
    cache.clear()
    url = SETUP_CHECKS_URL if mode is None else f"{SETUP_CHECKS_URL}?mode={mode}"
    with (
        patch(
            "tfc.views.setup_checks._run_probes",
            return_value=probe_results if probe_results is not None else all_up(),
        ),
        patch("tfc.views.setup_checks._setup", return_value=setup),
    ):
        response = client.get(url, **extra)
    assert response.status_code == status.HTTP_200_OK
    return response.json()["result"]


def by_id(result, check_id):
    return next(c for c in result["checks"] if c["id"] == check_id)


@pytest.fixture(autouse=True)
def _clear_snapshot_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _self_hosted():
    """The endpoint is self-hosted only — cloud and EE answer 404 — so every
    test here has to stand in an OSS deployment to reach the view at all."""
    with patch("tfc.views.setup_checks.is_oss", return_value=True):
        yield


@pytest.mark.integration
@pytest.mark.api
class TestSetupChecksResponseShape:
    def test_requires_no_authentication(self, api_client):
        """The screen runs before the first account exists, so auth is impossible."""
        with patch("tfc.views.setup_checks._run_probes", return_value=all_up()):
            response = api_client.get(SETUP_CHECKS_URL)

        assert response.status_code == status.HTTP_200_OK

    def test_success_envelope(self, api_client):
        with patch("tfc.views.setup_checks._run_probes", return_value=all_up()):
            body = api_client.get(SETUP_CHECKS_URL).json()

        assert body["status"] is True
        assert set(body["result"]) == {
            "status",
            "mode",
            "setup",
            "collector_http_url",
            "account_exists",
            "checks",
        }

    def test_hands_out_the_public_collector_url(self, api_client, monkeypatch):
        """What an SDK outside the stack sets as FI_BASE_URL; the installer
        moves the port when 4318 is taken."""
        monkeypatch.setattr(
            setup_checks.settings, "FI_COLLECTOR_PUBLIC_URL", "http://localhost:4319"
        )

        assert get_checks(api_client)["collector_http_url"] == "http://localhost:4319"

    def test_every_check_carries_the_contracted_fields(self, api_client):
        result = get_checks(api_client)

        for check in result["checks"]:
            assert set(check) == {
                "id",
                "label",
                "status",
                "required",
                "detail",
                "fix",
                "docs_url",
            }
            assert check["status"] in {PASSED, WARNING, FAILED, SKIPPED}
            assert isinstance(check["required"], bool)
            assert isinstance(check["detail"], str)
            assert check["label"]

    def test_returns_every_check_every_time(self, api_client):
        """The list is never filtered — a passing check still gets a row."""
        assert [c["id"] for c in get_checks(api_client)["checks"]] == ALL_IDS

    def test_check_ids_are_unique(self, api_client):
        ids = [c["id"] for c in get_checks(api_client)["checks"]]
        assert len(ids) == len(set(ids))


@pytest.mark.integration
@pytest.mark.api
class TestLaunchMode:
    def test_defaults_to_live_when_mode_is_absent(self, api_client):
        assert get_checks(api_client)["mode"] == LIVE

    def test_experiment_mode_is_echoed_back(self, api_client):
        assert get_checks(api_client, mode=EXPERIMENT)["mode"] == EXPERIMENT

    @pytest.mark.parametrize("bad_mode", ["staging", "LIVE", "", "1", "prod"])
    def test_unknown_mode_falls_back_to_live(self, api_client, bad_mode):
        """An unrecognised mode must not 400 — it degrades to the strict mode."""
        assert get_checks(api_client, mode=bad_mode)["mode"] == LIVE

    def test_falls_back_to_the_stricter_mode(self, api_client):
        """Fallback picks live, so a typo cannot silently relax the gate."""
        garbage = get_checks(api_client, mode="typo", probe_results=all_down())
        live = get_checks(api_client, mode=LIVE, probe_results=all_down())

        assert garbage["status"] == live["status"]
        assert [c["required"] for c in garbage["checks"]] == [
            c["required"] for c in live["checks"]
        ]


@pytest.mark.integration
@pytest.mark.api
class TestVerdict:
    def test_all_services_up_is_ok(self, api_client):
        result = get_checks(api_client, probe_results=all_up())

        assert result["status"] == "ok"
        assert all(c["status"] == PASSED for c in result["checks"])

    def test_required_service_down_blocks_in_live(self, api_client):
        result = get_checks(api_client, mode=LIVE, probe_results=down_only("storage"))

        assert result["status"] == "issues"
        assert by_id(result, "storage")["status"] == FAILED
        assert by_id(result, "storage")["required"] is True

    def test_same_service_down_only_warns_in_experiment(self, api_client):
        """Experimenting must stay unblocked by an optional service."""
        result = get_checks(
            api_client, mode=EXPERIMENT, probe_results=down_only("storage")
        )

        assert result["status"] == "ok"
        assert by_id(result, "storage")["status"] == WARNING
        assert by_id(result, "storage")["required"] is False

    def test_every_check_is_required_in_live(self, api_client):
        """Live mode draws no line between stack-level and feature-level: a
        deployment serving real traffic is expected to have all of it. Anything
        down therefore blocks, and experiment mode is where that relaxes. A
        service the install does not run at all is ABSENT, not down; see
        TestSkipped."""
        result = get_checks(
            api_client, mode=LIVE, probe_results=down_only("code_executor")
        )

        assert by_id(result, "code_executor")["status"] == FAILED
        assert by_id(result, "code_executor")["required"] is True
        assert result["status"] == "issues"
        assert all(c[LIVE]["required"] for c in CHECKS)

    def test_issues_requires_a_check_that_is_both_required_and_failed(self, api_client):
        for mode in (LIVE, EXPERIMENT):
            result = get_checks(api_client, mode=mode, probe_results=all_down())
            blocking = [
                c for c in result["checks"] if c["required"] and c["status"] == FAILED
            ]
            assert (result["status"] == "issues") is bool(blocking)

    def test_database_blocks_in_both_modes(self, api_client):
        """Nothing works without Postgres, so neither mode may continue past it."""
        for mode in (LIVE, EXPERIMENT):
            result = get_checks(
                api_client, mode=mode, probe_results=down_only("database")
            )
            assert result["status"] == "issues"
            assert by_id(result, "database")["required"] is True
            assert by_id(result, "database")["status"] == FAILED


@pytest.mark.integration
@pytest.mark.api
class TestCoreServicesBlockInBothModes:
    """The seven core services — Postgres, ClickHouse, AgentCC, Temporal,
    fi-collector, backend, frontend — depend on each other, so neither mode can
    start without them.

    Redis is deliberately not in this set: sessions and caching degrade without
    it but nothing else stops, so experimenting proceeds on a warning."""

    CORE = [
        "database",
        "clickhouse",
        "gateway",
        "temporal",
        "collector",
        "backend",
        "frontend",
    ]

    @pytest.mark.parametrize("check_id", CORE)
    def test_core_service_blocks_in_experiment_too(self, api_client, check_id):
        result = get_checks(
            api_client, mode=EXPERIMENT, probe_results=down_only(check_id)
        )

        assert by_id(result, check_id)["required"] is True
        assert by_id(result, check_id)["status"] == FAILED
        assert result["status"] == "issues"

    def test_redis_is_the_one_core_service_experimenting_survives(self, api_client):
        """Sessions and caching degrade, but nothing else stops, so this alone
        stays a warning."""
        result = get_checks(
            api_client, mode=EXPERIMENT, probe_results=down_only("cache")
        )

        assert by_id(result, "cache")["required"] is False
        assert by_id(result, "cache")["status"] == WARNING
        assert result["status"] == "ok"

    def test_redis_still_blocks_in_live(self, api_client):
        result = get_checks(api_client, mode=LIVE, probe_results=down_only("cache"))

        assert by_id(result, "cache")["required"] is True
        assert result["status"] == "issues"

    def test_required_always_means_blocking(self):
        """`required` and `on_down` are independent, and blocking needs both —
        so a check marked required whose on_down is WARNING would render as
        required in the UI while quietly letting the operator continue."""
        for check in CHECKS:
            for mode in (LIVE, EXPERIMENT):
                if check[mode]["required"]:
                    assert check[mode]["on_down"] == FAILED, (
                        f"{check['id']} is required in {mode} but downgrades to "
                        f"{check[mode]['on_down']}, so the flag never enforces"
                    )


@pytest.mark.integration
@pytest.mark.api
class TestSkipped:
    @pytest.mark.parametrize("check_id", ["ssl"])
    def test_service_not_run_in_experiment_is_skipped_not_failed(
        self, api_client, check_id
    ):
        """What the mode never expects to be there is expected to be down, not
        broken — experimenting locally is not a certificate misconfiguration."""
        result = get_checks(
            api_client, mode=EXPERIMENT, probe_results=down_only(check_id)
        )

        assert by_id(result, check_id)["status"] == SKIPPED
        assert by_id(result, check_id)["required"] is False

    def test_skipped_never_blocks(self, api_client):
        result = get_checks(api_client, mode=EXPERIMENT, probe_results=all_down())
        skipped = [c for c in result["checks"] if c["status"] == SKIPPED]

        assert skipped, "expected experiment mode to skip at least one service"
        assert all(not c["required"] for c in skipped)

    @pytest.mark.parametrize("mode", [LIVE, EXPERIMENT])
    @pytest.mark.parametrize(
        "setup, how_to_enable",
        [(STANDALONE, "`ml` to COMPOSE_PROFILES"), (DISTRIBUTED, "MODEL_SERVING_URL")],
    )
    def test_absent_model_serving_is_skipped_in_both_modes(
        self, api_client, mode, setup, how_to_enable
    ):
        """The standalone install only runs model serving with the `ml` profile,
        and every feature that needs it degrades on its own, so its absence is
        an install choice in either mode — never a blocker. The way to turn it
        on depends on the setup."""
        probes = all_up()
        probes["model_serving"] = ABSENT
        result = get_checks(api_client, mode=mode, probe_results=probes, setup=setup)

        check = by_id(result, "model_serving")
        assert check["status"] == SKIPPED
        assert check["required"] is False
        assert how_to_enable in check["fix"]
        assert "everything else works" in check["detail"]
        assert check["docs_url"].startswith("https://")
        assert result["status"] == "ok"

    @pytest.mark.parametrize("mode", [LIVE, EXPERIMENT])
    def test_absent_code_executor_on_helm_is_optional(self, api_client, mode):
        """The chart runs no sandbox by default and leaves CODE_EXECUTOR_URL
        empty: that is a choice, not an outage. Eval code never runs in the
        worker pods (codeExecutor.localFallback has no effect), so the fix
        offers only the sandbox."""
        probes = all_up()
        probes["code_executor"] = ABSENT
        result = get_checks(api_client, mode=mode, probe_results=probes, setup=HELM)

        check = by_id(result, "code_executor")
        assert check["status"] == SKIPPED
        assert check["required"] is False
        assert "codeExecutor.enabled=true" in check["fix"]
        assert "localFallback" not in check["fix"]
        assert result["status"] == "ok"

    def test_an_empty_code_executor_url_is_absent_without_a_probe(self, monkeypatch):
        monkeypatch.setenv("CODE_EXECUTOR_URL", "")
        with patch.object(setup_checks, "_http_ok") as http_ok:
            assert setup_checks._code_executor_up() == ABSENT
        http_ok.assert_not_called()

    @pytest.mark.parametrize(
        "mode, status_when_down, blocks",
        [(LIVE, FAILED, True), (EXPERIMENT, WARNING, False)],
    )
    def test_deployed_model_serving_that_is_down_is_not_skipped(
        self, api_client, mode, status_when_down, blocks
    ):
        """The distributed stack and the `ml` profile run serving; a crashed or
        unhealthy container there is an outage, not an opt-out."""
        result = get_checks(
            api_client, mode=mode, probe_results=down_only("model_serving")
        )

        check = by_id(result, "model_serving")
        assert check["status"] == status_when_down
        assert check["required"] is blocks
        assert "up -d serving" in check["fix"]
        assert "COMPOSE_PROFILES" not in check["fix"]
        assert (result["status"] == "issues") is blocks

    def test_absent_is_down_for_a_check_that_cannot_be_optional(self, api_client):
        """Only a check with an ``absent`` block may be skipped as not
        deployed; any other probe answering ABSENT reads as down."""
        probes = all_up()
        probes["cache"] = ABSENT
        result = get_checks(api_client, mode=LIVE, probe_results=probes)

        assert by_id(result, "cache")["status"] == FAILED
        assert result["status"] == "issues"

    def test_the_absent_verdict_survives_the_fail_closed_wrapper(self):
        assert _safe(lambda: ABSENT) == ABSENT
        assert _safe(lambda: "anything else") is True


@pytest.mark.integration
@pytest.mark.api
class TestDetail:
    def test_detail_is_empty_when_the_check_passed(self, api_client):
        result = get_checks(api_client, probe_results=all_up())

        assert all(c["detail"] == "" for c in result["checks"])

    def test_detail_explains_what_breaks_when_down(self, api_client):
        result = get_checks(api_client, mode=LIVE, probe_results=down_only("storage"))

        assert by_id(result, "storage")["detail"]

    def test_detail_does_not_vary_by_mode(self, api_client):
        """One string per check. The mode changes the verdict, never the wording."""
        live = get_checks(api_client, mode=LIVE, probe_results=all_down())
        experiment = get_checks(api_client, mode=EXPERIMENT, probe_results=all_down())

        assert {c["id"]: c["detail"] for c in live["checks"]} == {
            c["id"]: c["detail"] for c in experiment["checks"]
        }

    def test_fix_is_empty_when_the_check_passed(self, api_client):
        result = get_checks(api_client)

        assert all(c["fix"] == "" for c in result["checks"])
        assert all(c["docs_url"] == "" for c in result["checks"])

    def test_fix_and_docs_url_are_served_when_down(self, api_client):
        result = get_checks(api_client, probe_results=all_down())

        for check in result["checks"]:
            assert check["fix"], f"{check['id']} came back down with no fix"
            assert check["docs_url"].startswith("https://")

    def test_fix_does_not_vary_by_mode(self, api_client):
        live = get_checks(api_client, mode=LIVE, probe_results=all_down())
        experiment = get_checks(api_client, mode=EXPERIMENT, probe_results=all_down())

        assert {c["id"]: c["fix"] for c in live["checks"]} == {
            c["id"]: c["fix"] for c in experiment["checks"]
        }

    def test_detail_never_mentions_the_launch_mode(self, api_client):
        """Copy is shared across modes, so mode wording would be wrong in one."""
        result = get_checks(api_client, mode=LIVE, probe_results=all_down())

        for check in result["checks"]:
            assert "mode" not in check["detail"].lower()
            assert "experiment" not in check["detail"].lower()


@pytest.mark.integration
@pytest.mark.api
class TestFailsClosed:
    def test_a_probe_that_raises_reports_down_not_500(self, api_client):
        """One broken probe must never take out the whole screen."""
        cache.clear()

        def exploding_probe():
            raise RuntimeError("connection refused")

        with patch.dict(
            CHECKS[1], {"probe": exploding_probe}
        ):  # any network-backed check
            response = api_client.get(f"{SETUP_CHECKS_URL}?mode={LIVE}")

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert by_id(result, CHECKS[1]["id"])["status"] == FAILED

    def test_missing_probe_result_is_treated_as_down(self, api_client):
        """A probe absent from the mapping must not be reported as healthy."""
        result = get_checks(api_client, mode=LIVE, probe_results={})

        assert all(c["status"] != PASSED for c in result["checks"])


@pytest.mark.integration
@pytest.mark.api
class TestObjectStorageProbe:
    def test_a_bucket_that_does_not_exist_yet_is_not_an_outage(self):
        """The upload bucket is created on the first upload, so a fresh install
        has none and every operator saw a red row with nothing behind it."""
        missing = ClientError(
            {"ResponseMetadata": {"HTTPStatusCode": 404}}, "HeadBucket"
        )

        with patch("tfc.views.setup_checks.boto3.client") as factory:
            factory.return_value.head_bucket.side_effect = missing

            assert _object_storage_up() is True

    def test_an_endpoint_that_does_not_answer_is_down(self):
        with patch("tfc.views.setup_checks.boto3.client") as factory:
            factory.return_value.head_bucket.side_effect = EndpointConnectionError(
                endpoint_url="http://minio:9000"
            )

            assert _safe(_object_storage_up) is False


@pytest.fixture
def health_server():
    """A loopback server answering ``GET /health`` like the in-container
    executor (and serving) do. Yields its base URL."""

    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/health" else 404)
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _closed_port_url():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{sock.getsockname()[1]}"


@pytest.fixture
def executor(monkeypatch):
    """A loopback code executor serving ``GET /health`` and ``POST /execute``
    as code-executor/server.py does. Set ``health`` (the /health body) and
    ``answer`` (the /execute status code, body and delay in seconds) before
    probing; every /execute request body lands in ``received``.

    CODE_EXECUTOR_URL points at it both where the probe reads it (the
    environment) and where the run client bound it at import."""
    state = SimpleNamespace(
        health={"status": "ok", "nsjail": True, "node": "/usr/bin/node"},
        answer=(200, EXECUTOR_SUCCESS, 0),
        received=[],
        # Ends a slow answer's wait at teardown, so no request outlives the test.
        released=threading.Event(),
    )

    class Executor(BaseHTTPRequestHandler):
        def _reply(self, code, body):
            payload = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path == "/health":
                self._reply(200, state.health)
            else:
                self._reply(404, {"status": "error"})

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path != "/execute":
                self._reply(404, {"status": "error"})
                return
            state.received.append(json.loads(body))
            code, answer, delay = state.answer
            state.released.wait(delay)
            try:
                self._reply(code, answer)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the probe stopped waiting

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Executor)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setenv("CODE_EXECUTOR_URL", state.url)
    monkeypatch.setattr(code_sandbox, "CODE_EXECUTOR_URL", state.url)
    yield state
    state.released.set()
    server.shutdown()
    server.server_close()


def _failed_run(executor, sentence):
    """The verdict the real probe returns for a run the executor refuses with
    ``sentence``."""
    executor.answer = (200, {"status": "error", "data": sentence}, 0)
    return _safe(_code_executor_up)


@pytest.mark.integration
@pytest.mark.api
class TestCodeExecutorProbe:
    def test_passes_when_the_executor_runs_the_test_evaluation(
        self, monkeypatch, executor
    ):
        """The executor answers /health and runs the test evaluation; a trailing
        slash on CODE_EXECUTOR_URL changes nothing."""
        monkeypatch.setenv("CODE_EXECUTOR_URL", f"{executor.url}/")

        assert _code_executor_up() is True

    def test_nothing_listening_is_down(self, monkeypatch):
        monkeypatch.setenv("CODE_EXECUTOR_URL", _closed_port_url())

        assert _safe(_code_executor_up) is False

    def test_an_executor_without_node_is_limited(self, executor):
        """An executor without Node.js answers ``"node": null`` on /health."""
        executor.health = {"status": "ok", "nsjail": False, "node": None}
        assert _safe(_code_executor_up) == LIMITED

        executor.health = {"status": "ok", "node": "/usr/bin/node"}
        assert _safe(_code_executor_up) is True

    @pytest.mark.parametrize("mode", [LIVE, EXPERIMENT])
    def test_limited_passes_and_says_how_to_run_javascript(self, api_client, mode):
        results = all_up()
        results["code_executor"] = LIMITED

        standalone = get_checks(
            api_client, mode=mode, probe_results=results, setup=STANDALONE
        )
        distributed = get_checks(api_client, mode=mode, probe_results=results)

        check = by_id(standalone, "code_executor")
        assert check["status"] == PASSED
        assert "JavaScript" in check["detail"]
        # The shipped code-executor service has Node.js; no profile adds it.
        assert "code-executor" in check["detail"]
        assert "COMPOSE_PROFILES" not in check["detail"]
        assert check["fix"] == ""
        assert standalone["status"] == "ok"
        assert "Node.js" in by_id(distributed, "code_executor")["detail"]


@pytest.mark.integration
@pytest.mark.api
class TestCodeExecutorTestRun:
    """A sandbox can answer /health on a host where it cannot start a single
    run, so the probe also sends it one test evaluation through the client
    real runs use, and the row says what that run said."""

    def test_the_probe_sends_one_test_evaluation_through_the_run_client(
        self, monkeypatch, executor
    ):
        waits = []
        real_urlopen = urllib.request.urlopen

        def urlopen(request, *args, **kwargs):
            waits.append(kwargs.get("timeout"))
            return real_urlopen(request, *args, **kwargs)

        monkeypatch.setattr(urllib.request, "urlopen", urlopen)

        assert _safe(_code_executor_up) is True

        assert len(executor.received) == 1
        sent = executor.received[0]
        assert sent["code"] == TEST_EVALUATION
        assert sent["language"] == "python"
        assert sent["input_data"] == {}
        assert sent["timeout"] == getattr(setup_checks, "TEST_RUN_LIMIT_SECONDS", None)
        assert waits == [getattr(setup_checks, "TEST_RUN_ANSWER_SECONDS", None)]

    def test_a_run_the_sandbox_cannot_start_carries_the_run_s_own_sentence(
        self, executor
    ):
        verdict = _failed_run(executor, SANDBOX_CANNOT_START)

        assert not verdict
        assert getattr(verdict, "reason", None) == SANDBOX_CANNOT_START

    @pytest.mark.parametrize(
        "answer",
        [
            pytest.param((500, {"status": "error"}, 0), id="http-error"),
            pytest.param((200, b"<html>not json</html>", 0), id="not-json"),
            pytest.param((200, EXECUTOR_SUCCESS, 1.5), id="too-slow"),
        ],
    )
    def test_no_usable_answer_is_one_plain_sentence(
        self, monkeypatch, executor, answer
    ):
        monkeypatch.setattr(setup_checks, "TEST_RUN_ANSWER_SECONDS", 0.3, raising=False)
        executor.answer = answer

        verdict = _safe(_code_executor_up)

        assert not verdict
        assert getattr(verdict, "reason", None) == NO_ANSWER

    def test_a_run_client_that_raises_is_no_answer_not_a_down_sandbox(
        self, monkeypatch, executor
    ):
        """A raise would otherwise read as down, which blocks Production."""

        def broken(*args, **kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(code_sandbox, "execute_sandboxed_python", broken)
        logger = Mock()
        monkeypatch.setattr(setup_checks, "logger", logger, raising=False)

        assert getattr(_safe(_code_executor_up), "reason", None) == NO_ANSWER
        # The raise is a fault in the probe, not an answer: it is logged.
        logger.exception.assert_called_once()

    def test_limited_only_when_the_test_run_succeeds(self, executor):
        executor.health = {"status": "ok", "nsjail": False, "node": None}

        verdict = _failed_run(executor, SANDBOX_CANNOT_START)

        assert verdict != LIMITED
        assert getattr(verdict, "reason", None) == SANDBOX_CANNOT_START

    @pytest.mark.parametrize("mode", [LIVE, EXPERIMENT])
    @pytest.mark.parametrize("setup", [STANDALONE, DISTRIBUTED, HELM])
    def test_a_run_that_cannot_start_warns_in_both_modes_and_never_blocks(
        self, api_client, executor, mode, setup
    ):
        probes = all_up()
        probes["code_executor"] = _failed_run(executor, SANDBOX_CANNOT_START)

        result = get_checks(api_client, mode=mode, probe_results=probes, setup=setup)

        check = by_id(result, "code_executor")
        assert check["status"] == WARNING
        assert check["required"] is False
        assert result["status"] == "ok"
        assert check["detail"] == (
            f"Custom code evaluations will not run: {SANDBOX_CANNOT_START}"
        )
        assert "kernel.apparmor_restrict_unprivileged_userns=1" in check["fix"]
        assert check["docs_url"].startswith("https://")

    @pytest.mark.parametrize("setup", [STANDALONE, DISTRIBUTED])
    def test_the_fix_names_both_host_settings_and_where_they_are_changed(
        self, api_client, executor, setup
    ):
        probes = all_up()
        probes["code_executor"] = _failed_run(executor, SANDBOX_CANNOT_START)

        result = get_checks(api_client, probe_results=probes, setup=setup)
        fix = by_id(result, "code_executor")["fix"]

        # The install page's sentence for the two settings, then where each
        # one is changed.
        assert (
            "Ubuntu 23.10 and later restrict unprivileged user namespaces by "
            "default (`kernel.apparmor_restrict_unprivileged_userns=1`), and a "
            "Docker daemon configured with `userns-remap` puts every container "
            "inside a remapped user namespace."
        ) in fix
        assert "sysctl on the Docker host" in fix
        assert "Docker daemon's config" in fix

    def test_no_answer_reads_as_a_warning_too(self, api_client, monkeypatch, executor):
        monkeypatch.setattr(setup_checks, "TEST_RUN_ANSWER_SECONDS", 0.3, raising=False)
        executor.answer = (502, b"", 0)
        probes = all_up()
        probes["code_executor"] = _safe(_code_executor_up)

        check = by_id(get_checks(api_client, probe_results=probes), "code_executor")

        assert check["status"] == WARNING
        assert check["detail"] == f"Custom code evaluations will not run: {NO_ANSWER}"

    def test_the_warning_status_is_set_in_one_place(
        self, api_client, monkeypatch, executor
    ):
        """Making it block is a one-line change, and it then blocks for real."""
        check = next(c for c in CHECKS if c["id"] == "code_executor")
        assert check["cannot_run"]["status"] == WARNING

        monkeypatch.setitem(check["cannot_run"], "status", FAILED)
        probes = all_up()
        probes["code_executor"] = _failed_run(executor, SANDBOX_CANNOT_START)
        result = get_checks(api_client, mode=EXPERIMENT, probe_results=probes)

        assert by_id(result, "code_executor")["status"] == FAILED
        assert by_id(result, "code_executor")["required"] is True
        assert result["status"] == "issues"

    def test_the_test_run_fits_inside_the_setup_screen_s_request(self):
        """Probes run side by side, so the code sandbox's /health and its test
        run must answer well inside the screen's request timeout."""
        client = SETUP_SCREEN_CLIENT.read_text(encoding="utf-8")
        match = re.search(r"axios\.create\(\{[^}]*\btimeout:\s*(\d+)", client)
        assert match, f"no request timeout in {SETUP_SCREEN_CLIENT}"
        screen_seconds = int(match.group(1)) / 1000
        limit = getattr(setup_checks, "TEST_RUN_LIMIT_SECONDS", None)
        answer = getattr(setup_checks, "TEST_RUN_ANSWER_SECONDS", None)

        assert limit is not None and answer is not None
        # The executor stops the run before the probe stops waiting for it.
        assert limit < answer
        assert PROBE_TIMEOUT_SECONDS + answer <= screen_seconds - 2

    def test_real_runs_keep_their_own_wait(self, monkeypatch):
        """Only the probe passes a shorter wait; a real run still waits for
        its own limit plus 10 s."""
        waits = []

        def refused(request, timeout=None):
            waits.append(timeout)
            raise urllib.error.URLError(
                ConnectionRefusedError(errno.ECONNREFUSED, "refused")
            )

        monkeypatch.setattr(urllib.request, "urlopen", refused)
        monkeypatch.setattr(code_sandbox, "CODE_EXECUTOR_URL", "http://127.0.0.1:9")

        code_sandbox.execute_sandboxed_python(TEST_EVALUATION, {}, timeout=30)

        assert waits == [40]


COMPOSE_FILES = (REPO / "docker-compose.yml", REPO / "docker-compose.distributed.yml")


def _compose_service(path, name):
    """The lines of one service in a compose file, comments included."""
    lines = path.read_text(encoding="utf-8").splitlines()
    block = []
    for line in lines[lines.index(f"  {name}:") + 1 :]:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped and not stripped.startswith("#") and indent < 4:
            break
        block.append(line)
    return "\n".join(block)


@pytest.mark.integration
@pytest.mark.api
class TestCodeExecutorTextsMatchTheShippedSetup:
    """Both compose files run `code-executor` with the stack, not privileged,
    from an image with Node.js; on Helm it is opt-in and the cluster must
    allow unprivileged user namespaces. The row's texts say that, not the
    profile, the in-app sandbox and the privileged container they named."""

    STALE = (
        "privileged: true",
        "COMPOSE_PROFILES=sandbox",
        "restart app",
        "built-in sandbox",
        "localFallback",
    )

    def test_the_facts_the_texts_rest_on(self):
        for path in COMPOSE_FILES:
            service = _compose_service(path, "code-executor")
            assert "profiles:" not in service, path.name
            assert "privileged: true" not in service, path.name
        base = REPO / "futureagi" / "code-executor" / "Dockerfile.base"
        assert "nodejs" in base.read_text(encoding="utf-8")

    @pytest.mark.parametrize("setup", [STANDALONE, DISTRIBUTED, HELM])
    def test_no_code_executor_text_names_the_old_setup(self, api_client, setup):
        for verdict in (False, LIMITED, ABSENT):
            probes = all_up()
            probes["code_executor"] = verdict
            result = get_checks(api_client, probe_results=probes, setup=setup)
            row = by_id(result, "code_executor")
            for stale in self.STALE:
                assert stale not in row["detail"] + row["fix"], (verdict, stale)

    @pytest.mark.parametrize("setup", [STANDALONE, DISTRIBUTED])
    def test_a_down_sandbox_on_compose_is_the_code_executor_service(
        self, api_client, setup
    ):
        probes = down_only("code_executor")
        result = get_checks(api_client, probe_results=probes, setup=setup)
        fix = by_id(result, "code_executor")["fix"]

        assert "docker compose up -d code-executor" in fix
        # The install page's one line on the host it needs.
        assert "`code-executor` is not privileged" in fix
        assert "Ubuntu 23.10+" in fix
        assert "`userns-remap`" in fix

    def test_a_down_sandbox_on_helm_names_its_value_and_what_the_cluster_needs(
        self, api_client
    ):
        probes = down_only("code_executor")
        result = get_checks(api_client, probe_results=probes, setup=HELM)
        fix = by_id(result, "code_executor")["fix"]

        assert "codeExecutor.enabled=true" in fix
        assert "unprivileged user namespaces" in fix


@pytest.mark.integration
@pytest.mark.api
class TestModelServingProbe:
    """The row uses the same probe as the embedding features, so these run the
    real HTTP probe rather than the stand-in conftest installs."""

    @pytest.fixture(autouse=True)
    def _real_probe(self, monkeypatch):
        monkeypatch.setattr(serving_client, "_probe", REAL_SERVING_PROBE)

    def test_up_when_health_answers(self, monkeypatch, health_server):
        monkeypatch.setenv("MODEL_SERVING_URL", health_server)

        assert _model_serving_up() is True

    def test_down_when_nothing_listens(self, monkeypatch):
        monkeypatch.setenv("MODEL_SERVING_URL", _closed_port_url())

        assert _model_serving_up() is False

    def test_an_empty_url_is_absent_without_a_probe(self, monkeypatch):
        """``MODEL_SERVING_URL=`` switches serving off outright."""
        monkeypatch.setenv("MODEL_SERVING_URL", "")
        with patch.object(serving_client, "_probe") as probe:
            assert _model_serving_up() == ABSENT
        probe.assert_not_called()

    @pytest.fixture
    def no_serving_host(self, monkeypatch):
        monkeypatch.setenv("MODEL_SERVING_URL", "http://serving:8080")

        def no_such_host(host, *args, **kwargs):
            assert host == "serving"
            raise socket.gaierror(socket.EAI_NONAME, "Name does not resolve")

        monkeypatch.setattr(setup_checks.socket, "getaddrinfo", no_such_host)

    def test_default_install_host_that_does_not_resolve_is_absent(
        self, monkeypatch, no_serving_host
    ):
        """The standalone install's `serving` host exists only with the `ml`
        profile. Its API runs the Temporal worker in-process."""
        monkeypatch.setenv("FI_EMBEDDED_TEMPORAL_WORKER", "true")
        with patch.object(serving_client, "_probe") as probe:
            assert _model_serving_up() == ABSENT
        probe.assert_not_called()

    def test_full_install_host_that_does_not_resolve_is_down(
        self, monkeypatch, no_serving_host
    ):
        """The distributed install always runs serving; a host that does not
        resolve there is a stopped or crash-looping container."""
        monkeypatch.delenv("FI_EMBEDDED_TEMPORAL_WORKER", raising=False)
        with patch.object(serving_client, "_probe", return_value=False) as probe:
            assert _model_serving_up() is False
        probe.assert_called_once_with("http://serving:8080")

    def test_ignores_a_cached_verdict(self, monkeypatch, health_server):
        """The screen polls while the stack starts; a stale 'down' from an
        embedding call must not hide serving coming up."""
        monkeypatch.setenv("MODEL_SERVING_URL", health_server)
        serving_client.mark_serving_unavailable()

        assert _model_serving_up() is True


@pytest.mark.integration
@pytest.mark.api
class TestSnapshotCache:
    def test_repeated_requests_reuse_the_snapshot(self, api_client):
        cache.clear()
        with patch(
            "tfc.views.setup_checks._run_probes", return_value=all_up()
        ) as probes:
            api_client.get(SETUP_CHECKS_URL)
            api_client.get(SETUP_CHECKS_URL)

        assert probes.call_count == 1

    def test_modes_do_not_share_a_snapshot(self, api_client):
        """live and experiment report the same outage differently, so each caches
        separately — otherwise switching modes would show the other's verdict."""
        cache.clear()
        with patch(
            "tfc.views.setup_checks._run_probes", return_value=down_only("storage")
        ):
            live = api_client.get(f"{SETUP_CHECKS_URL}?mode={LIVE}").json()["result"]
            experiment = api_client.get(f"{SETUP_CHECKS_URL}?mode={EXPERIMENT}").json()[
                "result"
            ]

        assert live["status"] == "issues"
        assert experiment["status"] == "ok"


# How long a test thread waits on another before the test fails.
WAIT_SECONDS = 5


def held_first_run(first, later, hold):
    """A ``_run_probes`` stand-in. The first call sets ``started``, stays in
    progress until ``hold()`` returns, then answers ``first``; later calls
    answer ``later`` at once. ``calls`` gets one entry per call."""
    started = threading.Event()
    calls = []

    def run_probes(request_host=None):
        calls.append(request_host)
        if len(calls) > 1:
            return later
        started.set()
        hold()
        return first

    return run_probes, started, calls


class CacheDown:
    """The view's cache with Redis down: every call raises as django-redis
    does. ``calls`` names each call made."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, method):
        def unavailable(*args, **kwargs):
            self.calls.append(method)
            raise RedisConnectionError("Connection refused")

        return unavailable


@pytest.mark.integration
@pytest.mark.api
class TestOneProbeRunAtATime:
    """A miss runs every probe, a jailed test evaluation among them, so requests
    that miss together share one run."""

    def test_two_concurrent_misses_start_one_run(self, api_client):
        both_missed = threading.Barrier(2, timeout=WAIT_SECONDS)
        run_probes, started, calls = held_first_run(
            down_only("storage"), all_up(), both_missed.wait
        )
        real_cached_snapshot = setup_checks._cached_snapshot
        second_lookup = []

        def cached_snapshot(cache_key):
            found = real_cached_snapshot(cache_key)
            if started.is_set() and not second_lookup:
                # The first request is inside its run, so this lookup is the
                # second request's, made before the run has stored anything.
                second_lookup.append(found)
                both_missed.wait()
            return found

        with (
            patch("tfc.views.setup_checks._run_probes", side_effect=run_probes),
            patch("tfc.views.setup_checks._cached_snapshot", cached_snapshot),
            patch("tfc.views.setup_checks._account_exists", return_value=False),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            first = pool.submit(APIClient().get, SETUP_CHECKS_URL)
            assert started.wait(WAIT_SECONDS)
            second = api_client.get(SETUP_CHECKS_URL)
            first_response = first.result(timeout=WAIT_SECONDS)

        assert second_lookup == [None]
        assert len(calls) == 1
        assert second.status_code == status.HTTP_200_OK
        assert second.json()["result"] == first_response.json()["result"]

    def test_a_request_that_loses_the_lock_serves_the_last_snapshot(self, api_client):
        # Expired at once, so the next request misses and starts a run.
        with (
            patch("tfc.views.setup_checks.SNAPSHOT_TTL_SECONDS", 0),
            patch(
                "tfc.views.setup_checks._run_probes",
                return_value=down_only("storage"),
            ),
        ):
            last = api_client.get(SETUP_CHECKS_URL).json()["result"]
        assert last["status"] == "issues"

        finish = threading.Event()
        run_probes, started, calls = held_first_run(
            all_up(), all_up(), lambda: finish.wait(WAIT_SECONDS)
        )
        with (
            patch("tfc.views.setup_checks._run_probes", side_effect=run_probes),
            patch("tfc.views.setup_checks._account_exists", return_value=False),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            first = pool.submit(APIClient().get, SETUP_CHECKS_URL)
            try:
                assert started.wait(WAIT_SECONDS)
                second = api_client.get(SETUP_CHECKS_URL)
            finally:
                finish.set()
            first_result = first.result(timeout=WAIT_SECONDS).json()["result"]

        assert len(calls) == 1
        assert second.json()["result"] == last
        assert first_result["status"] == "ok"

    def test_a_request_that_loses_the_lock_before_any_snapshot_waits_for_the_run(
        self, api_client
    ):
        finish = threading.Event()
        run_probes, started, calls = held_first_run(
            down_only("storage"), all_up(), lambda: finish.wait(WAIT_SECONDS)
        )
        with (
            patch("tfc.views.setup_checks._run_probes", side_effect=run_probes),
            patch("tfc.views.setup_checks._account_exists", return_value=False),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            first = pool.submit(APIClient().get, SETUP_CHECKS_URL)

            def end_the_run(_seconds):
                finish.set()
                first.result(timeout=WAIT_SECONDS)

            try:
                assert started.wait(WAIT_SECONDS)
                with patch("time.sleep", side_effect=end_the_run):
                    second = api_client.get(SETUP_CHECKS_URL)
            finally:
                finish.set()
            first_response = first.result(timeout=WAIT_SECONDS)

        assert len(calls) == 1
        assert second.json()["result"] == first_response.json()["result"]

    def test_a_run_that_raises_releases_the_lock(self, api_client):
        with (
            patch(
                "tfc.views.setup_checks._run_probes",
                side_effect=RuntimeError("probe run failed"),
            ),
            pytest.raises(RuntimeError),
        ):
            api_client.get(SETUP_CHECKS_URL)

        with (
            patch(
                "tfc.views.setup_checks._run_probes", return_value=all_up()
            ) as probes,
            patch(
                "time.sleep",
                side_effect=AssertionError("waited for a run that had failed"),
            ),
        ):
            response = api_client.get(SETUP_CHECKS_URL)

        assert response.status_code == status.HTTP_200_OK
        probes.assert_called_once()

    def test_a_cache_that_raises_still_answers_without_waiting(self, api_client):
        cache_down = CacheDown()
        with (
            patch("tfc.views.setup_checks.cache", cache_down),
            patch(
                "tfc.views.setup_checks._run_probes", return_value=down_only("cache")
            ) as probes,
            patch(
                "time.sleep",
                side_effect=AssertionError("waited on a cache that is down"),
            ),
        ):
            response = api_client.get(SETUP_CHECKS_URL)

        assert response.status_code == status.HTTP_200_OK
        assert by_id(response.json()["result"], "cache")["status"] == FAILED
        probes.assert_called_once()
        # Each call to an unreachable Redis can take its socket timeout.
        assert len(cache_down.calls) <= 2


@pytest.mark.integration
@pytest.mark.api
class TestCheckInventory:
    """Regressions for checks that were deliberately removed."""

    @pytest.mark.parametrize("removed_id", ["ports", "email"])
    def test_unobservable_checks_are_not_reported(self, api_client, removed_id):
        """These were dropped because neither could be probed from the backend —
        ports describe how the browser arrived, and mail delivery can only be
        read from config. Re-adding one belongs in deployment-info."""
        assert removed_id not in ALL_IDS

    @pytest.mark.parametrize("check_id", ["backend", "frontend"])
    def test_backend_and_frontend_always_pass(self, check_id):
        """Both are true by construction: one answered the request, the other
        rendered the screen. They exist so the list shows the whole stack.

        Asserted on the probe itself rather than through a response — the
        guarantee lives in the probe, and every other test here injects probe
        results, which would paper straight over it."""
        check = next(c for c in CHECKS if c["id"] == check_id)

        assert check["probe"]() is True

    def test_every_check_declares_both_modes(self):
        for check in CHECKS:
            for mode in (LIVE, EXPERIMENT):
                assert mode in check, f"{check['id']} is missing {mode}"
                assert "required" in check[mode]
                assert "on_down" in check[mode]

    def test_every_check_declares_a_fix_and_a_docs_url(self):
        for check in CHECKS:
            assert check.get("fix"), f"{check['id']} has no fix line"
            assert check.get("docs_url", "").startswith("https://"), (
                f"{check['id']} has no docs link"
            )

    def test_a_fix_written_per_setup_covers_every_setup(self):
        """A per-setup fix with a setup missing would render an empty remedy."""
        for check in CHECKS:
            for fix in (
                check["fix"],
                check.get("absent", {}).get("fix", ""),
                check.get("cannot_run", {}).get("fix", ""),
            ):
                if isinstance(fix, dict):
                    assert set(fix) == {STANDALONE, DISTRIBUTED, HELM}, check["id"]
                    assert all(isinstance(v, str) and v for v in fix.values())

    def test_every_docs_url_points_at_a_heading_that_exists(self):
        """A dead anchor drops the operator at the top of a page instead of at
        the service that failed, which is the bug this screen is fixing."""
        headings = {
            _github_anchor(line.lstrip("#").strip())
            for line in INSTALLATION.read_text(encoding="utf-8").splitlines()
            if line.startswith("#")
        }

        for check in CHECKS:
            urls = [check["docs_url"]]
            if "cannot_run" in check:
                urls.append(check["cannot_run"]["docs_url"])
            for url in urls:
                page, _, anchor = url.partition("#")
                assert page.endswith("/INSTALLATION.md"), (
                    f"{check['id']} links to {page}, where no test can see the anchor"
                )
                assert anchor in headings, f"{check['id']} links to a missing #{anchor}"

    def test_down_detail_lives_on_the_check_not_the_mode(self):
        """Hoisted so the two modes cannot drift into describing one outage
        two different ways."""
        for check in CHECKS:
            for mode in (LIVE, EXPERIMENT):
                assert "down_detail" not in check[mode], (
                    f"{check['id']} still declares a per-mode down_detail"
                )

    def test_no_row_promises_a_feature_the_open_source_app_lacks(self, api_client):
        """The screen only runs on open-source installs (404 on EE and cloud),
        where Error Feed is a locked cloud feature."""
        for probe_results in (all_up(), all_down()):
            result = get_checks(api_client, probe_results=probe_results)
            assert "Error Feed" not in str(result)
        serving = next(c for c in CHECKS if c["id"] == "model_serving")
        assert "Error Feed" not in str(serving)


@pytest.mark.integration
@pytest.mark.api
class TestAccountExists:
    """./bin/install usually makes the owner before anyone opens the screen,
    which then sends people to sign in instead of sign-up."""

    @pytest.mark.django_db
    def test_false_before_any_account(self, api_client):
        from accounts.models import User

        User.objects.all().delete()

        assert get_checks(api_client)["account_exists"] is False

    @pytest.mark.django_db
    def test_true_once_an_account_exists_and_nothing_about_it_leaks(self, api_client):
        from accounts.models import User

        User.objects.all().delete()
        User.objects.create_user(
            email="owner@example.com", password="Owner-Passw0rd!", name="Owner"
        )

        result = get_checks(api_client)

        assert result["account_exists"] is True
        # A yes or no to anyone who asks: never the address.
        assert "owner@example.com" not in str(result)
        assert "Owner" not in str(result)

    @pytest.mark.django_db
    def test_deactivated_accounts_cannot_sign_in(self, api_client):
        from accounts.models import User

        User.objects.all().delete()
        User.objects.create_user(
            email="gone@example.com", password="Gone-Passw0rd!", name="Gone"
        ).__class__.objects.update(is_active=False)

        assert get_checks(api_client)["account_exists"] is False

    def test_no_database_keeps_the_sign_up_path(self):
        with patch(
            "accounts.models.User.objects.filter", side_effect=RuntimeError("down")
        ):
            assert setup_checks._account_exists() is False


@pytest.mark.integration
@pytest.mark.api
class TestSetup:
    """The screen says which setup is running, and each fix names the
    commands of that setup only."""

    @pytest.mark.parametrize("setup", [STANDALONE, DISTRIBUTED, HELM])
    def test_the_setup_is_reported(self, api_client, setup):
        assert get_checks(api_client, setup=setup)["setup"] == setup

    def test_the_embedded_worker_marks_the_standalone_install(self, monkeypatch):
        monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
        monkeypatch.setenv("FI_EMBEDDED_TEMPORAL_WORKER", "true")
        assert _setup() == STANDALONE

        monkeypatch.delenv("FI_EMBEDDED_TEMPORAL_WORKER")
        assert _setup() == DISTRIBUTED

    def test_a_kubernetes_pod_is_the_helm_install(self, monkeypatch):
        monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
        assert _setup() == HELM

    def test_helm_fixes_name_kubectl_and_values_not_compose(self, api_client):
        result = get_checks(api_client, probe_results=all_down(), setup=HELM)

        for check in result["checks"]:
            if check["status"] == PASSED:
                continue
            assert "docker compose" not in check["fix"], check["id"]
            assert ".env" not in check["fix"], check["id"]
            assert "kubectl" in check["fix"] or "values" in check["fix"], check["id"]

    def test_helm_fixes_name_the_chart_objects(self, api_client):
        result = get_checks(
            api_client,
            probe_results=down_only("collector", "database", "ssl"),
            setup=HELM,
        )

        collector_fix = by_id(result, "collector")["fix"]
        assert (
            "kubectl -n <namespace> logs deploy/<release>-futureagi-fi-collector"
            in collector_fix
        )
        assert "postgres.external" in by_id(result, "database")["fix"]
        assert "`urls.app` and `urls.api`" in by_id(result, "ssl")["fix"]

    def test_a_standalone_fix_names_the_app_container(self, api_client):
        result = get_checks(
            api_client, probe_results=down_only("storage"), setup=STANDALONE
        )
        fix = by_id(result, "storage")["fix"]

        assert "docker compose restart app" in fix
        assert "minio" not in fix

    def test_a_distributed_fix_names_the_service(self, api_client):
        result = get_checks(
            api_client, probe_results=down_only("storage"), setup=DISTRIBUTED
        )
        fix = by_id(result, "storage")["fix"]

        assert "docker compose up -d minio" in fix
        assert "restart app" not in fix

    def test_a_setup_independent_fix_is_served_as_is(self, api_client):
        for setup in (STANDALONE, DISTRIBUTED):
            result = get_checks(
                api_client, probe_results=down_only("database"), setup=setup
            )
            assert "docker compose up -d postgres" in by_id(result, "database")["fix"]


@pytest.mark.integration
@pytest.mark.api
class TestLocalInstallSkipsSsl:
    """A laptop or a private network has no certificate to hold, so the SSL
    row is SKIPPED there in both modes, and Production is green. A public host
    keeps it strict."""

    @pytest.mark.parametrize(
        "host",
        [
            "localhost",
            "app.localhost",
            "127.0.0.1",
            "::1",
            "0.0.0.0",
            "10.0.0.12",
            "172.20.1.5",
            "192.168.1.10",
            "169.254.10.1",
            "100.101.102.103",
            "fd12:3456::1",
            "::ffff:192.168.1.10",
            "host.docker.internal",
            "backend",
            "mybox.local",
            "futureagi.internal",
            "LOCALHOST.",
        ],
    )
    def test_local_hosts(self, host):
        assert _is_local_host(host) is True

    @pytest.mark.parametrize(
        "host",
        ["", None, "8.8.8.8", "34.120.1.5", "2606:4700::1111", "ai.example.com"],
    )
    def test_public_hosts(self, host):
        assert _is_local_host(host) is False

    @pytest.fixture
    def public_urls(self, monkeypatch):
        def set_urls(frontend="", api="", base=""):
            monkeypatch.setenv("FRONTEND_URL", frontend)
            monkeypatch.setenv("VITE_HOST_API", api)
            monkeypatch.setenv("BASE_URL", base)

        return set_urls

    @pytest.mark.parametrize("request_host", [None, "localhost", "192.168.1.10"])
    def test_no_url_reached_locally_is_absent(self, public_urls, request_host):
        """The default install sets neither URL; the SPA then calls
        http://localhost:8000, which only a browser on this machine reaches."""
        public_urls()
        with patch("tfc.views.setup_checks._tls_verified") as handshake:
            assert _tls_up(request_host) == ABSENT
        handshake.assert_not_called()

    def test_no_url_reached_on_a_public_host_is_down(self, public_urls):
        public_urls()
        assert _tls_up("34.120.1.5") is False

    LOCAL_URLS = [
        ("http://localhost:3000", "http://localhost:8000"),
        ("http://192.168.1.10:3000", "http://192.168.1.10:8000"),
        ("", "http://127.0.0.1:8000"),
        ("https://localhost:3443", ""),
    ]

    @pytest.mark.parametrize("frontend, api", LOCAL_URLS)
    @pytest.mark.parametrize("request_host", [None, "localhost", "192.168.1.10"])
    def test_local_urls_reached_locally_are_absent(
        self, public_urls, frontend, api, request_host
    ):
        public_urls(frontend, api)
        with patch("tfc.views.setup_checks._tls_verified") as handshake:
            assert _tls_up(request_host) == ABSENT
        handshake.assert_not_called()

    @pytest.mark.parametrize("frontend, api", LOCAL_URLS)
    def test_local_urls_do_not_excuse_a_browser_on_a_public_host(
        self, public_urls, frontend, api
    ):
        """The Helm chart defaults FRONTEND_URL to http://localhost:3000, so a
        local URL does not prove only this machine reaches the install: a
        browser that came in on a public host did, in plain http."""
        public_urls(frontend, api)
        with patch("tfc.views.setup_checks._tls_verified") as handshake:
            assert _tls_up("34.120.1.5") is False
        handshake.assert_not_called()

    def test_helm_api_url_is_base_url(self, public_urls):
        """The chart hands VITE_HOST_API to the frontend pod only; the backend
        has BASE_URL (``urls.api``)."""
        public_urls("http://localhost:3000", base="http://api.example.com")
        assert _tls_up("localhost") is False

    def test_helm_https_urls_are_verified(self, public_urls):
        public_urls("https://ai.example.com", base="https://api.example.com")
        with patch(
            "tfc.views.setup_checks._tls_verified", return_value=True
        ) as handshake:
            assert _tls_up("ai.example.com") is True

        assert sorted(call.args[0] for call in handshake.call_args_list) == [
            "https://ai.example.com",
            "https://api.example.com",
        ]

    def test_an_https_ui_does_not_excuse_a_plaintext_api(self, public_urls):
        public_urls("https://ai.example.com", base="http://api.example.com")
        with patch(
            "tfc.views.setup_checks._tls_verified",
            side_effect=lambda u: u.startswith("https://"),
        ):
            assert _tls_up("ai.example.com") is False

    def test_vite_host_api_wins_over_base_url(self, public_urls):
        public_urls(api="http://localhost:8000", base="http://api.example.com")
        assert _tls_up("localhost") == ABSENT

    def test_a_public_http_url_is_down(self, public_urls):
        public_urls("http://ai.example.com", "http://api.example.com")
        assert _tls_up("localhost") is False

    def test_public_https_urls_are_verified(self, public_urls):
        public_urls("https://ai.example.com", "https://api.example.com")
        with patch(
            "tfc.views.setup_checks._tls_verified", return_value=True
        ) as handshake:
            assert _tls_up("localhost") is True
        assert handshake.call_count == 2

    def test_one_local_url_does_not_excuse_a_public_one(self, public_urls):
        public_urls("https://ai.example.com", "http://localhost:8000")
        with patch("tfc.views.setup_checks._tls_verified", return_value=False):
            assert _tls_up("localhost") is False

    @pytest.mark.parametrize("mode", [LIVE, EXPERIMENT])
    def test_absent_ssl_is_skipped_and_production_stays_green(self, api_client, mode):
        probes = all_up()
        probes["ssl"] = ABSENT
        result = get_checks(api_client, mode=mode, probe_results=probes)

        check = by_id(result, "ssl")
        assert check["status"] == SKIPPED
        assert check["required"] is False
        assert "local install" in check["detail"]
        assert "FRONTEND_URL" in check["fix"]
        assert result["status"] == "ok"

    def test_ssl_down_on_a_public_host_still_blocks_production(self, api_client):
        result = get_checks(api_client, mode=LIVE, probe_results=down_only("ssl"))

        assert by_id(result, "ssl")["status"] == FAILED
        assert by_id(result, "ssl")["required"] is True
        assert result["status"] == "issues"

    def test_the_probe_is_told_the_host_the_browser_came_in_on(self, api_client):
        cache.clear()
        with patch(
            "tfc.views.setup_checks._run_probes", return_value=all_up()
        ) as probes:
            api_client.get(SETUP_CHECKS_URL, HTTP_HOST="ai.example.com:8000")

        probes.assert_called_once_with("ai.example.com")

    def test_the_ssl_probe_receives_the_request_host(self, monkeypatch):
        """_run_probes hands the host to the probes that ask for it only."""
        seen = []
        ssl_check = next(c for c in CHECKS if c["id"] == "ssl")
        monkeypatch.setitem(ssl_check, "probe", lambda host: seen.append(host) or True)
        for check in CHECKS:
            if check["id"] not in ("ssl", "database"):
                monkeypatch.setitem(check, "probe", lambda: True)

        with patch("tfc.views.setup_checks._postgres_up", return_value=True):
            results = setup_checks._run_probes("ai.example.com")

        assert seen == ["ai.example.com"]
        assert results["ssl"] is True

    def test_local_and_remote_browsers_do_not_share_a_snapshot(self, api_client):
        """The SSL verdict depends on the host the browser came in on."""
        cache.clear()
        with patch(
            "tfc.views.setup_checks._run_probes", return_value=all_up()
        ) as probes:
            api_client.get(SETUP_CHECKS_URL, HTTP_HOST="localhost:8000")
            api_client.get(SETUP_CHECKS_URL, HTTP_HOST="ai.example.com")
            api_client.get(SETUP_CHECKS_URL, HTTP_HOST="127.0.0.1:8000")

        assert probes.call_count == 2
