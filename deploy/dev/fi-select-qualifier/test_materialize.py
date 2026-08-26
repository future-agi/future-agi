from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
QUALIFIER_DIR = REPO / "futureagi/scripts/fi_current_select_qualifier"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


materialize = _load_module("fi_dev_materialize", HERE / "materialize.py")
sys.modules["materialize"] = materialize
ssh_materialize = _load_module(
    "fi_dev_ssh_materialize", HERE / "materialize_ssh.py"
)
sys.path.insert(0, str(QUALIFIER_DIR))
try:
    apply_deletions = _load_module(
        "fi_apply_runtime_deletions",
        QUALIFIER_DIR / "apply_runtime_deletions.py",
    )
    assembler = _load_module(
        "fi_qualifier_assemble",
        QUALIFIER_DIR / "assemble.py",
    )
finally:
    sys.path.remove(str(QUALIFIER_DIR))


class MaterializerFixture(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="fi-materializer-test-")
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.config_path = self.root / "config.yaml"
        self.kubeconfig_path = self.root / "kubeconfig"
        self.gcloud_root = self.root / "gcloud"
        self.gcloud_configurations = self.gcloud_root / "configurations"
        self.gcloud_configurations.mkdir(parents=True)
        self.gcloud_active = self.gcloud_root / "active_config"
        self.run_id = "fi-dev-test-001"
        self.frozen_end = "2026-08-15T12:00:00Z"
        self.config_data = self._valid_config()
        self._write_config()
        self._write_local_target()
        self._write_bundle()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _valid_config() -> dict[str, object]:
        return {
            "format": materialize.FORMAT,
            "version": materialize.VERSION,
            "gcp": {
                "project": "futureagi-dev",
                "kube_context": "gke_futureagi-dev_us-central1_futureagi-dev",
            },
            "kubernetes": {
                "namespace": "fi-qualifier-dev",
                "service_account": "fi-qualifier-dev",
                "read_only_runtime_secret": "fi-qualifier-dev-readonly",
                "read_only_secret_contract_sha256": "1" * 64,
                "image_pull_secret": "fi-qualifier-dev-pull",
            },
            "image": {
                "derived": (
                    "us-docker.pkg.dev/futureagi-dev/fi-qualifier-dev/"
                    f"backend@sha256:{'2' * 64}"
                )
            },
            "catalog": {
                "database": "fi_catalog_dev_qualification",
                "workspace_allowlist": [
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000002",
                ],
            },
            "network": {
                "dns_namespace": "kube-system",
                "dns_pod_labels": {"k8s-app": "kube-dns"},
                "egress": [
                    {
                        "name": "catalog-clickhouse",
                        "purpose": "clickhouse_catalog",
                        "cidr": "10.20.0.12/32",
                        "ports": [9440],
                    },
                    {
                        "name": "source-clickhouse",
                        "purpose": "clickhouse_source",
                        "cidr": "10.20.0.11/32",
                        "ports": [9440],
                    },
                    {
                        "name": "source-postgresql",
                        "purpose": "postgresql",
                        "cidr": "10.20.0.10/32",
                        "ports": [5432],
                    },
                ],
            },
        }

    def _write_config(self) -> None:
        self.config_path.write_text(
            yaml.safe_dump(self.config_data, sort_keys=False),
            encoding="utf-8",
        )

    def _write_local_target(self) -> None:
        context = self.config_data["gcp"]["kube_context"]
        namespace = self.config_data["kubernetes"]["namespace"]
        kubeconfig = {
            "apiVersion": "v1",
            "kind": "Config",
            "current-context": context,
            "contexts": [
                {
                    "name": context,
                    "context": {"cluster": context, "namespace": namespace},
                }
            ],
            "clusters": [
                {
                    "name": context,
                    "cluster": {"server": "https://10.40.0.1"},
                }
            ],
        }
        self.kubeconfig_path.write_text(
            yaml.safe_dump(kubeconfig, sort_keys=False), encoding="utf-8"
        )
        self.gcloud_active.write_text("dev-qualifier\n", encoding="utf-8")
        (self.gcloud_configurations / "config_dev-qualifier").write_text(
            "[core]\nproject = futureagi-dev\n", encoding="utf-8"
        )

    @staticmethod
    def _job_template() -> str:
        return """apiVersion: batch/v1
kind: Job
metadata:
  name: fi-current-select-qualifier-__QUALIFIER_SHARD__
spec:
  activeDeadlineSeconds: 5400
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      containers:
        - name: qualify
          image: __DERIVED_IMAGE_DIGEST__
          command: ["python", "/harness/qualify.py"]
          envFrom:
            - secretRef:
                name: __READ_ONLY_RUNTIME_SECRET__
          env: []
          volumeMounts: []
      volumes: []
"""

    def _write_bundle(self, source_overrides: dict[str, object] | None = None) -> None:
        source_manifest: dict[str, object] = {
            "schema": materialize.QUALIFIER_SCHEMA,
            "base_commit": "a" * 40,
            "base_image": f"registry.invalid/backend@sha256:{'b' * 64}",
            "runtime_files": {"keep.py": "3" * 64},
            "runtime_deletions": ["deleted.py"],
            "runtime_deletion_base_sha256": {"deleted.py": "4" * 64},
        }
        source_manifest.update(source_overrides or {})
        source_bytes = json.dumps(
            source_manifest, sort_keys=True, separators=(",", ":")
        ).encode("utf-8") + b"\n"
        artifacts = {
            "Dockerfile": b"FROM scratch\n",
            "harness.tar": b"harness",
            "job.yaml.template": self._job_template().encode("utf-8"),
            "runtime-overlay.tar": b"overlay",
            "source-manifest.json": source_bytes,
        }
        for name, payload in artifacts.items():
            (self.bundle / name).write_bytes(payload)
        bundle_manifest = {
            "schema": f"{materialize.QUALIFIER_SCHEMA}/bundle",
            "base_commit": source_manifest["base_commit"],
            "base_image": source_manifest["base_image"],
            "source_manifest_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "qualifier_sha256": "5" * 64,
            "artifacts": {
                name: hashlib.sha256(payload).hexdigest()
                for name, payload in artifacts.items()
            },
        }
        (self.bundle / "bundle-manifest.json").write_text(
            json.dumps(bundle_manifest, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )

    def _loaded(self):
        config = materialize.load_config(self.config_path)
        bundle_manifest, source_manifest = materialize.load_bundle(self.bundle)
        target = materialize.verify_local_target(
            config,
            kubeconfig_path=self.kubeconfig_path,
            gcloud_active_config_path=self.gcloud_active,
            gcloud_configurations_dir=self.gcloud_configurations,
        )
        return config, bundle_manifest, source_manifest, target

    def _documents(self):
        config, bundle_manifest, _source_manifest, target = self._loaded()
        return config, bundle_manifest, materialize.materialize(
            bundle=self.bundle,
            config=config,
            bundle_manifest=bundle_manifest,
            local_target=target,
            shard="whatfix",
            run_id=self.run_id,
            frozen_end=self.frozen_end,
            prior_result_chain_sha256=hashlib.sha256(b"").hexdigest(),
        )

    def test_success_is_one_hardened_job_and_separate_prerequisites(self) -> None:
        config, _bundle_manifest, documents = self._documents()
        self.assertEqual(
            [document["kind"] for document in documents],
            ["ServiceAccount", "NetworkPolicy", "NetworkPolicy", "Job"],
        )
        self.assertTrue(
            all(document["metadata"]["namespace"] == config.namespace for document in documents)
        )
        service_account, default_deny, allowlist, job = documents
        self.assertFalse(service_account["automountServiceAccountToken"])
        self.assertEqual(default_deny["spec"]["ingress"], [])
        self.assertEqual(default_deny["spec"]["egress"], [])
        self.assertEqual(len(allowlist["spec"]["egress"]), 4)
        self.assertEqual(job["metadata"]["name"], materialize.WORKLOAD_NAME)
        self.assertEqual(job["spec"]["parallelism"], 1)
        self.assertEqual(job["spec"]["completions"], 1)
        pod = job["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(pod["serviceAccountName"], config.service_account)
        container = pod["containers"][0]
        self.assertEqual(container["image"], config.derived_image)
        env = {row["name"]: row["value"] for row in container["env"]}
        self.assertEqual(env["PROPERTY_CATALOG_READ_MODE"], "read")
        self.assertEqual(env["PROPERTY_CATALOG_DATABASE"], config.catalog_database)
        self.assertEqual(env["PROPERTY_CATALOG_CH_DATABASE"], config.catalog_database)
        self.assertEqual(env["PROPERTY_CATALOG_DEV_RECONCILE_ENABLED"], "false")
        self.assertEqual(env["PROPERTY_CATALOG_DEV_WRITE_CH_HOST"], "")
        self.assertEqual(env["PROPERTY_CATALOG_DEV_WRITE_CH_PORT"], "")
        self.assertEqual(env["QUALIFIER_AUTH_MODE"], "direct_existing_principal")
        self.assertEqual(env["QUALIFIER_SOS_FORBIDDEN"], "true")
        self.assertFalse(any(document["kind"] == "Secret" for document in documents))

        output = self.root / "materialized"
        materialize._atomic_write_directory(output, documents)
        self.assertEqual(
            sorted(path.name for path in output.iterdir()),
            ["00-prerequisites.yaml", "10-job.yaml"],
        )
        prerequisites = list(
            yaml.safe_load_all((output / "00-prerequisites.yaml").read_text())
        )
        jobs = list(yaml.safe_load_all((output / "10-job.yaml").read_text()))
        self.assertEqual([row["kind"] for row in prerequisites], [
            "ServiceAccount",
            "NetworkPolicy",
            "NetworkPolicy",
        ])
        self.assertEqual([row["kind"] for row in jobs], ["Job"])
        self.assertEqual(
            stat.S_IMODE((output / "10-job.yaml").stat().st_mode), 0o600
        )
        with self.assertRaisesRegex(materialize.MaterializationError, "already exists"):
            materialize._atomic_write_directory(output, documents)

    def test_production_and_reusable_inputs_fail_closed(self) -> None:
        cases = (
            (("gcp", "project"), "futureagiproduction", "DEV token"),
            (("gcp", "project"), "futureagi-prod", "production/live"),
            (("kubernetes", "namespace"), "default", "DEV token"),
            (
                ("kubernetes", "read_only_runtime_secret"),
                "core-backend-dev-secret",
                "purpose-built",
            ),
            (("image", "derived"), "registry.invalid/dev/backend:latest", "digest pinned"),
        )
        for path, value, error in cases:
            with self.subTest(path=path, value=value):
                changed = copy.deepcopy(self.config_data)
                changed[path[0]][path[1]] = value
                if path == ("gcp", "project"):
                    changed["gcp"]["kube_context"] = f"gke_{value}_zone_cluster-dev"
                self.config_data = changed
                self._write_config()
                with self.assertRaisesRegex(materialize.MaterializationError, error):
                    materialize.load_config(self.config_path)
                self.config_data = self._valid_config()

    def test_network_allowlist_rejects_broad_unsafe_and_wrong_purpose_ports(self) -> None:
        cases = (
            ("10.20.0.0/24", [9440], "safe IPv4 /32"),
            ("169.254.1.1/32", [9440], "safe IPv4 /32"),
            ("10.20.0.12/32", [22], "not valid for its database purpose"),
        )
        for cidr, ports, error in cases:
            with self.subTest(cidr=cidr, ports=ports):
                self.config_data = self._valid_config()
                self.config_data["network"]["egress"][0]["cidr"] = cidr
                self.config_data["network"]["egress"][0]["ports"] = ports
                self._write_config()
                with self.assertRaisesRegex(materialize.MaterializationError, error):
                    materialize.load_config(self.config_path)

    def test_local_context_project_namespace_and_api_endpoint_are_exact(self) -> None:
        config = materialize.load_config(self.config_path)
        self.kubeconfig_path.write_text(
            self.kubeconfig_path.read_text().replace(
                "current-context: gke_futureagi-dev_us-central1_futureagi-dev",
                "current-context: gke_futureagiprimary_zone_production",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(materialize.MaterializationError, "current kube"):
            materialize.verify_local_target(
                config,
                kubeconfig_path=self.kubeconfig_path,
                gcloud_active_config_path=self.gcloud_active,
                gcloud_configurations_dir=self.gcloud_configurations,
            )
        self._write_local_target()
        (self.gcloud_configurations / "config_dev-qualifier").write_text(
            "[core]\nproject = futureagiprimary\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(materialize.MaterializationError, "gcloud project"):
            materialize.verify_local_target(
                config,
                kubeconfig_path=self.kubeconfig_path,
                gcloud_active_config_path=self.gcloud_active,
                gcloud_configurations_dir=self.gcloud_configurations,
            )
        self._write_local_target()
        self.config_data["network"]["egress"][0]["cidr"] = "10.40.0.1/32"
        self._write_config()
        config = materialize.load_config(self.config_path)
        with self.assertRaisesRegex(materialize.MaterializationError, "kube API"):
            materialize.verify_local_target(
                config,
                kubeconfig_path=self.kubeconfig_path,
                gcloud_active_config_path=self.gcloud_active,
                gcloud_configurations_dir=self.gcloud_configurations,
            )

    def test_bundle_deletion_contract_rejects_overlap_unsafe_and_hash_drift(self) -> None:
        cases = (
            (
                {
                    "runtime_files": {"deleted.py": "3" * 64},
                    "runtime_deletions": ["deleted.py"],
                    "runtime_deletion_base_sha256": {"deleted.py": "4" * 64},
                },
                "overlap",
            ),
            (
                {
                    "runtime_deletions": ["../deleted.py"],
                    "runtime_deletion_base_sha256": {"../deleted.py": "4" * 64},
                },
                "entry is invalid",
            ),
            (
                {
                    "runtime_deletions": ["deleted.py"],
                    "runtime_deletion_base_sha256": {},
                },
                "hash map is not exact",
            ),
        )
        for overrides, error in cases:
            with self.subTest(error=error):
                self._write_bundle(overrides)
                with self.assertRaisesRegex(materialize.MaterializationError, error):
                    materialize.load_bundle(self.bundle)
        self._write_bundle()
        (self.bundle / "runtime-overlay.tar").write_bytes(b"tampered")
        with self.assertRaisesRegex(materialize.MaterializationError, "failed verification"):
            materialize.load_bundle(self.bundle)

    def _prior_result(self, shard_index: int) -> dict[str, object]:
        config, bundle_manifest, _source_manifest, _target = self._loaded()
        return {
            "schema": materialize.QUALIFIER_SCHEMA,
            "run_id": self.run_id,
            "frozen_end": datetime.strptime(
                self.frozen_end, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=UTC).isoformat(),
            "qualified": True,
            "exit_code": 0,
            "shard": materialize.QUALIFIER_SHARDS[shard_index],
            "shard_index": shard_index,
            "shard_count": len(materialize.QUALIFIER_SHARDS),
            "source_identity": {
                "base_commit": bundle_manifest["base_commit"],
                "derived_image_digest": config.derived_image,
                "source_manifest_sha256": bundle_manifest["source_manifest_sha256"],
                "qualifier_sha256": bundle_manifest["qualifier_sha256"],
            },
            "counts": {
                "pg_blocked": 0,
                "ch_blocked": 0,
                "redis_blocked": 0,
                "celery_blocked": 0,
                "temporal_blocked": 0,
                "scheduler_blocked": 0,
                "external_cache_blocked": 0,
            },
        }

    def test_prior_results_enforce_exact_green_sequence_and_source(self) -> None:
        config, bundle_manifest, _source_manifest, _target = self._loaded()
        paths = []
        for index in range(2):
            path = self.root / f"result-{index}.json"
            path.write_text(json.dumps(self._prior_result(index)), encoding="utf-8")
            paths.append(path)
        digest = materialize.validate_prior_results(
            paths,
            shard="mudflap",
            run_id=self.run_id,
            frozen_end=self.frozen_end,
            config=config,
            bundle_manifest=bundle_manifest,
        )
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        with self.assertRaisesRegex(materialize.MaterializationError, "precede"):
            materialize.validate_prior_results(
                paths[:1],
                shard="mudflap",
                run_id=self.run_id,
                frozen_end=self.frozen_end,
                config=config,
                bundle_manifest=bundle_manifest,
            )
        payload = self._prior_result(1)
        payload["counts"]["celery_blocked"] = 1
        paths[1].write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(materialize.MaterializationError, "tripwire"):
            materialize.validate_prior_results(
                paths,
                shard="mudflap",
                run_id=self.run_id,
                frozen_end=self.frozen_end,
                config=config,
                bundle_manifest=bundle_manifest,
            )


class PackagingSafetyTests(unittest.TestCase):
    def test_manifest_bound_runtime_deletion_only_removes_exact_regular_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fi-delete-test-") as raw:
            root = Path(raw) / "backend"
            root.mkdir()
            target = root / "tracer/deleted.py"
            target.parent.mkdir()
            target.write_bytes(b"pinned base content\n")
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            manifest = Path(raw) / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "runtime_deletions": ["tracer/deleted.py"],
                        "runtime_deletion_base_sha256": {
                            "tracer/deleted.py": digest
                        },
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                apply_deletions.apply_runtime_deletions(
                    manifest_path=manifest, backend_root=root
                ),
                1,
            )
            self.assertFalse(os.path.lexists(target))
            self.assertEqual(
                apply_deletions.apply_runtime_deletions(
                    manifest_path=manifest, backend_root=root
                ),
                0,
            )

            target.write_bytes(b"different image content\n")
            with self.assertRaisesRegex(
                apply_deletions.SafetyViolation, "content drifted"
            ):
                apply_deletions.apply_runtime_deletions(
                    manifest_path=manifest, backend_root=root
                )

    def test_runtime_deletion_refuses_symlink_parent_and_unsafe_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fi-delete-link-test-") as raw:
            root = Path(raw) / "backend"
            outside = Path(raw) / "outside"
            root.mkdir()
            outside.mkdir()
            (outside / "deleted.py").write_bytes(b"pinned\n")
            (root / "tracer").symlink_to(outside, target_is_directory=True)
            digest = hashlib.sha256(b"pinned\n").hexdigest()
            manifest = Path(raw) / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "runtime_deletions": ["tracer/deleted.py"],
                        "runtime_deletion_base_sha256": {
                            "tracer/deleted.py": digest
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                apply_deletions.SafetyViolation, "physical directory"
            ):
                apply_deletions.apply_runtime_deletions(
                    manifest_path=manifest, backend_root=root
                )
            manifest.write_text(
                json.dumps(
                    {
                        "runtime_deletions": ["../outside/deleted.py"],
                        "runtime_deletion_base_sha256": {
                            "../outside/deleted.py": digest
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(apply_deletions.SafetyViolation, "unsafe"):
                apply_deletions.apply_runtime_deletions(
                    manifest_path=manifest, backend_root=root
                )
            self.assertTrue((outside / "deleted.py").is_file())

    def test_assembler_binds_deleted_runtime_file_to_git_base_digest(self) -> None:
        payload = b"tracked base content\n"

        def fake_git(_repo: Path, *args: str) -> bytes:
            if args[0] == "ls-tree":
                return (
                    b"100644 blob "
                    + b"a" * 40
                    + b"\tfutureagi/tracer/deleted.py\0"
                )
            if args[0] == "show":
                return payload
            raise AssertionError(args)

        with mock.patch.object(assembler, "_git", side_effect=fake_git):
            deleted, runtime = assembler._deletion_inventory(
                Path("/unused"),
                {"futureagi/tracer/deleted.py": "D"},
            )
        digest = hashlib.sha256(payload).hexdigest()
        self.assertEqual(deleted, {"futureagi/tracer/deleted.py": digest})
        self.assertEqual(runtime, {"tracer/deleted.py": digest})

    def test_materializer_and_sync_exact_seam_have_no_launch_or_publish_path(self) -> None:
        materializer_source = (HERE / "materialize.py").read_text(encoding="utf-8")
        tree = ast.parse(materializer_source)
        imported_roots = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertNotIn("subprocess", imported_roots)
        self.assertNotIn("requests", imported_roots)
        self.assertNotIn("kubernetes", imported_roots)

        qualifier_source = (QUALIFIER_DIR / "qualify.py").read_text(encoding="utf-8")
        qualifier_tree = ast.parse(qualifier_source)
        seam = next(
            node
            for node in qualifier_tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_select_only_exact_snapshot"
        )
        seam_source = ast.unparse(seam)
        self.assertIn("_load_exact_payload", seam_source)
        for forbidden in (
            "read_or_schedule_exact_snapshot",
            "publish_exact_snapshot",
            "apply_async",
            "delay(",
        ):
            self.assertNotIn(forbidden, seam_source)
        install_source = ast.unparse(
            next(
                node
                for node in qualifier_tree.body
                if isinstance(node, ast.FunctionDef)
                and node.name == "_install_dispatch_tripwires"
            )
        )
        self.assertIn(
            "graph_dispatch.read_or_schedule_exact_snapshot = _select_only_exact_snapshot",
            install_source,
        )
        self.assertIn(
            "session_graph.read_or_schedule_exact_snapshot = _select_only_exact_snapshot",
            install_source,
        )
        self.assertIn(
            "dashboard.read_or_schedule_exact_snapshot = _select_only_exact_snapshot",
            install_source,
        )


class SSHMaterializerFixture(MaterializerFixture):
    def setUp(self) -> None:
        super().setUp()
        self.ssh_config_path = self.root / "ssh_config"
        self.known_hosts_path = self.root / "known_hosts"
        self.identity_path = self.root / "qualifier-dev-key"
        self.identity_path.write_text("test-only-private-key\n", encoding="utf-8")
        self.identity_path.chmod(0o600)
        self.hostname = "ec2-98-92-229-253.compute-1.amazonaws.com"
        self.alias = "nv-dev-nik"
        self.host_key_bytes = b"offline-test-ed25519-host-key"
        self.host_key_sha256 = hashlib.sha256(self.host_key_bytes).hexdigest()
        import base64

        self.known_hosts_path.write_text(
            f"{self.hostname} ssh-ed25519 "
            f"{base64.b64encode(self.host_key_bytes).decode()}\n",
            encoding="utf-8",
        )
        self.ssh_config_path.write_text(
            f"Host {self.alias}\n"
            f"  HostName {self.hostname}\n"
            "  User ubuntu\n"
            "  IdentitiesOnly yes\n"
            f"  IdentityFile {self.identity_path}\n",
            encoding="utf-8",
        )
        self.env_file = (
            "/home/ubuntu/fi-qualifier-dev/"
            "fi-qualifier-dev-readonly.env"
        )
        self.env_contract_path = self.root / "env-contract.json"
        self.egress_attestation_path = self.root / "egress-attestation.json"
        self.host_config_path = self.root / "host-config.yaml"
        self.host_config_data = self._host_config()
        self._write_host_contracts_and_config()

    def _egress(self) -> list[dict[str, object]]:
        return copy.deepcopy(self._valid_config()["network"]["egress"])

    def _host_config(self) -> dict[str, object]:
        return {
            "format": ssh_materialize.FORMAT,
            "version": ssh_materialize.VERSION,
            "host": {
                "alias": self.alias,
                "hostname": self.hostname,
                "user": "ubuntu",
                "port": 22,
                "host_key_sha256": self.host_key_sha256,
                "remote_workdir": "/home/ubuntu/fi-qualifier-dev",
            },
            "runtime": {
                "container_runtime": "docker",
                "container_network": "fi-qualifier-dev-readonly",
                "read_only_env_file": self.env_file,
                "read_only_env_contract_sha256": "f" * 64,
            },
            "image": copy.deepcopy(self._valid_config()["image"]),
            "catalog": copy.deepcopy(self._valid_config()["catalog"]),
            "network": {
                "egress_attestation_sha256": "e" * 64,
                "egress": self._egress(),
            },
        }

    def _write_host_contracts_and_config(self) -> None:
        env_contract = {
            "schema": f"{ssh_materialize.FORMAT}/read-only-env-contract/v1",
            "env_file": self.env_file,
            "keys": sorted(ssh_materialize._REQUIRED_SECRET_KEYS),
            "assertions": dict(ssh_materialize._SECRET_ASSERTIONS),
        }
        env_bytes = json.dumps(
            env_contract, sort_keys=False, separators=(",", ":")
        ).encode() + b"\n"
        self.env_contract_path.write_bytes(env_bytes)
        egress_contract = {
            "schema": f"{ssh_materialize.FORMAT}/host-egress-attestation/v1",
            "host_alias": self.alias,
            "default_deny": True,
            "dns_restricted": True,
            "egress": self._egress(),
        }
        egress_bytes = json.dumps(
            egress_contract, sort_keys=False, separators=(",", ":")
        ).encode() + b"\n"
        self.egress_attestation_path.write_bytes(egress_bytes)
        self.host_config_data["runtime"]["read_only_env_contract_sha256"] = (
            hashlib.sha256(env_bytes).hexdigest()
        )
        self.host_config_data["network"]["egress_attestation_sha256"] = (
            hashlib.sha256(egress_bytes).hexdigest()
        )
        self.host_config_path.write_text(
            yaml.safe_dump(self.host_config_data, sort_keys=False), encoding="utf-8"
        )

    def test_ssh_plan_is_source_bound_hardened_and_does_not_assume_kubernetes(self) -> None:
        config = ssh_materialize.load_config(self.host_config_path)
        target = ssh_materialize.verify_ssh_target(
            config,
            ssh_config_path=self.ssh_config_path,
            known_hosts_path=self.known_hosts_path,
        )
        ssh_materialize.verify_env_contract(self.env_contract_path, config)
        ssh_materialize.verify_egress_attestation(
            self.egress_attestation_path, config
        )
        bundle_manifest, _source = materialize.load_bundle(self.bundle)
        plan = ssh_materialize.materialize_plan(
            config,
            bundle_manifest,
            target,
            shard="whatfix",
            run_id=self.run_id,
            frozen_end=self.frozen_end,
            prior_chain=hashlib.sha256(b"").hexdigest(),
        )
        self.assertFalse(plan["launch_authorized"])
        self.assertEqual(plan["action"], "review_only_no_execution")
        self.assertEqual(plan["source"]["derived_image"], config.derived_image)
        argv = plan["container_argv"]
        self.assertEqual(argv[:2], ["docker", "run"])
        for value in (
            "--pull=never",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--network=fi-qualifier-dev-readonly",
            f"--env-file={self.env_file}",
        ):
            self.assertIn(value, argv)
        self.assertEqual(argv[-1], config.derived_image)
        env = plan["environment"]
        self.assertEqual(env["PROPERTY_CATALOG_READ_MODE"], "read")
        self.assertEqual(env["PROPERTY_CATALOG_DEV_RECONCILE_ENABLED"], "false")
        self.assertEqual(env["QUALIFIER_SOS_FORBIDDEN"], "true")
        self.assertEqual(env["QUALIFIER_EXECUTION_TARGET"], "ssh-host")
        serialized = json.dumps(plan).lower()
        self.assertNotIn("kubernetes", serialized)
        self.assertNotIn("serviceaccount", serialized)

    def test_ssh_host_key_and_alias_production_drift_fail_closed(self) -> None:
        self.known_hosts_path.write_text(
            self.known_hosts_path.read_text().replace(
                "offline-test-ed25519-host-key", "not-present"
            ),
            encoding="utf-8",
        )
        # Replacing the decoded phrase does not alter base64, so alter the pin.
        changed = copy.deepcopy(self.host_config_data)
        changed["host"]["host_key_sha256"] = "9" * 64
        self.host_config_path.write_text(
            yaml.safe_dump(changed, sort_keys=False), encoding="utf-8"
        )
        changed_config = ssh_materialize.load_config(self.host_config_path)
        with self.assertRaisesRegex(materialize.MaterializationError, "host-key"):
            ssh_materialize.verify_ssh_target(
                changed_config,
                ssh_config_path=self.ssh_config_path,
                known_hosts_path=self.known_hosts_path,
            )
        changed = copy.deepcopy(self.host_config_data)
        changed["host"]["alias"] = "production-host"
        self.host_config_path.write_text(
            yaml.safe_dump(changed, sort_keys=False), encoding="utf-8"
        )
        with self.assertRaisesRegex(materialize.MaterializationError, "production/live"):
            ssh_materialize.load_config(self.host_config_path)

    def test_ssh_contract_rejects_broker_or_sos_capable_env_keys(self) -> None:
        payload = json.loads(self.env_contract_path.read_text())
        payload["keys"].append("CELERY_BROKER_URL")
        payload["keys"].sort()
        raw = json.dumps(payload, sort_keys=False, separators=(",", ":")).encode() + b"\n"
        self.env_contract_path.write_bytes(raw)
        changed = copy.deepcopy(self.host_config_data)
        changed["runtime"]["read_only_env_contract_sha256"] = hashlib.sha256(raw).hexdigest()
        self.host_config_path.write_text(
            yaml.safe_dump(changed, sort_keys=False), encoding="utf-8"
        )
        changed_config = ssh_materialize.load_config(self.host_config_path)
        with self.assertRaisesRegex(materialize.MaterializationError, "key inventory"):
            ssh_materialize.verify_env_contract(
                self.env_contract_path, changed_config
            )


if __name__ == "__main__":
    unittest.main()
