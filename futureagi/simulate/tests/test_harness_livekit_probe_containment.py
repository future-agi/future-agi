"""Future AGI Cloud never sends a preflight request to a customer-supplied LIVEKIT_URL.

The LiveKit credential and agent probes POST to whatever URL the form carries, from
the platform backend, so on a hosted deployment they would reach private addresses,
cloud metadata and anything a redirect points at. On Future AGI Cloud they are
skipped and the preflight says why; a self-hosted install keeps them. DB-free: the
transport and DNS are replaced by fakes that record and refuse every call.

Release-lead ruling #7 binds the skipped state: the preflight reports LiveKit as not
verified on hosted, never as verified, and a run against a public LiveKit URL still
proceeds, because refusing the probe must not block the simulation.
"""

import socket
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests
from requests.adapters import HTTPAdapter

from simulate.serializers.harness_job import (
    HarnessJobCreateSerializer,
    HarnessPreflightSerializer,
)
from simulate.services.harness_provider import (
    HostedHarnessProvider,
    _preflight_credential_probe,
    run_preflight_checks,
)

METADATA_URL = "http://169.254.169.254/latest/meta-data/"
REDIRECTING_URL = "https://redirect.example.com"
PUBLIC_LIVEKIT_URL = "wss://demo.livekit.cloud"

# Ruling #7 wording. The credential row carries it verbatim; the agent row puts the
# agent's name after "LiveKit agent" and keeps the rest.
NOT_VERIFIED = (
    "not verified on Future AGI Cloud: preflight does not contact customer LiveKit "
    "URLs; connection problems will show when the run connects."
)
LIVEKIT_NOT_VERIFIED = "LiveKit " + NOT_VERIFIED

CUSTOMER_URLS = [
    pytest.param("wss://demo.livekit.cloud", id="public-host"),
    pytest.param("https://203.0.113.10", id="public-ip"),
    pytest.param("http://10.0.0.5:7880", id="private-ip"),
    pytest.param("ws://127.0.0.1:7880", id="loopback"),
    pytest.param("http://localhost:7880", id="loopback-name"),
    pytest.param("http://169.254.1.1", id="link-local"),
    pytest.param(METADATA_URL, id="metadata"),
    pytest.param("ws://[::1]:7880", id="ipv6-loopback"),
    pytest.param("wss://[fd00::1]", id="ipv6-private"),
    pytest.param("http://[::ffff:169.254.169.254]", id="ipv6-mapped-metadata"),
    pytest.param(REDIRECTING_URL, id="redirecting"),
]


@pytest.fixture
def network():
    """Record every request the HTTP layer is asked to send, and refuse it.

    ``HTTPAdapter.send`` sits under ``requests.request``, ``requests.post`` and the
    redirect loop, so a followed redirect is recorded too. The redirecting host
    answers 302 to cloud metadata; everything else fails to connect, which keeps
    the base behaviour fast instead of polling a fake LiveKit for seconds.
    """
    sent = []
    resolved = []

    def send(self, request, **kwargs):
        sent.append((request.method, request.url))
        if request.url.startswith(REDIRECTING_URL):
            response = requests.Response()
            response.status_code = 302
            response.headers["Location"] = METADATA_URL
            response.url = request.url
            response.request = request
            response._content = b""
            response.raw = SimpleNamespace(
                release_conn=lambda: None, close=lambda: None
            )
            return response
        raise requests.ConnectionError(f"test network refused {request.url}")

    # Name lookups fail like an offline resolver (requests' proxy-bypass check calls
    # gethostbyname on macOS), so nothing here ever reaches a real DNS server.
    def resolve(host, *args, **kwargs):
        resolved.append(host)
        raise socket.gaierror(f"test resolver refused {host}")

    def create_connection(address, *args, **kwargs):
        resolved.append(address)
        raise ConnectionRefusedError(f"test network refused {address}")

    with (
        patch.object(HTTPAdapter, "send", send),
        patch.object(socket, "getaddrinfo", resolve),
        patch.object(socket, "gethostbyname", resolve),
        patch.object(socket, "gethostbyname_ex", resolve),
        patch.object(socket, "create_connection", create_connection),
    ):
        yield SimpleNamespace(sent=sent, resolved=resolved)


def _payload(url, *, route="credential_values", mode="connect_only"):
    config = {"agent_name": "returns-agent"}
    values = {"LIVEKIT_API_KEY": "APIdemo", "LIVEKIT_API_SECRET": "s" * 32}
    if route == "config":
        config["livekit_url"] = url
    else:
        values["LIVEKIT_URL"] = url
    return {
        "source": {"kind": "provider"},
        "agent": {
            "connector": "livekit",
            "mode": mode,
            "config": config,
            "secret_refs": {alias: {"key": alias} for alias in values},
        },
        "credential_values": values,
    }


def _by_provider(results):
    return {item["provider"]: item for item in results}


@pytest.mark.parametrize("deployment", ["US", "EU", "DEV"])
@pytest.mark.parametrize("route", ["credential_values", "config"])
@pytest.mark.parametrize("url", CUSTOMER_URLS)
def test_hosted_preflight_sends_nothing_to_a_customer_livekit_url(
    settings, network, deployment, route, url
):
    settings.CLOUD_DEPLOYMENT = deployment

    results = _preflight_credential_probe(_payload(url, route=route))

    assert network.sent == []
    assert network.resolved == []
    probes = _by_provider(results)
    for provider in ("livekit", "livekit_target"):
        item = probes[provider]
        assert item["ok"] is True
        assert item["skipped"] is True
        assert item["message"].endswith(NOT_VERIFIED)
        assert "target_name" not in item
    assert probes["livekit"]["message"] == LIVEKIT_NOT_VERIFIED
    assert probes["livekit_target"]["message"] == (
        "LiveKit agent 'returns-agent' " + NOT_VERIFIED
    )


def test_hosted_preflight_does_not_follow_a_livekit_redirect(settings, network):
    settings.CLOUD_DEPLOYMENT = "US"

    _preflight_credential_probe(_payload(REDIRECTING_URL))

    assert ("GET", METADATA_URL) not in network.sent
    assert network.sent == []


def test_hosted_preflight_skips_only_the_agent_probe_it_would_have_run(
    settings, network
):
    settings.CLOUD_DEPLOYMENT = "US"

    results = _preflight_credential_probe(_payload(METADATA_URL, mode=None))

    assert network.sent == []
    assert set(_by_provider(results)) == {"livekit"}


def test_hosted_preflight_says_why_and_does_not_block_the_run(settings, network):
    settings.CLOUD_DEPLOYMENT = "US"

    checks, _readiness, probe, _error, _analysis = run_preflight_checks(
        None, _payload(METADATA_URL, route="config"), ([], [], 0)
    )

    assert network.sent == []
    by_id = {check["id"]: check for check in checks}
    for check_id in ("credentials_valid", "provider_target"):
        assert by_id[check_id]["status"] == "skipped"
        assert NOT_VERIFIED in by_id[check_id]["detail"]
        assert by_id[check_id]["missing"] == []
    assert not [check for check in checks if check["status"] == "failed"]
    assert all(item["ok"] for item in probe)


def test_hosted_preflight_still_checks_fixed_vendor_endpoints(settings, network):
    settings.CLOUD_DEPLOYMENT = "US"
    payload = _payload(METADATA_URL)
    payload["credential_values"]["OPENAI_API_KEY"] = "sk-test"
    openai = SimpleNamespace(
        as_dict=lambda: {
            "provider": "openai",
            "label": "OpenAI",
            "aliases": ["OPENAI_API_KEY"],
            "ok": True,
            "message": "OpenAI accepted OPENAI_API_KEY",
        }
    )

    with patch(
        "simulate.services.harness_credential_probes.probe_all",
        return_value=[openai],
    ) as probe_all:
        checks, *_ = run_preflight_checks(None, payload, ([], [], 0))

    (values,) = probe_all.call_args.args
    assert values["OPENAI_API_KEY"] == "sk-test"
    assert "LIVEKIT_URL" not in values
    assert network.sent == []
    by_id = {check["id"]: check for check in checks}
    assert by_id["credentials_valid"]["status"] == "passed"
    assert "OpenAI accepted OPENAI_API_KEY" in by_id["credentials_valid"]["detail"]
    assert LIVEKIT_NOT_VERIFIED in by_id["credentials_valid"]["detail"]


@pytest.mark.parametrize("deployment", ["", "self-hosted", "false"])
def test_self_hosted_preflight_still_probes_its_livekit_server(settings, deployment):
    settings.CLOUD_DEPLOYMENT = deployment

    with (
        patch(
            "simulate.services.harness_credential_probes.probe_all",
            return_value=[],
        ) as probe_all,
        patch(
            "simulate.services.harness_credential_probes.probe_livekit_agent",
            return_value=None,
        ) as probe_agent,
    ):
        results = _preflight_credential_probe(_payload("http://10.0.0.5:7880"))

    assert results == []
    (values,) = probe_all.call_args.args
    assert values["LIVEKIT_URL"] == "http://10.0.0.5:7880"
    agent_name, agent_values = probe_agent.call_args.args
    assert agent_name == "returns-agent"
    assert agent_values["LIVEKIT_URL"] == "http://10.0.0.5:7880"


def _hosted_livekit_run(serializer_class):
    """A connected LiveKit agent on a public LiveKit Cloud URL, as the form sends it,
    validated by the endpoint's own serializer so every other input is a valid one."""
    serializer = serializer_class(
        data={
            "agent": {
                "connector": "livekit",
                "mode": "connect_only",
                "config": {
                    "agent_name": "returns-agent",
                    "livekit_url": PUBLIC_LIVEKIT_URL,
                    "target_system_prompt": "You handle returns for an online shop.",
                },
                "secret_refs": {},
            },
            "credential_values": {
                "LIVEKIT_API_KEY": "APIdemo",
                "LIVEKIT_API_SECRET": "s" * 32,
            },
            "scenario_count": 1,
            "artifacts": {
                "level": "full",
                "retention_days": 30,
                "allow_bundle_download": False,
                "max_artifact_bytes": 1024,
            },
        }
    )
    assert serializer.is_valid(), serializer.errors
    return serializer.validated_data


def _assert_not_verified_and_not_failed(checks, probe):
    """The LiveKit rows read "not verified", never "verified", and fail nothing."""
    by_id = {check["id"]: check for check in checks}
    assert [check for check in checks if check["status"] == "failed"] == []
    for check_id in ("credentials_valid", "provider_target"):
        assert by_id[check_id]["status"] == "skipped"
        assert NOT_VERIFIED in by_id[check_id]["detail"]
    rows = _by_provider(probe)
    assert set(rows) == {"livekit", "livekit_target"}
    for item in rows.values():
        assert item["skipped"] is True
        # ok stays true: a refused probe is not a rejected key.
        assert item["ok"] is True
        assert "verified" not in item["message"].replace("not verified", "")
    assert rows["livekit"]["message"] == LIVEKIT_NOT_VERIFIED


@pytest.fixture
def hosted(settings):
    settings.CLOUD_DEPLOYMENT = "US"
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = []
    settings.ALK_HOSTED_SIMULATOR_SECRET_ENV = {}
    return settings


def test_hosted_preflight_with_a_public_livekit_url_is_ready_to_submit(hosted, network):
    """The gate the UI starts a run on: ``ready_to_submit`` from POST preflight."""
    request = SimpleNamespace(
        validated_data=_hosted_livekit_run(HarnessPreflightSerializer),
        build_absolute_uri=lambda _path: "https://harness.example.test/",
    )

    response = HostedHarnessProvider().preflight(request)

    assert network.sent == []
    assert network.resolved == []
    assert response.status_code == 200
    assert response.data["ready_to_submit"] is True
    assert response.data["state"] == "connected"
    _assert_not_verified_and_not_failed(
        response.data["checks"], response.data["credentials"]["probe"]
    )


def test_hosted_create_with_a_public_livekit_url_passes_the_credential_gate(
    hosted, network
):
    """The server's own gate: create re-checks the credentials live and answers 422
    ``agent_preflight_failed`` when they fail. With the LiveKit probe refused it must
    pass, so the job is created and its workflow started. Persistence, billing,
    GitHub and Temporal are mocked; the credential check is the real one."""
    from simulate.services import harness_agent_versions

    hosted.HARNESS_VERSIONING_ENABLED = True
    payload = _hosted_livekit_run(HarnessJobCreateSerializer)
    job = SimpleNamespace(
        id="job-1",
        payload={
            "retry": {
                "max_infrastructure_attempts": 2,
                "initial_backoff_seconds": 1,
                "max_backoff_seconds": 15,
            }
        },
    )
    request = SimpleNamespace(
        validated_data=payload,
        headers={"Idempotency-Key": "idem-livekit-public"},
        organization=SimpleNamespace(id="org-1"),
        user=SimpleNamespace(id="user-1"),
        build_absolute_uri=lambda _path: "https://harness.example.test/",
    )
    real_gate = harness_agent_versions.check_creation_credentials
    checked = []

    def gate(*args, **kwargs):
        checked.append(real_gate(*args, **kwargs))
        return checked[-1]

    start_workflow = MagicMock()

    with (
        patch.object(harness_agent_versions, "check_creation_credentials", gate),
        patch.object(harness_agent_versions, "advisory_xact_lock"),
        patch("django.db.transaction.atomic", return_value=nullcontext()),
        patch("simulate.services.hosted_harness.find_replayed_job", return_value=None),
        patch(
            "simulate.services.hosted_harness.create_hosted_job",
            return_value=(job, True),
        ),
        patch(
            "simulate.services.harness_agent_changes.validate_public_github_source",
            return_value=None,
        ),
        patch("simulate.services.harness_usage.require_harness_run_usage"),
        patch("simulate.services.harness_usage.require_harness_source_call_usage"),
        patch(
            "simulate.temporal.client.start_hosted_harness_gateway_workflow",
            start_workflow,
        ),
        patch(
            "simulate.services.harness_provider.serialize_job",
            return_value={"job_id": "job-1"},
        ),
    ):
        response = HostedHarnessProvider().create(request)

    assert network.sent == []
    assert network.resolved == []
    assert response.status_code == 202, response.data
    start_workflow.assert_called_once()
    (check,) = checked
    report = check.report
    assert report["invalid"] == []
    assert report["error"] is None
    statuses = {check["id"]: check["status"] for check in report["checks"]}
    assert "failed" not in statuses.values()
    assert statuses["credentials_valid"] == "skipped"
    assert statuses["provider_target"] == "skipped"
