from __future__ import annotations

import re
from typing import Any

from django.conf import settings
from rest_framework import serializers

from simulate.serializers.hosted_harness_conversation import (
    HarnessConversationReadSerializer,
)

# Port-generic loopback pattern for the C4 §7 Channel-1 literal-endpoint scan.
# The declared fixed port is unknowable platform-side (the bundle is authored
# in-sandbox), so the match is any port on localhost / 127.0.0.1 / [::1].
_LOOPBACK_ENDPOINT_RE = re.compile(r"(?:localhost|127\.0\.0\.1|\[::1\]):\d+")

RUNNER_RESERVED_ENVIRONMENT = {
    "DOCKER_HOST",
    "FI_API_KEY",
    "FI_BASE_URL",
    "FI_SECRET_KEY",
    "HARNESS_PLATFORM_API_KEY",
    "HARNESS_PLATFORM_SECRET_KEY",
    "HARNESS_PLATFORM_URL",
    "HOME",
    "PATH",
    "PYTHONPATH",
}


class SecretReferenceSerializer(serializers.Serializer):
    manager = serializers.ChoiceField(choices=("platform-vault", "platform-config"))
    key = serializers.CharField(max_length=255)
    version = serializers.CharField(max_length=255, required=False, allow_null=True)
    purpose = serializers.ChoiceField(
        choices=("target_provider", "simulator_provider", "source_checkout")
    )


class HarnessSourceSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=("github", "archive", "remote", "provider"))
    repository = serializers.RegexField(
        r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", required=False, allow_null=True
    )
    ref = serializers.RegexField(r"^[A-Za-z0-9._/-]+$", required=False, allow_null=True)
    commit_sha = serializers.RegexField(
        r"^[0-9a-fA-F]{40}$", required=False, allow_null=True
    )
    installation_id = serializers.CharField(
        max_length=255, required=False, allow_null=True
    )
    archive_artifact_id = serializers.UUIDField(required=False, allow_null=True)
    endpoint = serializers.URLField(required=False, allow_null=True)
    visibility = serializers.ChoiceField(
        choices=("public", "private"), default="public"
    )
    # Inline plaintext environment values (C4 §7 Channel 1). The most
    # platform-visible literal-endpoint channel: scanned at submit for W>1
    # requests (HarnessJobCreateSerializer.validate).
    environment_values = serializers.DictField(
        child=serializers.CharField(max_length=65_536, trim_whitespace=False),
        required=False,
    )

    def validate(self, attrs):
        kind = attrs["kind"]
        if kind == "github":
            if not attrs.get("repository"):
                raise serializers.ValidationError(
                    {"repository": "required for github sources"}
                )
            if attrs["visibility"] == "private" and not attrs.get("installation_id"):
                raise serializers.ValidationError(
                    {"installation_id": "required for private github sources"}
                )
        elif kind == "archive" and not attrs.get("archive_artifact_id"):
            raise serializers.ValidationError(
                {"archive_artifact_id": "required for archive sources"}
            )
        elif kind == "remote" and not attrs.get("endpoint"):
            raise serializers.ValidationError(
                {"endpoint": "required for remote sources"}
            )
        return attrs


# Connectors whose transport carries audio. ``auto`` is unresolved at admission
# time, so it is permitted here and settled during authoring.
VOICE_CONNECTORS = ("livekit", "vapi", "retell", "phone")

# Connectors that reach an already-running agent, so the run carries no source tree.
PROVIDER_TARGET_CONNECTORS = {"vapi", "retell", "retell_chat", "phone"}

# Mirrors the SDK's own E.164 rule so a malformed number is refused at admission.
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")

TARGET_SYSTEM_PROMPT_MAX_CHARS = 65_536


class HarnessAgentSerializer(serializers.Serializer):
    connector = serializers.ChoiceField(
        choices=("livekit", "vapi", "retell", "retell_chat", "phone", "auto")
    )
    mode = serializers.ChoiceField(
        choices=("connect_only", "environment_backed", "provider_import"),
        required=False,
        allow_null=True,
    )
    # Who places the call. Declared on the agent rather than left to an
    # ``config`` scalar so an unsupported value is refused at admission instead
    # of riding to the guest and being ignored there.
    call_direction = serializers.ChoiceField(
        choices=("inbound", "outbound"),
        required=False,
        allow_null=True,
        help_text=(
            "inbound: the simulated caller dials the agent. outbound: the agent "
            "dials the simulated caller. Voice connectors only."
        ),
    )
    config = serializers.DictField(default=dict)
    secret_refs = serializers.DictField(
        child=SecretReferenceSerializer(), required=False, default=dict
    )

    def validate_config(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("config must be an object")
        for name in ("inbound", "target_speaks_first"):
            if name in value and not isinstance(value[name], bool):
                raise serializers.ValidationError(f"{name} must be a boolean")
        secret_names = ("token", "secret", "password", "api_key", "private_key")
        invalid = []
        for key, item in value.items():
            if any(marker in str(key).lower() for marker in secret_names):
                invalid.append(str(key))
                continue
            if key == "dynamic_variables" and isinstance(item, dict):
                if all(
                    isinstance(name, str) and isinstance(entry, (str, int, float, bool))
                    for name, entry in item.items()
                ):
                    continue
            if not isinstance(item, (str, int, float, bool)):
                invalid.append(str(key))
        if invalid:
            raise serializers.ValidationError(
                "config must contain scalar non-secret values; move "
                f"{', '.join(sorted(invalid))} to credential values/secret_refs"
            )
        return value

    def validate_secret_refs(self, value):
        for alias, reference in value.items():
            if not alias or not alias.replace("_", "").isalnum():
                raise serializers.ValidationError(
                    f"invalid environment-variable alias: {alias!r}"
                )
            if alias.startswith("SIMULATOR_"):
                raise serializers.ValidationError(
                    "SIMULATOR_ aliases are reserved for platform-managed credentials"
                )
            if reference["manager"] != "platform-vault":
                raise serializers.ValidationError(
                    "agent secret_refs only accept manager platform-vault"
                )
            if reference["purpose"] != "target_provider":
                raise serializers.ValidationError(
                    "agent secret_refs only accept purpose target_provider"
                )
        return value

    def validate(self, attrs):
        connector = attrs["connector"]
        mode = attrs.get("mode")
        config = attrs.get("config") or {}
        provider_connector = "retell" if connector == "retell_chat" else connector
        # A chat target has no call to place in either direction, so a direction
        # here means the caller has the wrong connector rather than a preference
        # worth silently dropping.
        if attrs.get("call_direction") and connector not in (
            *VOICE_CONNECTORS,
            "auto",
        ):
            raise serializers.ValidationError(
                {
                    "call_direction": (
                        "call_direction applies to voice connectors "
                        f"({', '.join(VOICE_CONNECTORS)}); {connector} is chat"
                    )
                }
            )
        if "phone_number" in config and connector != "phone":
            if connector not in {"vapi", "retell"} or mode != "connect_only":
                raise serializers.ValidationError(
                    {
                        "config": "phone_number is supported only for connect-only Vapi or Retell voice agents"
                    }
                )
            if not _E164.fullmatch(str(config.get("phone_number") or "").strip()):
                raise serializers.ValidationError(
                    {"config": "phone_number must be in E.164 format"}
                )
            if any(
                str(name).lower().startswith(("sip_", "livekit_")) for name in config
            ):
                raise serializers.ValidationError(
                    {"config": "phone dialer and LiveKit settings are platform-owned"}
                )
            target_key = "assistant_id" if connector == "vapi" else "agent_id"
            key_alias = "VAPI_API_KEY" if connector == "vapi" else "RETELL_API_KEY"
            has_id = bool(str(config.get(target_key) or "").strip())
            has_key = bool((attrs.get("secret_refs") or {}).get(key_alias))
            if has_id != has_key:
                raise serializers.ValidationError(
                    {
                        "config": "Supply both provider API key and agent ID, or neither and paste the system prompt"
                    }
                )
            if not has_id:
                attrs["connector"] = connector = provider_connector = "phone"
        if mode and provider_connector not in {"vapi", "retell", "phone"}:
            raise serializers.ValidationError(
                {"mode": "provider mode is supported only for Vapi, Retell, and phone"}
            )
        if connector == "phone":
            if mode != "connect_only":
                raise serializers.ValidationError(
                    {"mode": "phone targets support connect_only only"}
                )
            forbidden = {
                str(name)
                for name in config
                if str(name).lower().startswith(("sip_", "livekit_"))
            }
            if forbidden:
                raise serializers.ValidationError(
                    {"config": "phone dialer and LiveKit settings are platform-owned"}
                )
            if not _E164.fullmatch(str(config.get("phone_number") or "").strip()):
                raise serializers.ValidationError(
                    {
                        "config": "phone_number must be in E.164 format, e.g. +14155551234"
                    }
                )
            prompt = str(config.get("target_system_prompt") or "").strip()
            if not prompt or len(prompt) > 65536:
                raise serializers.ValidationError(
                    {
                        "config": "target_system_prompt is required (maximum 65536 characters)"
                    }
                )
            return attrs
        if mode == "connect_only":
            target_key = "assistant_id" if provider_connector == "vapi" else "agent_id"
            if not str(config.get(target_key) or "").strip():
                raise serializers.ValidationError(
                    {"config": f"{target_key} is required for connect_only"}
                )
        if mode == "environment_backed":
            if connector == "retell_chat":
                raise serializers.ValidationError(
                    {
                        "mode": "native Retell chat supports connect_only and provider_import"
                    }
                )
            if "assistant_id" in config or "agent_id" in config:
                raise serializers.ValidationError(
                    {"config": "the provider target ID is produced by repository code"}
                )
            manifest = str(config.get("lifecycle_manifest") or "alk.yaml")
            if manifest.startswith("/") or ".." in manifest.split("/"):
                raise serializers.ValidationError(
                    {"config": "lifecycle_manifest must be repository-relative"}
                )
        if mode == "provider_import":
            target_key = "assistant_id" if provider_connector == "vapi" else "agent_id"
            if not str(config.get(target_key) or "").strip():
                raise serializers.ValidationError(
                    {"config": f"{target_key} is required for provider_import"}
                )
            for path_key in ("event_path", "tool_path"):
                path = str(config.get(path_key) or "")
                if path and (
                    not path.startswith("/")
                    or path.startswith("//")
                    or ".." in path.split("/")
                ):
                    raise serializers.ValidationError(
                        {"config": f"{path_key} must be an absolute safe URL path"}
                    )
        return attrs


class HarnessRuntimeSerializer(serializers.Serializer):
    isolation = serializers.ChoiceField(
        choices=("dedicated_vm",), default="dedicated_vm"
    )
    cpu_units = serializers.IntegerField(default=4, min_value=1)
    memory_mb = serializers.IntegerField(default=8192, min_value=1024)
    parallelism = serializers.IntegerField(default=1, min_value=1, max_value=8)
    concurrency_weight = serializers.IntegerField(default=1, min_value=1, max_value=10)
    max_duration_seconds = serializers.IntegerField(
        default=3600, min_value=60, max_value=86400
    )
    network_policy = serializers.ChoiceField(choices=("live",), default="live")


class HarnessSecuritySerializer(serializers.Serializer):
    untrusted_source = serializers.BooleanField(default=True)
    read_only_source = serializers.BooleanField(default=True)
    allow_privileged = serializers.BooleanField(default=False)
    allow_host_runtime_control = serializers.BooleanField(default=False)
    allowed_egress_domains = serializers.ListField(
        child=serializers.RegexField(r"^(?:[A-Za-z0-9-]+\.)*[A-Za-z0-9-]+$"),
        default=list,
    )

    def validate(self, attrs):
        if (
            not attrs["untrusted_source"]
            or not attrs["read_only_source"]
            or attrs["allow_privileged"]
            or attrs["allow_host_runtime_control"]
        ):
            raise serializers.ValidationError(
                "hosted security invariants cannot be relaxed"
            )
        return attrs


class HarnessRetrySerializer(serializers.Serializer):
    max_infrastructure_attempts = serializers.IntegerField(
        default=2, min_value=1, max_value=5
    )
    initial_backoff_seconds = serializers.FloatField(
        default=1, min_value=0, max_value=60
    )
    max_backoff_seconds = serializers.FloatField(default=15, min_value=0, max_value=300)
    retryable_domains = serializers.ListField(
        child=serializers.ChoiceField(
            choices=("infrastructure", "connectivity", "platform_sync")
        ),
        default=lambda: ["infrastructure", "connectivity"],
    )

    def validate(self, attrs):
        if attrs["max_backoff_seconds"] < attrs["initial_backoff_seconds"]:
            raise serializers.ValidationError(
                "max_backoff_seconds must be >= initial_backoff_seconds"
            )
        return attrs


class HarnessArtifactSerializer(serializers.Serializer):
    level = serializers.ChoiceField(
        choices=("metadata-only", "traces", "traces-and-recordings", "full")
    )
    retention_days = serializers.IntegerField(default=30, min_value=1, max_value=3650)
    allow_bundle_download = serializers.BooleanField(default=False)
    max_artifact_bytes = serializers.IntegerField(default=1_073_741_824, min_value=0)


class HarnessJobCreateSerializer(serializers.Serializer):
    # Create refuses a job whose connector has no credentials; preflight reports them as
    # unmet requirements instead, so the readiness panel can tell the user what to add.
    reject_missing_credentials = True
    schema_version = serializers.ChoiceField(
        choices=("futureagi.harness-job.v1",),
        default="futureagi.harness-job.v1",
    )
    run_id = serializers.UUIDField(required=False)
    source = HarnessSourceSerializer(required=False)
    agent = HarnessAgentSerializer()
    scenario_count = serializers.IntegerField(default=10, min_value=1, max_value=200)
    seed = serializers.IntegerField(required=False, allow_null=True)
    runtime = HarnessRuntimeSerializer(default=dict)
    security = HarnessSecuritySerializer(default=dict)
    retry = HarnessRetrySerializer(default=dict)
    artifacts = HarnessArtifactSerializer()
    platform_run_id = serializers.CharField(
        max_length=255, required=False, allow_null=True
    )
    metadata = serializers.DictField(default=dict)

    def validate_metadata(self, value):
        reserved = {
            "attempt_cycle_start",
            "parallelism_clamped",
            "parallelism_warnings",
        }
        if reserved.intersection(value):
            raise serializers.ValidationError(
                "metadata contains reserved execution-control keys"
            )
        return value

    def validate(self, attrs):
        # ``default=dict`` stores a literal ``{}`` for omitted nested objects,
        # which skips the child field defaults. Re-run the child serializer so
        # runtime/security/retry always carry their full defaults.
        for name in ("runtime", "security", "retry"):
            if not attrs.get(name):
                attrs[name] = self.fields[name].run_validation({})
        runtime = attrs["runtime"]
        agent = attrs["agent"]
        source = attrs.get("source")
        provider_target_mode = agent.get("mode") in {
            "connect_only",
            "provider_import",
        }
        if source is None:
            if (
                agent["connector"] not in {"vapi", "retell", "retell_chat", "phone"}
                or not provider_target_mode
            ):
                raise serializers.ValidationError(
                    {
                        "source": "required unless a hosted agent ID or phone number is connected"
                    }
                )
            attrs["source"] = {"kind": "provider", "visibility": "public"}
        elif source["kind"] == "provider" and (
            agent["connector"] not in {"vapi", "retell", "retell_chat", "phone"}
            or not provider_target_mode
        ):
            raise serializers.ValidationError(
                {
                    "source": "provider sources require a connected Vapi/Retell agent or phone number"
                }
            )
        # Voice scenarios are intentionally sequential by default and may each consume the
        # full conversation plus teardown/retry allowance.  A fixed one-hour ceiling made
        # otherwise healthy large runs expire partway through (typically around scenario
        # 20-30).  Enforce the same conservative per-scenario budget server-side so older
        # UIs and direct API clients cannot reintroduce that failure mode.
        if attrs["scenario_count"] > 10:
            runtime["max_duration_seconds"] = max(
                runtime["max_duration_seconds"], attrs["scenario_count"] * 360
            )
        self._apply_sandbox_runtime_limits(runtime)
        connector = agent["connector"]
        if connector in {"livekit", "vapi", "retell", "phone", "auto"} and (
            runtime["parallelism"] > runtime["cpu_units"]
        ):
            raise serializers.ValidationError(
                {"runtime": "voice parallelism must not exceed cpu_units"}
            )
        if attrs["source"]["kind"] == "remote" and agent["secret_refs"]:
            raise serializers.ValidationError(
                {"agent": "remote sources must own their target credentials"}
            )
        if self.reject_missing_credentials and attrs["source"]["kind"] != "remote":
            connector = agent["connector"]
            missing = missing_provider_credentials(agent)
            if missing:
                raise serializers.ValidationError(
                    {
                        "agent": {
                            "secret_refs": (
                                f"{connector} target needs {', '.join(missing)} in "
                                "secret_refs; without them the agent can never register "
                                "and the run fails after the 180s startup check"
                            )
                        }
                    }
                )
        # An alias may not appear in BOTH the inline plaintext
        # source.environment_values (Channel 1) and agent.secret_refs — only
        # here are both operands visible (secret_refs lives on the sibling agent).
        source_environment = attrs["source"].get("environment_values") or {}
        if set(source_environment) & set(attrs["agent"]["secret_refs"]):
            raise serializers.ValidationError(
                "an environment variable cannot be both uploaded and a secret reference"
            )
        self._apply_parallelism_belt(attrs, runtime)
        return attrs

    @staticmethod
    def _apply_sandbox_runtime_limits(runtime: dict[str, Any]) -> None:
        """Resolve provider-owned limits before admission and persistence."""
        from simulate.services.hosted_sandbox import sandbox_runtime_policy

        policy = sandbox_runtime_policy()
        if policy.fixed_resources:
            cpu_units, memory_mb, _disk_gb = policy.fixed_resources
            runtime["cpu_units"] = cpu_units
            runtime["memory_mb"] = memory_mb
        if policy.max_ttl_seconds is None:
            return
        if policy.max_ttl_seconds <= 0:
            raise serializers.ValidationError(
                {"runtime": "sandbox provider maximum lifetime is not configured"}
            )
        authoring_ttl = int(getattr(settings, "ALK_HOSTED_AUTHORING_TIMEOUT", 3900))
        configured_ttl = int(getattr(settings, "ALK_HOSTED_SANDBOX_TTL_SECONDS", 7200))
        if max(authoring_ttl, configured_ttl) > policy.max_ttl_seconds:
            raise serializers.ValidationError(
                {"runtime": "configured sandbox lifetime exceeds the provider limit"}
            )
        authoring_seconds = max(
            0,
            int(
                getattr(
                    settings,
                    "ALK_HOSTED_AUTHORING_MAX_DURATION_SECONDS",
                    3600,
                )
            ),
        )
        execution_limit = policy.max_ttl_seconds - authoring_seconds - 120
        if execution_limit < 60:
            raise serializers.ValidationError(
                {"runtime": "sandbox lifetime leaves no supported execution window"}
            )
        runtime["max_duration_seconds"] = min(
            runtime["max_duration_seconds"], execution_limit
        )

    def _apply_parallelism_belt(self, attrs, runtime):
        """Create-time W>1 admission belt (C4 §5 / §7 Channel 1, Track E).

        This is the EARLY, FRIENDLY surface — ``register_attempt`` is the
        authoritative enforcement. It runs the SAME shared guard at submit so a
        fresh create gets immediate feedback: a W>1 request the guard denies is
        CLAMPED (recorded on ``metadata.parallelism_clamped``), never rejected,
        and the requested ``runtime.parallelism`` is preserved so a later rerun
        re-evaluates honestly. It additionally scans the inline plaintext
        ``source.environment_values`` for loopback literals and WARNS (never
        rejects) at W>1 — the primary platform literal-endpoint channel.
        """
        requested = runtime.get("parallelism") or 1
        if requested <= 1:
            return
        # Lazy import keeps the serializer module import-cycle-free.
        from simulate.services.harness_capacity import configured_capacity
        from simulate.services.hosted_harness import clamp_parallelism

        metadata = dict(attrs.get("metadata") or {})
        try:
            capacity = configured_capacity(attrs)
        except ValueError as exc:
            raise serializers.ValidationError({"runtime": str(exc)}) from exc
        from simulate.services.hosted_sandbox import sandbox_runtime_policy

        digest = capacity.snapshot_digest or sandbox_runtime_policy().digest
        admitted, _ = clamp_parallelism(requested, digest)
        admitted = min(admitted, capacity.parallelism)
        if admitted != requested:
            metadata["parallelism_clamped"] = {
                "requested": requested,
                "admitted": admitted,
            }
        environment_values = attrs["source"].get("environment_values") or {}
        flagged = sorted(
            alias
            for alias, value in environment_values.items()
            if isinstance(value, str) and _LOOPBACK_ENDPOINT_RE.search(value)
        )
        if flagged:
            warnings = list(metadata.get("parallelism_warnings") or [])
            warnings.append(
                {
                    "code": "literal_local_endpoint",
                    "channel": "environment_values",
                    "aliases": flagged,
                }
            )
            metadata["parallelism_warnings"] = warnings
        attrs["metadata"] = metadata


LIVEKIT_ALIASES = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
CONNECTOR_ALIASES = {
    "livekit": LIVEKIT_ALIASES,
    "vapi": ("VAPI_API_KEY",),
    "retell": ("RETELL_API_KEY",),
    "retell_chat": ("RETELL_API_KEY",),
    # The platform's simulator owns LiveKit/SIP credentials for a phone target.
    "phone": (),
}


def present_provider_aliases(agent) -> set[str]:
    """Target-provider aliases the job carries, by secret_ref or public config."""
    present = {str(name).upper() for name in (agent.get("secret_refs") or {})}
    config = agent.get("config") or {}
    if str(config.get("livekit_url") or config.get("LIVEKIT_URL") or "").strip():
        present.add("LIVEKIT_URL")
    return present


def complete_provider_families(agent) -> list[str]:
    """Connectors whose whole credential family the job carries."""
    present = present_provider_aliases(agent)
    return [
        connector
        for connector, aliases in CONNECTOR_ALIASES.items()
        if aliases and all(alias in present for alias in aliases)
    ]


def missing_provider_credentials(agent, detected_connectors=()):
    """Aliases the connector needs that the job does not carry.

    ``auto`` is unresolved at admission time: fresh authoring may discover a chat target that
    needs no voice-provider credential at all, so nothing is required by default. When the
    submitted source itself names a voice provider (``detected_connectors``), the run must carry
    at least one complete family or it can only fail after authoring has already been paid for.
    Any complete family satisfies the check: detection is a heuristic and the user may know
    better than a dependency grep, while "no credentials at all" is never right for a voice agent.
    """
    present = present_provider_aliases(agent)
    connector = agent["connector"]
    # The platform dials a phone target with its own dialler, so the run needs no
    # target-provider credential at all. Kept out of ``CONNECTOR_ALIASES`` so an empty
    # family cannot satisfy ``complete_provider_families`` for every other connector.
    if connector == "phone":
        return []
    if connector != "auto":
        return [alias for alias in CONNECTOR_ALIASES[connector] if alias not in present]
    detected = [name for name in detected_connectors if name in CONNECTOR_ALIASES]
    if not detected or complete_provider_families(agent):
        return []
    return [
        alias
        for name in detected
        for alias in CONNECTOR_ALIASES[name]
        if alias not in present
    ]


class HarnessJobActionSerializer(serializers.Serializer):
    reason = serializers.ChoiceField(
        choices=("user_canceled", "ttl_exceeded"), default="user_canceled"
    )


class HarnessPreflightSerializer(HarnessJobCreateSerializer):
    reject_missing_credentials = False
    # Raw target-provider values the form holds before Run stores them. Preflight exercises
    # them against the provider and discards them; they are never persisted or echoed.
    credential_values = serializers.DictField(
        child=serializers.CharField(allow_blank=True, max_length=4096),
        required=False,
        write_only=True,
        help_text="Target-provider values to verify live; used for this check only.",
    )


class HarnessPreflightCheckSerializer(serializers.Serializer):
    id = serializers.CharField()
    label = serializers.CharField()
    status = serializers.ChoiceField(choices=("passed", "failed", "skipped"))
    detail = serializers.CharField(allow_blank=True)
    missing = serializers.ListField(child=serializers.CharField())
    fix = serializers.CharField(allow_null=True)


class HarnessPreflightCredentialsSerializer(serializers.Serializer):
    scanned_files = serializers.IntegerField()
    detected_connectors = serializers.ListField(child=serializers.CharField())
    requirements = serializers.ListField(child=serializers.JSONField())
    credential_choices = serializers.ListField(child=serializers.JSONField())
    probe = serializers.ListField(child=serializers.JSONField())


class HarnessPreflightResponseSerializer(serializers.Serializer):
    ready_to_submit = serializers.BooleanField()
    state = serializers.ChoiceField(choices=("connected", "failed"))
    checks = HarnessPreflightCheckSerializer(many=True)
    credentials = HarnessPreflightCredentialsSerializer()
    parallelism_enabled = serializers.BooleanField()
    effective_parallelism = serializers.IntegerField()
    resource_profile = serializers.JSONField()
    snapshot = serializers.JSONField()


class HarnessJobAdjustmentSerializer(serializers.Serializer):
    instruction = serializers.CharField(
        min_length=1,
        max_length=2000,
        trim_whitespace=True,
        help_text="A user correction to apply at the next safe harness stage boundary.",
    )
    client_request_id = serializers.CharField(
        max_length=128, required=False, allow_blank=False
    )


class HarnessJobExtendSerializer(serializers.Serializer):
    # The finished-run chat box adds scenarios through an explicit "Add scenarios" action, so
    # the request carries a structured ``count`` plus optional free-text ``guidance`` rather
    # than prose we have to infer intent from. Rerun is a separate action, not this endpoint.
    count = serializers.IntegerField(
        min_value=1,
        max_value=50,
        help_text="How many new scenarios to add to the saved world.",
    )
    guidance = serializers.CharField(
        max_length=2000,
        trim_whitespace=True,
        required=False,
        allow_blank=True,
        default="",
        help_text=(
            "Optional natural-language steering for the added scenarios (e.g. 'calm "
            "first-time riders booking an airport pickup'). Existing scenarios are preserved."
        ),
    )
    client_request_id = serializers.CharField(
        max_length=128, required=False, allow_blank=False
    )


class HarnessSourceUploadResponseSerializer(serializers.Serializer):
    source_id = serializers.UUIDField()
    name = serializers.CharField()
    file_count = serializers.IntegerField()
    total_bytes = serializers.IntegerField()


class HarnessSecretFileUploadResponseSerializer(serializers.Serializer):
    environment_name = serializers.RegexField(r"^[A-Za-z_][A-Za-z0-9_]*$")
    secret_ref = SecretReferenceSerializer()
    size = serializers.IntegerField(min_value=1)


class HarnessSecretValuesSerializer(serializers.Serializer):
    environment_values = serializers.DictField(
        child=serializers.CharField(max_length=65_536, trim_whitespace=False),
        allow_empty=False,
    )

    def validate_environment_values(self, values):
        if len(values) > 100:
            raise serializers.ValidationError(
                "at most 100 values may be stored at once"
            )
        invalid = [
            name
            for name in values
            if not name
            or not name.replace("_", "").isalnum()
            or name[0].isdigit()
            or name in RUNNER_RESERVED_ENVIRONMENT
        ]
        if invalid:
            raise serializers.ValidationError(
                f"invalid or runner-reserved environment names: {', '.join(sorted(invalid))}"
            )
        if sum(len(value.encode("utf-8")) for value in values.values()) > 1_048_576:
            raise serializers.ValidationError("environment values may not exceed 1 MiB")
        return values


class HarnessSecretValuesResponseSerializer(serializers.Serializer):
    secret_refs = serializers.DictField(child=SecretReferenceSerializer())


# ── Read DTO response serializers ───────────────────────────────────────


class HarnessJobInfoSerializer(serializers.Serializer):
    job_id = serializers.UUIDField()
    run_id = serializers.UUIDField()
    source = serializers.DictField()
    metadata = serializers.DictField()
    runtime = serializers.DictField(required=False)
    run_test_id = serializers.UUIDField(allow_null=True)
    test_execution_id = serializers.UUIDField(allow_null=True)


class HarnessJobStatusSerializer(serializers.Serializer):
    state = serializers.CharField()
    stage = serializers.CharField()
    updated_at = serializers.CharField()
    attempt = serializers.IntegerField()
    completed_scenarios = serializers.IntegerField()
    failed_scenarios = serializers.IntegerField()
    active_scenarios = serializers.IntegerField(required=False)
    queued_scenarios = serializers.IntegerField(required=False)
    total_scenarios = serializers.IntegerField()
    deadline_at = serializers.CharField()
    failure = serializers.JSONField(allow_null=True)


class HarnessJobEventSerializer(serializers.Serializer):
    event_id = serializers.CharField()
    sequence = serializers.IntegerField()
    stage = serializers.CharField()
    type = serializers.CharField()
    payload = serializers.JSONField(allow_null=True)
    emitted_at = serializers.CharField()


class HarnessStageOutputSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField()
    summary = serializers.CharField(allow_blank=True)
    kind = serializers.CharField()
    data = serializers.JSONField()


class HarnessScenarioSerializer(serializers.Serializer):
    scenario_key = serializers.CharField()
    scenario_id = serializers.UUIDField()
    name = serializers.CharField(allow_blank=True)
    instruction = serializers.CharField(
        allow_null=True, allow_blank=True, required=False
    )
    use_case = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    call_execution_id = serializers.UUIDField(allow_null=True, required=False)
    status = serializers.CharField(allow_null=True, required=False)


class HarnessPlatformSerializer(serializers.Serializer):
    run_test_id = serializers.UUIDField(allow_null=True)
    test_execution_id = serializers.UUIDField(allow_null=True)
    url = serializers.CharField(allow_null=True)


class HarnessDiagnosticsSerializer(serializers.Serializer):
    object_key = serializers.CharField(required=False)
    sha256 = serializers.CharField(required=False)
    size = serializers.IntegerField()
    captured_at = serializers.CharField(required=False)
    final = serializers.BooleanField()
    error = serializers.CharField(allow_blank=True)


class HarnessRuntimeReadSerializer(serializers.Serializer):
    sandbox_id = serializers.CharField(required=False)
    diagnostics = HarnessDiagnosticsSerializer(required=False)


class HarnessParallelismSerializer(serializers.Serializer):
    requested = serializers.IntegerField()
    admitted = serializers.IntegerField()
    effective = serializers.IntegerField()
    degrade_reasons = serializers.ListField(child=serializers.CharField())


class HarnessConsumptionSerializer(serializers.Serializer):
    text_sim_tokens = serializers.IntegerField(min_value=0)
    voice_sim_minutes = serializers.FloatField(min_value=0)
    ai_credits = serializers.FloatField(min_value=0, allow_null=True)
    sandbox_seconds = serializers.FloatField(min_value=0)


class HarnessJobReadSerializer(serializers.Serializer):
    """Consolidated public read DTO for list/create/retrieve/cancel/poll."""

    job = HarnessJobInfoSerializer()
    status = HarnessJobStatusSerializer()
    events = HarnessJobEventSerializer(many=True)
    stage_outputs = HarnessStageOutputSerializer(many=True)
    scenarios = HarnessScenarioSerializer(many=True)
    receipts = serializers.ListField(child=serializers.JSONField())
    platform = HarnessPlatformSerializer()
    runtime = HarnessRuntimeReadSerializer(required=False)
    parallelism = HarnessParallelismSerializer(required=False)
    conversation = HarnessConversationReadSerializer(allow_null=True, required=False)
    consumption = HarnessConsumptionSerializer(required=False, allow_null=True)
    usage_limit = serializers.JSONField(required=False, allow_null=True)
