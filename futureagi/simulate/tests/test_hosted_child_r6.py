"""Reviewed-source contract and Vapi parity; DB-free, socket-blocked AST tests.

Run with python -I -B. Set HOSTED_TEST_ALK_REPO to a local ALK checkout for
pinned SDK producer tests (git objects only; never import the SDK or settings).
"""

from __future__ import annotations

import ast
import json
import os
import random
import runpy
import subprocess
import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

_ROOT = Path(__file__).resolve().parents[1]
_ACTIVITY = _ROOT / "temporal/activities/hosted_runner.py"
_R5 = runpy.run_path(str(_ROOT / "tests/test_hosted_child_r5.py"))
_R4 = _R5["_R4"]
load = _R4["load_functions"]
finish = _R4["finish"]
_ALK_PIN = "c95d01efe3ecaa4d28e5bc0d0ec536f877ef3d9f"
_BACKEND = frozenset(
    """RABBITMQ_DEFAULT_PASS SIGNING_KEY FI_API_SECRET_PEPPER
    FI_API_SECRET_PEPPERS_RETIRED AWS_SECRET_ACCESS_KEY GCP_MARKETPLACE_SA_JSON
    MINIO_ACCESS_KEY DAYTONA_API_KEY EE_LICENSE_KEY FUTUREAGI_CLOUD_API_KEY
    API_SECRET_PEPPER DATABASES CACHES SECRET_KEY_FALLBACKS""".split()
)
_OPERATOR = frozenset({"VAPI_API_KEY", "VAPI_PUBLIC_API_KEY", "RETELL_API_KEY"})
_SETTINGS = frozenset({"LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"})


def ref_job(scope, source, destination, manager="env"):
    ref = {"key": destination, "source": source, "setting": source, "manager": manager}
    if scope == "metadata":
        return {"metadata": {"secret_env": [ref]}}
    if scope == "target":
        return {"spec": {"target": {"secret_refs": {"auth": ref}}}}
    ref["purpose"] = "api_key"
    return {"sink": {"secret_refs": {"auth": ref}}}


class HostedChildR6Tests(_R4["_ADMISSION"]["NoSockets"]):
    harness = _R5["HostedChildR5Tests"].harness
    run_job = _R4["HostedChildR4Tests"].run_job
    vapi_job = _R4["HostedChildR4Tests"].vapi_job

    def test_all_lens_a_aliases_absent_at_final_spawn_in_every_scope(self):
        for deployment in ("US", "false"):
            for scope in ("target", "metadata", "sink"):
                for manager in ("env", "setting"):
                    ns, spawn, request, worker, *_ = self.harness(deployment)
                    for source in sorted(_BACKEND):
                        with self.subTest(
                            deployment=deployment,
                            scope=scope,
                            manager=manager,
                            source=source,
                        ):
                            marker = "synthetic-backend-" + source
                            worker[source] = marker
                            setattr(ns["settings"], source, marker)
                            self.run_job(
                                ns,
                                request,
                                ref_job(scope, source, "OPENAI_API_KEY", manager),
                            )
                            env = spawn.call_args.kwargs["env"]
                            self.assertNotIn(marker, env.values())
                            self.assertNotIn(
                                "FI_API_KEY" if scope == "sink" else "OPENAI_API_KEY",
                                env,
                            )

    def test_generated_noncontract_sources_never_reach_any_destination_class(self):
        rng = random.Random(431)
        sources = sorted(_BACKEND) + [
            "SYNTHETIC_"
            + "".join(rng.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=12))
            + suffix
            for suffix in ("_SECRET", "_TOKEN", "_PEPPER", "_KEY", "_CONFIG", "")
            for _ in range(8)
        ]
        destinations = (
            "OPENAI_API_KEY",
            "AGENTCC_API_KEY",
            "AGENTCC_BASE_URL",
            "SIMULATOR_LLM_API_KEY",
            "LIVEKIT_API_SECRET",
            "FI_API_KEY",
            "FI_INTERNAL_SUBMIT_SECRET",
            "CUSTOM_AUTH",
            "PG_PASSWORD",
        )
        for deployment in ("US", "false"):
            for scope in ("target", "metadata", "sink"):
                for manager in ("env", "setting"):
                    ns, spawn, request, worker, *_ = self.harness(deployment)
                    for source in sources:
                        worker[source] = "forbidden-generated-value"
                        setattr(ns["settings"], source, "forbidden-generated-value")
                    for destination in destinations:
                        for source in sources:
                            with self.subTest(
                                deployment=deployment,
                                scope=scope,
                                manager=manager,
                                destination=destination,
                                source=source,
                            ):
                                # Each source gets its own final spawn: a later
                                # denied duplicate must not mask an earlier leak.
                                self.run_job(
                                    ns,
                                    request,
                                    ref_job(scope, source, destination, manager),
                                )
                                env = spawn.call_args.kwargs["env"]
                                self.assertNotIn(
                                    "forbidden-generated-value", env.values()
                                )
                                self.assertNotIn(
                                    "forbidden-generated-value",
                                    repr(ns["activity"].logger.mock_calls),
                                )
                                spawn.reset_mock()
                                ns["activity"].logger.reset_mock()

    def test_final_boundary_independently_rejects_unknown_sources_and_nonstrings(self):
        for deployment in ("US", "false"):
            ns, spawn, request, *_ = self.harness(deployment)
            for manager in ("env", "setting"):
                for scope in ("target", "metadata", "sink"):
                    for source in sorted(_BACKEND | {"SYNTHETIC_FUTURE_SECRET"}):
                        with self.subTest(
                            deployment=deployment,
                            source=source,
                            scope=scope,
                            manager=manager,
                        ):

                            async def overlay(
                                job,
                                *,
                                origins,
                                source=source,
                                scope=scope,
                                manager=manager,
                            ):
                                origins["OPENAI_API_KEY"] = (manager, source, scope)
                                return {"OPENAI_API_KEY": "late-forbidden-value"}

                            ns["_resolve_voice_secret_env"] = overlay
                            self.run_job(ns, request, {})
                            self.assertNotIn(
                                "OPENAI_API_KEY", spawn.call_args.kwargs["env"]
                            )
            for value in (
                {"password": "synthetic"},
                ["synthetic"],
                ("synthetic",),
                b"synthetic",
                None,
            ):

                async def overlay(job, *, origins, value=value):
                    origins["LIVEKIT_API_KEY"] = (
                        "setting",
                        "LIVEKIT_API_KEY",
                        "metadata",
                    )
                    return {"LIVEKIT_API_KEY": value}

                ns["_resolve_voice_secret_env"] = overlay
                self.run_job(ns, request, {})
                self.assertNotIn("LIVEKIT_API_KEY", spawn.call_args.kwargs["env"])

    def test_source_contract_blocks_lookup_before_reading_worker_values(self):
        class Worker(dict):
            def get(self, key, default=None):
                if key in _BACKEND:
                    raise AssertionError("forbidden env lookup: " + key)
                return super().get(key, default)

        class Settings:
            def __getattr__(self, key):
                if key in _BACKEND:
                    raise AssertionError("forbidden settings lookup: " + key)
                raise AttributeError(key)

        for deployment in ("US", "false"):
            ns, spawn, request, worker, *_ = self.harness(deployment)
            ns["os"].environ = Worker(worker)
            ns["settings"] = Settings()
            ns["settings"].CLOUD_DEPLOYMENT = deployment
            self.assertEqual(_OPERATOR, ns.get("_CHILD_ENV_OPERATOR_SOURCES"))
            self.assertEqual(_SETTINGS, ns.get("_CHILD_ENV_SETTING_SOURCES"))
            for source in sorted(_BACKEND):
                for scope in ("target", "metadata", "sink"):
                    for manager in ("env", "setting"):
                        self.run_job(
                            ns, request, ref_job(scope, source, "CUSTOM_AUTH", manager)
                        )
                        self.assertNotIn("CUSTOM_AUTH", spawn.call_args.kwargs["env"])

    def test_settings_only_reviewed_names_and_string_values(self):
        for deployment in ("US", "false"):
            ns, spawn, request, worker, *_ = self.harness(deployment)
            for source in sorted(_BACKEND | _SETTINGS | {"PATH", "VAPI_API_KEY"}):
                for value in (
                    {"password": "synthetic"},
                    ["synthetic"],
                    ("synthetic",),
                    b"synthetic",
                    None,
                ):
                    with self.subTest(
                        deployment=deployment, source=source, value=type(value).__name__
                    ):
                        worker.pop(source, None)
                        setattr(ns["settings"], source, value)
                        self.run_job(
                            ns,
                            request,
                            ref_job("metadata", source, "OPENAI_API_KEY", "setting"),
                        )
                        self.assertNotIn(
                            "OPENAI_API_KEY", spawn.call_args.kwargs["env"]
                        )
            for source in sorted(_SETTINGS):
                setattr(ns["settings"], source, "reviewed-string")
                self.run_job(
                    ns,
                    request,
                    ref_job("metadata", source, "OPENAI_API_KEY", "setting"),
                )
                self.assertEqual(
                    "reviewed-string", spawn.call_args.kwargs["env"]["OPENAI_API_KEY"]
                )
            # Non-string settings must not be rescued by a truthiness fallback.
            ns["settings"].LIVEKIT_API_KEY = []
            worker["LIVEKIT_API_KEY"] = "env-fallback"
            self.run_job(
                ns,
                request,
                ref_job("metadata", "LIVEKIT_API_KEY", "OPENAI_API_KEY", "setting"),
            )
            self.assertNotIn("OPENAI_API_KEY", spawn.call_args.kwargs["env"])

    def test_every_reviewed_env_source_reaches_spawn_without_widening_settings(self):
        for deployment in ("US", "false"):
            ns, spawn, request, worker, *_ = self.harness(deployment)
            sources = ns["_CHILD_ENV_ALLOWLIST"] | (
                _OPERATOR if deployment == "false" else set()
            )
            for source in sorted(sources):
                for scope in ("target", "metadata"):
                    with self.subTest(
                        deployment=deployment, source=source, scope=scope
                    ):
                        worker[source] = "reviewed-runtime-string"
                        self.run_job(ns, request, ref_job(scope, source, "CUSTOM_AUTH"))
                        self.assertEqual(
                            "reviewed-runtime-string",
                            spawn.call_args.kwargs["env"]["CUSTOM_AUTH"],
                        )
                if source not in _SETTINGS:
                    setattr(ns["settings"], source, "unreviewed-setting")
                    self.run_job(
                        ns,
                        request,
                        ref_job("metadata", source, "CUSTOM_AUTH", "setting"),
                    )
                    self.assertNotIn("CUSTOM_AUTH", spawn.call_args.kwargs["env"])

    def test_operator_contract_is_exact_and_cloud_does_not_borrow_it(self):
        for deployment in ("US", "false"):
            ns, spawn, request, worker, *_ = self.harness(deployment)
            for source in sorted(_OPERATOR):
                for variant in (
                    source,
                    source.lower(),
                    source + "_BACKUP",
                    "VAPI_FUTURE_KEY",
                ):
                    worker[variant] = "operator-synthetic"
                    self.run_job(ns, request, ref_job("target", variant, "CUSTOM_AUTH"))
                    env = spawn.call_args.kwargs["env"]
                    if deployment == "false" and variant == source:
                        self.assertEqual("operator-synthetic", env["CUSTOM_AUTH"])
                    else:
                        self.assertNotIn("CUSTOM_AUTH", env)
            if deployment == "false":
                for key in sorted(_OPERATOR):
                    self.run_job(ns, request, ref_job("metadata", key, key))
                    self.assertEqual(
                        "operator-synthetic", spawn.call_args.kwargs["env"][key]
                    )

    def test_reviewed_sink_pairs_gateway_and_genuine_customer_records(self):
        _R5[
            "HostedChildR5Tests"
        ].test_reviewed_sink_mappings_and_customer_credentials_survive(self)
        _R5[
            "HostedChildR5Tests"
        ].test_self_host_operator_vapi_parity_and_customer_precedence(self)
        for field in ("api_key", "api_secret"):
            ns, spawn, request, _, _, _, _, credential, _ = self.harness()
            job = {
                "metadata": {
                    "secret_env": [
                        {
                            "key": "CUSTOM_AUTH",
                            "manager": "provider_credentials",
                            "credential_id": credential.id,
                            "field": field,
                            "source": "DATABASES",
                        }
                    ]
                }
            }
            self.run_job(ns, request, job)
            self.assertEqual(
                "customer-private" if field == "api_key" else "not-the-public-key",
                spawn.call_args.kwargs["env"]["CUSTOM_AUTH"],
            )

    def test_unknown_vapi_policy_runs_enabled_missing_key_still_refuses(self):
        for deployment in ("US", " eu ", "dev", "false"):
            for config in (
                {},
                {"monitorPlan": {}},
                {"monitorPlan": {"controlAuthenticationEnabled": True}},
                {"monitorPlan": {"controlAuthenticationEnabled": False}},
            ):
                with self.subTest(deployment=deployment, config=config):
                    ns, spawn, request, _, _, _, _, credential, _ = self.harness(
                        deployment, config
                    )
                    outcome = self.run_job(
                        ns, request, self.vapi_job(credential, public=False)
                    )
                    enabled = (
                        config.get("monitorPlan", {}).get(
                            "controlAuthenticationEnabled"
                        )
                        is True
                    )
                    if deployment != "false" and enabled:
                        self.assertEqual("failed", outcome.phase)
                        self.assertIn("No call was started", outcome.detail)
                        spawn.assert_not_called()
                    else:
                        spawn.assert_awaited_once()
                        env = spawn.call_args.kwargs["env"]
                        if deployment == "false":
                            self.assertEqual(
                                "operator-public", env["VAPI_PUBLIC_API_KEY"]
                            )
                        else:
                            self.assertNotIn("VAPI_PUBLIC_API_KEY", env)
            ns, spawn, request, _, _, _, _, credential, _ = self.harness(deployment, {})
            job = self.vapi_job(credential, public=False)
            job["metadata"]["secret_env"] = []
            self.run_job(ns, request, job)
            spawn.assert_awaited_once()

    def test_duplicate_private_refs_cannot_erase_an_explicit_enabled_policy(self):
        for enabled_first in (True, False):
            ns, spawn, request, _, _, _, _, credential, model = self.harness(config={})
            other = NS(**{**vars(credential), "id": "other-credential"})
            known = credential if enabled_first else other
            known.config_json = {"monitorPlan": {"controlAuthenticationEnabled": True}}
            rows = {credential.id: credential, other.id: other}
            model.objects.get.side_effect = lambda *, id, rows=rows: rows[id]
            job = self.vapi_job(credential, public=False)
            job["metadata"]["secret_env"].append(
                {
                    "key": "VAPI_API_KEY",
                    "manager": "provider_credentials",
                    "credential_id": other.id,
                    "field": "api_key",
                }
            )
            outcome = self.run_job(ns, request, job)
            self.assertEqual("failed", outcome.phase)
            self.assertIn("No call was started", outcome.detail)
            spawn.assert_not_called()

    def test_pinned_sdk_config_error_and_v1561_parent_failure_parity(self):
        repo = os.environ.get("HOSTED_TEST_ALK_REPO")
        if not repo:
            self.skipTest("set HOSTED_TEST_ALK_REPO for local pinned SDK proof")

        def sdk_source(path):
            return subprocess.check_output(
                ["git", "-C", repo, "show", _ALK_PIN + ":src/fi/simulate/" + path],
                text=True,
            )

        tree = ast.parse(sdk_source("endpoints/vapi.py"))
        nodes = [
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef)
            and n.name in {"VapiCallOriginator", "VapiOriginatorConfigError"}
        ]
        self.assertEqual(2, len(nodes))
        ns, spawn, request, _, _, _, _, credential, _ = self.harness(config={})
        job = self.vapi_job(credential, public=False)
        self.run_job(ns, request, job)
        spawn.assert_awaited_once()
        env = dict(spawn.call_args.kwargs["env"])
        env.update(VAPI_ASSISTANT_ID="assistant", VAPI_PHONE_NUMBER_ID="phone")
        client = NS(
            get=AsyncMock(
                return_value=NS(
                    raise_for_status=lambda: None,
                    json=lambda: {
                        "monitorPlan": {"controlAuthenticationEnabled": True}
                    },
                )
            ),
            post=AsyncMock(),
        )
        consumer = {
            "os": NS(environ=env),
            "uuid": uuid,
            "httpx": NS(AsyncClient=lambda **kw: client, Timeout=lambda *a, **kw: None),
            "VapiCall": lambda **kw: NS(**kw),
        }
        future = ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0
        )
        exec(
            compile(
                ast.fix_missing_locations(
                    ast.Module(body=[future, *nodes], type_ignores=[])
                ),
                "pinned-vapi",
                "exec",
            ),
            consumer,
        )
        with self.assertRaises(consumer["VapiOriginatorConfigError"]) as caught:
            finish(consumer["VapiCallOriginator"].from_env().start())
        client.post.assert_not_called()

        # Execute the actual engine's exception handler, not an invented status
        # mapping. Its generic code intentionally survives ruling 435 unchanged.
        engine = ast.parse(sdk_source("simulation/engines/livekit.py"))
        handler = next(
            n
            for n in ast.walk(engine)
            if isinstance(n, ast.ExceptHandler)
            and any(
                isinstance(v, ast.JoinedStr) and "call_start_failed" in ast.unparse(v)
                for v in ast.walk(n)
            )
        )
        safe = next(
            n
            for n in engine.body
            if isinstance(n, ast.FunctionDef)
            and n.name == "_safe_provider_error_details"
        )
        failure_helper = next(
            n
            for n in engine.body
            if isinstance(n, ast.FunctionDef) and n.name == "_failure_outcome"
        )

        class Model(NS):
            def model_dump(self, **kwargs):
                return vars(self)

        preparation_failed = type("PreparationFailed", (Exception,), {})
        error_ns = {
            "exc": caught.exception,
            "name": "vapi",
            "logger": Mock(),
            "redacted_exc_info": lambda exc: None,
            "run_id": "run",
            "test_case_id": "case",
            "_CaseOutcome": Model,
            "SimulationFailure": Model,
            "_PreparationFailed": preparation_failed,
            "TestCaseStatus": NS(FAILED="failed", COMPLETED="completed"),
            "FailureStage": NS(PREPARING="preparing"),
        }
        module = ast.Module(
            body=[future, safe, failure_helper, *handler.body], type_ignores=[]
        )
        with self.assertRaises(preparation_failed):
            exec(
                compile(ast.fix_missing_locations(module), "pinned-engine", "exec"),
                error_ns,
            )
        self.assertEqual("vapi_call_start_failed", error_ns["outcome"].failure.code)
        self.assertIs(False, error_ns["outcome"].failure.retryable)
        self.assertEqual(
            {
                "operation": "vapi_call_start",
                "exception_type": "VapiOriginatorConfigError",
            },
            error_ns["outcome"].failure.details,
        )
        # Execute the SDK voice finalizer as well: per-case failure becomes the
        # overall report failure before the child selects its status detail.
        voice = ast.parse(sdk_source("environments/voice.py"))
        finalize = next(
            n
            for n in ast.walk(voice)
            if isinstance(n, ast.FunctionDef) and n.name == "finalize_run_status"
        )
        error_ns.update(
            RunStatus=NS(FAILED=NS(value="failed")),
            SimulationReport=NS(
                model_validate=lambda value: Model(
                    **{**value, "failure": Model(**value["failure"])}
                )
            ),
        )
        exec(
            compile(
                ast.Module(body=[future, finalize], type_ignores=[]),
                "pinned-finalize",
                "exec",
            ),
            error_ns,
        )
        report = error_ns["finalize_run_status"](
            None, Model(test_cases=[error_ns["outcome"]])
        )
        self.assertEqual("failed", report.status)
        self.assertEqual("vapi_call_start_failed", report.failure.code)
        self.assertIs(False, report.failure.retryable)

        # Actual child producer selection: report.failure.code, not message text.
        child = ast.parse(sdk_source("hosted/child_entrypoint.py"))
        detail_if = next(
            n
            for n in ast.walk(child)
            if isinstance(n, ast.If)
            and isinstance(n.test, ast.Name)
            and n.test.id == "completed"
        )
        detail_ns = {
            "completed": False,
            "run_completed": False,
            "report": report,
        }
        exec(
            compile(
                ast.Module(body=[detail_if], type_ignores=[]), "pinned-child", "exec"
            ),
            detail_ns,
        )
        self.assertEqual("vapi_call_start_failed", detail_ns["detail"])
        status = {"phase": "failed", "detail": detail_ns["detail"]}

        # Compare actual parent activity + workflow with exact v1.56.1 git
        # objects. Synthetic worker is empty for the historical parent, which
        # inherited its environment. Both consume identical real-producer data.
        outcomes = []
        workflow_source = (
            _ROOT / "temporal/workflows/simulation_runner_workflow.py"
        ).read_text()
        repo_root = _ROOT.parents[1]
        for revision in (None, "v1.56.1"):
            ns, spawn, request, worker, _, _, _, credential, _ = self.harness(config={})
            worker.clear()
            if revision:
                source = subprocess.check_output(
                    [
                        "git",
                        "-C",
                        str(repo_root),
                        "show",
                        revision
                        + ":futureagi/simulate/temporal/activities/hosted_runner.py",
                    ],
                    text=True,
                )
                old = ast.parse(source)
                names = {
                    "run_hosted_sdk_job",
                    "_child_environment",
                    "_resolve_secret",
                    "_resolve_voice_secret_env",
                    "_resolve_provider_credential",
                    "_secret_env_ref_keys",
                }
                old_nodes = [
                    n
                    for n in old.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name in names
                ]
                for node in old_nodes:
                    node.decorator_list = []
                exec(
                    compile(
                        ast.fix_missing_locations(
                            ast.Module(body=[future, *old_nodes], type_ignores=[])
                        ),
                        "v1561-activity",
                        "exec",
                    ),
                    ns,
                )
                flow = subprocess.check_output(
                    [
                        "git",
                        "-C",
                        str(repo_root),
                        "show",
                        revision
                        + ":futureagi/simulate/temporal/workflows/simulation_runner_workflow.py",
                    ],
                    text=True,
                )
            else:
                flow = workflow_source
            load(_ACTIVITY, {"_parse_status_line"}, ns)

            async def lines():
                yield json.dumps(status).encode()

            spawn.return_value.stdout = lines()
            spawn.return_value.wait.return_value = 1
            outcome = self.run_job(ns, request, self.vapi_job(credential, public=False))
            spawn.assert_awaited_once()
            self.assertEqual("vapi_call_start_failed", outcome.detail)
            self.assertEqual("failed", outcome.phase)
            self.assertEqual(1, outcome.return_code)
            ns["_child_semaphore"].release.assert_called_once()
            self.assertFalse(ns["shutil"].rmtree.called)
            built = NS(
                mode="voice_sip",
                run_seconds=30,
                job_id="job",
                run_id="run",
                job_json=request.job_json,
            )
            execute = AsyncMock(side_effect=[built, outcome, "failed"])
            flow_ns = {
                "workflow": NS(execute_activity=execute, logger=Mock()),
                "timedelta": timedelta,
                "CancelledError": type("Cancelled", (Exception,), {}),
                "HOSTED_RUNNER_CHAT_MODE": "chat",
                "HOSTED_RUNNER_CHAT_TIMEOUT_SECONDS": 300,
                "HOSTED_RUNNER_PARENT_SLACK_SECONDS": 60,
                "QUEUE_RUNNER": "simulation_runner",
                "DB_RETRY_POLICY": "db-retry",
                "NO_RETRY_POLICY": "no-retry",
            }
            for name in (
                "BuildRunnerJobInput",
                "BuildRunnerJobOutput",
                "FinalizeRunnerInput",
                "RunHostedJobInput",
                "RunHostedJobOutput",
                "SimulationRunnerOutput",
            ):
                flow_ns[name] = NS
            method = next(
                n
                for n in ast.walk(ast.parse(flow))
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "run"
            )
            method.decorator_list = []
            exec(
                compile(
                    ast.fix_missing_locations(
                        ast.Module(body=[future, method], type_ignores=[])
                    ),
                    "workflow",
                    "exec",
                ),
                flow_ns,
            )
            result = finish(
                flow_ns["run"](
                    None,
                    NS(
                        test_execution_id="execution",
                        run_test_id="run",
                        scenario_ids=[],
                        mode="voice_sip",
                        simulator_id=None,
                        call_execution_ids=[],
                    ),
                )
            )
            calls = execute.call_args_list
            self.assertEqual(
                ["build_runner_job", "run_hosted_sdk_job", "finalize_hosted_execution"],
                [c.args[0] for c in calls],
            )
            self.assertEqual("no-retry", calls[1].kwargs["retry_policy"])
            self.assertEqual("failed", calls[2].args[1].job_phase)
            self.assertEqual("db-retry", calls[2].kwargs["retry_policy"])
            outcomes.append(vars(result))
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual("FAILED", outcomes[0]["status"])
        self.assertEqual("vapi_call_start_failed", outcomes[0]["error"])


if __name__ == "__main__":
    unittest.main()
