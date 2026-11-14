"""Focused stdlib-only tests; these are NOT real product qualification."""
import hashlib
import importlib.util
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Also runnable with -I and no implicit project-directory import path.
SPEC = importlib.util.spec_from_file_location("real_automatic_under_test", Path(__file__).with_name("real_automatic.py"))
real = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(real)


def model(project="fagi-upd-e2e-real-unit"):
    volumes = {key: {"name": project + "_" + key} for key in ("app-data", "postgres-data", "clickhouse-data")}
    return {
        "services": {key: {"image": "fixture", "restart": "unless-stopped", "volumes": []}
                     for key in ("app", "postgres", "clickhouse")},
        "volumes": volumes,
        "networks": {"default": {"name": project + "_default"}},
    }


class HelperTests(unittest.TestCase):
    def test_migration_oracle_handles_exact_lists_and_envelopes(self):
        plan = [{'app_label':'tracer','name':'0118_fixture'}]
        envelope = {'measurement_basis':'normal-bootstrap-envelope-v1','pending_plan':plan}
        self.assertEqual(real.pending_migration_names(plan), {('tracer','0118_fixture')})
        self.assertEqual(real.pending_migration_names(envelope), {('tracer','0118_fixture')})
        self.assertEqual(real.pending_migration_names(dict(envelope,pending_plan=[])), set())
        for invalid in ({'measurement_basis':'unknown','pending_plan':plan}, plan*2,
                        {'measurement_basis':'normal-bootstrap-envelope-v1'}, ['not-an-identity']):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                real.pending_migration_names(invalid)

    def test_hash_is_order_independent(self):
        self.assertEqual(real.payload_hash({"b": [1, 2], "a": "λ"}), real.payload_hash({"a": "λ", "b": [1, 2]}))

    def test_hash_preserves_payload_difference(self):
        self.assertNotEqual(real.payload_hash({"x": 1}), real.payload_hash({"x": "1"}))

    def test_nan_is_not_canonical(self):
        with self.assertRaises(ValueError):
            real.canonical({"x": float("nan")})

    def test_home_bind_rejects_external_volume(self):
        with self.assertRaises(ValueError):
            real.home_path(Path("/Volumes/nik-ext-1/checkout"))

    def test_home_directory_itself_rejected(self):
        with self.assertRaises(ValueError):
            real.home_path(real.HOME)

    def test_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(real, "HOME", Path(root)):
                link = Path(root) / "outside"
                link.symlink_to("/tmp")
                with self.assertRaises(ValueError):
                    real.home_path(link / "config")

    def test_lane_resources_only(self):
        self.assertEqual(real.resource_name("fagi-upd-e2e-real-unit_1"), "fagi-upd-e2e-real-unit_1")
        for name in ("oss-night-1", "fagi-upd-e2e-sa-1", "fagi-upd-e2e-real-", "fagi-upd-e2e-real-../x"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                real.resource_name(name)

    def test_model_accepts_base(self):
        real.validate_model(model(), "fagi-upd-e2e-real-unit")

    def test_profiles_rejected(self):
        value = model()
        value["services"]["serving"] = {}
        with self.assertRaises(ValueError):
            real.validate_model(value, "fagi-upd-e2e-real-unit")

    def test_external_store_rejected(self):
        value = model()
        value["volumes"]["app-data"]["external"] = True
        with self.assertRaises(ValueError):
            real.validate_model(value, "fagi-upd-e2e-real-unit")

    def test_hidden_volume_bind_rejected(self):
        value = model()
        value["volumes"]["app-data"]["driver_opts"] = {"device": "/outside", "o": "bind", "type": "none"}
        with self.assertRaises(ValueError):
            real.validate_model(value, "fagi-upd-e2e-real-unit")

    def test_host_network_rejected(self):
        value = model()
        value["services"]["app"]["network_mode"] = "host"
        with self.assertRaises(ValueError):
            real.validate_model(value, "fagi-upd-e2e-real-unit")

    def test_wrong_store_name_rejected(self):
        value = model()
        value["volumes"]["app-data"]["name"] = "another-project_app-data"
        with self.assertRaises(ValueError):
            real.validate_model(value, "fagi-upd-e2e-real-unit")

    def test_always_restart_rejected(self):
        value = model()
        value["services"]["app"]["restart"] = "always"
        with self.assertRaises(ValueError):
            real.validate_model(value, "fagi-upd-e2e-real-unit")

    def test_writable_bind_rejected(self):
        value = model()
        value["services"]["app"]["volumes"] = [{"type": "bind", "source": str(real.HOME / "fixture"), "read_only": False}]
        with self.assertRaises(ValueError):
            real.validate_model(value, "fagi-upd-e2e-real-unit")

    def test_no_default_context(self):
        with self.assertRaises(ValueError):
            real.ProductHarness(real.HOME / "fixture", Path.cwd(), "fagi-upd-e2e-real-unit", context="default")

    def test_provider_environment_not_inherited(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "do-not-inherit", "COMPOSE_FILE": "/other", "DOCKER_HOST": "tcp://other"}):
            harness = real.ProductHarness(real.HOME / "fixture", Path.cwd(), "fagi-upd-e2e-real-unit")
        self.assertNotIn("OPENAI_API_KEY", harness.env)
        self.assertNotIn("COMPOSE_FILE", harness.env)
        self.assertNotIn("DOCKER_HOST", harness.env)
        self.assertEqual(harness.env["DOCKER_CONTEXT"], real.CONTEXT)

    def test_docker_always_explicit_context(self):
        harness = real.ProductHarness(real.HOME / "fixture", Path.cwd(), "fagi-upd-e2e-real-unit")
        with patch.object(harness, "command") as command:
            harness.docker("info")
        command.assert_called_once_with(["docker", "--context", "colima-fagi-update", "info"])

    def test_no_fake_harness_dependency(self):
        source = Path(real.__file__).read_text()
        self.assertNotIn("from .harness import", source)
        self.assertNotIn("Harness.start", source)
        self.assertNotIn("fakeapp", source)

    def test_seed_uses_supported_http_and_collector_operations(self):
        harness = real.ProductHarness(real.HOME / "fixture", Path.cwd(), "fagi-upd-e2e-real-unit")
        harness.ports = {"BACKEND_PORT": 18000, "FI_COLLECTOR_OTLP_HTTP_PORT": 14318}
        responses = [(200, {"result": {"access": "signup-token"}}), (200, {"access": "token"}),
                     (200, {"id": "user", "organization": {"id": "org"}, "default_workspace_id": "workspace"}),
                     (200, {"data": {"api_key": "api", "secret_key": "secret"}}), (200, {})]
        with patch.object(harness, "request", side_effect=responses) as request, patch.object(harness, "record"):
            witness = harness.seed_api_witnesses("unit-witness")
        self.assertEqual(witness["organization_id"], "org")
        call = request.call_args_list[-1]
        self.assertEqual(call.args, (14318, "/v1/traces"))
        wire = call.kwargs["value"]
        spans = wire["resourceSpans"][0]["scopeSpans"][0]["spans"]
        self.assertEqual(len(spans), 2)
        self.assertEqual(spans[1]["parentSpanId"], spans[0]["spanId"])
        self.assertEqual(witness["collector_payload_sha256"], real.payload_hash(wire))

    def test_failed_collector_is_not_a_witness(self):
        harness = real.ProductHarness(real.HOME / "fixture", Path.cwd(), "fagi-upd-e2e-real-unit")
        harness.ports = {"BACKEND_PORT": 18000, "FI_COLLECTOR_OTLP_HTTP_PORT": 14318}
        responses = [(200, {}), (200, {"access": "token"}),
                     (200, {"organization": {"id": "org"}, "default_workspace_id": "workspace"}),
                     (200, {"data": {"api_key": "api", "secret_key": "secret"}}), (503, {})]
        with patch.object(harness, "request", side_effect=responses), patch.object(harness, "record"), self.assertRaises(RuntimeError):
            harness.seed_api_witnesses("unit-witness")

    def test_cold_stop_orders_application_before_databases(self):
        harness = real.ProductHarness(real.HOME / "fixture", Path.cwd(), "fagi-upd-e2e-real-unit")
        def containers(running):
            return [{"Id": key + "-id", "Config": {"Labels": {"com.docker.compose.service": key}},
                     "State": {"Running": running, "ExitCode": 0, "OOMKilled": False}}
                    for key in ("app", "postgres", "clickhouse")]
        with patch.object(harness, "containers", side_effect=[containers(True), containers(False)]), \
                patch.object(harness, "docker") as docker, patch.object(harness, "record"):
            result = harness.stop_cold()
        self.assertTrue(result["clean"])
        self.assertEqual([call.args[-1] for call in docker.call_args_list], ["app-id", "postgres-id", "clickhouse-id"])
        self.assertTrue(all(call.args[:3] == ("stop", "--time", "60") for call in docker.call_args_list))

    def test_archive_does_not_create_helper_with_running_writers(self):
        harness = real.ProductHarness(real.HOME / "fixture", Path.cwd(), "fagi-upd-e2e-real-unit")
        with patch.object(harness, "containers", return_value=[{"State": {"Running": True}}] * 3), \
                patch.object(harness, "docker") as docker, self.assertRaises(ValueError):
            harness.seal_cold_volumes("unused")
        docker.assert_not_called()

    def test_health_after_deadline_is_not_accepted_or_retried(self):
        harness = real.ProductHarness(real.HOME / "fixture", Path.cwd(), "fagi-upd-e2e-real-unit")
        harness.ports = {"FRONTEND_PORT": 13000, "BACKEND_PORT": 18000}
        containers = [{"State": {"Health": {"Status": "healthy"}}}] * 3
        with patch.object(harness, "dc") as compose, patch.object(harness, "record"), \
                patch.object(harness, "request", side_effect=[(200, {"phase": "ready"}), (200, {})]), \
                patch.object(harness, "containers", return_value=containers), \
                patch.object(real.time, "monotonic", side_effect=[0, 0, 0, 0, 4201, 4201, 4201, 4201]), \
                patch.object(real.time, "sleep"), self.assertRaises(TimeoutError):
            harness.boot_source()
        compose.assert_called_once()

    def test_invalid_budget_prevents_compose(self):
        harness = real.ProductHarness(real.HOME / "fixture", Path.cwd(), "fagi-upd-e2e-real-unit")
        with patch.object(harness, "dc") as compose:
            for budget in (0, -1, 4201):
                with self.assertRaises(ValueError):
                    harness.boot_source(budget=budget)
        compose.assert_not_called()


class SourceEnvTests(unittest.TestCase):
    def test_source_env_starts_from_the_release_example_like_bin_install(self):
        import subprocess
        from deploy.update.envfile import example_keys, read_env
        with tempfile.TemporaryDirectory(dir=real.HOME) as temporary:
            harness = real.ProductHarness(Path(temporary) / "checkout", Path.cwd(), "fagi-upd-e2e-real-unit")
            harness.root.mkdir(parents=True)
            (harness.root / "docker-compose.yml").write_text("services: {}\n")
            (harness.root / ".env.example").write_text(
                "# Generated by bin/install\nSECRET_KEY=\nOPENAI_API_KEY=\nAGENTCC_WEBHOOK_SECRET=\n"
                "MINIO_ROOT_PASSWORD=CHANGEME-set-by-bin-install\nHARNESS_CALLER_BARGE_IN_RATE=0.4\n"
                "FUTURE_AGI_VERSION=\n")
            done = subprocess.CompletedProcess([], 0, b"", b"")
            with patch.object(harness, "docker", return_value=done), patch.object(harness, "install_updater"), \
                    patch.object(harness, "dc", return_value=subprocess.CompletedProcess([], 0, '{"services": {}}', "")):
                # .env is written before the (stubbed, empty) Compose model is validated.
                with self.assertRaisesRegex(ValueError, "three-service"):
                    harness.prepare("sha256:" + "a" * 64, version="v1.53.0")
            env = read_env(harness.root / ".env")
            self.assertEqual([k for k in example_keys(harness.root / ".env.example") if k not in env], [])
            self.assertEqual((env["FUTURE_AGI_VERSION"], env["OPENAI_API_KEY"], env["HARNESS_CALLER_BARGE_IN_RATE"]),
                             ("v1.53.0", "", "0.4"))
            for key in ("SECRET_KEY", "AGENTCC_WEBHOOK_SECRET", "MINIO_ROOT_PASSWORD", "PG_PASSWORD"):
                self.assertRegex(env[key], r"^[0-9a-f]{64}$")
            self.assertEqual(env["COMPOSE_PROJECT_NAME"], "fagi-upd-e2e-real-unit")


class NativeLauncherTests(unittest.TestCase):
    def test_native_construction_does_not_require_host_tools(self):
        with patch.object(real, "native_ci", return_value=True), \
                patch.object(real.shutil, "which", return_value=None) as which:
            real.ProductHarness(real.HOME / "fixture", Path.cwd(), "fagi-upd-e2e-real-unit")
        which.assert_not_called()

    def test_real_native_setup_still_refuses_missing_host_tools(self):
        harness = real.ProductHarness(real.HOME / "fixture", Path.cwd(), "fagi-upd-e2e-real-unit")
        with patch.object(real, "native_ci", return_value=True), \
                patch.object(real.shutil, "which", return_value=None) as which, \
                patch.object(harness, "command") as command, \
                self.assertRaisesRegex(ValueError, "native acceptance host tool missing: docker-compose"):
            harness.bin_update('setup', '--yes')
        self.assertEqual(which.call_args.args, ("docker-compose",))
        command.assert_not_called()

    def test_native_setup_uses_validated_path_before_command(self):
        harness = real.ProductHarness(real.HOME / "fixture", Path.cwd(), "fagi-upd-e2e-real-unit")
        harness.env['PYTHON'] = 'original-interpreter'
        with patch.object(real, "native_ci", return_value=True), \
                patch.object(real, "native_launcher_path", return_value=os.defpath) as launcher, \
                patch.object(harness, "command", side_effect=lambda *args, **kwargs: real.subprocess.CompletedProcess(
                    args, 0, json.dumps(dict(harness.env)).encode())):
            observed = json.loads(harness.bin_update('setup', '--yes').stdout)
        launcher.assert_called_once()
        self.assertEqual(observed['PATH'], os.defpath)
        self.assertEqual(observed['PYTHON'], real.sys.executable)
        self.assertEqual(observed['DOCKER_CONTEXT'], real.CONTEXT)
        self.assertEqual(harness.env['PYTHON'], 'original-interpreter')

    def test_setup_command_bound_is_supplied_by_the_caller(self):
        harness = real.ProductHarness(real.HOME / "fixture", Path.cwd(), "fagi-upd-e2e-real-unit")
        with patch.object(real, "native_ci", return_value=False), patch.object(harness, "command") as command:
            command.return_value.returncode = 0
            harness.bin_update('setup', '--yes', timeout=1680)
            harness.bin_update('setup', '--deactivate', '--yes')
        self.assertEqual([call.kwargs['timeout'] for call in command.call_args_list], [1680, 600])

    def test_setup_refusal_keeps_only_its_closed_reason_code(self):
        harness = real.ProductHarness(real.HOME / "fixture", Path.cwd(), "fagi-upd-e2e-real-unit")
        harness.sequence = 60
        private = b'Check host Python 3.9+ PRIVATE-TOKEN /home/runner/secret\n'
        cases = ((b'Setup incomplete: restore_capacity_unknown. Unattended work was not activated.\n' + private,
                  real.SetupIncomplete, 'command-0060 failed: setup incomplete: restore_capacity_unknown'),
                 (b'Setup incomplete: PRIVATE-TOKEN. x\n' + private, RuntimeError,
                  'command-0060 failed (unavailable); see private command logs'),
                 (private, RuntimeError, 'command-0060 failed (unavailable); see private command logs'))
        for stdout, kind, message in cases:
            with self.subTest(message=message), patch.object(real, "native_ci", return_value=False), \
                    patch.object(harness, "command", return_value=real.subprocess.CompletedProcess([], 3, stdout)):
                with self.assertRaises(RuntimeError) as raised:
                    harness.bin_update('setup', '--yes')
                self.assertIs(type(raised.exception), kind)
                self.assertEqual(str(raised.exception), message)
        with patch.object(real, "native_ci", return_value=False), \
                patch.object(harness, "command", return_value=real.subprocess.CompletedProcess([], 3, private)):
            self.assertEqual(harness.bin_update('setup', '--yes', check=False).returncode, 3)
        with tempfile.TemporaryDirectory() as directory:
            harness.evidence = Path(directory)
            (harness.evidence / 'command-0060.stderr').write_bytes(b'/home/runner/secret\nbin/update: ownership_unconfirmed\n')
            with patch.object(real, "native_ci", return_value=False), \
                    patch.object(harness, "command", return_value=real.subprocess.CompletedProcess([], 2, private)):
                with self.assertRaises(RuntimeError) as raised:
                    harness.bin_update('setup', '--deactivate', '--yes')
            self.assertEqual(str(raised.exception), 'command-0060 failed (ownership_unconfirmed); see private command logs')


def setup_output(reason='scheduler_busy', scheduler='incomplete', policy='disabled'):
    return ('Setup incomplete; scheduler=' + scheduler + '; policy=' + policy + '.\nReason: ' + reason + '\n').encode()


class SetupFailureTests(unittest.TestCase):
    def test_both_canonical_forms_and_closed_vocabulary(self):
        for reason in ('scheduler_busy', 'automatic_recovery_pending', 'scheduler_foreign_state',
                       'configuration_changed', 'setup_prerequisite_unavailable', 'control_changed', 'control_paused'):
            for output in (setup_output(reason), ('Setup incomplete: ' + reason +
                    '. Unattended work was not activated by this action.\n').encode()):
                with self.subTest(reason=reason, output=output):
                    error = real.setup_failure('command-0092', output, 3)
                    self.assertIsInstance(error, real.SetupIncomplete)
                    self.assertEqual(error.reason, reason)
                    details = real.setup_failure_metadata(error)
                    self.assertEqual((details['reason'], details['returncode'], details['setup_state']),
                                     (reason, 3, 'incomplete'))
                    expected = ('incomplete', 'disabled') if b'; scheduler=' in output else ('unknown', 'unknown')
                    self.assertEqual((details['scheduler_state'], details['policy_state']), expected)

    def test_ambiguous_malformed_or_unknown_output_stays_unknown_without_secrets(self):
        exception = b'Setup incomplete: scheduler_busy. Unattended work was not activated by this action.\n'
        outputs = [b'Reason: scheduler_busy\n', setup_output('private_token'), setup_output('PRIVATE-TOKEN'),
                   setup_output() + b'Reason: scheduler_foreign_state\n', setup_output() + b'Reason: scheduler_busy\n',
                   setup_output() + b'Reason:private_token\n', setup_output() * 2, exception * 2,
                   exception + b'Reason: scheduler_busy\n', setup_output(scheduler='private_token'),
                   setup_output(policy='private_token'), setup_output().replace(b'incomplete;', b'active;'),
                   setup_output().replace(b'\nReason:', b'\n Reason:'), setup_output().split(b'Reason:')[0],
                   exception + setup_output(), b'private_token\n', b'X' * 65537 + setup_output()]
        for output in outputs:
            with self.subTest(output=output[:100]):
                error = real.setup_failure('command-0092', output, 3)
                self.assertIs(type(error), RuntimeError)
                self.assertEqual(real.setup_failure_metadata(error), dict(returncode=3, setup_state='unknown',
                    scheduler_state='unknown', policy_state='unknown', reason='unknown'))
                self.assertNotIn('private_token', str(error) + json.dumps(error.setup_metadata))
        self.assertIsNone(real.setup_failure_metadata(real.setup_failure('command', b'', 10**100))['returncode'])

    def test_actual_setup_output_contract_without_host_actions(self):
        import contextlib
        from types import SimpleNamespace
        from unittest.mock import Mock
        from deploy.update import __main__ as cli, enrollment
        from deploy.update.contracts import ReasonCode
        from deploy.update.runner_state import StoreError
        store = Mock()
        store.read_registration.side_effect = StoreError()
        cases = [(enrollment.SetupResult('incomplete', 'incomplete', 'disabled', reason, action='PRIVATE-TOKEN'), reason)
                 for reason in ('scheduler_busy', 'scheduler_foreign_state', 'scheduler_manager_unavailable')]
        cases += [(enrollment.SetupResult('incomplete', 'retained', 'disabled', 'automatic_recovery_pending'),
                   'automatic_recovery_pending'), (StoreError(ReasonCode.CONFIGURATION_CHANGED), 'configuration_changed'),
                  (OSError('PRIVATE-TOKEN'), 'setup_prerequisite_unavailable')]
        for outcome, reason in cases:
            out = io.StringIO()
            kwargs = {'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome}
            with self.subTest(reason=reason), patch.object(enrollment, 'setup_automation', **kwargs), \
                    contextlib.redirect_stdout(out):
                code = cli._setup(SimpleNamespace(yes=True, deactivate=True), store)
            self.assertEqual(code, 3)
            failure = real.setup_failure('command', out.getvalue().encode(), code)
            self.assertIsInstance(failure, real.SetupIncomplete)
            self.assertEqual(failure.reason, reason)
            self.assertNotIn('PRIVATE-TOKEN', str(failure) + json.dumps(failure.setup_metadata))

    def enrollment_output(self, mode, outcome):
        import contextlib
        from types import SimpleNamespace
        from deploy.update import __main__ as cli, enrollment
        from deploy.update.runner_state import RunnerStateStore
        # The real CLI validates every ancestor; default /tmp is world-writable.
        with tempfile.TemporaryDirectory(dir=Path.home()) as directory:
            root = Path(directory).resolve()
            (root / '.env').write_text('')
            store = RunnerStateStore(root, root / '.env')
            options = SimpleNamespace(yes=True, mode=mode, docker_context='private-token-context')
            if mode == 'apply':
                options.backup_destination = str(root / 'PRIVATE-TOKEN-backups')
            out = io.StringIO()
            kwargs = {'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome}
            # Only the host action is stubbed: _setup, setup_summary and configuration
            # retrieval/rendering are real, with an unregistered private checkout.
            with patch.object(enrollment, 'setup_automation', **kwargs) as setup, contextlib.redirect_stdout(out):
                code = cli._setup(options, store)
            setup.assert_called_once_with(store, options)
            self.assertEqual(code, 3)
            output = out.getvalue().encode()
            self.assertIn(b'One-time host setup: mode=' + mode.encode(), output)
            self.assertIn(b'private-token-context', output)
            return output, code

    def assert_enrollment_reason(self, output, code, reason):
        failure = real.setup_failure('command', output, code)
        self.assertIsInstance(failure, real.SetupIncomplete)
        self.assertEqual(failure.reason, reason)
        self.assertEqual(failure.setup_metadata['returncode'], 3)
        public = str(failure) + json.dumps(real.setup_failure_metadata(failure))
        for private in ('PRIVATE-TOKEN', 'private-token-context', 'Private backups', 'Docker target'):
            self.assertNotIn(private, public)

    def test_actual_enrollment_result_summary_preserves_closed_reason(self):
        from deploy.update.enrollment import SetupResult
        for mode in ('apply', 'notify'):
            for reason in ('scheduler_busy', 'scheduler_foreign_state', 'scheduler_manager_unavailable'):
                with self.subTest(mode=mode, reason=reason):
                    output, code = self.enrollment_output(mode, SetupResult(
                        'incomplete', 'incomplete', 'disabled', reason, action='PRIVATE-TOKEN /private/action'))
                    self.assertIn(b'PRIVATE-TOKEN /private/action', output)
                    self.assert_enrollment_reason(output, code, reason)

    def test_actual_enrollment_exception_summary_preserves_closed_reason(self):
        from deploy.update.contracts import ReasonCode
        from deploy.update.runner_state import StoreError
        for mode in ('apply', 'notify'):
            for outcome, reason in ((StoreError(ReasonCode.CONFIGURATION_CHANGED), 'configuration_changed'),
                                    (OSError('PRIVATE-TOKEN'), 'setup_prerequisite_unavailable')):
                with self.subTest(mode=mode, reason=reason):
                    output, code = self.enrollment_output(mode, outcome)
                    self.assert_enrollment_reason(output, code, reason)

    def test_actual_enrollment_summary_rejects_changed_explanation_and_ambiguous_records(self):
        from deploy.update.enrollment import SetupResult
        for mode in ('apply', 'notify'):
            output, code = self.enrollment_output(mode, SetupResult('incomplete', 'incomplete', 'disabled', 'scheduler_busy'))
            header = b'Setup incomplete; scheduler=incomplete; policy=disabled.\n'
            mutations = [output.replace(b'Setup installs no packages,', b'Setup installs PRIVATE-TOKEN,'),
                         output.replace(b'Missing prerequisites leave setup incomplete.', b'Missing prerequisites leave setup incomplete. PRIVATE-TOKEN'),
                         output.replace(b'Setup installs', b' Setup installs'),
                         output + b'Setup PRIVATE-TOKEN\n', output + header,
                         output + b'Setup incomplete: scheduler_foreign_state. Refused.\n',
                         output + b'Reason: scheduler_busy\n', output + b'Reason: scheduler_foreign_state\n',
                         output.replace(b'Reason: scheduler_busy', b'Reason: PRIVATE-TOKEN'),
                         output.replace(b'Reason: scheduler_busy', b'Reason:scheduler_busy'),
                         output + b'Reason PRIVATE-TOKEN\n']
            for index, changed in enumerate(mutations):
                with self.subTest(mode=mode, mutation=index):
                    failure = real.setup_failure('command', changed, code)
                    self.assertIs(type(failure), RuntimeError)
                    self.assertEqual(real.setup_failure_metadata(failure), dict(returncode=3, setup_state='unknown',
                        scheduler_state='unknown', policy_state='unknown', reason='unknown'))
                    public = str(failure) + json.dumps(failure.setup_metadata)
                    self.assertNotIn('PRIVATE-TOKEN', public)
                    self.assertNotIn('private-token-context', public)

    def test_actual_reporting_init_refusal_keeps_unique_closed_reason(self):
        import contextlib
        from types import SimpleNamespace
        from deploy.update import __main__ as cli
        # Exercise the real refusal, not an earlier unsafe-checkout rejection.
        with tempfile.TemporaryDirectory(dir=Path.home()) as directory:
            root = Path(directory).resolve()
            (root / '.env').write_text('')
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = cli.main(['--root', str(root), 'reporting', 'init', '--directory', str(root / 'PRIVATE-TOKEN')])
            self.assertEqual(code, 2)
            self.assertEqual(stdout.getvalue(), '')
            self.assertFalse((root / 'PRIVATE-TOKEN').exists())
            output = stderr.getvalue().encode()
            self.assertEqual(len(output.splitlines()), 2)
            self.assertTrue(output.endswith(b'bin/update: scheduler_unregistered\n'))
            checkout = SimpleNamespace(evidence=root, sequence=92)
            leaf = root / 'command-0092.stderr'
            leaf.write_bytes(output)
            self.assertEqual(real.command_refusal(checkout, SimpleNamespace(returncode=code)), 'scheduler_unregistered')
            mutations = [output.replace(b'reporting init incomplete;', b'reporting init PRIVATE-TOKEN;'),
                         output.replace(b'do not remove foreign content or rebind\n', b'do not remove foreign content or rebind PRIVATE-TOKEN\n'),
                         output + b'bin/update: PRIVATE-TOKEN\n',
                         output + b'bin/update: scheduler_unregistered\n',
                         output + b'bin/update: scheduler_busy\n',
                         output.replace(b'bin/update: scheduler_unregistered\n', b'bin/update: private_token\n'),
                         output.replace(b'bin/update: scheduler_unregistered\n', b'bin/update:scheduler_unregistered\n')]
            for index, changed in enumerate(mutations):
                with self.subTest(mutation=index):
                    leaf.write_bytes(changed)
                    self.assertEqual(real.command_refusal(checkout, SimpleNamespace(returncode=code)), 'unclassified')
            # Even a closed busy token alongside the real guidance is stderr-only,
            # not the canonical stdout setup refusal needed for a teardown retry.
            leaf.write_bytes(output.replace(b'scheduler_unregistered', b'scheduler_busy'))
            harness = real.ProductHarness(real.HOME / 'fixture', Path.cwd(), 'fagi-upd-e2e-real-unit')
            harness.evidence, harness.sequence = root, 92
            with patch.object(real, 'native_ci', return_value=False), patch.object(harness, 'command',
                    return_value=real.subprocess.CompletedProcess([], 3, b'PRIVATE-TOKEN')) as command, \
                    patch.object(real, 'deactivation_retry_state') as guard, \
                    patch.object(real.time, 'sleep') as sleep:
                with self.assertRaises(RuntimeError) as raised:
                    real.deactivate_acceptance(harness, deadline=10000, clock=lambda: 0, sleep=sleep)
            self.assertIs(type(raised.exception), RuntimeError)
            self.assertEqual(raised.exception.setup_metadata['reason'], 'unknown')
            self.assertNotIn('PRIVATE-TOKEN', str(raised.exception) + json.dumps(raised.exception.deactivation_metadata))
            command.assert_called_once()
            guard.assert_not_called()
            sleep.assert_not_called()

    def test_readonly_diagnostics_preserve_closed_setup_metadata_not_output(self):
        from unittest.mock import Mock
        harness = real.ProductHarness(real.HOME / 'fixture', Path.cwd(), 'fagi-upd-e2e-real-unit')
        for output, reason in ((setup_output(), 'scheduler_busy'), (b'Reason: private_token\n', 'unknown')):
            failure = real.setup_failure('command-0092', output, 3)
            runner = Mock(side_effect=OSError('PRIVATE-TOKEN'))
            with patch('deploy.update.processes.ProcessSupervisor', return_value=runner), patch.object(harness, 'record'):
                result = harness.collect_diagnostics(failure)
            self.assertEqual(result['setup']['reason'], reason)
            self.assertEqual(result['setup']['returncode'], 3)
            self.assertNotIn('PRIVATE-TOKEN', json.dumps(result))
            self.assertNotIn('private_token', json.dumps(result))

    def test_stderr_reason_is_closed_unique_and_never_authorizes_retry(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            harness = real.ProductHarness(real.HOME / 'fixture', Path.cwd(), 'fagi-upd-e2e-real-unit')
            harness.evidence, harness.sequence = Path(directory), 92
            leaf = harness.evidence / 'command-0092.stderr'
            for output in (b'bin/update: private_token\n', b'bin/update: scheduler_busy\nbin/update: scheduler_busy\n'):
                leaf.write_bytes(output)
                self.assertEqual(real.command_refusal(harness, SimpleNamespace(returncode=3)), 'unclassified')
            leaf.write_bytes(b'bin/update: scheduler_busy\n')
            with patch.object(real, 'native_ci', return_value=False), patch.object(harness, 'command',
                    return_value=real.subprocess.CompletedProcess([], 3, b'private_token')):
                with self.assertRaises(RuntimeError) as raised:
                    harness.bin_update('setup', '--deactivate', '--yes')
            self.assertIs(type(raised.exception), RuntimeError)
            self.assertEqual(raised.exception.setup_metadata['reason'], 'unknown')


class DeactivationTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import Mock
        from deploy.tests.test_update_runner_state import registration, NOW
        from deploy.update.runner_state import RunnerStateStore
        temporary = tempfile.TemporaryDirectory(dir=real.HOME)
        self.addCleanup(temporary.cleanup)
        self.h = real.ProductHarness(Path(temporary.name), Path.cwd(), 'fagi-upd-e2e-real-unit')
        self.h.root.mkdir(mode=0o700)
        (self.h.root / '.env').write_text('')
        self.store = RunnerStateStore(self.h.root, self.h.root / '.env')
        self.store.register(registration(self.store))
        self.store.change_control('disable', NOW)
        self.now = 0
        self.sleeps, self.timeouts = [], []
        self.h.cleanup, self.h.collect_logs, self.h.collect_diagnostics, self.h.record = [Mock() for _ in range(4)]
        self.addCleanup(patch.stopall)
        patch.object(real, 'native_ci', return_value=False).start()

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def commands(self, outcomes, durations=None):
        from unittest.mock import Mock
        outcomes = iter(outcomes)
        durations = iter(durations or [])
        def command(args, **kwargs):
            self.assertEqual(args[1:], ['setup', '--deactivate', '--yes'])
            self.timeouts.append(kwargs['timeout'])
            self.now += next(durations, 0)
            value = next(outcomes)
            if isinstance(value, Exception):
                raise value
            return real.subprocess.CompletedProcess(args, 0 if value is None else 3, value or b'')
        self.h.command = Mock(side_effect=command)

    def finish(self, failure=None, deadline=10000):
        from deploy.tests.update_e2e.admission_diagnostics import finish_acceptance
        return finish_acceptance(self.h, failure=failure, deactivate=lambda: real.deactivate_acceptance(
            self.h, deadline=deadline, clock=lambda: self.now, sleep=self.sleep))

    def evidence(self):
        name, result = self.h.record.call_args.args
        self.assertEqual(name, 'teardown-diagnostics.json')
        return result

    def test_explicit_busy_disabled_idle_then_success(self):
        self.commands([setup_output(), None], [10, 5])
        result = self.finish()
        self.assertEqual(self.timeouts, [600, 588])
        self.assertEqual(self.sleeps, [2])
        self.h.cleanup.assert_called_once()
        details = result['deactivation_details']
        self.assertEqual((details['calls'], details['busy_count'], details['state'], details['elapsed_seconds']),
                         (2, 1, 'succeeded', 17))
        self.assertEqual((details['first_reason'], details['last_reason']), ('scheduler_busy', 'scheduler_busy'))
        self.assertEqual(details['last_setup']['policy_state'], 'disabled')
        self.assertEqual(details['guard']['state'], 'disabled_idle')

    def test_foreign_pending_manager_drift_unknown_and_timeout_never_retry_or_cleanup(self):
        errors = [setup_output(reason) for reason in ('automatic_recovery_pending', 'scheduler_foreign_state',
            'scheduler_configuration_changed', 'scheduler_manager_unavailable', 'scheduler_command_failed',
            'ownership_unconfirmed', 'private_token')]
        errors += [setup_output() + b'Reason: scheduler_foreign_state\n',
                   real.subprocess.TimeoutExpired(['private_command'], 600, output=b'PRIVATE-TOKEN'),
                   RuntimeError('PRIVATE-TOKEN')]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.commands([error])
                with self.assertRaises(Exception):
                    self.finish()
                self.h.command.assert_called_once()
                self.h.cleanup.assert_not_called()
                self.assertEqual(self.sleeps, [])
                self.assertEqual(self.evidence()['deactivation_details']['state'], 'failed')
                self.assertNotIn('PRIVATE-TOKEN', json.dumps(self.evidence()))
                self.assertNotIn('private_token', json.dumps(self.evidence()))

    def test_busy_then_terminal_error_never_retries_that_error_or_masks_original(self):
        for outcome, reason in ((setup_output('automatic_recovery_pending'), 'automatic_recovery_pending'),
                                (setup_output('scheduler_foreign_state'), 'scheduler_foreign_state'),
                                (real.subprocess.TimeoutExpired(['private'], 100, output=b'PRIVATE-TOKEN'), 'unknown')):
            with self.subTest(reason=reason):
                self.now, self.sleeps, self.timeouts = 0, [], []
                self.commands([setup_output(), outcome, None])
                original = AssertionError('original')
                with self.assertRaises(AssertionError) as raised:
                    try:
                        raise original
                    finally:
                        self.finish(failure=original)
                self.assertIs(raised.exception, original)
                self.assertEqual(self.h.command.call_count, 2)
                self.assertEqual(self.sleeps, [2])
                details = self.evidence()['deactivation_details']
                self.assertEqual((details['first_reason'], details['last_reason']), ('scheduler_busy', reason))
                self.assertEqual(details['busy_count'], 1)
                self.assertNotIn('PRIVATE-TOKEN', json.dumps(self.evidence()))
                self.h.cleanup.assert_not_called()

    def test_busy_without_setup_exit_code_three_does_not_retry(self):
        from unittest.mock import Mock
        self.h.command = Mock(return_value=real.subprocess.CompletedProcess([], 2, setup_output()))
        with self.assertRaises(real.SetupIncomplete):
            self.finish()
        self.h.command.assert_called_once()
        self.assertEqual(self.sleeps, [])
        self.h.cleanup.assert_not_called()

    def test_guard_refuses_enabled_missing_malformed_and_pending_evidence(self):
        from dataclasses import replace
        from deploy.tests.test_update_runner_state import NOW
        from deploy.update.receipt import StateDir
        journal = StateDir(self.h.root).journal_path
        control_path, state_path = self.store.path / 'control.json', self.store.path / 'state.json'
        control_raw, state_raw = control_path.read_bytes(), state_path.read_bytes()
        def active():
            state = self.store.read_state()
            self.store.save_state(replace(state, active_attempt_id='private-attempt', active_operation_token='private-token'), expected_generation=state.generation)
        mutations = [lambda: self.store.change_control('enable', NOW), control_path.unlink,
                     lambda: control_path.write_text('{}'), state_path.unlink, lambda: state_path.write_text('{}'),
                     active, lambda: journal.write_text('{}'), lambda: journal.write_text('malformed'),
                     lambda: journal.symlink_to(self.h.root / 'missing'), lambda: os.mkfifo(journal)]
        for mutate in mutations:
            with self.subTest(mutation=mutations.index(mutate)):
                mutate()
                self.commands([setup_output()])
                with self.assertRaises(real.SetupIncomplete):
                    self.finish()
                self.h.command.assert_called_once()
                self.assertEqual(self.sleeps, [])
                self.h.cleanup.assert_not_called()
                self.assertEqual(self.evidence()['deactivation_details']['state'], 'guard_refused')
                control_path.write_bytes(control_raw); state_path.write_bytes(state_raw)
                control_path.chmod(0o600); state_path.chmod(0o600)
                if os.path.lexists(journal):
                    journal.unlink()

    def test_guard_does_not_probe_or_bypass_runner_lock_and_requires_explicit_disable(self):
        from dataclasses import replace
        control = self.store.read_control()
        with self.store.runner_lock():
            self.assertEqual(real.deactivation_retry_state(self.h.root)['state'], 'disabled_idle')
        with patch('deploy.update.host_facts.selected_store', return_value=self.store), \
                patch.object(self.store, 'read_control', return_value=replace(control, enabled_override=None)):
            self.assertEqual(real.deactivation_retry_state(self.h.root)['state'], 'not_disabled')

    def test_missing_directories_are_not_created_or_treated_as_idle(self):
        before = sorted(str(p) for p in self.h.root.rglob('*'))
        sample = real.deactivation_retry_state(self.h.root / 'missing')
        self.assertEqual(sample['state'], 'unavailable')
        self.assertEqual(sorted(str(p) for p in self.h.root.rglob('*')), before)

    def test_changed_evidence_and_new_pending_after_sleep_stop_before_reinvoke(self):
        from dataclasses import replace
        with patch('deploy.update.host_facts.selected_store', return_value=self.store):
            state = self.store.read_state()
            with patch.object(self.store, 'read_state', side_effect=[state, replace(state, generation=state.generation+1)]):
                self.assertEqual(real.deactivation_retry_state(self.h.root)['state'], 'changed')
        self.commands([setup_output(), None])
        def sleep(seconds):
            self.sleep(seconds)
            state = self.store.read_state()
            self.store.save_state(replace(state, active_attempt_id='new-attempt', active_operation_token='private-token'), expected_generation=state.generation)
        with self.assertRaises(real.SetupIncomplete):
            real.deactivate_acceptance(self.h, deadline=10000, clock=lambda: self.now, sleep=sleep)
        self.h.command.assert_called_once()
        self.assertEqual(self.sleeps, [2])

    def test_total_budget_not_reset_and_exhaustion_retains_stack(self):
        self.commands([setup_output()] * 3, [200, 200, 196])
        with self.assertRaises(real.SetupIncomplete) as raised:
            self.finish()
        self.assertEqual(self.timeouts, [600, 398, 196])
        self.assertEqual(self.sleeps, [2, 2])
        details = self.evidence()['deactivation_details']
        self.assertEqual((details['busy_count'], details['elapsed_seconds'], details['state']), (3, 600, 'budget_exhausted'))
        self.assertEqual(raised.exception.reason, 'scheduler_busy')
        self.h.cleanup.assert_not_called()

    def test_job_deadline_caps_calls_and_final_sleep(self):
        self.commands([setup_output()] * 2, [5, 2])
        with self.assertRaises(real.SetupIncomplete):
            self.finish(deadline=10)
        self.assertEqual(self.timeouts, [10, 3])
        self.assertEqual(self.sleeps, [2, 1])
        self.assertEqual(self.evidence()['deactivation_details']['elapsed_seconds'], 10)
        self.h.cleanup.assert_not_called()

    def test_expired_budget_does_not_start_cli_or_cleanup(self):
        self.commands([None])
        with self.assertRaises(TimeoutError):
            self.finish(deadline=0)
        self.h.command.assert_not_called()
        self.h.cleanup.assert_not_called()

    def test_late_success_is_not_permission_for_partial_cleanup(self):
        self.commands([None], [601])
        with self.assertRaises(TimeoutError):
            self.finish()
        self.assertEqual(self.evidence()['deactivation_details']['state'], 'budget_exhausted')
        self.h.cleanup.assert_not_called()

    def test_bounded_observations_and_original_failure_survives_exhaustion(self):
        self.commands([setup_output()] * 300)
        original = AssertionError('PRIVATE-TOKEN original')
        with self.assertRaises(AssertionError) as raised:
            try:
                raise original
            finally:
                self.finish(failure=original)
        self.assertIs(raised.exception, original)
        self.h.cleanup.assert_not_called()
        evidence = self.evidence()
        self.assertEqual(evidence['deactivation_details']['busy_count'], 300)
        self.assertLess(len(json.dumps(evidence)), 2000)
        self.assertNotIn('PRIVATE-TOKEN', json.dumps(evidence))
        self.assertEqual(evidence['errors'][0]['setup']['reason'], 'scheduler_busy')

    def test_original_setup_failure_and_deactivation_metadata_both_survive(self):
        original = real.setup_failure('command-0001', setup_output('configuration_changed'), 3)
        self.commands([setup_output('scheduler_foreign_state')])
        result = self.finish(failure=original)
        self.assertEqual(result['original_setup']['reason'], 'configuration_changed')
        self.assertEqual(result['errors'][0]['setup']['reason'], 'scheduler_foreign_state')
        self.assertEqual(result['deactivation_details']['last_setup']['returncode'], 3)
        self.h.cleanup.assert_not_called()


class ArchiveTests(unittest.TestCase):
    def make_archive(self, root, names=(".", "./payload")):
        path = Path(root) / "fixture.tar"
        with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as archive:
            for name in names:
                member = tarfile.TarInfo(name)
                member.uid, member.gid, member.mode, member.mtime = 123, 456, 0o640, 1710000000
                if name == ".":
                    member.type = tarfile.DIRTYPE
                    archive.addfile(member)
                else:
                    member.size = len(b"real-helper-unit-fixture")
                    archive.addfile(member, io.BytesIO(b"real-helper-unit-fixture"))
        return path

    def test_inventory_retains_payload_metadata_and_hashes(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.make_archive(root)
            inventory = Path(root) / "members.jsonl"
            result = real.inspect_archive(path, inventory)
            rows = [json.loads(line) for line in inventory.read_text().splitlines()]
            self.assertEqual(result["members"], 2)
            self.assertEqual(result["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(rows[1]["sha256"], hashlib.sha256(b"real-helper-unit-fixture").hexdigest())
            self.assertEqual((rows[1]["uid"], rows[1]["gid"], rows[1]["mode"], rows[1]["mtime"]), (123, 456, 0o640, 1710000000))

    def test_no_extraction(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.make_archive(root)
            real.inspect_archive(path, Path(root) / "members.jsonl")
            self.assertFalse((Path(root) / "payload").exists())

    def test_traversal_rejected(self):
        for name in ("../escape", "/escape", "./dir/../../escape"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as root:
                path = self.make_archive(root, (name,))
                with self.assertRaises(ValueError):
                    real.inspect_archive(path, Path(root) / "members.jsonl")

    def test_duplicate_members_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.make_archive(root, ("./a", "./a"))
            with self.assertRaises(ValueError):
                real.inspect_archive(path, Path(root) / "members.jsonl")

    def test_canonical_duplicate_members_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.make_archive(root, ("./a", "a"))
            with self.assertRaises(ValueError):
                real.inspect_archive(path, Path(root) / "members.jsonl")

    def test_member_budget_is_enforced(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.make_archive(root)
            with self.assertRaises(ValueError):
                real.inspect_archive(path, Path(root) / "members.jsonl", max_members=1)

    def test_logical_size_budget_is_enforced(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.make_archive(root)
            with self.assertRaises(ValueError):
                real.inspect_archive(path, Path(root) / "members.jsonl", max_logical_bytes=1)

    def test_archive_deadline_is_enforced(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.make_archive(root)
            with self.assertRaises(ValueError):
                real.inspect_archive(path, Path(root) / "members.jsonl", timeout=0)

    def test_missing_end_marker_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.make_archive(root)
            path.write_bytes(path.read_bytes()[:1536])
            with self.assertRaises((ValueError, tarfile.ReadError)):
                real.inspect_archive(path, Path(root) / "members.jsonl")


if __name__ == "__main__":
    unittest.main()


class DiagnosticsTests(unittest.TestCase):
    def test_data_usage_keeps_only_product_entry_names_and_kib(self):
        from types import SimpleNamespace
        output = (b'161000\t/data/temporal\n12\t/data/objects\n7\t/data/.bootstrap-ok\n'
                  b'9\t/data/../etc\n5\t/elsewhere/x\nnot a row\n')
        calls = []
        checkout = SimpleNamespace(
            containers=lambda: [{'Id': 'app-id', 'Config': {'Labels': {'com.docker.compose.service': 'app'}}},
                                {'Id': 'db-id', 'Config': {'Labels': {'com.docker.compose.service': 'postgres'}}}],
            docker=lambda *args, **kwargs: calls.append(args) or SimpleNamespace(stdout=output))
        usage = real.data_usage(checkout)
        self.assertEqual(usage['kib'], {'temporal': 161000, 'objects': 12, '.bootstrap-ok': 7})
        self.assertEqual(calls[0][:2], ('exec', 'app-id'))
        self.assertEqual(real.data_usage(SimpleNamespace(containers=lambda: [])), {'error': 'app_container_unavailable'})
        self.assertEqual(real.data_usage(SimpleNamespace()), {'error': 'AttributeError'})

    def test_command_refusal_reads_only_the_closed_reason_token(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory)
            checkout = SimpleNamespace(evidence=evidence, sequence=7)
            (evidence/'command-0007.stderr').write_bytes(b'noise /home/runner/secret\nbin/update: status_reporting_unavailable\n')
            self.assertIsNone(real.command_refusal(checkout, SimpleNamespace(returncode=0)))
            self.assertEqual(real.command_refusal(checkout, SimpleNamespace(returncode=2)), 'status_reporting_unavailable')
            (evidence/'command-0007.stderr').write_bytes(b'bin/update: Path /home/runner/x is not allowed\n')
            self.assertEqual(real.command_refusal(checkout, SimpleNamespace(returncode=2)), 'unclassified')
            self.assertEqual(real.command_refusal(SimpleNamespace(evidence=evidence, sequence=8),
                                                  SimpleNamespace(returncode=2)), 'unavailable')
