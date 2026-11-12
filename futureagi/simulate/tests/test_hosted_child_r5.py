"""Source-provenance regressions at the real mocked spawn; python -I -B.

All credentials are synthetic. Reuse r4's DB-free, socket-blocked production-AST
harness, not its test methods. No Django startup, provider call or child process.
"""

from __future__ import annotations

import ast
import runpy
import unittest
from pathlib import Path
from unittest.mock import Mock

_ROOT = Path(__file__).resolve().parents[1]
_ACTIVITY = _ROOT / "temporal/activities/hosted_runner.py"
_R4 = runpy.run_path(str(_ROOT / "tests/test_hosted_child_r4.py"))


class HostedChildR5Tests(_R4["_ADMISSION"]["NoSockets"]):
    run_job = _R4["HostedChildR4Tests"].run_job
    vapi_job = _R4["HostedChildR4Tests"].vapi_job

    def harness(self, deployment="US", config=None):
        parts = _R4["HostedChildR4Tests"].harness(self, deployment, config)
        ns = parts[0]
        # Load any new production provenance dependencies without assuming they
        # exist at RED. Missing enforcement must fail assertions, not imports.
        tree = ast.parse(_ACTIVITY.read_text())
        constants = [
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "_SINK_SOURCE_DESTINATIONS"
                for target in node.targets
            )
        ]
        exec(
            compile(
                ast.Module(body=constants, type_ignores=[]), str(_ACTIVITY), "exec"
            ),
            ns,
        )
        _R4["load_functions"](
            _ACTIVITY, {"_secret_ref_origin", "_secret_source_allowed"}, ns
        )
        return parts

    def assert_dropped(self, job, source, destination, *, deployment="US"):
        ns, spawn, request, worker, *_ = self.harness(deployment)
        marker = "synthetic-forbidden-source-value"
        worker[source] = marker
        setattr(ns["settings"], source, marker)
        self.run_job(ns, request, job)
        spawn.assert_awaited_once()
        env = spawn.call_args.kwargs["env"]
        self.assertNotIn(marker, env.values())
        self.assertNotIn(destination, env)
        self.assertNotIn(marker, repr(ns["activity"].logger.mock_calls))

    def test_lens_a_metadata_aliases_never_reach_spawn(self):
        sources = {
            "PG_PASSWORD",
            "PGHOST",
            "PGPASSWORD",
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
            "AGENTCC_HARNESS_API_KEY",
            "AGENTCC_API_KEY_BACKUP",
            "AGENTCC_BASE_URL_BACKUP",
            "DATABASE_URL",
            "FI_PG_READ_PASSWORD",
            "CH_PASSWORD",
            "CH25_PASSWORD",
            "VAPI_PUBLIC_API_KEY",
            "VAPI_API_KEY",
            "RETELL_API_KEY",
        }
        sources |= {source.lower() for source in sources}
        for source in sorted(sources):
            for manager in ("env", "setting", "unknown"):
                with self.subTest(source=source, manager=manager):
                    destination = "SIMULATOR_LLM_API_KEY"
                    ref = {
                        "key": destination,
                        "manager": manager,
                        "source": source,
                        "setting": source,
                    }
                    self.assert_dropped(
                        {"metadata": {"secret_env": [ref]}}, source, destination
                    )

    def test_aliases_cannot_replace_the_gateway_pair_or_openai_key(self):
        for source, destination in (
            ("PG_PASSWORD", "AGENTCC_API_KEY"),
            ("PG_PASSWORD", "AGENTCC_BASE_URL"),
            ("AGENTCC_ADMIN_TOKEN", "OPENAI_API_KEY"),
        ):
            with self.subTest(source=source, destination=destination):
                self.assert_dropped(
                    {
                        "metadata": {
                            "secret_env": [
                                {"key": destination, "manager": "env", "source": source}
                            ]
                        }
                    },
                    source,
                    destination,
                )

    def test_unreviewed_sink_sources_and_purpose_swaps_are_dropped(self):
        for source, purpose, destination in (
            ("PG_PASSWORD", "api_key", "FI_API_KEY"),
            ("VAPI_API_KEY", "api_key", "FI_API_KEY"),
            ("VAPI_PUBLIC_API_KEY", "api_key", "FI_API_KEY"),
            ("INTERNAL_API_SECRET", "api_key", "FI_API_KEY"),
            ("INTERNAL_API_SECRET", "secret_key", "FI_SECRET_KEY"),
            ("SINK_API_TOKEN", "secret_key", "FI_SECRET_KEY"),
            ("UNREVIEWED_SINK_TOKEN", "api_key", "FI_API_KEY"),
        ):
            with self.subTest(source=source, purpose=purpose):
                self.assert_dropped(
                    {
                        "sink": {
                            "secret_refs": {
                                "auth": {
                                    "manager": "env",
                                    "key": source,
                                    "purpose": purpose,
                                }
                            }
                        }
                    },
                    source,
                    destination,
                )

    def test_target_backend_sources_are_dropped_before_spawn(self):
        for source in ("PG_PASSWORD", "AGENTCC_ADMIN_TOKEN", "VAPI_API_KEY"):
            with self.subTest(source=source):
                self.assert_dropped(
                    {
                        "spec": {
                            "target": {
                                "secret_refs": {
                                    "auth": {"manager": "env", "key": source}
                                }
                            }
                        }
                    },
                    source,
                    source,
                )

    def test_explicit_target_aliases_are_classified_by_their_actual_source(self):
        for source in ("PG_PASSWORD", "AGENTCC_ADMIN_TOKEN", "VAPI_PUBLIC_API_KEY"):
            with self.subTest(source=source):
                self.assert_dropped(
                    {
                        "spec": {
                            "target": {
                                "secret_refs": {
                                    "auth": {
                                        "manager": "env",
                                        "key": "OPENAI_API_KEY",
                                        "source": source,
                                    }
                                }
                            }
                        }
                    },
                    source,
                    "OPENAI_API_KEY",
                )

    def test_backend_sources_are_also_denied_on_self_host(self):
        self.assert_dropped(
            {
                "metadata": {
                    "secret_env": [
                        {
                            "manager": "setting",
                            "key": "OPENAI_API_KEY",
                            "setting": "PG_PASSWORD",
                        }
                    ]
                }
            },
            "PG_PASSWORD",
            "OPENAI_API_KEY",
            deployment="false",
        )

    def test_sink_exception_cannot_be_claimed_by_metadata_or_spoofed_origins(self):
        self.assert_dropped(
            {
                "metadata": {
                    "secret_env": [
                        {
                            "manager": "env",
                            "key": "FI_INTERNAL_SUBMIT_SECRET",
                            "source": "INTERNAL_API_SECRET",
                            "scope": "sink",
                            "purpose": "internal_api_secret",
                        }
                    ]
                },
                "origins": {
                    "FI_INTERNAL_SUBMIT_SECRET": ["env", "INTERNAL_API_SECRET", "sink"]
                },
            },
            "INTERNAL_API_SECRET",
            "FI_INTERNAL_SUBMIT_SECRET",
        )

    def test_final_boundary_rechecks_provenance_after_all_overlays(self):
        # Simulate a future resolver regression that returns a denied value.
        # The final guard must reject it independently of early lookup checks.
        for scope, source, destination in (
            ("metadata", "PG_PASSWORD", "OPENAI_API_KEY"),
            ("target", "AGENTCC_ADMIN_TOKEN", "SIMULATOR_LLM_API_KEY"),
            ("metadata", "VAPI_PUBLIC_API_KEY", "AGENTCC_API_KEY"),
            ("sink", "PG_PASSWORD", "FI_API_KEY"),
        ):
            with self.subTest(scope=scope, source=source):
                ns, spawn, request, *_ = self.harness()

                async def late_overlay(
                    job, *, origins, scope=scope, source=source, destination=destination
                ):
                    origins[destination] = ("env", source, scope)
                    return {destination: "synthetic-late-secret"}

                ns["_resolve_voice_secret_env"] = late_overlay
                self.run_job(ns, request, {})
                env = spawn.call_args.kwargs["env"]
                self.assertNotIn(destination, env)
                self.assertNotIn("synthetic-late-secret", env.values())
                self.assertNotIn(
                    "synthetic-late-secret", repr(ns["activity"].logger.mock_calls)
                )

    def test_reviewed_runtime_aliases_and_setting_fallback_keep_working(self):
        for manager in ("env", "setting"):
            for setting_value in (None, "settings-runtime"):
                with self.subTest(manager=manager, setting_value=setting_value):
                    ns, spawn, request, worker, *_ = self.harness()
                    worker["LIVEKIT_API_KEY"] = "worker-runtime"
                    ns["settings"].LIVEKIT_API_KEY = setting_value
                    refs = [
                        {
                            "key": "LIVEKIT_API_KEY",
                            "manager": manager,
                            "source": "LIVEKIT_API_KEY",
                            "setting": "LIVEKIT_API_KEY",
                        }
                    ]
                    self.run_job(ns, request, {"metadata": {"secret_env": refs}})
                    env = spawn.call_args.kwargs["env"]
                    expected = (
                        setting_value
                        if manager == "setting" and setting_value
                        else "worker-runtime"
                    )
                    self.assertEqual(expected, env["LIVEKIT_API_KEY"])

    def test_reviewed_sink_mappings_and_customer_credentials_survive(self):
        ns, spawn, request, worker, _, _, _, credentials, model = self.harness()
        mappings = (
            ("INTERNAL_API_SECRET", "internal_api_secret", "FI_INTERNAL_SUBMIT_SECRET"),
            ("SINK_API_TOKEN", "api_key", "FI_API_KEY"),
            ("SINK_SECRET", "secret_key", "FI_SECRET_KEY"),
        )
        job = self.vapi_job(credentials)
        job["sink"] = {"secret_refs": {}}
        for source, purpose, _ in mappings:
            worker[source] = "synthetic-" + source
            job["sink"]["secret_refs"][purpose] = {
                "key": source,
                "manager": "env",
                "purpose": purpose,
            }
        self.run_job(ns, request, job)
        env = spawn.call_args.kwargs["env"]
        for source, _, destination in mappings:
            self.assertEqual(worker[source], env[destination])
        self.assertEqual("customer-public", env["VAPI_PUBLIC_API_KEY"])
        self.assertEqual("customer-private", env["VAPI_API_KEY"])
        self.assertEqual("runtime-gateway", env["AGENTCC_API_KEY"])
        model.objects.get.assert_called_once_with(id=credentials.id)

    def test_self_host_operator_vapi_parity_and_customer_precedence(self):
        for public in (False, True):
            with self.subTest(public=public):
                ns, spawn, request, _, _, _, _, credentials, _ = self.harness("false")
                self.run_job(ns, request, self.vapi_job(credentials, public=public))
                env = spawn.call_args.kwargs["env"]
                self.assertEqual(
                    "customer-public" if public else "operator-public",
                    env["VAPI_PUBLIC_API_KEY"],
                )
                self.assertEqual("customer-private", env["VAPI_API_KEY"])

    def test_every_resolved_reference_carries_provenance_to_final_assembly(self):
        ns, spawn, request, worker, _, _, _, credentials, _ = self.harness()
        worker["LIVEKIT_API_KEY"] = "platform-livekit"
        job = self.vapi_job(credentials)
        job["metadata"]["secret_env"].append(
            {"key": "LIVEKIT_API_KEY", "manager": "env", "source": "LIVEKIT_API_KEY"}
        )
        job["sink"] = {
            "secret_refs": {
                "internal": {
                    "key": "INTERNAL_API_SECRET",
                    "manager": "env",
                    "purpose": "internal_api_secret",
                }
            }
        }
        job["spec"] = {
            "target": {
                "secret_refs": {"auth": {"key": "TARGET_TOKEN", "manager": "env"}}
            }
        }
        final = Mock(wraps=ns["_final_child_environment"])
        ns["_final_child_environment"] = final
        self.run_job(ns, request, job)
        spawn.assert_awaited_once()
        origins = final.call_args.kwargs.get("origins")
        self.assertIsNotNone(
            origins, "final spawn assembly must receive source provenance"
        )
        for key, expected in {
            "FI_INTERNAL_SUBMIT_SECRET": ("env", "INTERNAL_API_SECRET", "sink"),
            "TARGET_TOKEN": ("env", "TARGET_TOKEN", "target"),
            "LIVEKIT_API_KEY": ("env", "LIVEKIT_API_KEY", "metadata"),
            "VAPI_API_KEY": ("provider_credentials", credentials.id, "metadata"),
            "VAPI_PUBLIC_API_KEY": ("provider_credentials", credentials.id, "metadata"),
        }.items():
            self.assertEqual(expected, origins[key])


if __name__ == "__main__":
    unittest.main()
