"""DB-free final-spawn regressions; run directly with python -I -B.

Only production AST functions execute. Sockets, ORM, scratch IO, and subprocesses
are blocked or doubled; credentials and deployment settings are synthetic.
"""

from __future__ import annotations

import json
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cryptography.fernet import Fernet

_ROOT = Path(__file__).resolve().parents[1]
_ACTIVITY = _ROOT / "temporal/activities/hosted_runner.py"
# Site-activated refusal copy (activation-001, textSha256 fa2dd480...; main ruling 450).
_VAPI_REFUSAL = (
    "This test can't run on hosted Future AGI yet: the Vapi assistant has control "
    "authentication turned on, and saving its Vapi public key here is coming soon. "
    "No call was started. Until then, test a Vapi assistant without control "
    "authentication, or run the test on a self-hosted install, where the operator "
    "sets the Vapi public key."
)
_R3 = runpy.run_path(str(_ROOT / "tests/test_hosted_child_r3.py"))
_ADMISSION = _R3["_ADMISSION"]
load_functions = _ADMISSION["load_functions"]
finish = _ADMISSION["finish"]
ApplicationError = _ADMISSION["ApplicationError"]


def cloud_classifier():
    namespace = {"CLOUD_DEPLOYMENTS": ("US", "EU", "DEV")}
    load_functions(_ROOT.parent / "tfc/ee_loader.py", {"is_cloud_env"}, namespace)
    return namespace["is_cloud_env"]


class HostedChildR4Tests(_ADMISSION["NoSockets"]):
    def harness(self, deployment="US", config=None):
        # Reuse doubles, not test methods. The real resolver and full activity
        # run together, so assertions inspect the exact create_subprocess env.
        service, _, agent, version, credentials, model = _R3[
            "HostedChildR3Tests"
        ].vapi_harness(self)
        if config is not None:
            credentials.config_json = config
        settings = NS(CLOUD_DEPLOYMENT=deployment)
        service.update(settings=settings, is_cloud_env=cloud_classifier())
        ns, spawn, input_job = _ADMISSION["HostedLaunchAdmissionTests"].harness(
            self, settings=settings
        )
        worker = {
            "VAPI_PUBLIC_API_KEY": "operator-public",
            "VAPI_API_KEY": "operator-private",
            "AGENTCC_API_KEY": "runtime-gateway",
            "AGENTCC_BASE_URL": "https://gateway.invalid",
            "INTERNAL_API_SECRET": "sink-secret",
            "TARGET_TOKEN": "target-secret",
        }
        env_helpers = _R3["_ENV"]["_environment_helpers"](worker)
        ns.update(
            {
                key: value
                for key, value in env_helpers.items()
                if key.startswith("_CHILD_ENV_")
                or key in {"_SINK_ENV_BY_PURPOSE", "_SINK_SOURCE_DESTINATIONS"}
            }
        )
        ns["os"].environ = worker

        def sync_to_async(fn, **kwargs):
            async def wrapped():
                return fn()

            return wrapped

        ns.update(
            sync_to_async=sync_to_async,
            close_old_connections=Mock(),
            is_cloud_env=cloud_classifier(),
        )
        load_functions(
            _ACTIVITY,
            {
                "_child_environment",
                "_secret_env_ref_keys",
                "_resolve_secret",
                "_secret_ref_origin",
                "_secret_source_allowed",
                "_customer_provider_env_keys",
                "_resolve_voice_secret_env",
                "_resolve_provider_credential",
                "_is_backend_only_secret",
                "_final_child_environment",
                "_check_vapi_control_key",
            },
            ns,
        )
        return ns, spawn, input_job, worker, service, agent, version, credentials, model

    def run_job(self, ns, input_job, job):
        input_job.job_json = json.dumps(job)
        return finish(ns["run_hosted_sdk_job"](input_job))

    def vapi_job(self, credentials, *, public=True):
        refs = [
            {
                "manager": "provider_credentials",
                "key": "VAPI_API_KEY",
                "credential_id": credentials.id,
                "field": "api_key",
            }
        ]
        if public:
            refs.append(
                {
                    "manager": "provider_credentials",
                    "key": "VAPI_PUBLIC_API_KEY",
                    "credential_id": credentials.id,
                    "field": "public_api_key",
                }
            )
        return {
            "voice": {
                "agent_definition": {
                    "transport": {
                        "kind": "sip_inbound",
                        "inbound_call_originator": "vapi",
                    }
                }
            },
            "metadata": {"secret_env": refs, "leased_did": "+15555550100"},
        }

    def test_denied_target_and_metadata_overlays_are_dropped_and_recorded(self):
        denied = {
            "PG_PASSWORD",
            "PGHOST",
            "POSTGRES_PASSWORD",
            "REDIS_URL",
            "CLICKHOUSE_PASSWORD",
            "TEMPORAL_CLIENT_KEY",
            "CELERY_BROKER_URL",
            "SECRET_KEY",
            "INTERNAL_API_SECRET",
            "WEBHOOKS_ENCRYPTION_KEYS",
            "PREFIX_ENCRYPTION_KEY_SUFFIX",
            "ALK_CAB_GUEST_POC_PIN",
            "ALK_UNSAFE_INPROCESS_CODE_ACTORS",
            "AGENTCC_ADMIN_TOKEN",
            "AGENTCC_INTERNAL_API_KEY",
            "AGENTCC_HARNESS_API_KEY",
            "AGENTCC_API_KEY_BACKUP",
            "AGENTCC_BASE_URL_BACKUP",
            "DATABASE_URL",
            "FI_PG_READ_PASSWORD",
            "CH_PASSWORD",
        }
        denied = {
            variant for key in denied for variant in (key, key.lower(), key.title())
        }
        denied.update({"agentcc_api_key", "Agentcc_Base_Url"})
        for source in ("target", "metadata"):
            with self.subTest(source=source):
                ns, spawn, input_job, worker, *unused = self.harness()
                worker.update(dict.fromkeys(denied, "never-log-this-secret"))
                refs = [{"manager": "env", "key": key} for key in sorted(denied)]
                job = {
                    "sink": {
                        "secret_refs": {
                            "internal": {
                                "manager": "env",
                                "key": "INTERNAL_API_SECRET",
                                "purpose": "internal_api_secret",
                            }
                        }
                    },
                    "spec": {
                        "target": {
                            "secret_refs": {
                                "auth": {
                                    "manager": "env",
                                    "key": "TARGET_TOKEN",
                                }
                            }
                        }
                    },
                }
                if source == "target":
                    job["spec"]["target"]["secret_refs"].update(
                        {str(i): ref for i, ref in enumerate(refs)}
                    )
                else:
                    job["metadata"] = {"secret_env": refs}
                self.run_job(ns, input_job, job)
                spawn.assert_awaited_once()
                env = spawn.call_args.kwargs["env"]
                self.assertFalse(
                    denied.intersection(env), sorted(denied.intersection(env))
                )
                # Stricter source contract: an arbitrary worker token is not
                # authorized merely because a target reference names it.
                self.assertNotIn("TARGET_TOKEN", env)
                self.assertNotIn("target-secret", env.values())
                self.assertEqual(
                    worker["INTERNAL_API_SECRET"], env["FI_INTERNAL_SUBMIT_SECRET"]
                )
                self.assertEqual("runtime-gateway", env["AGENTCC_API_KEY"])
                self.assertEqual("https://gateway.invalid", env["AGENTCC_BASE_URL"])
                logs = repr(ns["activity"].logger.warning.call_args_list)
                self.assertIn("PG_PASSWORD", logs)
                self.assertIn("AGENTCC_ADMIN_TOKEN", logs)
                self.assertNotIn("never-log-this-secret", logs)

    def test_self_host_operator_key_reaches_spawn_without_saved_control_config(self):
        for deployment in ("", "false", "SELF_HOSTED"):
            with self.subTest(deployment=deployment):
                ns, spawn, input_job, _, _, _, _, credentials, _ = self.harness(
                    deployment, config={}
                )
                self.run_job(ns, input_job, self.vapi_job(credentials, public=False))
                self.assertEqual(
                    "operator-public",
                    spawn.call_args.kwargs["env"].get("VAPI_PUBLIC_API_KEY"),
                )
                self.assertEqual(
                    "customer-private", spawn.call_args.kwargs["env"]["VAPI_API_KEY"]
                )

    def test_self_host_builder_does_not_require_a_saved_public_key(self):
        _, _, _, _, service, agent, version, credentials, _ = self.harness(
            "false", config={"monitorPlan": {"controlAuthenticationEnabled": True}}
        )
        try:
            target, refs = service["_voice_agent_definition"](
                agent, "vapi", "sip_inbound", credentials, version
            )
        except ValueError as exc:
            self.fail(f"Self-host operator credentials must remain supported: {exc}")
        self.assertEqual("vapi", target["transport"]["inbound_call_originator"])
        self.assertFalse(any(ref["key"] == "VAPI_PUBLIC_API_KEY" for ref in refs))

    def test_cloud_enabled_missing_key_refuses_but_unknown_policy_runs(self):
        for deployment in ("US", " eu ", "dev"):
            for config in ({}, {"monitorPlan": {"controlAuthenticationEnabled": True}}):
                with self.subTest(deployment=deployment, config=config):
                    ns, spawn, input_job, _, _, _, _, credentials, _ = self.harness(
                        deployment, config
                    )
                    outcome = self.run_job(
                        ns, input_job, self.vapi_job(credentials, public=False)
                    )
                    if config:
                        self.assertEqual("failed", outcome.phase)
                        self.assertEqual(1, outcome.return_code)
                        self.assertEqual(outcome.detail, _VAPI_REFUSAL)
                        self.assertIn("No call was started", outcome.detail)
                        spawn.assert_not_called()
                    else:
                        # Main 435 supersedes 410's proposed SDK mapping.
                        spawn.assert_awaited_once()
                        self.assertNotIn(
                            "VAPI_PUBLIC_API_KEY", spawn.call_args.kwargs["env"]
                        )
                    ns["_child_semaphore"].release.assert_called_once()
                    ns["shutil"].rmtree.assert_called_once()

    def test_cloud_without_any_refs_has_unknown_policy_and_runs(self):
        ns, spawn, input_job, _, _, _, _, credentials, _ = self.harness()
        job = self.vapi_job(credentials, public=False)
        job["metadata"]["secret_env"] = []
        self.run_job(ns, input_job, job)
        spawn.assert_awaited_once()
        self.assertNotIn("VAPI_PUBLIC_API_KEY", spawn.call_args.kwargs["env"])

    def test_cloud_job_key_works_and_never_uses_operator_key(self):
        for deployment in ("US", "EU", "DEV"):
            with self.subTest(deployment=deployment):
                ns, spawn, input_job, _, service, agent, version, credentials, model = (
                    self.harness(deployment)
                )
                target, refs = service["_voice_agent_definition"](
                    agent, "vapi", "sip_inbound", credentials, version
                )
                job = {
                    "voice": {"agent_definition": target},
                    "metadata": {
                        "secret_env": refs,
                        "leased_did": "+15555550100",
                    },
                }
                self.run_job(ns, input_job, job)
                env = spawn.call_args.kwargs["env"]
                self.assertEqual("customer-public", env.get("VAPI_PUBLIC_API_KEY"))
                self.assertEqual("customer-private", env["VAPI_API_KEY"])
                self.assertNotIn("operator-public", env.values())
                model.objects.get.assert_called_once_with(id=credentials.id)

    def test_explicit_unauthenticated_cloud_target_works_without_worker_key(self):
        ns, spawn, input_job, _, _, _, _, credentials, _ = self.harness(
            config={"monitorPlan": {"controlAuthenticationEnabled": False}}
        )
        self.run_job(ns, input_job, self.vapi_job(credentials, public=False))
        self.assertNotIn("VAPI_PUBLIC_API_KEY", spawn.call_args.kwargs["env"])

    def test_cloud_worker_key_cannot_enter_via_env_or_setting_reference(self):
        for manager in ("env", "setting"):
            with self.subTest(manager=manager):
                ns, spawn, input_job, _, _, _, _, credentials, _ = self.harness()
                job = self.vapi_job(credentials)
                job["metadata"]["secret_env"][-1].update(
                    manager=manager, setting="VAPI_PUBLIC_API_KEY"
                )
                with self.assertRaises(ApplicationError):
                    self.run_job(ns, input_job, job)
                spawn.assert_not_called()

    def test_cloud_builder_missing_saved_key_returns_actionable_failure(self):
        ns, spawn, input_job, _, service, agent, version, credentials, _ = self.harness(
            config={"monitorPlan": {"controlAuthenticationEnabled": True}}
        )
        target, refs = service["_voice_agent_definition"](
            agent, "vapi", "sip_inbound", credentials, version
        )
        job = {
            "voice": {"agent_definition": target},
            "metadata": {
                "secret_env": refs,
                "leased_did": "+15555550100",
            },
        }
        outcome = self.run_job(ns, input_job, job)
        self.assertEqual("failed", outcome.phase)
        self.assertEqual(outcome.detail, _VAPI_REFUSAL)
        spawn.assert_not_called()

    def test_normal_credential_save_unknown_policy_runs_without_cloud_worker_key(self):
        for deployment in ("US", "false"):
            with self.subTest(deployment=deployment):
                ns, spawn, input_job, _, service, agent, version, credentials, model = (
                    self.harness(deployment, config={})
                )
                # Execute the real at-rest codec with a fresh synthetic key.
                # The persistence double must decrypt on read, not return ciphertext.
                cipher = Fernet(Fernet.generate_key())
                codec = {
                    # FWD-P1 1124fdfad4 adds persistent-key preflight.
                    "settings": NS(INTEGRATION_ENCRYPTION_KEY_EPHEMERAL=False),
                    "CredentialManager": NS(
                        _get_fernet=lambda: cipher,
                        encrypt=lambda payload: cipher.encrypt(json.dumps(payload).encode()),
                        decrypt=lambda blob: json.loads(cipher.decrypt(blob)),
                    ),
                    "PREFIX": "enc::",
                    "ERROR": "Agent credential storage is unavailable",
                    "KEY_ERROR": (
                        "Agent credentials require a valid persistent "
                        "INTEGRATION_ENCRYPTION_KEY; configure the same key "
                        "for all processes and restarts (not an ephemeral key)"
                    ),
                }
                load_functions(
                    _ROOT / "services/agent_api_key_storage.py",
                    {"require_persistent_key", "encrypt_api_key", "decrypt_api_key"},
                    codec,
                )
                credentials.save = Mock()
                credentials.get_api_key = Mock(
                    side_effect=lambda credentials=credentials: codec["decrypt_api_key"](
                        credentials.api_key
                    )
                )
                model.ProviderType = NS(VAPI="vapi", RETELL="retell", LIVEKIT="livekit")
                encrypt = Mock(wraps=codec["encrypt_api_key"])
                persist = {
                    "ProviderCredentials": model,
                    "MASKED_VALUE": "********",
                    "encrypt_api_key": encrypt,
                }
                load_functions(
                    _ROOT / "services/agent_definition.py",
                    {
                        "sync_provider_credentials",
                        "_has_credential_fields",
                        "_apply_secrets",
                        "_apply_non_secrets",
                        "is_masked",
                    },
                    persist,
                )
                data = NS(
                    provider="vapi",
                    api_key="customer-private",
                    assistant_id="assistant",
                    provider_was_provided=True,
                    livekit_url=None,
                    livekit_api_key=None,
                    livekit_api_secret=None,
                    livekit_agent_name=None,
                    livekit_max_concurrency=None,
                    livekit_config_json={
                        "monitorPlan": {"controlAuthenticationEnabled": True},
                        "public_api_key": "attempted-public",
                    },
                )
                persist["sync_provider_credentials"](version, data)
                credentials.save.assert_called_once()
                encrypt.assert_called_once_with(data.api_key)
                self.assertNotEqual(credentials.api_key, data.api_key)
                self.assertEqual(credentials.get_api_key(), data.api_key)
                self.assertEqual({}, credentials.config_json)
                target, refs = service["_voice_agent_definition"](
                    agent, "vapi", "sip_inbound", credentials, version
                )
                job = {
                    "voice": {"agent_definition": target},
                    "metadata": {
                        "secret_env": refs,
                        "leased_did": "+15555550100",
                    },
                }
                self.run_job(ns, input_job, job)
                spawn.assert_awaited_once()
                if deployment == "US":
                    self.assertNotIn(
                        "VAPI_PUBLIC_API_KEY", spawn.call_args.kwargs["env"]
                    )
                else:
                    self.assertEqual(
                        "operator-public",
                        spawn.call_args.kwargs["env"]["VAPI_PUBLIC_API_KEY"],
                    )

    def test_per_job_key_overrides_operator_key_on_self_host(self):
        ns, spawn, input_job, _, _, _, _, credentials, _ = self.harness("false")
        self.run_job(ns, input_job, self.vapi_job(credentials))
        self.assertEqual(
            "customer-public", spawn.call_args.kwargs["env"]["VAPI_PUBLIC_API_KEY"]
        )


if __name__ == "__main__":
    unittest.main()
