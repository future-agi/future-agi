"""Synthetic observation/migration contracts; no fixture is product qualification."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from deploy.update import standalone_state as state
from deploy.update import docker_host as host
from deploy.update.compatibility import topology_profile
from deploy.update.discovery import DiscoveredRelease
from deploy.update.envfile import read_env, render_changes
from deploy.update.install import Installation
from deploy.update.manifest import manifest_hash, canonical_json
from deploy.update.orchestrator import GuardRefused, fingerprints
from deploy.update.contracts import VerificationSnapshot, InstallationBinding, ReasonCode
from deploy.tests.test_update_manifest import valid_manifest

NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)
LAYOUT = topology_profile("standalone", ())
DOCUMENT = {"schema_version": 2, "contract": "standalone-runtime-v2", "persistence": dict(LAYOUT.persistence),
            "migration_inventory_sha256": "a" * 64, "applied_migrations_sha256": "b" * 64,
            "active_app_mapping_sha256": manifest_hash(state.load_migration_mapping()), "ready": True}


def probe_module():
    spec = importlib.util.spec_from_file_location("fixed_probe_fixture", ROOT / "deploy/update/probes/standalone.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProbeOutputTests(unittest.TestCase):
    def test_complete_closed_document(self):
        self.assertEqual(state._probe_document(json.dumps(DOCUMENT).encode()), DOCUMENT)

    def test_every_missing_field_refuses(self):
        for key in DOCUMENT:
            data = dict(DOCUMENT)
            del data[key]
            with self.subTest(key=key), self.assertRaises(GuardRefused):
                state._probe_document(json.dumps(data).encode())

    def test_malformed_duplicate_oversized_unknown_and_legacy_refuse(self):
        for raw in (b"not json", b" " * 20000, b'{"schema_version":2,"schema_version":2}',
                    json.dumps(dict(DOCUMENT, formats={})).encode(), json.dumps(dict(DOCUMENT, schema_version=True)).encode(),
                    json.dumps(dict(DOCUMENT, ready=1)).encode(), json.dumps(dict(DOCUMENT, ready=False)).encode()):
            with self.subTest(raw=raw[:20]), self.assertRaises(GuardRefused):
                state._probe_document(raw)

    def test_hash_and_persistence_coercion_refuse(self):
        for key in ("migration_inventory_sha256", "applied_migrations_sha256", "active_app_mapping_sha256"):
            for value in ("bad", "f" * 64 + "\n", False):
                with self.subTest(key=key, value=value), self.assertRaises(GuardRefused):
                    state._probe_document(json.dumps(dict(DOCUMENT, **{key: value})).encode())
        data = copy.deepcopy(DOCUMENT)
        data["persistence"]["redis"] = 0
        with self.assertRaises(GuardRefused):
            state._probe_document(json.dumps(data).encode())

    def test_no_descriptors_bootstrap_or_schema_fingerprints(self):
        source = host.fixed_source("standalone.py")
        for prohibited in (b"django.setup(", b"import django", b"import tfc", b"call_command", b"standalone-observation-v1",
                           b"protocols-v1.json", b"shared-formats-v1.json", b"pg_get_constraintdef", b"sqlite_master"):
            self.assertNotIn(prohibited, source)
        self.assertIn(b"-S", state.probe_source())

    def test_mapping_and_layout_are_fixed_bytes_and_tamper_refuses(self):
        for name in ("migration-apps.json", "automatic-layout.json"):
            self.assertTrue(host.load_fixed_data(name))
            with patch.dict(host.SOURCE_DIGESTS, {name: "0" * 64}), self.assertRaises(host.ObservationFailure):
                host.load_fixed_data(name)


class MigrationInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.probe = probe_module()
        self.mapping = copy.deepcopy(state.load_migration_mapping())
        self.mapping["apps"] = [copy.deepcopy(next(a for a in self.mapping["apps"] if a["label"] == label))
                                for label in ("accounts", "ee_licensing", "prism")]
        self.settings = self.root / "tfc/settings/settings.py"
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text('INSTALLED_APPS = ["accounts", "agentcc"]\nif has_ee("ee.licensing"):\n    INSTALLED_APPS.append("ee.licensing")\n')
        for label in ("accounts", "prism", "ee_licensing"):
            app = next(a for a in self.mapping["apps"] if a["label"] == label)
            path = self.root / app["source_root"]
            (path / "migrations").mkdir(parents=True)
            (path / "apps.py").write_text('class Config:\n    name = ' + repr(app["module"]) + '\n    label = ' + repr(label) + '\n')
            (path / "migrations/0001_initial.py").write_text('class Migration:\n    dependencies = []\n    operations = []\n')

    def inventory(self):
        return self.probe.migration_inventory(self.mapping, self.root, self.root)

    def test_explicit_app_labels_not_directory_basenames(self):
        inventory = self.inventory()
        self.assertEqual(self.probe.migration_names(inventory), [["accounts", "0001_initial"], ["ee_licensing", "0001_initial"], ["prism", "0001_initial"]])
        self.assertEqual(self.probe.digest(inventory), manifest_hash(inventory))

    def test_source_is_data_even_module_level_code_never_executes(self):
        path = self.root / "accounts/migrations/0001_initial.py"
        before = self.inventory()
        path.write_text('raise RuntimeError("must not execute")\nclass Migration:\n    operations = []\n')
        after = self.inventory()
        self.assertNotEqual(before, after)

    def test_non_numeric_migrations_are_not_silently_omitted(self):
        (self.root / "accounts/migrations/custom.py").write_text('class Migration: pass\n')
        self.assertIn(["accounts", "custom"], self.probe.migration_names(self.inventory()))

    def test_unknown_app_custom_modules_squash_and_wrong_label_refuse(self):
        cases = [(self.settings, 'INSTALLED_APPS = ["accounts", "unknown"]\n'),
                 (self.settings.parent / "other.py", 'MIGRATION_MODULES = {"accounts": "other"}\n'),
                 (self.root / "accounts/migrations/0001_initial.py", 'class Migration:\n    replaces = [("accounts", "old")]\n'),
                 (self.root / "agentcc/apps.py", 'class Config:\n    name = "agentcc"\n    label = "agentcc"\n')]
        for path, text in cases:
            original = path.read_bytes() if path.exists() else None
            path.write_text(text)
            with self.subTest(path=path), self.assertRaises(self.probe.ProbeError) as error:
                self.inventory()
            self.assertEqual(str(error.exception), "migration_inventory_unsupported")
            path.unlink() if original is None else path.write_bytes(original)

    def test_unknown_registry_mutation_and_conditional_refuse(self):
        original = self.settings.read_text()
        for extra in ('INSTALLED_APPS += ["accounts"]\n', 'alias = INSTALLED_APPS\n',
                      'if unknown():\n    INSTALLED_APPS.append("accounts")\n'):
            self.settings.write_text(original + extra)
            with self.subTest(extra=extra), self.assertRaises(self.probe.ProbeError):
                self.inventory()

    def test_optional_oss_app_presence_is_not_assumed(self):
        import shutil
        shutil.rmtree(self.root / "ee/licensing")
        self.assertNotIn("ee_licensing", {r["app_label"] for r in self.inventory()})

    def test_symlink_and_malformed_source_refuse(self):
        path = self.root / "accounts/migrations/0001_initial.py"
        raw = path.read_bytes()
        path.unlink()
        target = self.root / "outside.py"
        target.write_bytes(raw)
        path.symlink_to(target)
        with self.assertRaises(self.probe.ProbeError):
            self.inventory()
        path.unlink()
        path.write_text('class Migration: !!!')
        with self.assertRaises(SyntaxError):
            self.inventory()

    def test_mapping_traversal_and_duplicates_refuse(self):
        for change in (lambda m: m["apps"][0].update(source_root="../accounts"),
                       lambda m: m["apps"].append(copy.deepcopy(m["apps"][0]))):
            mapping = copy.deepcopy(self.mapping)
            change(mapping)
            with self.assertRaises(self.probe.ProbeError):
                self.probe.migration_inventory(mapping, self.root, self.root)


class PostgresReadOnlyTests(unittest.TestCase):
    def connection(self, applied=None, size_row=(123, 4096, "r")):
        probe = probe_module()
        executed = []
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def execute(self, query, params=None):
                executed.append((query, params))
                self.query = query
            def fetchone(self): return ("on",)
            def fetchmany(self, size):
                return applied if self.query.startswith("SELECT app,name") else ([] if size_row is None else [size_row])
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def cursor(self): return Cursor()
            def rollback(self): executed.append(("ROLLBACK", None))
        return probe, Connection(), executed

    def test_exact_applied_equality_missing_extra_duplicate_wrong_label_refuse(self):
        expected = [["prism", "0001"], ["ee_licensing", "0001"]]
        expected.sort()
        for applied in (expected[:1], expected + [["accounts", "old"]], expected + expected[:1], [["agentcc", "0001"], expected[0]]):
            probe, conn, _ = self.connection(applied)
            with patch.object(probe, "postgres_connection", return_value=conn), self.assertRaises(probe.ProbeError) as error:
                probe.postgres_migrations(expected)
            self.assertEqual(str(error.exception), "migration_inventory_mismatch")
        probe, conn, queries = self.connection(list(reversed(expected)))
        with patch.object(probe, "postgres_connection", return_value=conn):
            self.assertEqual(probe.postgres_migrations(expected), manifest_hash(expected))
        self.assertEqual(queries[-1], ("ROLLBACK", None))

    def test_sizes_use_estimates_and_total_relation_size_parameterized(self):
        probe, conn, queries = self.connection()
        with patch.object(probe, "postgres_connection", return_value=conn):
            self.assertEqual(probe.postgres_table_sizes(["public.tracer"]), [{"engine": "postgres", "table": "public.tracer", "rows": 123, "bytes": 4096}])
        query, params = next((q, p) for q, p in queries if "pg_class" in q)
        self.assertIn("pg_total_relation_size", query)
        self.assertEqual(params, ("public", "tracer"))
        self.assertNotIn("public.tracer", query)
        self.assertTrue(all(q.startswith(("SELECT ", "SHOW ", "SET TRANSACTION ", "ROLLBACK")) for q, _ in queries))
        self.assertEqual(queries[-1][0], "ROLLBACK")

    def test_unknown_size_missing_partitioned_and_boolean_are_not_zero(self):
        for row in (None, (-1, 10, "r"), (1, 2, "p"), (True, 100, "r"), (1, -1, "r")):
            probe, conn, _ = self.connection(size_row=row)
            with patch.object(probe, "postgres_connection", return_value=conn), self.assertRaises(probe.ProbeError):
                probe.postgres_table_sizes(["public.trace"])

    def test_table_identifier_injection_rejected_before_connect(self):
        probe, conn, _ = self.connection()
        for values in (["public.x;DELETE"], ["trace"], ["public.trace\n"], ["Public.trace"], ["public.trace"] * 2, [True]):
            with patch.object(probe, "postgres_connection", return_value=conn) as connect, self.assertRaises(probe.ProbeError):
                probe.postgres_table_sizes(values)
            connect.assert_not_called()

    def test_clickhouse_uses_POST_explicit_readonly_and_bounded_catalog_results(self):
        probe = probe_module()
        with patch.dict(os.environ, {"CH_USERNAME": "fixture", "CH_PASSWORD": "secret"}), \
                patch.object(probe, "http", return_value=b'{"data":[[1]]}') as http:
            self.assertEqual(probe.clickhouse("SELECT 1 FORMAT JSONCompact"), [[1]])
        args, kwargs = http.call_args
        self.assertIn("readonly=1", args[0])
        self.assertIn("max_execution_time=5", args[0])
        self.assertIn("result_overflow_mode=throw", args[0])
        self.assertEqual(kwargs["data"], b"")
        self.assertNotIn("secret", args[0])

    def test_clickhouse_active_parts_parameterized_and_empty_table_is_measured(self):
        probe = probe_module()
        calls = []
        def query(sql, params):
            calls.append((sql, params))
            return [[1]] if "system.tables" in sql else [["12", "456"]]
        with patch.object(probe, "clickhouse", side_effect=query):
            self.assertEqual(probe.clickhouse_table_sizes(["default.trace"])[0]["bytes"], 456)
        self.assertIn("WHERE active AND", calls[1][0])
        self.assertNotIn("default.trace", calls[1][0])
        self.assertEqual(calls[1][1], {"param_database": "default", "param_table": "trace"})
        with patch.object(probe, "clickhouse", side_effect=[[[1]], [[0, 0]]]):
            self.assertEqual(probe.clickhouse_table_sizes(["default.trace"])[0]["rows"], 0)
        with patch.object(probe, "clickhouse", return_value=[[0]]), self.assertRaises(probe.ProbeError):
            probe.clickhouse_table_sizes(["default.missing"])


class RuntimeFixture:
    def __init__(self, root):
        self.root = root
        self.env_file = root / ".env"
        self.env_file.write_bytes(b"FUTURE_AGI_VERSION=v1.52.0\n")
        self.env_target = self.env_file
        self.env = read_env(self.env_file)
        self.environ = {"DOCKER_CONTEXT": "fixture"}
        self.env_overlay = {}
        self.mode, self.production = "standalone", False
        self.docker = ["synthetic-docker"]
        self.compose_file = root / "docker-compose.yml"
        self.compose_file.write_text("synthetic ownership file\n")
        self.binding = InstallationBinding(slot_id="a" * 32, selection_hash="a" * 64, installation_fingerprint="b" * 64,
            checkout=root, env_alias=self.env_file, env_target=self.env_file, topology="standalone", production=False,
            compose_project="fixture", compose_files=(self.compose_file,), profiles=(), docker_context="fixture",
            daemon_identity="daemon-a", host_uid=os.geteuid())
        self.manifest = valid_manifest()
        image = self.manifest["components"]["standalone"]
        self.model = {"services": {}, "volumes": {}}
        self.records, self.conts, self.volume_records = {}, [], {}
        self.daemon = {"ID": "daemon-a", "Architecture": "arm64", "OSType": "linux", "Driver": "overlayfs"}
        contract = host.load_fixed_data("automatic-layout.json")
        for index, (name, (destination, logical)) in enumerate(state.MOUNTS.items(), 1):
            cid, iid = str(index) * 64, "sha256:" + str(index) * 64
            ref = image["image"] if name == "app" else "docker.io/library/" + name + ":1"
            dgst = image["digest"] if name == "app" else "sha256:" + "c" * 64
            mount = {"Destination": destination, "Type": "volume", "RW": True, "Name": "fixture-" + logical,
                     "Source": "/var/lib/docker/volumes/fixture-" + logical + "/_data"}
            binds, model_mounts = [], []
            for entry in contract["services"][name]["mounts"]:
                entry = copy.deepcopy(entry)
                if entry["type"] == "bind":
                    if entry["source"].startswith("./"):
                        path = root / entry["source"]
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text("synthetic config\n")
                        entry["source"] = str(path.resolve())
                    binds.append({"Destination": entry["target"], "Type": "bind", "RW": False, "Source": entry["source"]})
                model_mounts.append(entry)
            self.model["services"][name] = {"image": ref, "environment": {"FIXTURE": "yes"}, "restart": "unless-stopped", "volumes": model_mounts}
            self.model["volumes"][logical] = {"name": mount["Name"]}
            self.records[cid] = {"Id": cid, "Image": iid, "State": {"Running": True, "StartedAt": "start", "Health": {"Status": "healthy"}},
                "Config": {"Env": ["FIXTURE=yes"]}, "RestartCount": 0,
                "HostConfig": {"Privileged": False, "NetworkMode": "fixture_default", "RestartPolicy": {"Name": "unless-stopped"}},
                "Mounts": [mount] + binds, "NetworkSettings": {"Networks": {"fixture_default": {"NetworkID": "d" * 64}}}}
            self.conts.append({"id": cid, "service": name, "image_id": iid, "status": "running", "health": "healthy"})
            self.volume_records[mount["Name"]] = {"Name": mount["Name"], "Mountpoint": mount["Source"], "Driver": "local",
                "CreatedAt": "2026-01-01", "Labels": {"com.docker.compose.project": "fixture", "com.docker.compose.volume": logical}}
            setattr(self, "img_" + name, {"id": iid, "repo_digests": [ref.split(":")[0] + "@" + dgst]})
        self.foreign = []
        self.network_peers = None

    def config(self, refresh=False): return copy.deepcopy(self.model)
    def containers(self): return copy.deepcopy(self.conts)
    def image(self, ref):
        for name, value in self.model["services"].items():
            obj = getattr(self, "img_" + name)
            if ref in (obj["id"], value["image"]): return copy.deepcopy(obj)
        return None
    def _ownership_files(self): return [self.compose_file]
    def subprocess_env(self): return dict(self.environ)
    def docker_run(self, *args, **kwargs):
        if args[0] == "ps":
            return subprocess.CompletedProcess(args, 0, "\n".join(list(self.records) + [r["Id"] for r in self.foreign]), "")
        if args[0] == "inspect":
            all_rows = dict(self.records, **{r["Id"]: r for r in self.foreign})
            result = [all_rows[cid] for cid in args[1:]]
        elif args[:2] == ("volume", "inspect"): result = [self.volume_records[args[2]]]
        elif args[:2] == ("network", "inspect"):
            result = [{"Id": "d" * 64, "Name": "fixture_default", "Driver": "bridge",
                       "Labels": {"com.docker.compose.project": "fixture"},
                       "Containers": {cid: {} for cid in (self.network_peers or self.records)}}]
        elif args[0] == "info": result = self.daemon
        else: raise AssertionError("unexpected synthetic command " + str(args))
        return subprocess.CompletedProcess(args, 0, json.dumps(result), "")


class RuntimeBindingTests(unittest.TestCase):
    def setUp(self):
        # Positive ownership fixtures need trusted ancestors. On Ubuntu the
        # default /tmp is world-writable, so _runtime correctly refuses its
        # bind paths before the capacity guard can examine the supplied sizes.
        self.temp = tempfile.TemporaryDirectory(dir=Path.home())
        self.addCleanup(self.temp.cleanup)
        self.inst = RuntimeFixture(Path(self.temp.name).resolve())
        self.release = SimpleNamespace(manifest=self.inst.manifest)

    def runtime(self): return state._runtime(self.inst, self.inst.binding, self.release, LAYOUT)

    def test_published_ports_match_declared_host_address_and_are_retained(self):
        self.inst.model['services']['app']['ports'] = [dict(target=80, published='18080', host_ip='127.0.0.1')]
        row = self.inst.records['1'*64]
        ports = {'80/tcp': [dict(HostIp='127.0.0.1', HostPort='18080')]}
        row['HostConfig']['PortBindings'] = copy.deepcopy(ports)
        row['NetworkSettings']['Ports'] = copy.deepcopy(ports)
        before = self.runtime()
        self.assertIn('published_ports', before[1]['app'])
        for field in ('HostConfig', 'NetworkSettings'):
            key = 'PortBindings' if field == 'HostConfig' else 'Ports'
            for wrong in ({}, {'80/tcp': [dict(HostIp='0.0.0.0', HostPort='18080')]},
                          {'80/udp': [dict(HostIp='127.0.0.1', HostPort='18080')]}):
                with self.subTest(field=field, wrong=wrong):
                    row[field][key] = wrong
                    with self.assertRaises(GuardRefused) as caught:
                        self.runtime()
                    self.assertEqual(caught.exception.reason_code, ReasonCode.CONFIGURATION_CHANGED)
                    row[field][key] = copy.deepcopy(ports)
        self.assertEqual(self.runtime(), before)

    def test_wildcard_port_family_order_is_canonical_but_loopback_is_not_wildcard(self):
        self.inst.model['services']['app']['ports'] = [dict(target=80, published=18080)]
        row = self.inst.records['1'*64]
        row['HostConfig']['PortBindings'] = {'80/tcp': [dict(HostIp='', HostPort='18080')]}
        rows = [dict(HostIp='::', HostPort='18080'), dict(HostIp='0.0.0.0', HostPort='18080')]
        row['NetworkSettings']['Ports'] = {'80/tcp': rows}
        before = self.runtime()
        rows.reverse()
        self.assertEqual(self.runtime(), before)
        row['NetworkSettings']['Ports']['80/tcp'] = [dict(HostIp='127.0.0.1', HostPort='18080')]
        with self.assertRaises(GuardRefused):
            self.runtime()

    def test_every_image_volume_restart_and_network_observed(self):
        images, identities, volumes = self.runtime()
        self.assertEqual(set(images), {"app", "postgres", "clickhouse"})
        self.assertEqual(set(volumes), set(images))
        self.assertEqual({r["restart_policy"] for r in identities.values()}, {"unless-stopped"})
        self.assertIn("network", identities["app"])

    def test_mount_order_does_not_change_runtime_identity_or_roster_hash(self):
        before = self.runtime()
        for row in self.inst.records.values():
            row["Mounts"].reverse()
        after = self.runtime()
        self.assertEqual(before, after)
        self.assertEqual(manifest_hash(before[1]), manifest_hash(after[1]))
        for row in after[1].values():
            destinations = [mount["Destination"] for mount in row["mounts"]]
            self.assertEqual(destinations, sorted(destinations))

    def test_duplicate_mount_destination_is_not_a_mount_set(self):
        mounts = self.inst.records["1" * 64]["Mounts"]
        mounts[2] = copy.deepcopy(mounts[1])
        with self.assertRaises(GuardRefused):
            self.runtime()

    def test_mount_order_changes_during_stability_and_cold_hold_are_allowed(self):
        original = self.inst.docker_run
        def rotating_inspections(*args, **kwargs):
            if args[0] == "inspect":
                for row in self.inst.records.values():
                    row["Mounts"] = row["Mounts"][1:] + row["Mounts"][:1]
            return original(*args, **kwargs)
        self.inst.docker_run = rotating_inspections
        observer, observed, storage, ticks = self.make_observation()
        self.assertGreaterEqual(ticks[0], 2)
        for row in self.inst.records.values():
            row["State"].update(Running=False, Status="exited", ExitCode=0)
        with patch.object(state, "bounded_installation", side_effect=lambda obj, *args: obj), patch.object(state, "check_binding"), \
                patch.object(state, "inspect_volume_storage", return_value=storage):
            hold = observer.observe_cold_hold(self.inst, source_observation=observed)
            self.assertEqual(hold.source_observation_sha256, observed.runtime.sha256)
            self.inst.records["1" * 64]["Mounts"][0]["Source"] += "-changed"
            with self.assertRaises(GuardRefused):
                observer.observe_cold_hold(self.inst, source_observation=observed)

    def test_always_on_failure_missing_and_mismatched_restart_policies_refuse(self):
        record = self.inst.records["1" * 64]
        for policy in ("always", "on-failure", "no", ""):
            record["HostConfig"]["RestartPolicy"]["Name"] = policy
            with self.subTest(policy=policy), self.assertRaises(GuardRefused): self.runtime()
        record["HostConfig"]["RestartPolicy"]["Name"] = "no"
        self.inst.model["services"]["app"]["restart"] = "no"
        self.assertTrue(self.runtime())

    def test_foreign_even_stopped_or_bind_alias_consumer_refuses(self):
        source = self.inst.records["1" * 64]["Mounts"][0]["Source"]
        for mount in ({"Name": "fixture-app-data"}, {"Source": source + "/child"}, {"Source": "/var/lib/docker"}):
            self.inst.foreign = [{"Id": "f" * 64, "Mounts": [mount], "State": {"Running": False}}]
            with self.subTest(mount=mount), self.assertRaises(GuardRefused): self.runtime()

    def test_unowned_network_peer_refuses(self):
        self.inst.network_peers = list(self.inst.records) + ["f" * 64]
        with self.assertRaises(GuardRefused): self.runtime()

    def test_image_swap_wrong_volume_owner_and_unowned_mount_refuse(self):
        self.inst.records["1" * 64]["Image"] = "sha256:" + "f" * 64
        with self.assertRaises(GuardRefused): self.runtime()
        self.inst.records["1" * 64]["Image"] = "sha256:" + "1" * 64
        self.inst.volume_records["fixture-app-data"]["Labels"] = {}
        with self.assertRaises(GuardRefused): self.runtime()
        self.inst.volume_records["fixture-app-data"]["Labels"] = {"com.docker.compose.project": "fixture", "com.docker.compose.volume": "app-data"}
        self.inst.records["1" * 64]["Mounts"].append({"Destination": "/app/backend", "Type": "bind", "Source": "/host/source", "RW": False})
        with self.assertRaises(GuardRefused): self.runtime()

    def test_layout_additions_and_writable_bind_refuse(self):
        self.inst.model["services"]["app"]["volumes"][1]["read_only"] = False
        with self.assertRaises(GuardRefused): self.runtime()
        self.inst.model["services"]["app"]["volumes"][1]["read_only"] = True
        self.inst.model["volumes"]["extra"] = {}
        with self.assertRaises(GuardRefused): self.runtime()

    def make_observation(self, *, probe_effect=None, storage=None):
        verified = VerificationSnapshot(True, "synthetic", "fixture", "fixture", "unit-test-only", NOW)
        release = DiscoveredRelease(canonical_json(self.inst.manifest), b"fixture", manifest_hash(self.inst.manifest), "d" * 64, verified)
        ticks = [0.0]
        observer = state.StandaloneStateObserver(SimpleNamespace(binding=self.inst.binding), clock=lambda: NOW,
            stability_period_seconds=2, monotonic=lambda: ticks[0], sleeper=lambda s: ticks.__setitem__(0, ticks[0] + s))
        files = {"docker-compose.yml": state._sha(self.inst.compose_file.read_bytes())}
        storage = storage or host.VolumeStorageObservation("available", tuple(host.VolumeFilesystem(role, "fixture-" + role,
            "2049:" + str(i), "8:1", "ext4", "e" * 64, 999999, 99999, None) for i, role in enumerate(host.VOLUME_ROLES, 1)))
        with patch.object(state, "bounded_installation", side_effect=lambda obj, *args: obj), \
                patch.object(state, "check_binding"), patch.object(state, "inspect_volume_storage", return_value=storage), \
                patch.object(observer, "_probe", side_effect=probe_effect, return_value=canonical_json(DOCUMENT)):
            observed = observer.observe(self.inst, current_release=release, execution_deployment={"files": files}, boundary="enrollment")
        return observer, observed, storage, ticks

    def test_full_v2_observation_raw_manifest_deployment_roots_and_elapsed_stability(self):
        observer, observed, storage, ticks = self.make_observation()
        self.assertIsInstance(observed, state.StandaloneObservation)
        self.assertTrue(observed.runtime.stable)
        self.assertGreaterEqual(ticks[0], 2)
        self.assertEqual(observed.probe_contract, "standalone-runtime-v2")
        self.assertEqual(observed.runtime.deployment_sha256, manifest_hash({"files": {"docker-compose.yml": state._sha(self.inst.compose_file.read_bytes())}}))
        self.assertEqual(observed.volume_storage_sha256, storage.storage_sha256)
        self.assertEqual(set(observed.runtime.restart_counts), set(self.inst.records))
        self.assertEqual(observed.env_bytes, self.inst.env_file.read_bytes())
        self.assertFalse(observed.facts.baseline_attested)
        self.assertIs(observed.facts.runtime_observation, observed.runtime)

    def test_envelope_identity_is_stable_across_observation_timestamps(self):
        from dataclasses import replace
        from datetime import timedelta
        _, observed, _, _ = self.make_observation()
        runtime = replace(observed.runtime, observed_at=NOW + timedelta(seconds=60))
        later = replace(observed, runtime_observation=runtime, facts=replace(observed.facts, runtime_observation=runtime))
        self.assertNotEqual(observed.runtime.sha256, later.runtime.sha256)
        self.assertEqual(observed.structure_sha256, later.structure_sha256)
        self.assertEqual(observed.installation_sha256, later.installation_sha256)
        from deploy.update.baseline import normalise_observation
        self.assertEqual(normalise_observation(observed).structure_sha256, observed.structure_sha256)

    def test_measure_update_sizes_is_the_baseline_runner_frozen_API(self):
        from deploy.update.contracts import (MigrationEvidence, TransitionMeasurements, VolumeTransferMeasurement,
                                            pending_migration_plan_hash)
        migration = MigrationEvidence("a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64,
                                      pending_migration_plan_hash([]), 0)
        transfer = VolumeTransferMeasurement({role: 10 for role in host.VOLUME_ROLES}, 1000)
        measurements = TransitionMeasurements(migration, (), transfer, transfer)
        observer = state.StandaloneStateObserver(SimpleNamespace(binding=self.inst.binding))
        full = host.VolumeStorageObservation("available", tuple(host.VolumeFilesystem(role, "fixture-" + role,
            "2049:" + str(i), "8:1", "ext4", "e" * 64, 999999, 99999, 100, 200, 2) for i, role in enumerate(host.VOLUME_ROLES, 1)))
        with patch.object(state, "bounded_installation", side_effect=lambda obj, *args: obj), patch.object(state, "check_binding"), \
                patch.object(state, "inspect_volume_storage", return_value=full), patch.object(observer, "_probe", return_value=canonical_json({"schema_version": 1, "table_sizes": []})), \
                patch.object(state, "inspect_backup_storage", return_value={"free_bytes": 98765}):
            sizes = observer.measure_update_sizes(self.inst, measurements=measurements, backup_destination=self.inst.root)
            self.assertEqual(sizes.table_sizes, ())
            self.assertEqual(sizes.docker_free_bytes, 999999)

    def test_cold_hold_detects_bind_file_edit_and_same_named_volume_replacement(self):
        observer, observed, storage, _ = self.make_observation()
        for row in self.inst.records.values(): row["State"].update(Running=False, Status="exited", ExitCode=0)
        with patch.object(state, "bounded_installation", side_effect=lambda obj, *args: obj), patch.object(state, "check_binding"), \
                patch.object(state, "inspect_volume_storage", return_value=storage):
            volume = self.inst.volume_records["fixture-app-data"]
            original = volume["CreatedAt"]
            volume["CreatedAt"] = "new"
            with self.assertRaises(GuardRefused): observer.observe_cold_hold(self.inst, source_observation=observed)
            volume["CreatedAt"] = original
            path = Path(self.inst.records["1" * 64]["Mounts"][1]["Source"])
            path.write_text("operator edit")
            with self.assertRaises(GuardRefused): observer.observe_cold_hold(self.inst, source_observation=observed)

    def test_restart_during_stability_fails_instead_of_stable_true(self):
        calls = [0]
        def probe(*args):
            calls[0] += 1
            self.inst.records["2" * 64]["RestartCount"] += 1
            return canonical_json(DOCUMENT)
        with self.assertRaises(GuardRefused): self.make_observation(probe_effect=probe)

    def test_stopped_phases_refuse_live_probe_even_when_A_was_healthy(self):
        observer = state.StandaloneStateObserver(SimpleNamespace(binding=self.inst.binding))
        verified = VerificationSnapshot(True, "synthetic", "fixture", "fixture", "unit-test-only", NOW)
        release = DiscoveredRelease(canonical_json(self.inst.manifest), b"fixture", manifest_hash(self.inst.manifest), "d" * 64, verified)
        for boundary in ("after_backup", "before_env_commit", "before_rollout"):
            with patch.object(observer, "_probe") as probe, self.assertRaises(GuardRefused):
                observer.observe(self.inst, current_release=release, execution_deployment={"files": {}}, boundary=boundary)
            probe.assert_not_called()

    def test_cold_hold_checks_stopped_exact_ids_without_reprobing_A(self):
        observer, observed, storage, _ = self.make_observation()
        for row in self.inst.records.values(): row["State"].update(Running=False, Status="exited", ExitCode=0)
        with patch.object(state, "bounded_installation", side_effect=lambda obj, *args: obj), patch.object(state, "check_binding"), \
                patch.object(state, "inspect_volume_storage", return_value=storage), patch.object(observer, "_probe") as probe:
            hold = observer.observe_cold_hold(self.inst, source_observation=observed)
            self.assertEqual(hold.source_observation_sha256, observed.runtime.sha256)
            self.assertEqual(hold.storage_sha256, observed.runtime.storage_sha256)
            probe.assert_not_called()
            self.inst.records["1" * 64]["State"]["Running"] = True
            with self.assertRaises(GuardRefused): observer.observe_cold_hold(self.inst, source_observation=observed)

    def test_measure_sizes_returns_typed_A6_dimensions_and_conservative_colocation(self):
        observer, observed, storage, _ = self.make_observation()
        full = host.VolumeStorageObservation("available", tuple(host.VolumeFilesystem(v.role, v.name, v.root_identity,
            v.device, v.filesystem, v.backing_sha256, v.free_bytes, v.free_inodes, 100, 200, 2) for v in storage.volumes))
        tables = {"schema_version": 1, "table_sizes": [{"engine": "postgres", "table": "public.trace", "rows": 1, "bytes": 123}]}
        with patch.object(state, "bounded_installation", side_effect=lambda obj, *args: obj), patch.object(state, "check_binding"), \
                patch.object(state, "inspect_volume_storage", return_value=full), patch.object(observer, "_probe", return_value=canonical_json(tables)), \
                patch.object(state, "inspect_backup_storage", return_value={"free_bytes": 98765}):
            sizes = observer.measure_sizes(self.inst, source_observation=observed, postgres_tables=["public.trace"], backup_destination=self.inst.root)
            self.assertIsInstance(sizes, state.HostSizeObservation)
            self.assertEqual(dict(sizes.volume_bytes), {role: 200 + 2 * 8192 for role in host.VOLUME_ROLES})
            self.assertEqual(dict(sizes.volume_allocated_bytes), {role: 100 for role in host.VOLUME_ROLES})
            self.assertEqual(dict(sizes.volume_logical_bytes), {role: 200 for role in host.VOLUME_ROLES})
            self.assertEqual(dict(sizes.volume_members), {role: 2 for role in host.VOLUME_ROLES})
            with self.assertRaises(TypeError):
                sizes.volume_allocated_bytes["app-data"] = 0
            self.assertEqual(sizes.backup_free_bytes, 98765)
            self.assertTrue(sizes.backup_shares_docker_filesystem)

    def test_preflight_uses_logical_bytes_and_metadata_not_allocated_blocks(self):
        from deploy.update.contracts import (AutomationPolicy, MigrationEvidence, TransitionMeasurements,
                                            VolumeTransferMeasurement, pending_migration_plan_hash)
        from deploy.update.compatibility import estimate_automatic_update
        observer, observed, storage, _ = self.make_observation()
        migration = MigrationEvidence("a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64,
                                      pending_migration_plan_hash([]), 0)
        transfer = VolumeTransferMeasurement({role: 10 for role in host.VOLUME_ROLES}, 1024 ** 3)
        measurements = TransitionMeasurements(migration, (), transfer, transfer)
        for logical, members in ((8 * 1024 ** 3, 2), (0, 300000)):
            with self.subTest(logical=logical, members=members):
                full = host.VolumeStorageObservation("available", tuple(host.VolumeFilesystem(v.role, v.name,
                    v.root_identity, v.device, v.filesystem, v.backing_sha256, 4 * 1024 ** 3, 999999,
                    4096, logical if v.role == "app-data" else 0, members if v.role == "app-data" else 1)
                    for v in storage.volumes))
                with patch.object(state, "bounded_installation", side_effect=lambda obj, *args: obj), patch.object(state, "check_binding"), \
                        patch.object(state, "inspect_volume_storage", return_value=full), \
                        patch.object(observer, "_probe", return_value=canonical_json({"schema_version": 1, "table_sizes": []})), \
                        patch.object(state, "inspect_backup_storage", return_value={"free_bytes": 4 * 1024 ** 3}):
                    sizes = observer.measure_update_sizes(self.inst, measurements=measurements, backup_destination=self.inst.root)
                decision = estimate_automatic_update(measurements, sizes,
                    AutomationPolicy(qualification_policy="standalone-cold-restore-v1"),
                    window_remaining_seconds=100000, tick_remaining_seconds=100000)
                self.assertFalse(decision.allowed)
                self.assertIn(ReasonCode.INSUFFICIENT_DISK_FOR_BACKUP, decision.reason_codes)
                self.assertEqual(sizes.volume_allocated_bytes["app-data"], 4096)
                self.assertEqual(sizes.volume_logical_bytes["app-data"], logical)
                self.assertEqual(sizes.volume_members["app-data"], members)
                self.assertEqual(sizes.volume_bytes["app-data"], max(logical, 4096) + members * 8192)
                self.assertGreater(decision.estimate.protected_restore_reserve_bytes, logical + members * 8192)

    def test_unknown_logical_workload_cannot_fall_back_to_allocated_bytes(self):
        observer, observed, storage, _ = self.make_observation()
        full = host.VolumeStorageObservation("available", tuple(host.VolumeFilesystem(v.role, v.name,
            v.root_identity, v.device, v.filesystem, v.backing_sha256, v.free_bytes, v.free_inodes, 1)
            for v in storage.volumes))
        with patch.object(state, "bounded_installation", side_effect=lambda obj, *args: obj), patch.object(state, "check_binding"), \
                patch.object(state, "inspect_volume_storage", return_value=full), \
                patch.object(observer, "_probe", return_value=canonical_json({"schema_version": 1, "table_sizes": []})), \
                patch.object(state, "inspect_backup_storage", return_value={"free_bytes": 98765}), self.assertRaises(GuardRefused):
            observer.measure_sizes(self.inst, source_observation=observed, backup_destination=self.inst.root)

    def _inode_capacity_volumes(self, *, free_inodes=5000, members=(10001, 10001, 10001)):
        return tuple(host.VolumeFilesystem(role, "fixture-" + role, "2049:" + str(index), "8:1", "ext4",
            "e" * 64, 100 * 1024 ** 3, free_inodes, 4096, 10000, count)
            for index, (role, count) in enumerate(zip(host.VOLUME_ROLES, members), 1))

    def _measure_inode_sizes(self, volumes, *, source_bound=False):
        from deploy.update.contracts import (MigrationEvidence, TransitionMeasurements, VolumeTransferMeasurement,
                                            pending_migration_plan_hash)
        storage = host.VolumeStorageObservation("available", volumes)
        observer, observed, _, _ = self.make_observation(storage=storage)
        migration = MigrationEvidence("a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64,
                                      pending_migration_plan_hash([]), 0)
        transfer = VolumeTransferMeasurement({role: 10000 for role in host.VOLUME_ROLES}, 1024 ** 3)
        measurements = TransitionMeasurements(migration, (), transfer, transfer)
        with patch.object(state, "bounded_installation", side_effect=lambda obj, *args: obj), patch.object(state, "check_binding"), \
                patch.object(state, "inspect_volume_storage", return_value=storage), \
                patch.object(observer, "_probe", return_value=canonical_json({"schema_version": 1, "table_sizes": []})), \
                patch.object(state, "inspect_backup_storage", return_value={"free_bytes": 100 * 1024 ** 3, "free_inodes": 100000}):
            if source_bound:
                sizes = observer.measure_sizes(self.inst, source_observation=observed, backup_destination=self.inst.root)
            else:
                sizes = observer.measure_update_sizes(self.inst, measurements=measurements, backup_destination=self.inst.root)
        return sizes, measurements

    def test_preoutage_inode_shortage_refuses_both_size_APIs_without_stopping_A(self):
        # Reviewer counterexample: bytes are ample, but three 10,001-member
        # volumes on ONE device need 30,003 + 1,024 available inodes.
        before = self.runtime()
        for source_bound in (False, True):
            with self.subTest(source_bound=source_bound):
                with self.assertRaises(GuardRefused) as error:
                    self._measure_inode_sizes(self._inode_capacity_volumes(), source_bound=source_bound)
                self.assertEqual(error.exception.reason_codes, (ReasonCode.RESTORE_CAPACITY_INSUFFICIENT,))
        self.assertEqual(self.runtime(), before)
        self.assertTrue(all(row["State"]["Running"] for row in self.inst.records.values()))

    def test_preoutage_inode_capacity_sums_members_and_uses_lowest_sample_per_device(self):
        from dataclasses import replace
        from deploy.update.compatibility import estimate_automatic_update
        from deploy.update.contracts import AutomationPolicy
        members = (100, 200, 300)
        required = sum(members) + 1024
        for low_index in range(3):
            for available in (required - 1, required):
                volumes = tuple(replace(v, free_inodes=available if i == low_index else required + 10000)
                    for i, v in enumerate(self._inode_capacity_volumes(members=members)))
                with self.subTest(low_index=low_index, available=available):
                    if available < required:
                        with self.assertRaises(GuardRefused) as error:
                            self._measure_inode_sizes(volumes)
                        self.assertEqual(error.exception.reason_codes, (ReasonCode.RESTORE_CAPACITY_INSUFFICIENT,))
                    else:
                        sizes, measurements = self._measure_inode_sizes(volumes)
                        decision = estimate_automatic_update(measurements, sizes,
                            AutomationPolicy(qualification_policy="standalone-cold-restore-v1"),
                            window_remaining_seconds=100000, tick_remaining_seconds=100000)
                        self.assertTrue(decision.allowed, decision.reason_codes)

    def test_inode_reserve_is_per_backing_filesystem_not_per_volume_or_global(self):
        from dataclasses import replace
        # Separate filesystem groups each need their own reserve, not the sum
        # of unrelated filesystem workloads. This tests the typed guard only;
        # production byte admission may conservatively refuse other layouts.
        volumes = tuple(replace(v, device="8:" + str(i), backing_sha256=str(i) * 64,
            free_inodes=v.members + 1024) for i, v in enumerate(self._inode_capacity_volumes(), 1))
        def typed(rows):
            return state.StandaloneHostSizeObservation((), {v.role: v.restore_work_bytes for v in rows},
                min(v.free_bytes for v in rows), 100 * 1024 ** 3, volume_storage=rows)
        self.assertEqual(typed(volumes).volume_storage, volumes)
        for i in range(3):
            rows = tuple(replace(v, free_inodes=v.free_inodes - (index == i)) for index, v in enumerate(volumes))
            with self.subTest(short_filesystem=i):
                with self.assertRaises(GuardRefused) as error:
                    typed(rows)
                self.assertEqual(error.exception.reason_codes, (ReasonCode.RESTORE_CAPACITY_INSUFFICIENT,))

    def test_unknown_inode_capacity_and_conflicting_backing_identity_never_admit(self):
        from dataclasses import replace
        base = self._inode_capacity_volumes(free_inodes=100000)
        invalid = [replace(base[1], free_inodes=value) for value in (None, True, -1, "100000", 100000.0, 2 ** 63)]
        invalid += [replace(base[1], device=value) for value in (None, "", "unknown", "8:1\n")]
        invalid += [replace(base[1], filesystem="xfs"), replace(base[1], backing_sha256="a" * 64)]
        for bad in invalid:
            with self.subTest(bad=bad):
                with self.assertRaises(GuardRefused) as error:
                    self._measure_inode_sizes((base[0], bad, base[2]))
                self.assertEqual(error.exception.reason_codes, (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))

    def test_untrusted_fixture_ancestry_refuses_before_capacity_is_examined(self):
        # Reproduce Ubuntu's default /tmp ancestry without relying on a host
        # path or changing any system permission. The production refusal wins
        # before measurements; making the fixture genuinely safe reaches it.
        ancestor = self.inst.root/'untrusted-parent'
        ancestor.mkdir()
        ancestor.chmod(0o777)
        root = ancestor/'installation'
        root.mkdir(mode=0o700)
        installation = RuntimeFixture(root)
        release = SimpleNamespace(manifest=installation.manifest)
        with self.assertRaises(GuardRefused) as error:
            state._runtime(installation, installation.binding, release, LAYOUT)
        self.assertEqual(error.exception.reason_codes, (ReasonCode.OWNERSHIP_UNCONFIRMED,))
        ancestor.chmod(0o700)
        self.assertTrue(state._runtime(installation, installation.binding, release, LAYOUT))

    def test_conflicting_backing_identity_is_unknown_capacity_at_the_typed_boundary(self):
        from dataclasses import replace
        base = self._inode_capacity_volumes(free_inodes=100000)
        for bad in (replace(base[1], filesystem='xfs'), replace(base[1], backing_sha256='a'*64)):
            rows = (base[0], bad, base[2])
            with self.subTest(backing=(bad.filesystem, bad.backing_sha256)):
                # Do not let an unrelated runtime ownership refusal satisfy
                # this test: exercise the actual shared-device capacity guard.
                with self.assertRaises(GuardRefused) as error:
                    state.StandaloneHostSizeObservation((), {v.role: v.restore_work_bytes for v in rows},
                        min(v.free_bytes for v in rows), 100*1024**3, volume_storage=rows)
                self.assertEqual(error.exception.reason_codes, (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))

    def test_preoutage_inode_threshold_matches_actual_restore_capacity_guard(self):
        from deploy.update.standalone_backup import BackupPolicy
        from deploy.update.standalone_restore import StandaloneRestore, RestoreError
        required = 3 * 10001 + 1024
        for available in (5000, required - 1, required):
            volumes = self._inode_capacity_volumes(free_inodes=available)
            by_role = {v.role: v for v in volumes}
            restore = SimpleNamespace(_verified_inputs=True,
                inventory={"archives": {v.role: {"logical_bytes": v.logical_bytes, "members": v.members,
                    "members_bytes": 0} for v in volumes}},
                ledger=SimpleNamespace(directory=self.inst.root, checkpoint=lambda *args, **kwargs: None),
                _verify_stores=lambda: None, helpers=SimpleNamespace(assert_no_running_consumers=lambda key: None),
                storage_inspector=lambda key: by_role[key],
                b={"volumes": {v.role: {"Name": v.name} for v in volumes},
                   "volume_root_ids": {v.role: v.root_identity for v in volumes}},
                policy=BackupPolicy())
            with self.subTest(available=available):
                if available < required:
                    with self.assertRaises(GuardRefused) as error:
                        self._measure_inode_sizes(volumes)
                    self.assertEqual(error.exception.reason_codes, (ReasonCode.RESTORE_CAPACITY_INSUFFICIENT,))
                    with self.assertRaises(RestoreError) as error:
                        StandaloneRestore.preflight_capacity(restore)
                    self.assertEqual(str(error.exception), "restore_capacity_insufficient")
                else:
                    self._measure_inode_sizes(volumes)
                    self.assertEqual(set(StandaloneRestore.preflight_capacity(restore)), set(by_role))


class RendererTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "docker-compose.yml").write_text("name: fixture\n")
        self.raw = b"FUTURE_AGI_VERSION=v1.51.0\nSECRET=kept\n"
        (self.root / ".env").write_bytes(self.raw)
        self.commands = []
        def fake(args, **kwargs):
            self.commands.append((args, kwargs))
            model = {"name": "fixture", "services": {"app": {"image": "futureagi/standalone:" + read_env(self.root / ".env")["FUTURE_AGI_VERSION"]}}}
            return subprocess.CompletedProcess(args, 0, "app\n" if args[-1] == "--services" else json.dumps(model), "")
        self.inst = Installation(self.root, compose=["synthetic-compose"], environ={"DOCKER_CONTEXT": "fixture"}, runner=fake)
        self.addCleanup(patch.stopall)
        patch.object(state, "bounded_installation", side_effect=lambda obj, *args: obj).start()
        def render(inst, envpath, files):
            self.assertEqual(tuple(files), tuple(self.inst._ownership_files()))
            self.assertEqual(os.stat(envpath).st_mode & 0o777, 0o600)
            result = copy.deepcopy(inst.config(refresh=True))
            result["services"]["app"]["image"] = "futureagi/standalone:" + read_env(envpath)["FUTURE_AGI_VERSION"]
            return result
        self.render_patch = patch.object(state, "_render", side_effect=render)
        self.render_patch.start()

    def test_current_equals_orchestrator_prediction_equals_materialized_fixture(self):
        before = self.inst.env_file.stat()
        self.assertEqual(state.fingerprint_for_env(self.inst, self.raw), fingerprints(self.inst)[0])
        future = render_changes(self.raw.decode(), {"FUTURE_AGI_VERSION": "v1.52.0"}).encode()
        expected = state.fingerprint_for_env(self.inst, future)
        self.assertEqual(self.inst.env_file.read_bytes(), self.raw)
        self.assertEqual(self.inst.env_file.stat().st_mtime_ns, before.st_mtime_ns)
        self.inst.env_file.write_bytes(future)
        self.inst.reload_env()
        self.assertEqual(expected, fingerprints(self.inst)[0])
        self.assertTrue(all("config" in args for args, _ in self.commands))

    def test_reject_unrelated_changes_deletions_and_pin_injection(self):
        for raw in (self.raw.replace(b"kept", b"changed"), self.raw.replace(b"SECRET=kept\n", b""), self.raw.replace(b"v1.51.0", b"$(evil)"), self.raw + b"# rewrite\n"):
            with self.subTest(raw=raw), self.assertRaises(GuardRefused): state.fingerprint_for_env(self.inst, raw)

    def test_ambient_pin_override_or_overlay_refused(self):
        self.inst.environ["FUTURE_AGI_VERSION"] = "v1.51.0"
        with self.assertRaises(GuardRefused): state.fingerprint_for_env(self.inst, self.raw)
        self.inst.environ.pop("FUTURE_AGI_VERSION")
        self.inst.env_overlay["SECRET"] = "changed"
        with self.assertRaises(GuardRefused): state.fingerprint_for_env(self.inst, self.raw)

    def test_current_reconstruction_must_match_real_model(self):
        self.render_patch.stop()
        with patch.object(state, "_render", return_value={"different": True}), self.assertRaises(GuardRefused):
            state.fingerprint_for_env(self.inst, self.raw)


if __name__ == "__main__":
    unittest.main()
