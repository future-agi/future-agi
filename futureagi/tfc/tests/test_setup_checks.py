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

from unittest.mock import patch

import pytest
from django.core.cache import cache
from rest_framework import status

from tfc.views.setup_checks import (
    CHECKS,
    EXPERIMENT,
    FAILED,
    LIVE,
    PASSED,
    SKIPPED,
    WARNING,
)

SETUP_CHECKS_URL = "/api/setup-checks/"

ALL_IDS = [c["id"] for c in CHECKS]


def all_up():
    return {check_id: True for check_id in ALL_IDS}


def all_down():
    return {check_id: False for check_id in ALL_IDS}


def down_only(*check_ids):
    results = all_up()
    for check_id in check_ids:
        results[check_id] = False
    return results


def get_checks(client, mode=None, probe_results=None):
    """Request a snapshot with probe results forced, and return result payload."""
    cache.clear()
    url = SETUP_CHECKS_URL if mode is None else f"{SETUP_CHECKS_URL}?mode={mode}"
    with patch(
        "tfc.views.setup_checks._run_probes",
        return_value=probe_results if probe_results is not None else all_up(),
    ):
        response = client.get(url)
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
        assert set(body["result"]) == {"status", "mode", "checks"}

    def test_every_check_carries_the_contracted_fields(self, api_client):
        result = get_checks(api_client)

        for check in result["checks"]:
            assert set(check) == {"id", "label", "status", "required", "detail"}
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

    def test_optional_failure_does_not_block_in_live(self, api_client):
        """code_executor warns rather than fails, so it must not flip the verdict."""
        result = get_checks(
            api_client, mode=LIVE, probe_results=down_only("code_executor")
        )

        assert by_id(result, "code_executor")["status"] == WARNING
        assert by_id(result, "code_executor")["required"] is False
        assert result["status"] == "ok"

    def test_issues_requires_a_check_that_is_both_required_and_failed(
        self, api_client
    ):
        for mode in (LIVE, EXPERIMENT):
            result = get_checks(api_client, mode=mode, probe_results=all_down())
            blocking = [
                c
                for c in result["checks"]
                if c["required"] and c["status"] == FAILED
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
    @pytest.mark.parametrize("check_id", ["model_serving", "code_executor"])
    def test_service_not_run_in_experiment_is_skipped_not_failed(
        self, api_client, check_id
    ):
        """A container the mode never starts is expected to be down, not broken."""
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

    def test_model_serving_warns_in_live_rather_than_blocking(self, api_client):
        """Feature-level, not stack-level. Running the platform for observability
        alone is a legitimate live deployment, so a missing eval runtime is
        reported loudly and left as the operator's call."""
        result = get_checks(
            api_client, mode=LIVE, probe_results=down_only("model_serving")
        )

        assert by_id(result, "model_serving")["status"] == WARNING
        assert by_id(result, "model_serving")["required"] is False
        assert result["status"] == "ok"


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
            experiment = api_client.get(
                f"{SETUP_CHECKS_URL}?mode={EXPERIMENT}"
            ).json()["result"]

        assert live["status"] == "issues"
        assert experiment["status"] == "ok"


@pytest.mark.integration
@pytest.mark.api
class TestCheckInventory:
    """Regressions for checks that were deliberately removed."""

    @pytest.mark.parametrize("removed_id", ["ssl", "ports", "email"])
    def test_unobservable_checks_are_not_reported(self, api_client, removed_id):
        """These were dropped because none could be probed from the backend —
        TLS and ports describe how the browser arrived, and mail delivery can
        only be read from config. Re-adding one belongs in deployment-info."""
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

    def test_down_detail_lives_on_the_check_not_the_mode(self):
        """Hoisted so the two modes cannot drift into describing one outage
        two different ways."""
        for check in CHECKS:
            for mode in (LIVE, EXPERIMENT):
                assert "down_detail" not in check[mode], (
                    f"{check['id']} still declares a per-mode down_detail"
                )
