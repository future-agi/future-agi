"""Offline r3 regressions. Run directly with python -I; no Django or sockets.

Production functions are AST-loaded; ORM, workflow and thread adapters are
explicit doubles. Only synthetic credentials enter these tests.
"""

from __future__ import annotations

import ast
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, Mock, patch

_ROOT = Path(__file__).resolve().parents[1]
_ACTIVITY = _ROOT / "temporal/activities/hosted_runner.py"
_SERVICE = _ROOT / "services/hosted_runner.py"
_VIEW = _ROOT / "views/run_test.py"
_ENV = runpy.run_path(str(_ROOT / "tests/test_hosted_child_environment.py"))
_ADMISSION = runpy.run_path(str(_ROOT / "tests/test_hosted_child_admission.py"))
load_functions = _ADMISSION["load_functions"]
finish = _ADMISSION["finish"]
NoSockets = _ADMISSION["NoSockets"]
ApplicationError = _ADMISSION["ApplicationError"]


class HostedChildR3Tests(NoSockets):
    def test_gateway_pair_only_and_backend_names_absent(self):
        allowed = {
            "AGENTCC_API_KEY": "job-runtime",
            "AGENTCC_BASE_URL": "https://gateway.invalid",
        }
        denied = {
            "AGENTCC_INTERNAL_API_KEY",
            "AGENTCC_ADMIN_TOKEN",
            "AGENTCC_HARNESS_API_KEY",
            "AGENTCC_UNKNOWN",
            "AGENTCC_API_KEY_BACKUP",
            "AGENTCC_BASE_URL_BACKUP",
            "agentcc_api_key",
            "Agentcc_Api_Key",
            "PG_PASSWORD",
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
        }
        worker = {**allowed, **dict.fromkeys(denied, "synthetic-backend")}
        env = _ENV["_environment_helpers"](worker)["_child_environment"]({})
        self.assertEqual(allowed, env)
        self.assertFalse(denied.intersection(env))

    def vapi_harness(self, authenticated=True, public_key="customer-public"):
        credentials = NS(
            id="customer-credential",
            provider_type="vapi",
            config_json={
                "monitorPlan": {"controlAuthenticationEnabled": authenticated},
                "public_api_key": public_key,
            },
            get_api_key=Mock(return_value="customer-private"),
            get_api_secret=Mock(return_value="not-the-public-key"),
        )
        profile = {
            "api_key_env": "VAPI_API_KEY",
            "sip_inbound_originator": "vapi",
            "sip_inbound_originator_fields": (),
            "target_id_field": "assistant_id",
            "emits_web_evidence": True,
        }
        service = {
            "HostedRunnerBuildError": ValueError,
            "_PROVIDER_ENV": {"vapi": {"api_key": "VAPI_API_KEY"}},
            "_provider_profile": lambda provider: profile,
            "settings": NS(CLOUD_DEPLOYMENT="US"),
            "CLOUD_DEPLOYMENTS": ("US", "EU", "DEV"),
        }
        load_functions(_ROOT.parent / "tfc/ee_loader.py", {"is_cloud_env"}, service)
        load_functions(
            _SERVICE,
            {
                "_voice_agent_definition",
                "_credential_or_env",
                "_provider_credential_ref",
                "_env_passthrough_ref",
                "_agent_field",
                "_resolve_agent_version",
                "_vapi_control_secret_ref",
                "_require",
            },
            service,
        )
        version = NS(configuration_snapshot={"agent_name": "target"})
        agent = NS(active_version=version)
        provider_model = NS(
            objects=Mock(get=Mock(return_value=credentials)),
            DoesNotExist=type("Missing", (Exception,), {}),
        )
        imports = patch.dict(
            "sys.modules",
            {
                "simulate.models.agent_definition": NS(
                    ProviderCredentials=provider_model
                ),
                "simulate.services.hosted_runner": NS(
                    _PROVIDER_PROFILES={"vapi": profile}
                ),
            },
        )
        imports.start()
        self.addCleanup(imports.stop)
        worker = {
            "VAPI_PUBLIC_API_KEY": "worker-public",
            "VAPI_API_KEY": "worker-private",
        }
        ns = _ENV["_environment_helpers"](worker)

        def sync_to_async(fn, **kwargs):
            async def wrapped():
                return fn()

            return wrapped

        ns.update(
            {
                "ApplicationError": ApplicationError,
                "close_old_connections": Mock(),
                "sync_to_async": sync_to_async,
                "settings": service["settings"],
                "is_cloud_env": service["is_cloud_env"],
            }
        )
        load_functions(
            _ACTIVITY,
            {
                "_resolve_voice_secret_env",
                "_resolve_provider_credential",
                "_customer_provider_env_keys",
                "_check_vapi_control_key",
            },
            ns,
        )
        return service, ns, agent, version, credentials, provider_model

    def test_vapi_originator_public_key_is_per_job_and_conditional(self):
        for authenticated in (True, False):
            with self.subTest(authenticated=authenticated):
                service, ns, agent, version, credentials, model = self.vapi_harness(
                    authenticated
                )
                target, refs = service["_voice_agent_definition"](
                    agent, "vapi", "sip_inbound", credentials, version
                )
                self.assertEqual("vapi", target["transport"]["inbound_call_originator"])
                job = {
                    "metadata": {"secret_env": refs},
                    "voice": {"agent_definition": target},
                }
                env = ns["_child_environment"](job)
                self.assertNotIn("VAPI_PUBLIC_API_KEY", env)
                env.update(finish(ns["_resolve_voice_secret_env"](job)))
                self.assertEqual("customer-private", env["VAPI_API_KEY"])
                if authenticated:
                    self.assertEqual("customer-public", env.get("VAPI_PUBLIC_API_KEY"))
                    public_ref = next(
                        ref for ref in refs if ref["key"] == "VAPI_PUBLIC_API_KEY"
                    )
                    self.assertEqual("provider_credentials", public_ref["manager"])
                    self.assertEqual(credentials.id, public_ref["credential_id"])
                    self.assertNotIn("customer-public", repr(job))
                else:
                    self.assertNotIn("VAPI_PUBLIC_API_KEY", env)
                    self.assertFalse(
                        any(ref["key"] == "VAPI_PUBLIC_API_KEY" for ref in refs)
                    )
                model.objects.get.assert_called_once_with(id=credentials.id)
                credentials.get_api_secret.assert_not_called()

    def test_vapi_missing_public_key_fails_closed_not_worker_or_private_key(self):
        service, ns, agent, version, credentials, _ = self.vapi_harness(public_key="")
        with self.assertRaises((ValueError, ApplicationError)):
            _, refs = service["_voice_agent_definition"](
                agent, "vapi", "sip_inbound", credentials, version
            )
            finish(ns["_resolve_voice_secret_env"]({"metadata": {"secret_env": refs}}))

    def test_vapi_public_key_rejects_non_customer_managers(self):
        _, ns, _, _, _, _ = self.vapi_harness()
        for manager in ("env", "setting", "unknown"):
            with self.subTest(manager=manager), self.assertRaises(ApplicationError):
                finish(
                    ns["_resolve_voice_secret_env"](
                        {
                            "metadata": {
                                "secret_env": [
                                    {
                                        "key": "VAPI_PUBLIC_API_KEY",
                                        "manager": manager,
                                        "source": "VAPI_PUBLIC_API_KEY",
                                        "setting": "VAPI_PUBLIC_API_KEY",
                                    }
                                ]
                            }
                        }
                    )
                )

    def test_vapi_non_originator_does_not_receive_control_key(self):
        service, ns, agent, version, credentials, _ = self.vapi_harness()
        credentials.assistant_id = "assistant"
        _, refs = service["_voice_agent_definition"](
            agent, "vapi", "vapi_websocket", credentials, version
        )
        env = finish(
            ns["_resolve_voice_secret_env"]({"metadata": {"secret_env": refs}})
        )
        self.assertEqual({"VAPI_API_KEY": "customer-private"}, env)


class HostedRerunR3Tests(NoSockets):
    def harness(
        self,
        owner,
        *,
        enabled=False,
        voice_enabled=False,
        historical=True,
        legacy=False,
    ):
        run = NS(
            id="run",
            organization_id="org",
            agent_definition=NS(agent_type="VOICE"),
            workspace_id=None,
        )
        execution = MagicMock()
        execution.id = "execution"
        execution.run_test = run
        execution.status = "completed"
        execution.scenario_ids = ["case"]
        execution.simulator_agent_id = None
        execution.execution_metadata = (
            {"hosted_runner": True} if historical and not legacy else {}
        )
        execution.calls.filter.return_value.exists.return_value = historical and legacy
        call = MagicMock(id="call")
        qs = MagicMock()
        qs.__iter__.side_effect = lambda: iter([execution])
        for attr in ("select_related", "exclude", "order_by"):
            getattr(qs, attr).return_value = qs
        qs.exists.return_value = True
        calls = MagicMock()
        calls.__iter__.side_effect = lambda: iter([call])
        calls.exclude.return_value = calls
        calls.exists.return_value = True
        gm = Mock()
        gm.internal_server_error_response.side_effect = AssertionError(
            "unexpected view exception"
        )
        gm.bad_request.side_effect = lambda message: {"status": 400, "message": message}
        view = Mock(_gm=gm)
        view._prepare_call_executions_for_rerun_bulk.return_value = (["call"], [])
        native = Mock(return_value={"workflow_id": "native-workflow", "merged": False})
        workflow = Mock(return_value="hosted-workflow")
        services = NS(
            resolve_run_agent_version=lambda *a, **kw: "version",
            agent_field_for_run=lambda *a, **kw: "VOICE",
            hosted_runner_supports=lambda *a: True,
            resolve_runner_mode=lambda *a: "voice_sip",
        )
        imports = patch.dict(
            "sys.modules",
            {
                "simulate.services.hosted_runner": services,
                "simulate.temporal.client": NS(
                    rerun_call_executions=native,
                    start_simulation_runner_workflow=workflow,
                ),
            },
        )
        imports.start()
        self.addCleanup(imports.stop)
        ns = {
            "app_settings": NS(
                HOSTED_RUNNER_ENABLED=enabled, HOSTED_RUNNER_VOICE_ENABLED=voice_enabled
            ),
            "AgentDefinition": NS(AgentTypeChoices=NS(TEXT="TEXT", VOICE="VOICE")),
            "get_object_or_404": Mock(
                return_value=execution if owner == "CallExecutionRerunView" else run
            ),
            "run_test_workspace_filter": Mock(),
            "RunTest": Mock(),
            "TestExecution": NS(
                objects=Mock(filter=Mock(return_value=qs)),
                ExecutionStatus=NS(
                    PENDING="pending",
                    CANCELLING="cancelling",
                    RUNNING="running",
                    EVALUATING="evaluating",
                    FAILED="failed",
                ),
            ),
            "CallExecution": NS(
                objects=Mock(filter=Mock(return_value=calls)),
                CallStatus=NS(PENDING="pending", FAILED="failed"),
            ),
            "_repository_harness_job_id": lambda *a: None,
            "_latest_repository_harness_execution": lambda *a: None,
            "_voice_sim_gate_response": lambda *a: None,
            "_create_rerun_call_execution": Mock(),
            "clear_call_for_rerun": Mock(),
            "Response": lambda data, **kw: data,
            "RerunCallsResponseSerializer": lambda data: NS(data=data),
            "status": NS(HTTP_200_OK=200, HTTP_500_INTERNAL_SERVER_ERROR=500),
            "logger": Mock(),
            "traceback": Mock(),
            "Http404": type("Http404", (Exception,), {}),
        }
        # Real eligibility and provenance predicates, never a forced True stub.
        names = {
            n.name
            for n in ast.parse(_VIEW.read_text()).body
            if isinstance(n, ast.FunctionDef)
            and (
                n.name.startswith("_hosted_")
                or n.name.startswith("_historically_hosted")
            )
        }
        load_functions(_VIEW, names | {"_dispatch_hosted_rerun"}, ns)
        load_functions(_VIEW, {"post"}, ns, owner=owner)
        request = NS(
            organization=NS(id="org"),
            validated_data={"rerun_type": "call_and_eval", "select_all": True},
        )
        return ns, view, request, execution, native, workflow

    def test_both_views_refuse_historical_hosted_before_any_mutation(self):
        for owner in ("CallExecutionRerunView", "TestExecutionRerunView"):
            for enabled, voice_enabled in (
                (False, False),
                (False, True),
                (True, False),
            ):
                for legacy in (False, True):
                    with self.subTest(
                        owner=owner, enabled=enabled, voice=voice_enabled, legacy=legacy
                    ):
                        ns, view, request, execution, native, workflow = self.harness(
                            owner,
                            enabled=enabled,
                            voice_enabled=voice_enabled,
                            legacy=legacy,
                        )
                        self.assertEqual(
                            voice_enabled,
                            ns["_hosted_execution_eligible"](
                                execution.run_test, execution
                            ),
                        )
                        response = ns["post"](view, request, "synthetic-id")
                        self.assertEqual(400, response.get("status"))
                        ns["clear_call_for_rerun"].assert_not_called()
                        ns["_create_rerun_call_execution"].assert_not_called()
                        view._clear_call_execution_data.assert_not_called()
                        view._prepare_call_executions_for_rerun_bulk.assert_not_called()
                        execution.save.assert_not_called()
                        native.assert_not_called()
                        workflow.assert_not_called()

    def test_native_history_stays_native_and_enabled_hosted_stays_hosted(self):
        for owner in ("CallExecutionRerunView", "TestExecutionRerunView"):
            for historical in (False, True):
                with self.subTest(owner=owner, historical=historical):
                    ns, view, request, execution, native, workflow = self.harness(
                        owner,
                        historical=historical,
                        legacy=historical,
                        enabled=historical,
                        voice_enabled=historical,
                    )
                    response = ns["post"](view, request, "synthetic-id")
                    self.assertNotEqual(400, response.get("status"))
                    self.assertEqual(int(not historical), native.call_count)
                    self.assertEqual(int(historical), workflow.call_count)
                    if historical:
                        self.assertIs(
                            True, execution.execution_metadata.get("hosted_runner")
                        )

    def test_dispatch_refuses_when_either_voice_routing_switch_is_disabled(self):
        for enabled, voice_enabled in ((False, False), (False, True), (True, False)):
            with self.subTest(enabled=enabled, voice=voice_enabled):
                ns, _, _, execution, native, workflow = self.harness(
                    "CallExecutionRerunView",
                    enabled=enabled,
                    voice_enabled=voice_enabled,
                )
                with self.assertRaisesRegex(
                    ValueError, "Hosted simulation reruns are disabled"
                ):
                    ns["_dispatch_hosted_rerun"](execution, ["call"])
                native.assert_not_called()
                workflow.assert_not_called()
                execution.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
