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
                patch.object(harness, "command", side_effect=lambda *args, **kwargs: dict(harness.env)):
            observed = harness.bin_update('setup', '--yes')
        launcher.assert_called_once()
        self.assertEqual(observed['PATH'], os.defpath)
        self.assertEqual(observed['PYTHON'], real.sys.executable)
        self.assertEqual(observed['DOCKER_CONTEXT'], real.CONTEXT)
        self.assertEqual(harness.env['PYTHON'], 'original-interpreter')

    def test_setup_command_bound_is_supplied_by_the_caller(self):
        harness = real.ProductHarness(real.HOME / "fixture", Path.cwd(), "fagi-upd-e2e-real-unit")
        with patch.object(real, "native_ci", return_value=False), patch.object(harness, "command") as command:
            harness.bin_update('setup', '--yes', timeout=1680)
            harness.bin_update('setup', '--deactivate', '--yes')
        self.assertEqual([call.kwargs['timeout'] for call in command.call_args_list], [1680, 600])


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
