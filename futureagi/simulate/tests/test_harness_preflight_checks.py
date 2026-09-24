# ruff: noqa: F811
from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from simulate.services.harness_credentials import (
    HOSTED_FILE_KEY_PREFIX,
    PLATFORM_FILE_MANAGER,
    is_credential_file_ref,
)
from simulate.services.harness_provider import (
    _dialer_check,
    _packaging_check,
    _preflight_checks,
    _sandbox_preflight_body,
)
from simulate.services.hosted_harness import HostedHarnessError, create_hosted_job
from simulate.services.hosted_harness_gateway import (
    HOSTED_ENGINE_CATALOG,
    HOSTED_RUNTIME_CATALOG,
    HostedSourceAcquirer,
    _seeded_stores,
    authoring_stage_outputs,
    platform_dialer_status,
)
from simulate.utils.ended_reason import _SDK_TO_CANONICAL, canonical_ended_reasons

from .test_harness_environment_evals import environment  # noqa: F401
from .test_hosted_harness_channels import _payload

CHECK_IDS = [
    "source",
    "credentials_present",
    "credential_files",
    "credentials_valid",
    "provider_target",
    "platform_dialer",
]

TELEPHONY = {
    "LIVEKIT_URL": "wss://livekit.example",
    "LIVEKIT_API_KEY": "key",
    "LIVEKIT_API_SECRET": "secret",
    "LIVEKIT_OUTBOUND_TRUNK_ID": "ST_trunk",
    "PSTN_CALLER_NUMBER": "+14155550100",
}

TELEPHONY_ENV_FALLBACKS = ("SIP_OUTBOUND_TRUNK_ID", "SIP_OUTBOUND_FROM_NUMBER")


def _by_id(checks):
    return {check["id"]: check for check in checks}


def _probe(provider, *, ok, aliases=(), message="probe"):
    return {
        "provider": provider,
        "ok": ok,
        "aliases": list(aliases),
        "message": message,
    }


@pytest.fixture
def dialer_configured(settings, monkeypatch):
    for name, value in TELEPHONY.items():
        setattr(settings, name, value)
        monkeypatch.delenv(name, raising=False)
    for name in TELEPHONY_ENV_FALLBACKS:
        monkeypatch.delenv(name, raising=False)
    return settings


class TestPreflightChecks:
    def test_every_check_is_present_in_order(self):
        checks = _preflight_checks("github", None, 3, [], [], [], "auto")

        assert [check["id"] for check in checks] == CHECK_IDS
        assert {check["status"] for check in checks} <= {"passed", "failed", "skipped"}
        for check in checks:
            assert set(check) == {"id", "label", "status", "detail", "missing", "fix"}

    @pytest.mark.parametrize("kind", ["remote", "provider"])
    def test_targets_without_a_source_tree_skip_the_source_check(self, kind):
        by_id = _by_id(_preflight_checks(kind, None, 0, [], [], [], "vapi"))

        assert by_id["source"]["status"] == "skipped"
        assert kind in by_id["source"]["detail"]

    def test_remote_targets_own_their_credentials(self):
        by_id = _by_id(
            _preflight_checks("remote", None, 0, ["VAPI_API_KEY"], [], [], "vapi")
        )

        assert by_id["credentials_present"]["status"] == "skipped"
        assert by_id["credentials_present"]["missing"] == []

    def test_a_readable_source_reports_the_scan(self):
        by_id = _by_id(_preflight_checks("github", None, 42, [], [], [], "auto"))

        assert by_id["source"]["status"] == "passed"
        assert by_id["source"]["detail"] == "42 files scanned"
        assert by_id["source"]["fix"] is None

    def test_a_known_source_error_carries_a_fix_hint(self):
        error = HostedHarnessError(
            "github_repository_not_found", "repository acme/x was not found"
        )

        by_id = _by_id(_preflight_checks("github", error, 0, [], [], [], "auto"))

        assert by_id["source"]["status"] == "failed"
        assert by_id["source"]["detail"] == "repository acme/x was not found"
        assert "GitHub App" in by_id["source"]["fix"]

    def test_an_unknown_source_error_falls_back_to_its_own_message(self):
        error = HostedHarnessError("something_else", "disk full")

        by_id = _by_id(_preflight_checks("archive", error, 0, [], [], [], "auto"))

        assert by_id["source"]["status"] == "failed"
        assert by_id["source"]["fix"] == "disk full"

    def test_missing_credentials_are_split_from_missing_files(self):
        by_id = _by_id(
            _preflight_checks(
                "github",
                None,
                5,
                ["VAPI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS_JSON"],
                ["GOOGLE_APPLICATION_CREDENTIALS_JSON"],
                [],
                "vapi",
            )
        )

        present = by_id["credentials_present"]
        assert present["status"] == "failed"
        assert present["missing"] == ["VAPI_API_KEY"]
        assert present["detail"] == "1 required credential(s) not provided"
        assert present["fix"] == "Add VAPI_API_KEY under target credentials"
        files = by_id["credential_files"]
        assert files["status"] == "failed"
        assert files["missing"] == ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
        assert files["fix"] == (
            "Upload the JSON credential file for GOOGLE_APPLICATION_CREDENTIALS_JSON"
        )

    def test_uploaded_credential_files_pass(self):
        by_id = _by_id(
            _preflight_checks(
                "github",
                None,
                5,
                [],
                ["GOOGLE_APPLICATION_CREDENTIALS_JSON"],
                [],
                "auto",
            )
        )

        assert by_id["credential_files"]["status"] == "passed"
        assert by_id["credentials_present"]["status"] == "passed"

    def test_no_required_files_skips_the_file_check(self):
        by_id = _by_id(_preflight_checks("github", None, 5, [], [], [], "auto"))

        assert by_id["credential_files"]["status"] == "skipped"

    def test_probes_are_split_into_key_and_target_checks(self):
        probe = [
            _probe("vapi", ok=True, message="VAPI_API_KEY accepted"),
            _probe(
                "vapi_target",
                ok=False,
                aliases=["VAPI_API_KEY"],
                message="assistant asst_1 was not found",
            ),
        ]

        by_id = _by_id(_preflight_checks("provider", None, 0, [], [], probe, "vapi"))

        assert by_id["credentials_valid"]["status"] == "passed"
        assert by_id["credentials_valid"]["detail"] == "VAPI_API_KEY accepted"
        target = by_id["provider_target"]
        assert target["status"] == "failed"
        assert target["missing"] == ["VAPI_API_KEY"]
        assert target["detail"] == "assistant asst_1 was not found"
        assert target["fix"] == (
            "Check the agent ID belongs to the account behind VAPI_API_KEY"
        )

    def test_a_rejected_key_names_every_alias_once(self):
        probe = [
            _probe("vapi", ok=False, aliases=["VAPI_API_KEY"], message="401"),
            _probe("retell", ok=False, aliases=["RETELL_API_KEY", "VAPI_API_KEY"]),
        ]

        by_id = _by_id(_preflight_checks("provider", None, 0, [], [], probe, "vapi"))

        valid = by_id["credentials_valid"]
        assert valid["status"] == "failed"
        assert valid["missing"] == ["RETELL_API_KEY", "VAPI_API_KEY"]
        assert valid["detail"] == "401; probe"
        assert valid["fix"] == (
            "Replace RETELL_API_KEY, VAPI_API_KEY with a key the provider accepts"
        )

    def test_no_probe_results_skip_both_probe_checks(self):
        by_id = _by_id(_preflight_checks("github", None, 1, [], [], [], "auto"))

        assert by_id["credentials_valid"]["status"] == "skipped"
        assert by_id["provider_target"]["status"] == "skipped"


class TestDialerCheck:
    def test_non_phone_targets_skip_the_dialer(self, dialer_configured):
        for connector in ("vapi", "retell", "livekit", "auto", ""):
            assert _dialer_check(connector)["status"] == "skipped"

    def test_a_configured_dialer_passes(self, dialer_configured):
        check = _dialer_check("phone")

        assert check["status"] == "passed"
        assert check["missing"] == []

    def test_an_unconfigured_dialer_fails_with_what_is_missing(self, dialer_configured):
        dialer_configured.LIVEKIT_OUTBOUND_TRUNK_ID = ""
        dialer_configured.PSTN_CALLER_NUMBER = ""

        check = _dialer_check("phone")

        assert check["status"] == "failed"
        assert check["missing"] == ["SIP_OUTBOUND_TRUNK_ID", "SIP_OUTBOUND_FROM_NUMBER"]
        assert "administrator" in check["fix"]

    def test_status_reads_settings_before_the_environment(
        self, dialer_configured, monkeypatch
    ):
        dialer_configured.LIVEKIT_OUTBOUND_TRUNK_ID = ""
        monkeypatch.setenv("SIP_OUTBOUND_TRUNK_ID", "ST_from_env")

        assert platform_dialer_status() == {"available": True, "missing": []}

        monkeypatch.delenv("SIP_OUTBOUND_TRUNK_ID")

        assert platform_dialer_status() == {
            "available": False,
            "missing": ["SIP_OUTBOUND_TRUNK_ID"],
        }

    def test_the_check_is_included_in_the_preflight_list(self, dialer_configured):
        by_id = _by_id(_preflight_checks("provider", None, 0, [], [], [], "phone"))

        assert by_id["platform_dialer"]["status"] == "passed"


class TestPackagingCheck:
    def test_no_analysis_is_skipped(self):
        assert _packaging_check({})["status"] == "skipped"
        assert _packaging_check(None)["status"] == "skipped"

    def test_a_ready_build_names_its_path(self):
        check = _packaging_check({"ready": True, "selected_path": "agent/Dockerfile"})

        assert check["status"] == "passed"
        assert check["detail"] == "building from agent/Dockerfile"

    def test_a_ready_build_without_a_path_still_passes(self):
        assert _packaging_check({"ready": True})["detail"] == "a build was found"

    def test_an_unpackaged_runtime_fails_even_when_ready(self):
        check = _packaging_check(
            {"ready": True, "agent_runtime_packaged": False, "notes": ["no entrypoint"]}
        )

        assert check["status"] == "failed"
        assert check["detail"] == "no entrypoint"
        assert "Dockerfile" in check["fix"]

    def test_an_unready_build_uses_the_first_note_or_a_default(self):
        assert (
            _packaging_check({"ready": False, "notes": ["  ", "second"]})["detail"]
            == "second"
        )
        assert (
            _packaging_check({"ready": False})["detail"]
            == "no Dockerfile or compose file packages the agent"
        )


class TestSandboxPreflightBody:
    def _payload(self, connector="auto"):
        return {
            "source": {"kind": "github"},
            "agent": {"connector": connector},
            "runtime": {"parallelism": 3},
        }

    def test_maps_the_sandbox_report_onto_the_declared_shape(self):
        report = {
            "ready_to_submit": False,
            "credentials": {
                "scanned_files": 7,
                "detected_connectors": ["livekit"],
                "requirements": [
                    {
                        "id": "LIVEKIT_URL",
                        "required": True,
                        "status": "missing",
                        "kind": "value",
                    },
                    {
                        "environment_name": "GOOGLE_APPLICATION_CREDENTIALS_JSON",
                        "required": True,
                        "status": "missing",
                        "kind": "file",
                    },
                    {"id": "OPTIONAL", "required": False, "status": "missing"},
                ],
                "credential_choices": [],
            },
            "packaging": {"ready": True, "selected_path": "Dockerfile"},
        }

        body = _sandbox_preflight_body(self._payload(), report)

        assert body["ready_to_submit"] is False
        assert body["state"] == "failed"
        assert [check["id"] for check in body["checks"]] == [*CHECK_IDS, "packaging"]
        by_id = _by_id(body["checks"])
        assert by_id["source"]["detail"] == "7 files scanned"
        assert by_id["credentials_present"]["missing"] == ["LIVEKIT_URL"]
        assert by_id["credential_files"]["missing"] == [
            "GOOGLE_APPLICATION_CREDENTIALS_JSON"
        ]
        assert by_id["credentials_valid"]["status"] == "skipped"
        assert by_id["provider_target"]["status"] == "skipped"
        assert by_id["packaging"]["status"] == "passed"
        assert body["credentials"]["probe"] == []
        assert body["credentials"]["scanned_files"] == 7
        assert body["parallelism_enabled"] is False
        assert body["effective_parallelism"] == 3
        assert body["resource_profile"] is None
        assert body["snapshot"] == {
            "name": None,
            "digest": None,
            "engines": HOSTED_ENGINE_CATALOG,
            "runtimes": HOSTED_RUNTIME_CATALOG,
        }

    def test_state_follows_the_sandbox_verdict(self):
        body = _sandbox_preflight_body(
            self._payload(), {"ready_to_submit": True, "credentials": {}}
        )

        assert body["state"] == "connected"
        assert body["ready_to_submit"] is True
        assert _by_id(body["checks"])["packaging"]["status"] == "skipped"

    def test_a_missing_credentials_block_is_tolerated(self):
        body = _sandbox_preflight_body(self._payload(), {})

        assert body["state"] == "failed"
        assert body["credentials"] == {"probe": []}
        assert _by_id(body["checks"])["source"]["detail"] == "0 files scanned"


@pytest.mark.django_db
class TestGitHubCloneErrors:
    def _acquire(self, organization, monkeypatch, stderr):
        job, _ = create_hosted_job(
            organization,
            _payload(
                source={
                    "kind": "github",
                    "repository": "acme/private-agent",
                    "ref": "main",
                    "visibility": "private",
                }
            ),
            idempotency_key=f"clone-{stderr[:8]}",
        )
        monkeypatch.setattr(
            "simulate.services.hosted_harness_gateway.GitHubAppTokenProvider.from_settings",
            lambda: SimpleNamespace(credential=lambda _: nullcontext("token-value")),
        )

        def run(command, **kwargs):
            if command[:2] == ["git", "init"]:
                from pathlib import Path

                path = Path(command[-1])
                path.mkdir(parents=True)
                (path / ".git").mkdir()
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if "fetch" in command:
                return SimpleNamespace(returncode=128, stdout="", stderr=stderr)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(
            "simulate.services.hosted_harness_gateway.subprocess.run", run
        )
        with pytest.raises(HostedHarnessError) as raised:
            HostedSourceAcquirer().acquire(job)
        return raised.value

    @pytest.mark.parametrize(
        "stderr",
        [
            "fatal: could not read Username for 'https://github.com': terminal prompts disabled",
            "remote: Repository not found.\nfatal: repository 'https://github.com/acme/private-agent/' not found",
        ],
    )
    def test_an_unreadable_repository_is_a_422_with_a_named_cause(
        self, organization, monkeypatch, stderr
    ):
        error = self._acquire(organization, monkeypatch, stderr)

        assert error.code == "github_repository_not_found"
        assert error.status_code == 422
        assert error.retryable is False
        assert "acme/private-agent" in error.message
        assert "GitHub App" in error.message
        assert "token-value" not in error.message

    def test_any_other_git_failure_stays_a_retryable_502(
        self, organization, monkeypatch
    ):
        error = self._acquire(
            organization, monkeypatch, "fatal: unable to access: token-value expired"
        )

        assert error.code == "github_clone_failed"
        assert error.status_code == 502
        assert error.retryable is True
        assert "[REDACTED]" in error.message
        assert "token-value" not in error.message


class TestCredentialFileRef:
    def test_recognises_both_ways_a_file_ref_is_written(self):
        assert is_credential_file_ref({"manager": PLATFORM_FILE_MANAGER}) is True
        assert (
            is_credential_file_ref(
                {"manager": "platform-vault", "key": f"{HOSTED_FILE_KEY_PREFIX}abc"}
            )
            is True
        )

    def test_rejects_typed_values_and_non_refs(self):
        assert (
            is_credential_file_ref({"manager": "platform-vault", "key": "harness-vapi"})
            is False
        )
        assert is_credential_file_ref({"key": None}) is False
        assert is_credential_file_ref({}) is False
        assert is_credential_file_ref("harness-google-adc-abc") is False
        assert is_credential_file_ref(None) is False


class TestCanonicalEndedReasons:
    def test_is_the_sorted_distinct_target_vocabulary(self):
        reasons = canonical_ended_reasons()

        assert reasons == sorted(reasons)
        assert len(reasons) == len(set(reasons))
        assert set(reasons) == set(_SDK_TO_CANONICAL.values())
        assert "silence-timed-out" in reasons
        assert "simulator_end_call" not in reasons


class TestSeededStores:
    def test_only_stores_with_integer_row_counts_are_reported(self):
        build_output = {
            "stores": [
                {
                    "capability": "orders_db",
                    "engine": "postgres",
                    "strategy": "seed",
                    "row_counts": {"users": 2, "orders": 3, "audit": "n/a"},
                    "baseline": "s3://ignored",
                },
                {"capability": "cache", "engine": "redis", "row_counts": None},
                {"capability": "blob", "engine": "s3"},
                "not-a-record",
            ]
        }

        stores = _seeded_stores(build_output)

        assert stores == [
            {
                "capability": "orders_db",
                "engine": "postgres",
                "strategy": "seed",
                "tables": [{"name": "orders", "rows": 3}, {"name": "users", "rows": 2}],
                "total_rows": 5,
            }
        ]

    def test_absent_or_malformed_build_output_yields_nothing(self):
        assert _seeded_stores(None) == []
        assert _seeded_stores({"stores": "nope"}) == []
        assert _seeded_stores({}) == []

    def test_stage_outputs_carry_the_catalogue_and_the_seeded_world(self):
        outputs = authoring_stage_outputs(
            {"modality": "voice"},
            {"runtime": {}},
            [{"scenario_key": "one"}],
            sub_goals={
                "sub_goals": [{"name": "verify"}, {"name": "refund"}],
                "suite_evals": [{"name": "tone"}],
            },
            build_output={
                "stores": [
                    {
                        "capability": "db",
                        "engine": "postgres",
                        "row_counts": {"a": 1, "b": 4},
                    }
                ]
            },
        )

        by_kind = {item["kind"]: item for item in outputs}
        assert by_kind["sub_goals"]["summary"] == "2 sub-goals · 1 suite evals"
        assert by_kind["sub_goals"]["data"]["sub_goals"][1]["name"] == "refund"
        assert by_kind["stores"]["summary"] == "2 tables · 5 rows"
        assert by_kind["stores"]["data"][0]["total_rows"] == 5
        ids = [item["id"] for item in outputs]
        assert len(ids) == len(set(ids))

    def test_no_catalogue_and_no_build_adds_neither_stage(self):
        outputs = authoring_stage_outputs({"modality": "voice"}, {}, [])

        assert {item["kind"] for item in outputs} & {"sub_goals", "stores"} == set()


@pytest.mark.django_db
class TestBindEvalConfig:
    def test_rebinding_a_removed_name_revives_the_same_row(self, environment):
        from django.utils import timezone

        from simulate.models import SimulateEvalConfig
        from simulate.services.harness_evals import (
            bind_eval_config,
            resolve_eval_mapping,
        )

        from .test_harness_environment_evals import _template

        template = _template("no_misselling", ["conversation"])
        mapping = resolve_eval_mapping(template, "voice")
        assert mapping

        first = bind_eval_config(environment.run_test, template, mapping)
        first.deleted = True
        first.deleted_at = timezone.now()
        first.mapping = {}
        first.error_localizer = False
        first.save(
            update_fields=["deleted", "deleted_at", "mapping", "error_localizer"]
        )

        revived = bind_eval_config(environment.run_test, template, mapping)

        assert revived.id == first.id
        assert revived.deleted is False
        assert revived.deleted_at is None
        assert revived.mapping == mapping
        assert revived.error_localizer is True
        assert (
            SimulateEvalConfig.all_objects.filter(
                run_test=environment.run_test, eval_template=template
            ).count()
            == 1
        )

    def test_binding_twice_returns_the_existing_row(self, environment):
        from simulate.models import SimulateEvalConfig
        from simulate.services.harness_evals import (
            bind_eval_config,
            resolve_eval_mapping,
        )

        from .test_harness_environment_evals import _template

        template = _template("no_misselling", ["conversation"])
        mapping = resolve_eval_mapping(template, "voice")

        first = bind_eval_config(environment.run_test, template, mapping)
        again = bind_eval_config(environment.run_test, template, mapping)

        assert again.id == first.id
        assert (
            SimulateEvalConfig.objects.filter(run_test=environment.run_test).count()
            == 1
        )
