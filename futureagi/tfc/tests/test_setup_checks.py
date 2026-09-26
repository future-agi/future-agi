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
"""

import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from django.core.cache import cache
from rest_framework import status

from agentic_eval.core.embeddings import serving_client
from tfc.views import setup_checks
from tfc.views.setup_checks import (
    ABSENT,
    CHECKS,
    DISTRIBUTED,
    EXPERIMENT,
    FAILED,
    HELM,
    LIVE,
    PASSED,
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

INSTALLATION = Path(__file__).resolve().parents[3] / "INSTALLATION.md"

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
        [(STANDALONE, "--profile ml"), (DISTRIBUTED, "MODEL_SERVING_URL")],
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
        """The chart runs no sandbox by default (it needs privileged pods), and
        leaves CODE_EXECUTOR_URL empty: that is a choice, not an outage."""
        probes = all_up()
        probes["code_executor"] = ABSENT
        result = get_checks(api_client, mode=mode, probe_results=probes, setup=HELM)

        check = by_id(result, "code_executor")
        assert check["status"] == SKIPPED
        assert check["required"] is False
        assert "codeExecutor.enabled=true" in check["fix"]
        assert "codeExecutor.localFallback=true" in check["fix"]
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
        assert "--profile ml" not in check["fix"]
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


@pytest.mark.integration
@pytest.mark.api
class TestCodeExecutorProbe:
    def test_passes_against_the_in_container_executor(self, monkeypatch, health_server):
        """The standalone install serves the executor on loopback inside `app`."""
        monkeypatch.setenv("CODE_EXECUTOR_URL", f"{health_server}/")

        assert _code_executor_up() is True

    def test_nothing_listening_is_down(self, monkeypatch):
        monkeypatch.setenv("CODE_EXECUTOR_URL", _closed_port_url())

        assert _safe(_code_executor_up) is False


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
            for fix in (check["fix"], check.get("absent", {}).get("fix", "")):
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
            page, _, anchor = check["docs_url"].partition("#")
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
