from __future__ import annotations

import io
import json
import tarfile
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simulate.models import HostedHarnessAttempt, HostedHarnessJob
from simulate.services.hosted_harness import (
    HostedHarnessError,
    create_hosted_job,
    request_cancellation,
)
from simulate.services.hosted_harness_gateway import (
    _ADJUSTMENTS_PATH,
    _SIMULATOR_SECRETS_PATH,
    _SIMULATOR_VERTEX_CREDENTIALS_PATH,
    DaytonaHostedGateway,
    HostedSourceAcquirer,
    _authoring_archive_for,
    _connector_egress_domains,
    _normalize_egress_domains,
    _platform_simulator_material,
    _provider_egress_domains,
    _provider_import_authoring_material,
    _resolved_egress_domains,
    _validate_resolved_egress_domains,
    attach_platform_simulator_secret_refs,
    pack_authoring_archive,
    prepare_dispatch_payload,
    resolve_authored_connector,
    resolve_platform_simulator_secrets,
)


@pytest.fixture(autouse=True)
def _isolate_platform_simulator_environment(settings):
    """Tests opt in explicitly instead of reading the developer machine's provider keys."""
    settings.ALK_HOSTED_SIMULATOR_SECRET_ENV = {}


def test_platform_simulator_material_uses_deployment_credentials_only(
    tmp_path, monkeypatch
):
    credentials = tmp_path / "vertex.json"
    credentials.write_text(
        json.dumps({"project_id": "platform-simulator-project"}), encoding="utf-8"
    )
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(credentials))
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setenv("SIMULATOR_LLM_PROVIDER", "vertex")
    monkeypatch.setenv("SIMULATOR_LLM_MODEL", "gemini-2.5-flash")
    monkeypatch.delenv("ALK_HARNESS", raising=False)
    monkeypatch.delenv("ALK_HARNESS_MODEL", raising=False)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "platform-deepgram-secret")
    monkeypatch.setenv("LIVEKIT_URL", "wss://platform-livekit.example")
    monkeypatch.setenv("LIVEKIT_API_KEY", "platform-livekit-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "platform-livekit-secret")

    values, credential_bytes = _platform_simulator_material()

    assert values["GOOGLE_CLOUD_PROJECT"] == "platform-simulator-project"
    assert values["GOOGLE_APPLICATION_CREDENTIALS"] == (
        _SIMULATOR_VERTEX_CREDENTIALS_PATH
    )
    assert values["DEEPGRAM_API_KEY"] == "platform-deepgram-secret"
    assert values["LIVEKIT_URL"] == "wss://platform-livekit.example"
    assert values["LIVEKIT_API_KEY"] == "platform-livekit-key"
    assert values["LIVEKIT_API_SECRET"] == "platform-livekit-secret"
    assert values["ALK_HARNESS"] == "vertex-gemini"
    assert credential_bytes == credentials.read_bytes()


def test_platform_authoring_backend_is_independent_from_simulated_caller(
    tmp_path, monkeypatch
):
    credentials = tmp_path / "vertex.json"
    credentials.write_text('{"project_id":"platform-project"}', encoding="utf-8")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(credentials))
    monkeypatch.setenv("SIMULATOR_LLM_PROVIDER", "vertex")
    monkeypatch.setenv("SIMULATOR_LLM_MODEL", "gemini-3.1-flash-lite")
    monkeypatch.setenv("ALK_HARNESS", "claude")
    monkeypatch.setenv("ALK_HARNESS_MODEL", "claude-sonnet-4-6")

    values, _credential_bytes = _platform_simulator_material()

    assert values["ALK_HARNESS"] == "claude"
    assert values["ALK_HARNESS_MODEL"] == "claude-sonnet-4-6"
    assert values["SIMULATOR_LLM_PROVIDER"] == "vertex"
    assert values["SIMULATOR_LLM_MODEL"] == "gemini-3.1-flash-lite"


def test_provider_egress_includes_vertex_auth_and_both_model_regions():
    domains = _provider_egress_domains(
        {
            "GOOGLE_APPLICATION_CREDENTIALS_JSON": "<encrypted-at-rest>",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
        }
    )

    assert domains == {
        "aiplatform.googleapis.com",
        "oauth2.googleapis.com",
        "us-central1-aiplatform.googleapis.com",
        "us-east5-aiplatform.googleapis.com",
    }


def test_provider_egress_includes_vapi_and_retell_call_hosts():
    assert _provider_egress_domains({"VAPI_API_KEY": "opaque"}) == {
        "api.vapi.ai",
    }


def test_provider_import_authoring_gets_only_the_matching_target_key():
    job = SimpleNamespace()
    payload = {"agent": {"connector": "retell", "mode": "provider_import"}}
    with patch(
        "simulate.services.hosted_harness_gateway.PlatformSecretResolver.resolve",
        return_value={
            "RETELL_API_KEY": "retell-target-secret",
            "UNRELATED_AGENT_SECRET": "must-not-travel",
        },
    ):
        material, connector = _provider_import_authoring_material(job, payload)

    assert connector == "retell"
    assert material == {"RETELL_API_KEY": "retell-target-secret"}
    assert _provider_egress_domains({"RETELL_API_KEY": "opaque"}) == {
        "api.retellai.com",
        "*.livekit.cloud",
        "*.turn.livekit.cloud",
    }


def test_platform_simulator_secrets_are_namespaced_and_not_request_controlled(
    settings, monkeypatch, tmp_path
):
    adc = tmp_path / "vertex.json"
    adc.write_text('{"project_id":"futureagi"}', encoding="utf-8")
    settings.ALK_HOSTED_SIMULATOR_SECRET_ENV = {
        "SIMULATOR_DEEPGRAM_API_KEY": "DEEPGRAM_API_KEY",
        "SIMULATOR_GOOGLE_APPLICATION_CREDENTIALS_JSON": (
            "GOOGLE_APPLICATION_CREDENTIALS_JSON"
        ),
    }
    monkeypatch.setenv("DEEPGRAM_API_KEY", "platform-deepgram")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(adc))

    resolved = resolve_platform_simulator_secrets()

    assert resolved == {
        "SIMULATOR_DEEPGRAM_API_KEY": "platform-deepgram",
        "SIMULATOR_GOOGLE_APPLICATION_CREDENTIALS_JSON": '{"project_id":"futureagi"}',
    }


def test_platform_livekit_is_available_only_as_simulator_configuration(
    settings, monkeypatch
):
    settings.ALK_HOSTED_SIMULATOR_SECRET_ENV = {
        "SIMULATOR_LIVEKIT_URL": "LIVEKIT_URL",
        "SIMULATOR_LIVEKIT_API_KEY": "LIVEKIT_API_KEY",
        "SIMULATOR_LIVEKIT_API_SECRET": "LIVEKIT_API_SECRET",
    }
    monkeypatch.setenv("LIVEKIT_URL", "wss://platform-livekit.example")
    monkeypatch.setenv("LIVEKIT_API_KEY", "platform-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "platform-secret")

    assert resolve_platform_simulator_secrets() == {
        "SIMULATOR_LIVEKIT_URL": "wss://platform-livekit.example",
        "SIMULATOR_LIVEKIT_API_KEY": "platform-key",
        "SIMULATOR_LIVEKIT_API_SECRET": "platform-secret",
    }


def test_platform_simulator_refs_are_ephemeral_value_free_and_do_not_mutate_payload():
    payload = _payload()
    original = json.loads(json.dumps(payload))

    dispatched = attach_platform_simulator_secret_refs(
        payload,
        {"SIMULATOR_DEEPGRAM_API_KEY": "must-never-appear"},
    )

    assert payload == original
    assert dispatched["agent"]["secret_refs"]["SIMULATOR_DEEPGRAM_API_KEY"] == {
        "manager": "platform-config",
        "key": "SIMULATOR_DEEPGRAM_API_KEY",
        "purpose": "simulator_provider",
    }
    assert "must-never-appear" not in json.dumps(dispatched)


def test_resolved_egress_rejects_daytona_domain_overflow():
    with pytest.raises(HostedHarnessError, match="Daytona supports at most 20"):
        _validate_resolved_egress_domains(
            {f"provider-{index}.example.com" for index in range(21)}
        )


def test_normalize_egress_domains_wildcard_coverage_keeps_apex():
    assert _normalize_egress_domains(
        [
            " Foo.Example.COM. ",
            "foo.example.com",
            "*.EXAMPLE.com.",
            "example.com.",
            "bar.other.test.",
        ]
    ) == {"*.example.com", "example.com", "bar.other.test"}


@pytest.mark.parametrize(
    ("alias", "domain"),
    [
        ("DEEPGRAM_API_KEY", "api.deepgram.com"),
        ("SIMULATOR_DEEPGRAM_API_KEY", "api.deepgram.com"),
        ("CARTESIA_API_KEY", "api.cartesia.ai"),
        ("OPENAI_API_KEY", "api.openai.com"),
        ("ANTHROPIC_API_KEY", "api.anthropic.com"),
    ],
)
def test_provider_egress_includes_direct_and_simulator_aliases(alias, domain):
    assert _provider_egress_domains({alias: "configured"}) == {domain}

def test_provider_egress_uses_selected_simulator_audio_provider():
    domains = _provider_egress_domains(
        {
            "SIMULATOR_DEEPGRAM_API_KEY": "configured",
            "SIMULATOR_CARTESIA_API_KEY": "configured",
            "SIMULATOR_STT_PROVIDER": "deepgram",
            "SIMULATOR_TTS_PROVIDER": "deepgram",
        }
    )

    assert domains == {"api.deepgram.com"}


def test_provider_egress_uses_selected_simulator_llm_provider():
    domains = _provider_egress_domains(
        {
            "SIMULATOR_GEMINI_API_KEY": "configured",
            "SIMULATOR_OPENAI_API_KEY": "configured",
            "SIMULATOR_LLM_PROVIDER": "openai",
        }
    )

    assert domains == {"api.openai.com"}


def test_livekit_cloud_connector_egress_is_wildcard_minimized(settings):
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = []
    payload = {
        "agent": {
            "connector": "livekit",
            "config": {"livekit_url": "wss://FOO.livekit.cloud."},
        },
        "security": {"allowed_egress_domains": []},
    }

    assert _resolved_egress_domains(payload, {}, {}, None) == {
        "*.livekit.cloud",
        "*.turn.livekit.cloud",
    }


def test_self_hosted_livekit_connector_only_adds_signal_host(settings):
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = []
    payload = {
        "agent": {
            "connector": "livekit",
            "config": {"LIVEKIT_URL": "wss://voice.customer.example"},
        },
        "security": {"allowed_egress_domains": []},
    }

    assert _connector_egress_domains(payload, {}) == {"voice.customer.example"}


def test_livekit_secret_url_takes_precedence_over_configured_url():
    payload = {
        "agent": {
            "connector": "livekit",
            "config": {"livekit_url": "wss://config.example.test"},
        },
        "security": {"allowed_egress_domains": []},
    }

    assert _connector_egress_domains(
        payload, {"LIVEKIT_URL": "wss://resolved.example.test"}
    ) == {"resolved.example.test"}


def test_vapi_connector_adds_static_and_configured_endpoint_hosts():
    payload = {
        "agent": {
            "connector": "vapi",
            "config": {
                "api_base_url": "https://proxy.example.test",
                "websocketCallUrl": "wss://call.example.test/socket",
            },
        },
        "security": {"allowed_egress_domains": []},
    }

    assert _connector_egress_domains(payload, {}) == {
        "api.vapi.ai",
        "call.example.test",
        "proxy.example.test",
    }


def test_livekit_futureagi_eu_connector_adds_coturn_host():
    payload = {
        "agent": {
            "connector": "livekit",
            "config": {"livekit_url": "wss://livekit-eu.futureagi.com"},
        },
        "security": {"allowed_egress_domains": []},
    }

    assert _connector_egress_domains(payload, {}) == {
        "coturn.turn-eu.futureagi.com",
        "livekit-eu.futureagi.com",
        "turn-eu.futureagi.com",
    }


def test_retell_connector_includes_default_livekit_cloud_egress(settings):
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = []
    settings.RETELL_LIVEKIT_URL = "wss://retell-ai.example.livekit.cloud"
    payload = {
        "agent": {"connector": "retell", "config": {}},
        "security": {"allowed_egress_domains": []},
    }

    assert _resolved_egress_domains(payload, {}, {}, None) == {
        "*.livekit.cloud",
        "*.turn.livekit.cloud",
        "api.retellai.com",
    }


def test_retell_job_allowlists_platform_simulator_livekit_signaling_and_turn(settings):
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = []
    settings.RETELL_LIVEKIT_URL = "wss://retell-ai.example.livekit.cloud"
    payload = {
        "agent": {"connector": "retell", "config": {}},
        "security": {"allowed_egress_domains": []},
    }
    simulator_env = {"LIVEKIT_URL": "wss://livekit-eu.futureagi.com"}

    assert _resolved_egress_domains(payload, {}, simulator_env, None) == {
        "*.livekit.cloud",
        "*.turn.livekit.cloud",
        "api.retellai.com",
        "livekit-eu.futureagi.com",
        "coturn.turn-eu.futureagi.com",
        "turn-eu.futureagi.com",
    }


def test_auto_connector_resolves_livekit_and_adds_cloud_turn_edges():
    payload = {
        "agent": {"connector": "auto", "config": {}},
        "security": {"allowed_egress_domains": []},
    }

    assert _connector_egress_domains(
        payload, {"LIVEKIT_URL": "wss://tenant-abc.livekit.cloud"}
    ) == {
        "tenant-abc.livekit.cloud",
        "*.livekit.cloud",
        "*.turn.livekit.cloud",
    }


def test_resolved_egress_cap_applies_after_wildcard_minimization(settings):
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = []
    payload = {
        "agent": {"connector": "auto", "config": {}},
        "security": {
            "allowed_egress_domains": [
                "*.example.com",
                "foo.example.com",
                *[f"domain-{index}.example.test" for index in range(19)],
            ]
        },
    }

    domains = _resolved_egress_domains(payload, {}, {}, None)
    assert "foo.example.com" not in domains
    assert "example.com" not in domains
    assert len(domains) == 20
    _validate_resolved_egress_domains(domains)


def _payload():
    return {
        "schema_version": "futureagi.harness-job.v1",
        "source": {
            "kind": "github",
            "repository": "future-agi/reference-agent",
            "ref": "main",
            "commit_sha": "a" * 40,
            "visibility": "private",
            "installation_id": "installation-1",
        },
        "agent": {"connector": "vapi", "config": {}, "secret_refs": {}},
        "scenario_count": 1,
        "seed": 1,
        "runtime": {
            "isolation": "dedicated_vm",
            "cpu_units": 2,
            "memory_mb": 4096,
            "parallelism": 1,
            "concurrency_weight": 1,
            "max_duration_seconds": 600,
            "network_policy": "live",
        },
        "security": {
            "untrusted_source": True,
            "read_only_source": True,
            "allow_privileged": False,
            "allow_host_runtime_control": False,
            "allowed_egress_domains": ["agent.example.com"],
        },
        "retry": {
            "max_infrastructure_attempts": 2,
            "initial_backoff_seconds": 1,
            "max_backoff_seconds": 15,
            "retryable_domains": ["infrastructure", "connectivity"],
        },
        "artifacts": {
            "level": "full",
            "retention_days": 30,
            "allow_bundle_download": True,
            "max_artifact_bytes": 1024,
        },
        "metadata": {},
    }


def test_authoring_archive_resolves_uploaded_source_by_explicit_key(tmp_path, settings):
    root = tmp_path / "authoring"
    scenario = root / "uploaded-agent" / "scenarios" / "one"
    scenario.mkdir(parents=True)
    (scenario / "scenario.json").write_text('{"name":"one"}', encoding="utf-8")
    stale = root / "uploaded-agent" / "webrtc-runs" / "old" / "postgres"
    stale.mkdir(parents=True)
    (stale / "pg_wal").write_bytes(b"must-not-upload")
    settings.ALK_HOSTED_BUNDLE_DIR = str(root)
    job = SimpleNamespace(
        payload={
            "source": {
                "kind": "archive",
                "archive_artifact_id": "0f95b074-d3db-47a1-a3a1-2e37991aa946",
            },
            "metadata": {"authoring_key": "uploaded-agent"},
        }
    )

    archive = _authoring_archive_for(job)

    assert archive is not None
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        assert "scenarios/one/scenario.json" in tar.getnames()
        assert "webrtc-runs/old/postgres/pg_wal" not in tar.getnames()


def test_fresh_authoring_archive_contains_contract_and_scenarios_only(tmp_path):
    root = tmp_path / "authoring"
    scenario = root / "scenarios" / "one"
    scenario.mkdir(parents=True)
    (root / "contract.json").write_text('{"agent":"ride"}', encoding="utf-8")
    (scenario / "scenario.json").write_text('{"name":"one"}', encoding="utf-8")
    ignored = root / "webrtc-runs" / "old"
    ignored.mkdir(parents=True)
    (ignored / "recording.wav").write_bytes(b"not an authoring input")

    body = pack_authoring_archive(root)

    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        assert sorted(archive.getnames()) == [
            "contract.json",
            "scenarios/one/scenario.json",
        ]


def test_fresh_authoring_archive_rejects_missing_scenarios(tmp_path):
    (tmp_path / "contract.json").write_text("{}", encoding="utf-8")

    with pytest.raises(HostedHarnessError, match="without scenario artifacts"):
        pack_authoring_archive(tmp_path)


@pytest.mark.django_db
def test_unified_progress_freezes_authoring_for_saved_reruns(organization):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="freeze-unified-authoring"
    )
    attempt = SimpleNamespace(id="attempt-1", job_id=job.id)
    files = {
        "/work/authoring/contract.json": b'{"modality":"voice"}',
        "/work/authoring/environment-bundle/environment-plan.json": b'{"runtime":{}}',
        "/work/authoring/scenarios.json": b'[{"scenario_key":"one"}]',
        "/work/bundle/manifest.json": b'{"schema_version":"v2"}',
        "/tmp/authoring-rerun.tar.gz": b"frozen-authoring",
    }
    sandbox = SimpleNamespace(
        fs=SimpleNamespace(download_file=lambda path: files[path]),
        process=SimpleNamespace(
            exec=lambda command, **kwargs: SimpleNamespace(exit_code=0, result="")
        ),
    )

    with patch(
        "simulate.services.hosted_harness_gateway.store_authoring_archive"
    ) as store:
        DaytonaHostedGateway._sync_authoring_progress(attempt, sandbox)

    store.assert_called_once_with(job, b"frozen-authoring", advance_lifecycle=False)


def test_voice_authoring_resolves_auto_connector_with_livekit_credentials(tmp_path):
    root = tmp_path / "authoring"
    scenario = root / "scenarios" / "one"
    scenario.mkdir(parents=True)
    (root / "contract.json").write_text('{"modality":"voice"}', encoding="utf-8")
    (scenario / "scenario.json").write_text('{"name":"one"}', encoding="utf-8")
    payload = _payload()
    payload["agent"] = {
        "connector": "auto",
        "config": {},
        "secret_refs": {
            "LIVEKIT_URL": {},
            "LIVEKIT_API_KEY": {},
            "LIVEKIT_API_SECRET": {},
        },
    }

    resolved = resolve_authored_connector(payload, pack_authoring_archive(root))

    assert resolved["agent"]["connector"] == "livekit"
    assert payload["agent"]["connector"] == "auto"


def test_authored_connector_never_overrides_explicit_or_ambiguous_input(tmp_path):
    root = tmp_path / "authoring"
    scenario = root / "scenarios" / "one"
    scenario.mkdir(parents=True)
    (root / "contract.json").write_text('{"modality":"voice"}', encoding="utf-8")
    (scenario / "scenario.json").write_text('{"name":"one"}', encoding="utf-8")
    body = pack_authoring_archive(root)
    explicit = _payload()
    explicit["agent"]["connector"] = "retell"
    ambiguous = _payload()
    ambiguous["agent"]["connector"] = "auto"

    assert resolve_authored_connector(explicit, body)["agent"]["connector"] == "retell"
    assert resolve_authored_connector(ambiguous, body)["agent"]["connector"] == "auto"


def test_dispatch_payload_mirrors_only_livekit_url():
    payload = {"agent": {"connector": "livekit", "config": {}}}

    dispatched = prepare_dispatch_payload(
        payload,
        {
            "LIVEKIT_URL": "wss://customer.livekit.cloud",
            "LIVEKIT_API_KEY": "must-not-be-copied",
            "LIVEKIT_API_SECRET": "must-not-be-copied",
        },
    )

    assert dispatched["agent"]["config"] == {
        "livekit_url": "wss://customer.livekit.cloud"
    }
    assert payload["agent"]["config"] == {}
    assert "must-not-be-copied" not in json.dumps(dispatched)


@pytest.mark.parametrize("connector", ["vapi", "retell"])
def test_dispatch_payload_uses_platform_livekit_url_for_provider_voice(connector):
    payload = {"agent": {"connector": connector, "config": {}}}

    dispatched = prepare_dispatch_payload(
        payload,
        {"VAPI_API_KEY": "target-only"},
        simulator_secrets={
            "LIVEKIT_URL": "wss://platform-livekit.example",
            "LIVEKIT_API_KEY": "must-not-be-copied",
            "LIVEKIT_API_SECRET": "must-not-be-copied",
        },
    )

    assert dispatched["agent"]["config"] == {
        "livekit_url": "wss://platform-livekit.example"
    }
    assert "must-not-be-copied" not in json.dumps(dispatched)


@pytest.mark.django_db
def test_gateway_clones_exact_commit_without_archiving_git_credentials(
    organization, monkeypatch
):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="source-gateway"
    )
    observed_commands = []
    observed_token = []

    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway.GitHubAppTokenProvider.from_settings",
        lambda: SimpleNamespace(
            credential=lambda _: nullcontext("installation-secret-token")
        ),
    )

    def run(command, **kwargs):
        observed_commands.append(command)
        if kwargs.get("env", {}).get("GIT_CONFIG_VALUE_0"):
            observed_token.append(kwargs["env"]["GIT_CONFIG_VALUE_0"])
        if command[:2] == ["git", "init"]:
            checkout = command[-1]
            from pathlib import Path

            path = Path(checkout)
            path.mkdir(parents=True)
            (path / ".git").mkdir()
            (path / "agent.py").write_text("print('agent')", encoding="utf-8")
        if "rev-parse" in command:
            return SimpleNamespace(returncode=0, stdout="a" * 40 + "\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("simulate.services.hosted_harness_gateway.subprocess.run", run)
    archive, commit_sha = HostedSourceAcquirer().acquire(job)

    assert commit_sha == "a" * 40
    assert all(
        "installation-secret-token" not in " ".join(cmd) for cmd in observed_commands
    )
    assert observed_token == ["Authorization: Bearer installation-secret-token"] * 4
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        names = set(tar.getnames())
    assert "source/agent.py" in names
    assert all(".git" not in name for name in names)


class _Filesystem:
    def __init__(self):
        self.uploads = {}

    def upload_file(self, content, path):
        self.uploads[path] = content

    def download_file(self, path):
        return b"command-1"


class _Process:
    def __init__(self):
        self.exec_calls = []
        self.sessions = []

    def exec(self, command, **kwargs):
        self.exec_calls.append(command)
        return SimpleNamespace(exit_code=0, result="")

    def create_session(self, session_id):
        self.sessions.append(session_id)

    def execute_session_command(self, session_id, request):
        self.session_request = request
        return SimpleNamespace(cmd_id="command-1")

    def get_session_command(self, session_id, command_id):
        return SimpleNamespace(exit_code=None, status="running")


class _Sandbox:
    def __init__(self):
        self.id = "sandbox-1"
        self.fs = _Filesystem()
        self.process = _Process()


class _Daytona:
    def __init__(self):
        self.sandbox = _Sandbox()
        self.params = None
        self.deleted = False

    def create(self, params, **kwargs):
        self.params = params
        return self.sandbox

    def get(self, sandbox_id):
        return self.sandbox

    def delete(self, sandbox, **kwargs):
        self.deleted = True


@pytest.mark.django_db
def test_daytona_launch_uploads_contract_files_and_starts_one_session(
    organization, settings, monkeypatch
):
    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(organization, payload, idempotency_key="launch-gateway")
    client = _Daytona()
    gateway = object.__new__(DaytonaHostedGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = "sha256:" + "b" * 64
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = ["ingest.example.com"]
    settings.ALK_HOSTED_AUTHORING_MAX_DURATION_SECONDS = 3600
    settings.ALK_HOSTED_SANDBOX_TTL_SECONDS = 7200
    simulator_values = {
        "ALK_HARNESS": "vertex-gemini",
        "ALK_HARNESS_MODEL": "gemini-2.5-flash",
        "DEEPGRAM_API_KEY": "platform-simulator-deepgram",
        "GOOGLE_APPLICATION_CREDENTIALS": _SIMULATOR_VERTEX_CREDENTIALS_PATH,
        "GOOGLE_CLOUD_PROJECT": "platform-simulator-project",
        "GOOGLE_CLOUD_LOCATION": "global",
    }
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway._platform_simulator_material",
        lambda: (simulator_values, b'{"project_id":"platform-simulator-project"}'),
    )

    attempt = gateway.launch(job, endpoint_base_url="https://platform.example.com")

    assert attempt.state == HostedHarnessAttempt.State.RUNNING
    assert attempt.provider_ref == "sandbox-1"
    assert set(client.sandbox.fs.uploads) >= {
        "/work/source.tar.gz",
        "/work/job.json",
        "/run/futureagi/secrets.json",
        _SIMULATOR_SECRETS_PATH,
        _SIMULATOR_VERTEX_CREDENTIALS_PATH,
        "/run/futureagi/capabilities.json",
        "/run/futureagi/entrypoint-command-id",
    }
    assert client.sandbox.process.sessions == ["alk-harness"]
    assert json.loads(client.sandbox.fs.uploads["/run/futureagi/secrets.json"]) == {}
    assert json.loads(client.sandbox.fs.uploads[_SIMULATOR_SECRETS_PATH]) == (
        simulator_values
    )
    assert b"platform-simulator-deepgram" not in (
        client.sandbox.process.session_request.command.encode()
    )
    assert "--adjustments /run/futureagi/adjustments.jsonl" in (
        client.sandbox.process.session_request.command
    )
    # Authoring and call execution are distinct bounded phases. The sandbox must survive the
    # former rather than using only the 10-minute call-runtime budget plus two minutes.
    assert client.params.ttl_minutes == 120
    prepare_command = client.sandbox.process.exec_calls[0]
    assert not prepare_command.startswith("mkdir -p /work/authoring")
    assert (
        "if [ -f /work/authoring.tar.gz ]; then mkdir -p /work/authoring"
        in prepare_command
    )
    # Egress domains present -> allowlist-only egress: block_all is False and the
    # domain_allow_list is the deny-by-default. block_all True is mutually
    # exclusive with an allow-list in Daytona (verified live), so the allow-list
    # itself enforces the boundary.
    assert client.params.network_block_all is False
    assert set(client.params.domain_allow_list.split(",")) == {
        "aiplatform.googleapis.com",
        "agent.example.com",
        "api.deepgram.com",
        "api.vapi.ai",
        "global-aiplatform.googleapis.com",
        "ingest.example.com",
        "oauth2.googleapis.com",
        "platform.example.com",
        "us-east5-aiplatform.googleapis.com",
    }


@pytest.mark.django_db
def test_unified_provider_import_authoring_receives_one_shot_target_key(
    organization, settings, monkeypatch
):
    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    payload["agent"] = {
        "connector": "retell",
        "mode": "provider_import",
        "config": {"agent_id": "source-retell-agent"},
        "secret_refs": {"RETELL_API_KEY": {}},
    }
    job, _ = create_hosted_job(
        organization, payload, idempotency_key="unified-provider-import-authoring"
    )
    client = _Daytona()
    gateway = object.__new__(DaytonaHostedGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = []
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway.PlatformSecretResolver.resolve",
        lambda _self, _job: {
            "RETELL_API_KEY": "retell-target-secret",
            "UNRELATED_AGENT_SECRET": "must-not-enter-authoring-file",
        },
    )
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway._platform_simulator_material",
        lambda: ({}, None),
    )

    gateway.launch(job, endpoint_base_url="https://platform.example.com")

    one_shot_path = "/run/futureagi/authoring-target-secrets.json"
    assert json.loads(client.sandbox.fs.uploads[one_shot_path]) == {
        "RETELL_API_KEY": "retell-target-secret"
    }
    command = client.sandbox.process.session_request.command
    assert f"--target-secrets {one_shot_path}" in command
    assert "--provider-profile-cache /work/provider-import-profile.json" in command
    assert "retell-target-secret" not in command
    assert "must-not-enter-authoring-file" not in client.sandbox.fs.uploads[one_shot_path].decode()
    assert one_shot_path in client.sandbox.process.exec_calls[0]


@pytest.mark.django_db
def test_daytona_adjustment_is_persisted_and_delivered_to_active_authoring(
    organization, settings, tmp_path
):
    payload = _payload()
    payload["scenario_count"] = 2
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(organization, payload, idempotency_key="adjust-gateway")
    client = _Daytona()
    gateway = object.__new__(DaytonaHostedGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""
    dockerfile = tmp_path / "Dockerfile.hosted"
    dockerfile.write_text("FROM python:3.12-slim\n", encoding="utf-8")
    gateway.dockerfile = str(dockerfile)
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = ["ingest.example.com"]
    with patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    ):
        gateway.launch(job, endpoint_base_url="https://platform.example.com")
    job.refresh_from_db()
    attempt = HostedHarnessAttempt.no_workspace_objects.get(
        job=job, attempt_number=job.current_attempt_number
    )
    assert attempt.snapshot_name == "direct-image-adjustments-v1"

    adjusted = gateway.adjust(
        job,
        {
            "instruction": "Create 1 more scenario covering discounts",
            "client_request_id": "browser-1",
        },
    )

    assert adjusted.scenario_count == 3
    records = [
        json.loads(line)
        for line in client.sandbox.fs.uploads["/run/futureagi/adjustments.jsonl"]
        .decode()
        .splitlines()
    ]
    assert records[0]["target_stage"] == "scenarios"
    assert records[0]["scenario_delta"] == 1
    assert records[0]["status"] == "pending"
    assert adjusted.payload["metadata"]["adjustments"] == records
    assert adjusted.payload["scenario_count"] == 3


@pytest.mark.django_db
def test_daytona_retry_replays_persisted_adjustments_before_authoring(
    organization, settings
):
    payload = _payload()
    payload["scenario_count"] = 2
    payload["metadata"] = {
        "adjustments": [
            {
                "adjustment_id": "adjustment-1",
                "instruction": "Create one more discount scenario",
                "target_stage": "scenarios",
                "scenario_delta": 1,
                "status": "applied",
            }
        ]
    }
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(
        organization, payload, idempotency_key="retry-adjustment-replay"
    )
    client = _Daytona()
    gateway = object.__new__(DaytonaHostedGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = ["ingest.example.com"]

    with patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    ):
        gateway.launch(job, endpoint_base_url="https://platform.example.com")

    replayed = [
        json.loads(line)
        for line in client.sandbox.fs.uploads[_ADJUSTMENTS_PATH].decode().splitlines()
    ]
    assert replayed == payload["metadata"]["adjustments"]
    assert client.sandbox.process.session_request is not None


@pytest.mark.django_db
def test_daytona_adjustment_accepts_natural_language_number(
    organization, settings, tmp_path
):
    payload = _payload()
    payload["scenario_count"] = 2
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(
        organization, payload, idempotency_key="adjust-word-number"
    )
    client = _Daytona()
    gateway = object.__new__(DaytonaHostedGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""
    dockerfile = tmp_path / "Dockerfile.hosted"
    dockerfile.write_text("FROM python:3.12-slim\n", encoding="utf-8")
    gateway.dockerfile = str(dockerfile)
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = ["ingest.example.com"]
    with patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    ):
        gateway.launch(job, endpoint_base_url="https://platform.example.com")
    job.refresh_from_db()

    adjusted = gateway.adjust(
        job,
        {
            "instruction": "Add one more scenario covering discounts",
            "client_request_id": "browser-word-1",
        },
    )

    assert adjusted.scenario_count == 3
    assert adjusted.payload["scenario_count"] == 3
    assert adjusted.payload["metadata"]["adjustments"][0]["scenario_delta"] == 1


@pytest.mark.django_db
def test_daytona_livekit_launch_uses_coturn_domain_allowlist(
    organization, settings, monkeypatch
):
    payload = _payload()
    payload["agent"]["connector"] = "livekit"
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(
        organization, payload, idempotency_key="launch-livekit-webrtc"
    )
    client = _Daytona()
    gateway = object.__new__(DaytonaHostedGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = "sha256:" + "b" * 64
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = [
        "ingest.example.com",
        "coturn.turn-eu.futureagi.com",
    ]
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway._platform_simulator_material",
        lambda: ({}, None),
    )

    gateway.launch(job, endpoint_base_url="https://platform.example.com")

    assert client.params.network_allow_list is None
    assert set(client.params.domain_allow_list.split(",")) == {
        "agent.example.com",
        "coturn.turn-eu.futureagi.com",
        "ingest.example.com",
        "platform.example.com",
    }


@pytest.mark.django_db
def test_cancel_writes_reason_signals_guest_and_holds_before_delete(
    organization, monkeypatch
):
    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(organization, payload, idempotency_key="cancel-gateway")
    client = _Daytona()
    gateway = object.__new__(DaytonaHostedGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""
    with patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    ):
        gateway.launch(job, endpoint_base_url="https://platform.example.com")
    observations = iter(({"exit_code": None}, {"exit_code": 0}))
    monkeypatch.setattr(gateway, "inspect", lambda _: next(observations))
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway.time.sleep", lambda _: None
    )
    monkeypatch.setattr(gateway, "_delete_and_record", lambda _: job)

    gateway.cancel(job, reason="user_canceled")

    assert (
        b'"reason":"user_canceled"'
        in client.sandbox.fs.uploads["/run/futureagi/cancel.json"]
    )
    assert any(
        "pkill -TERM" in command for command in client.sandbox.process.exec_calls
    )
    attempt = HostedHarnessAttempt.no_workspace_objects.get(job=job)
    assert attempt.terminal_stage == "canceled"
    assert attempt.terminal_reason == "user_canceled"
    assert attempt.terminal_failure is None


@pytest.mark.django_db
def test_cancel_immediately_projects_cleaning_up_stage(organization):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="cancel-visible-stage"
    )

    canceled = request_cancellation(job, "user_canceled")

    assert canceled.state == HostedHarnessJob.State.CLEANING_UP
    assert canceled.current_stage == HostedHarnessJob.State.CLEANING_UP
    assert canceled.cancel_requested_at is not None


@pytest.mark.django_db
def test_reconcile_relaunches_infra_failure_until_budget_then_fails(
    organization, monkeypatch
):
    from simulate.services.hosted_harness import record_cleanup

    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(organization, payload, idempotency_key="retry-gateway")
    gateway = object.__new__(DaytonaHostedGateway)
    gateway.client = _Daytona()
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""

    def _fake_delete(attempt, *, retry_pending=False):
        return record_cleanup(
            attempt.id,
            provider_ref=str(attempt.provider_ref),
            verified_absent=True,
            retry_pending=retry_pending,
            details={"provider": "test"},
        )

    monkeypatch.setattr(gateway, "_delete_and_record", _fake_delete)
    acquire = patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    )

    # Attempt 1 crashes (exit 2): infrastructure failure, 1 < 2 -> RETRY_WAIT.
    with acquire:
        attempt1 = gateway.launch(job, endpoint_base_url="https://platform.example.com")
    assert attempt1.attempt_number == 1
    monkeypatch.setattr(gateway, "inspect", lambda _: {"exit_code": 2, "logs": "boom"})
    assert gateway.reconcile_completed(attempt1).state == (
        HostedHarnessJob.State.RETRY_WAIT
    )

    # Relaunch a fresh attempt; register_attempt supersedes attempt 1.
    with acquire:
        attempt2 = gateway.launch(job, endpoint_base_url="https://platform.example.com")
    assert attempt2.attempt_number == 2
    # Attempt 2 crashes again: budget spent (2 not < 2) -> terminal FAILED.
    assert gateway.reconcile_completed(attempt2).state == HostedHarnessJob.State.FAILED


@pytest.mark.django_db
def test_reconcile_exit3_and_exit0_never_retry(organization, monkeypatch):
    from simulate.services.hosted_harness import record_cleanup

    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(organization, payload, idempotency_key="noretry-gateway")
    gateway = object.__new__(DaytonaHostedGateway)
    gateway.client = _Daytona()
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""
    captured = {}

    def _fake_delete(attempt, *, retry_pending=False):
        captured["retry_pending"] = retry_pending
        return record_cleanup(
            attempt.id,
            provider_ref=str(attempt.provider_ref),
            verified_absent=True,
            retry_pending=retry_pending,
            details={"provider": "test"},
        )

    monkeypatch.setattr(gateway, "_delete_and_record", _fake_delete)
    acquire = patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    )

    # Exit 3 (fenced/superseded): never retried.
    with acquire:
        attempt = gateway.launch(job, endpoint_base_url="https://platform.example.com")
    monkeypatch.setattr(gateway, "inspect", lambda _: {"exit_code": 3, "logs": ""})
    assert gateway.reconcile_completed(attempt).state != (
        HostedHarnessJob.State.RETRY_WAIT
    )
    assert captured["retry_pending"] is False

    # Exit 0 (terminal reached / verdict delivered): never retried.
    with acquire:
        attempt = gateway.launch(job, endpoint_base_url="https://platform.example.com")
    monkeypatch.setattr(gateway, "inspect", lambda _: {"exit_code": 0, "logs": ""})
    assert gateway.reconcile_completed(attempt).state != (
        HostedHarnessJob.State.RETRY_WAIT
    )
    assert captured["retry_pending"] is False


@pytest.mark.django_db
def test_reconcile_exit0_refreshes_terminal_delivery_flags(organization, monkeypatch):
    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(
        organization, payload, idempotency_key="terminal-refresh-gateway"
    )
    gateway = object.__new__(DaytonaHostedGateway)
    gateway.client = _Daytona()
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""
    with patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    ):
        stale_attempt = gateway.launch(
            job, endpoint_base_url="https://platform.example.com"
        )

    # Model the ingestion requests committing after launch returned but before the provider poll
    # observed process exit.  `stale_attempt` deliberately still carries both False values.
    HostedHarnessAttempt.no_workspace_objects.filter(id=stale_attempt.id).update(
        terminal_event_received=True,
        manifest_acked=True,
        terminal_stage="completed",
        terminal_failure=None,
    )
    assert stale_attempt.terminal_event_received is False
    assert stale_attempt.manifest_acked is False

    captured = {}

    def fake_delete(attempt, *, retry_pending=False):
        captured["attempt"] = attempt
        return attempt.job

    monkeypatch.setattr(gateway, "_delete_and_record", fake_delete)
    monkeypatch.setattr(
        gateway,
        "inspect",
        lambda _: {"exit_code": 0, "logs": "", "process_logs": ""},
    )

    gateway.reconcile_completed(stale_attempt)

    refreshed = captured["attempt"]
    assert refreshed.terminal_event_received is True
    assert refreshed.manifest_acked is True
    assert refreshed.terminal_stage == "completed"
    assert refreshed.terminal_failure is None
