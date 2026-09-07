"""Offline application-lane safety contracts; no Docker or database access."""

import json
import os
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock, patch

import application_lane as lane
from application_base import BASE_IMAGE, verified
from harness import GIB, canonical, digest, save_new


def manifest():
    return {
        "lane": lane.LANE,
        "run_id": "0123456789abcdef",
        "project": "pcapplication-0123456789abcdef",
        "token": "a" * 64,
        "password": "b" * 64,
        "owner_uid": os.getuid(),
        "owner_gid": os.getgid(),
        "database": "th7247_catalog_prod_0123456789abcdef",
        "candidate_topic": "pcapplication-0123456789abcdef.candidates.v2",
        "ordered_topic": "pcapplication-0123456789abcdef.ordered.v1",
        "files": {},
        "source_files": {},
    }


class PlanTests(unittest.TestCase):
    def test_three_direct_members_one_keeper_no_ports_private_linux_runner(self):
        m = manifest()
        doc = lane.configuration(m)
        self.assertEqual(
            set(doc["services"]),
            {*lane.NODES, "keeper", "postgres", "kafka", "redis", "runner"},
        )
        self.assertEqual(
            sum(s["mem_limit"] for s in doc["services"].values()), int(14.625 * GIB)
        )
        self.assertEqual(sum(float(s["cpus"]) for s in doc["services"].values()), 9)
        self.assertEqual(
            {name: service["cpus"] for name, service in doc["services"].items()},
            {
                "replica1": "1.5",
                "replica2": "1.5",
                "replica3": "1.5",
                "keeper": "0.25",
                "postgres": "0.5",
                "kafka": "1.5",
                "redis": "0.25",
                "runner": "2",
            },
        )
        self.assertTrue(doc["networks"]["default"]["internal"])
        for service in doc["services"].values():
            self.assertEqual(service["ports"], [])
            self.assertEqual(service["pull_policy"], "never")
            self.assertEqual(service["platform"], "linux/arm64")
            self.assertEqual(service["mem_limit"], service["memswap_limit"])
            self.assertEqual(service["restart"], "no")
            self.assertEqual(service["pids_limit"], 512)
            self.assertNotIn("build", service)
            self.assertNotIn("privileged", service)
            self.assertNotIn("env_file", service)
            self.assertNotIn("network_mode", service)
        runner = doc["services"]["runner"]
        self.assertEqual(runner["image"], BASE_IMAGE)
        self.assertTrue(runner["read_only"])
        self.assertIn(
            "/fixture/backend/tfc/logs:rw,nosuid,noexec,size=67108864,"
            f"uid={os.getuid()},gid={os.getgid()},mode=0700",
            runner["tmpfs"],
        )
        self.assertEqual(runner["cap_drop"], ["ALL"])
        self.assertEqual(runner["user"], f"{os.getuid()}:{os.getgid()}")
        self.assertTrue(all("docker.sock" not in v for v in runner["volumes"]))
        self.assertEqual(runner["entrypoint"][2], "/fixture/backend/" + lane.HELPER)
        self.assertEqual(
            doc["services"]["kafka"]["environment"]["KAFKA_ADVERTISED_LISTENERS"],
            "INTERNAL://kafka:9092",
        )

    def test_kafka_image_volumes_are_exact_container_private_sized_tmpfs(self):
        doc = lane.configuration(manifest())
        kafka = doc["services"]["kafka"]
        mounts = dict(value.split(":", 1) for value in kafka["tmpfs"])
        # Actual VOLUME inventory observed from the pinned cached image config.
        self.assertEqual(
            set(mounts),
            {"/etc/kafka/secrets", "/mnt/shared/config", "/var/lib/kafka/data"},
        )
        self.assertEqual(mounts, lane.KAFKA_TMPFS)
        capacities = {}
        for path, options in mounts.items():
            parsed = dict(
                option.split("=", 1) for option in options.split(",") if "=" in option
            )
            capacities[path] = int(parsed["size"])
            self.assertTrue(
                {"rw", "nosuid", "nodev", "noexec"}.issubset(options.split(","))
            )
            self.assertEqual(parsed["mode"], "1777")
        self.assertEqual(
            capacities,
            {
                "/etc/kafka/secrets": 1 << 20,
                "/mnt/shared/config": 1 << 20,
                "/var/lib/kafka/data": 256 << 20,
            },
        )
        self.assertEqual(sum(capacities.values()), 258 << 20)
        self.assertEqual(kafka["environment"]["KAFKA_LOG_DIRS"], "/var/lib/kafka/data")
        self.assertNotIn("volumes", kafka)
        self.assertEqual(len(doc["volumes"]), 5)
        self.assertEqual(kafka["mem_limit"], 3 * GIB // 2)
        self.assertEqual(kafka["memswap_limit"], kafka["mem_limit"])

    def test_real_three_node_inventory_and_unmodified_public_production_renderer(self):
        from tracer.services.clickhouse.v2.catalog_prod_schema import (
            render_catalog_prod_schema,
        )

        m = manifest()
        for name in lane.NODES:
            root = ET.fromstring(lane.xml_files(m)[name + ".xml"])
            replicas = root.findall("remote_servers/smoke_cluster/shard/replica")
            self.assertEqual([r.findtext("host") for r in replicas], list(lane.NODES))
            self.assertEqual([r.findtext("port") for r in replicas], ["9000"] * 3)
            self.assertEqual(root.findtext("macros/replica"), name)
            self.assertEqual(root.findtext("zookeeper/node/host"), "keeper")
            self.assertIsNone(root.find("keeper_server"))
        rendered = render_catalog_prod_schema(
            target_database=m["database"],
            cluster="smoke_cluster",
            keeper_path_prefix="/clickhouse/tables",
        )
        bundle = lane.schema(m)
        self.assertEqual(bundle["statements"], list(rendered.statements))
        self.assertEqual(bundle["sha256"], rendered.manifest_sha256)
        self.assertEqual(len(rendered.tables), 7)
        for table in rendered.tables:
            self.assertIn("ON CLUSTER", table.sql)
            self.assertIn("ENGINE = Replicated", table.sql)

    def test_rejects_scope_paths_credential_injection_and_root_runner(self):
        for changed in (
            {"owner_uid": 0},
            {"owner_uid": True},
            {"project": "th7247-native-igazce"},
            {"database": "property_catalog"},
            {"password": "x'; DROP"},
            {"source_files": {"outside/a": "a" * 64}},
            {"source_files": {"futureagi/.env": "a" * 64}},
            {"source_files": {"futureagi/a/../b": "a" * 64}},
            {"source_files": {"futureagi/runs/a": "a" * 64}},
        ):
            with (
                self.subTest(changed=changed),
                self.assertRaises((ValueError, TypeError)),
            ):
                lane.validate_manifest({**manifest(), **changed})

    def test_plan_and_reload_offline_cleanup_independent_of_changed_product(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(lane, "RUNS", Path(tmp).resolve()),
            patch.object(lane, "source_files", return_value={}),
            patch(
                "subprocess.run",
                side_effect=AssertionError("no Docker/build/network during planning"),
            ),
        ):
            directory, m = lane.plan()
            self.assertEqual(lane.load(directory), m)
            with patch.object(
                lane, "schema", side_effect=RuntimeError("parent product now changed")
            ):
                self.assertEqual(lane.load(directory, sources=False), m)
            doc = lane.configuration(m)
            doc["services"]["runner"]["volumes"].append("/foreign:/foreign")
            raw = canonical(doc)
            (directory / "compose.json").write_bytes(raw)
            m["files"]["compose.json"] = digest(raw)
            (directory / "manifest.json").write_bytes(canonical(m))
            with self.assertRaisesRegex(ValueError, "plan bytes changed"):
                lane.load(directory)

    def test_registry_digest_is_not_a_runtime_version(self):
        self.assertEqual(
            verified(b"immutable", "sha256:" + digest(b"immutable")), b"immutable"
        )
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            verified(b"different", "sha256:" + digest(b"immutable"))


class ExecutionSafetyTests(unittest.TestCase):
    def docker_for_preflight(
        self, *, existing_cpu=1_000_000_000, docker_cpus=10, kafka_extra_volume=False
    ):
        m = manifest()
        docker = lane.ApplicationDocker(Path("/unused"), m)
        docker.command = Mock(
            side_effect=[
                Mock(stdout=json.dumps({"MemTotal": 20927102976, "NCPU": docker_cpus})),
                Mock(stdout="exact-existing-protected-id"),
                Mock(
                    stdout=json.dumps(
                        [{"HostConfig": {"Memory": 2 * GIB, "NanoCpus": existing_cpu}}]
                    )
                ),
            ]
        )
        image_config = {
            "clickhouse": {"/var/lib/clickhouse": {}},
            "postgres": {"/var/lib/postgresql/data": {}},
            "kafka": dict.fromkeys(lane.KAFKA_TMPFS, {}),
            "redis": {"/data": {}},
            "runner": {},
        }
        if kafka_extra_volume:
            image_config["kafka"]["/unexpected"] = {}

        def inspect(kind, reference):
            if kind != "image":
                return None
            image = next(k for k, v in lane.IMAGES.items() if v == reference)
            return {
                "Id": "exact-" + image,
                "Os": "linux",
                "Architecture": "arm64",
                "Config": {"Volumes": image_config[image]},
            }

        docker.inspect = Mock(side_effect=inspect)
        return docker

    def test_cpu_memory_and_real_image_volume_inventory_fit_after_parent_cleanup(self):
        docker = self.docker_for_preflight()
        report = docker.preflight(manifest()["run_id"])
        self.assertEqual(report["requested_cpu_nanos"], 9_000_000_000)
        self.assertEqual(report["existing_cpu_nanos"], [1_000_000_000])
        self.assertEqual(report["docker_cpus"], 10)
        self.assertEqual(
            report["requested_cap"] + sum(report["existing_caps"]) + report["reserve"],
            int(18.625 * GIB),
        )
        self.assertEqual(
            [c.args[0][0] for c in docker.command.call_args_list],
            ["info", "ps", "container"],
        )

    def test_cpu_oversubscription_or_unknown_existing_cap_rejects_before_image_checks(
        self,
    ):
        for cap, total in ((2_000_000_000, 10), (0, 10), (1_000_000_000, 9)):
            with self.subTest(cap=cap, total=total):
                docker = self.docker_for_preflight(existing_cpu=cap, docker_cpus=total)
                with self.assertRaisesRegex(RuntimeError, "CPU headroom"):
                    docker.preflight(manifest()["run_id"])
                self.assertFalse(
                    any(c.args[0] == "image" for c in docker.inspect.call_args_list)
                )

    def test_new_image_volume_cannot_silently_allocate_unowned_storage(self):
        docker = self.docker_for_preflight(kafka_extra_volume=True)
        with self.assertRaisesRegex(RuntimeError, "unplanned anonymous volumes"):
            docker.preflight(manifest()["run_id"])

    def test_runtime_rejects_kafka_size_or_mount_type_changes(self):
        m = manifest()
        spec = lane.configuration(m)["services"]["kafka"]
        row = {
            "Config": {"Labels": {"com.docker.compose.service": "kafka"}},
            "State": {"Running": True},
            "RestartCount": 0,
            "HostConfig": {
                "Memory": spec["mem_limit"],
                "MemorySwap": spec["memswap_limit"],
                "NanoCpus": 1_500_000_000,
                "PidsLimit": 512,
                "SecurityOpt": ["no-new-privileges"],
                "Tmpfs": dict(lane.KAFKA_TMPFS),
            },
            "NetworkSettings": {
                "Ports": {},
                "Networks": {m["project"] + "-network": {}},
            },
        }
        docker = lane.ApplicationDocker(Path("/unused"), m)
        docker.owned = Mock(return_value=[("container", m["project"] + "-kafka", row)])
        docker.verify_running()
        for replacement in (
            {},
            {**lane.KAFKA_TMPFS, "/var/lib/kafka/data": "rw,size=536870912,mode=1777"},
        ):
            row["HostConfig"]["Tmpfs"] = replacement
            with self.assertRaisesRegex(RuntimeError, "tmpfs paths or size caps"):
                docker.verify_running()

    def test_budget_confirmation_precedes_every_command_and_compile(self):
        docker = lane.ApplicationDocker(Path("/unused"), manifest())
        docker.command = Mock(side_effect=AssertionError("not authorized"))
        with self.assertRaisesRegex(ValueError, "confirmation"):
            docker.preflight("other")
        with (
            patch("subprocess.run", side_effect=AssertionError("not authorized")),
            self.assertRaisesRegex(ValueError, "confirmation"),
        ):
            lane.prepare(Path("/unused"), manifest(), "other")
        docker.command.assert_not_called()

    def test_insufficient_headroom_never_mutates_or_inspects_image(self):
        m = manifest()
        docker = lane.ApplicationDocker(Path("/unused"), m)
        docker.inspect = Mock(return_value=None)
        docker.command = Mock(
            side_effect=[
                Mock(stdout=json.dumps({"MemTotal": 8 * GIB})),
                Mock(stdout=""),
            ]
        )
        with self.assertRaisesRegex(RuntimeError, "headroom"):
            docker.preflight(m["run_id"])
        self.assertTrue(
            all(c.args[0] != "image" for c in docker.inspect.call_args_list)
        )
        self.assertEqual(
            [c.args[0][0] for c in docker.command.call_args_list], ["info", "ps"]
        )

    def test_compile_is_explicit_offline_cross_build_with_no_image_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bin").mkdir()
            (root / "go-source").mkdir()

            def compile(argv, **kwargs):
                self.assertEqual(argv[:2], ["go", "build"])
                env = kwargs["env"]
                self.assertEqual(
                    (env["GOOS"], env["GOARCH"], env["CGO_ENABLED"]),
                    ("linux", "arm64", "0"),
                )
                self.assertEqual((env["GOPROXY"], env["GOSUMDB"]), ("off", "off"))
                Path(argv[3]).write_bytes(b"synthetic executable fixture")

            with patch("subprocess.run", side_effect=compile) as command:
                lane.prepare(root, manifest(), manifest()["run_id"])
            self.assertEqual(command.call_count, 3)
            self.assertEqual(len(json.loads((root / "binaries.json").read_bytes())), 3)

    def test_failed_start_and_failed_log_collection_still_attempt_exact_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bin").mkdir()
            binaries = {}
            for name in (
                "fi-collector",
                "fi-property-catalog-consumer",
                "fi-property-catalog-sequencer",
            ):
                (root / "bin" / name).write_bytes(b"synthetic")
                binaries[name] = digest(b"synthetic")
            save_new(root / "binaries.json", binaries)
            docker = Mock()
            docker.preflight.return_value = {"images": {}}
            docker.compose = []
            docker.command.side_effect = subprocess.TimeoutExpired("owned-start", 1)
            docker.owned.return_value = [
                ("container", "owned", {"Id": "exact-owned-id"})
            ]
            with (
                patch.object(lane, "ApplicationDocker", return_value=docker),
                self.assertRaises(subprocess.TimeoutExpired),
            ):
                lane.execute(root, manifest(), manifest()["run_id"])
            docker.cleanup.assert_called_once_with()
            self.assertTrue((root / "start-intent.json").is_file())


class KafkaEvidenceTests(unittest.TestCase):
    def records(self):
        import base64

        from application_evidence import EXPECTED_NAMES, ORDERED_NAMES, VALUE_FIELDS

        from tracer.services.clickhouse.v2.attribute_catalog_codec import (
            encode_catalog_scalar,
        )

        # Start from the cross-language fixture and let the real production
        # parser rederive all modified test identities; no product parser mocks.
        fixture_file = (
            lane.ROOT
            / "fi-collector/pkg/propertycatalog/testdata/wire_v1_fixtures.json"
        )
        sample = next(
            c
            for c in json.loads(fixture_file.read_bytes())["cases"]
            if c["name"] == "value"
        )
        document = json.loads(base64.b64decode(sample["wire_base64"]))
        chunk = document["payload"]["chunks"][0]
        row = json.loads(base64.b64decode(chunk["json_each_row"]))
        scalar = encode_catalog_scalar("initial")

        def compact(value):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()

        rows = [
            {
                **row,
                "attribute_key": name,
                "value_json": scalar.value_json,
                "value_fingerprint": scalar.fingerprint,
                "value_search_text_folded": "initial",
            }
            for name in sorted(EXPECTED_NAMES)
        ]
        ordered_rows = [
            value for value in rows if value["attribute_key"] in ORDERED_NAMES
        ]
        encoded = b"".join(compact(value) + b"\n" for value in ordered_rows)
        chunk.update(
            row_count=len(ordered_rows),
            encoded_sha256=digest(encoded),
            json_each_row=base64.b64encode(encoded).decode(),
        )
        document["payload"]["value_rows"] = len(ordered_rows)
        document["payload_sha256"] = digest(compact(document["payload"]))
        document["envelope_id"] = digest(
            compact({k: v for k, v in document.items() if k != "envelope_id"})
        )
        scope = {k: row[k] for k in ("organization_id", "workspace_id", "project_id")}
        candidate = {
            "format": "futureagi.property-catalog-candidate",
            "version": 2,
            **scope,
            "catalog_epoch": 0,
            "projection_version": 0,
            "gap_reasons": [],
            "values": [{k: r[k] for k in VALUE_FIELDS} for r in rows],
        }
        return [compact(candidate)], [compact(document)], scope

    def test_production_wire_parser_matches_actual_candidate_values(self):
        from application_evidence import match_records

        deliveries, values = match_records(*self.records())
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(len(values), 2)

    def test_missing_reconciliation_candidate_is_not_a_pass(self):
        from application_evidence import match_records

        candidates, ordered, scope = self.records()
        candidate = json.loads(candidates[0])
        candidate["values"] = [
            row for row in candidate["values"] if row["attribute_key"] != "otlp_late"
        ]
        with self.assertRaisesRegex(ValueError, "every pre/post restart"):
            match_records([json.dumps(candidate).encode()], ordered, scope)

    def test_cold_source_cannot_substitute_for_missing_or_corrupt_kafka_rows(self):
        from application_evidence import match_records

        candidates, ordered, scope = self.records()
        for records in (
            [],
            [ordered[0].replace(b'"payload_sha256":"', b'"payload_sha256":"f', 1)],
        ):
            with self.subTest(records=bool(records)), self.assertRaises(ValueError):
                match_records(candidates, records, scope)
        with self.assertRaisesRegex(ValueError, "managed tenant"):
            match_records(candidates, ordered, {**scope, "workspace_id": "foreign"})

    def test_kafka_auth_failure_and_truncated_inventory_are_not_idle_success(self):
        from application_evidence import topic_records

        docker = Mock()
        docker.exec_owned.side_effect = subprocess.CalledProcessError(
            1, "owned", output="{}\n", stderr="AuthenticationException"
        )
        with self.assertRaises(subprocess.CalledProcessError):
            topic_records(docker, "owned.topic")
        docker.exec_owned.side_effect = None
        docker.exec_owned.return_value.stdout = "{}\n" * 64
        with self.assertRaisesRegex(ValueError, "inventory bound"):
            topic_records(docker, "owned.topic")

    def test_every_serving_member_must_have_exact_kafka_receipts_and_values(self):
        import application_evidence as evidence

        candidates, ordered, scope = self.records()
        deliveries, _ = evidence.match_records(candidates, ordered, scope)
        for missing in (None, "replica3"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as tmp:
                root, m, docker = Path(tmp), manifest(), Mock()
                save_new(root / "source-fixture.json", scope)

                def query(node, argv, missing=missing, m=m, **kwargs):
                    sql = argv[-1]
                    if "SELECT DISTINCT envelope_id" in sql:
                        rows = (
                            []
                            if node == missing
                            else [
                                dict(
                                    zip(
                                        ("envelope_id", "payload_sha256"),
                                        d,
                                        strict=True,
                                    )
                                )
                                for d in deliveries
                            ]
                        )
                    elif "SELECT count()" in sql:
                        rows = [{"n": 1}]
                    elif "SELECT version()" in sql:
                        rows = [
                            {
                                "version": "observed-binary-version",
                                "hostname": m["project"] + "-" + node,
                            }
                        ]
                    else:
                        raise AssertionError("unexpected evidence query")
                    return Mock(stdout="\n".join(json.dumps(row) for row in rows))

                docker.exec_owned.side_effect = query
                with patch.object(
                    evidence, "topic_records", side_effect=[candidates, ordered]
                ):
                    if missing:
                        with self.assertRaisesRegex(ValueError, "serving member"):
                            evidence.qualify(docker, root, m)
                        self.assertFalse(
                            (root / "kafka-to-three-member-evidence.json").exists()
                        )
                    else:
                        result = evidence.qualify(docker, root, m)
                        self.assertEqual(result["all_member_count"], 3)
                        self.assertEqual(len(result["runtime"]), 3)


if __name__ == "__main__":
    unittest.main()
