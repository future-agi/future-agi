"""Offline ownership, exact-schema and fault-result tests. No Docker/real DBs."""

import copy
import http.client
import http.server
import json
import os
import subprocess
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock, patch

import harness
from adapter_ack import profile_statements, run_adapter_ack, synchronous_evidence
from harness import (
    GIB,
    HERE,
    LABEL,
    MEMORY,
    TOKEN_LABEL,
    Docker,
    canonical,
    configuration,
    load,
    read_file,
    save_new,
    xml_files,
)
from run import Runner, UnknownWrite, lose_ack, request_once, row_witness
from schema_probe import exact_schema, schema_bundle, topology_errors


def manifest():
    return {
        "version": 1,
        "run_id": "0123456789abcdef",
        "project": "pcreplicated-0123456789abcdef",
        "token": "a" * 64,
        "password": "b" * 64,
        "ports": dict(
            zip(
                (
                    "replica1_http",
                    "replica1_native",
                    "replica2_http",
                    "replica2_native",
                    "keeper",
                ),
                range(42001, 42006),
                strict=True,
            )
        ),
        "files": {},
        "source_files": {},
    }


class SafetyTests(unittest.TestCase):
    def test_bounded_two_servers_one_independent_keeper_and_loopback_only(self):
        m = manifest()
        doc = configuration(m)
        self.assertEqual(set(doc["services"]), set(MEMORY))
        self.assertEqual(
            sum(s["mem_limit"] for s in doc["services"].values()), int(6.5 * GIB)
        )
        self.assertLessEqual(sum(MEMORY.values()), 7 * GIB)
        for name, service in doc["services"].items():
            self.assertEqual(service["mem_limit"], service["memswap_limit"])
            self.assertEqual(
                service["labels"], {LABEL: m["run_id"], TOKEN_LABEL: m["token"]}
            )
            self.assertEqual(service["container_name"], m["project"] + "-" + name)
            self.assertEqual(service["restart"], "no")
            self.assertEqual(service["pull_policy"], "never")
            self.assertNotIn("env_file", service)
            self.assertNotIn("network_mode", service)
            self.assertNotIn("privileged", service)
            self.assertTrue(
                all(port.startswith("127.0.0.1:") for port in service["ports"])
            )
            self.assertTrue(
                all(
                    not mount.startswith("/") or mount.endswith(":ro")
                    for mount in service["volumes"]
                )
            )
        self.assertEqual(doc["services"]["keeper"]["entrypoint"][1], "keeper")
        for resource in [*doc["volumes"].values(), *doc["networks"].values()]:
            self.assertTrue(resource["name"].startswith(m["project"]))
            self.assertNotIn("external", resource)
        self.assertEqual(doc["networks"]["default"]["driver"], "bridge")
        self.assertNotIn("internal", doc["networks"]["default"])

    def test_both_replicas_share_only_one_keeper_and_distinct_replica_macros(self):
        files = xml_files(manifest())
        for name in ("replica1", "replica2"):
            root = ET.fromstring(files[name + ".xml"])
            self.assertIsNone(root.find("keeper_server"))
            self.assertEqual(root.findtext("zookeeper/node/host"), "keeper")
            self.assertEqual(root.findtext("zookeeper/node/port"), "9181")
            self.assertEqual(root.findtext("macros/shard"), "1")
            self.assertEqual(root.findtext("macros/replica"), name)
            self.assertEqual(
                len(root.findall("remote_servers/smoke_cluster/shard/replica")), 2
            )
            self.assertEqual(root.find("remote_servers").attrib, {"replace": "replace"})
        keeper = ET.fromstring(files["keeper.xml"])
        self.assertEqual(
            len(keeper.findall("keeper_server/raft_configuration/server")), 1
        )
        self.assertEqual(
            keeper.findtext("keeper_server/raft_configuration/server/hostname"),
            "keeper",
        )

    def test_plan_is_offline_and_load_rejects_rehashed_foreign_compose(self):
        with (
            tempfile.TemporaryDirectory(dir=HERE) as tmp,
            patch.object(harness, "RUNS", Path(tmp) / "runs"),
            patch.object(harness, "reserve_ports", return_value=manifest()["ports"]),
            patch("subprocess.run", side_effect=AssertionError("Docker must not run")),
        ):
            directory, m = harness.plan()
            self.assertEqual(load(directory), m)
            changed = configuration(m)
            changed["services"]["replica1"]["volumes"].append(
                "/foreign:/var/lib/foreign"
            )
            raw = canonical(changed)
            (directory / "compose.json").write_bytes(raw)
            m["files"]["compose.json"] = harness.digest(raw)
            (directory / "manifest.json").write_bytes(canonical(m))
            with self.assertRaisesRegex(ValueError, "changed run file"):
                load(directory)

    def test_manifest_rejects_foreign_names_ports_and_extra_fields(self):
        for change in (
            {"project": "th7247-native-igazce"},
            {"run_id": "../../foreign"},
            {"docker_host": "tcp://production:2375"},
            {"version": True},
            {"ports": {**manifest()["ports"], "keeper": 42001}},
        ):
            with (
                self.subTest(change=change),
                self.assertRaises((ValueError, TypeError)),
            ):
                harness.validate_manifest({**manifest(), **change})

    def test_bounded_reader_rejects_symlink_fifo_and_oversize(self):
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            root = Path(tmp)
            (root / "regular").write_bytes(b"x" * 9)
            (root / "link").symlink_to(root / "regular")
            os.mkfifo(root / "fifo")
            for name in ("regular", "link", "fifo"):
                with self.subTest(name=name), self.assertRaises((OSError, ValueError)):
                    read_file(root / name, limit=8)

    def test_exclusive_write_intent_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            path = Path(tmp) / "intent.json"
            save_new(path, {"outcome": "UNKNOWN"})
            with self.assertRaises(FileExistsError):
                save_new(path, {"outcome": "retry"})
            self.assertEqual(json.loads(read_file(path))["outcome"], "UNKNOWN")

    def test_docker_drops_ambient_credentials_and_rejects_remote_context(self):
        with patch.dict(
            os.environ,
            {
                "DOCKER_HOST": "tcp://production:2375",
                "COMPOSE_FILE": "/foreign.yml",
                "CH_PASSWORD": "production",
            },
        ):
            docker = Docker(HERE, manifest())
        self.assertNotIn("DOCKER_HOST", docker.env)
        self.assertNotIn("COMPOSE_FILE", docker.env)
        self.assertNotIn("CH_PASSWORD", docker.env)
        docker.command = Mock(
            return_value=subprocess.CompletedProcess(
                [],
                0,
                json.dumps([{"Endpoints": {"docker": {"Host": "tcp://remote:2375"}}}]),
                "",
            )
        )
        with self.assertRaisesRegex(RuntimeError, "local Unix"):
            docker.connect()

    def test_up_requires_explicit_headroom_and_never_reuses_resources(self):
        docker = Docker(HERE, manifest())
        docker.command = Mock(side_effect=AssertionError("no live command authorized"))
        with self.assertRaisesRegex(RuntimeError, "headroom"):
            docker.up(None)
        docker.inspect = Mock(return_value={"Name": "preexisting"})
        with (
            patch("schema_probe.source_fingerprints", return_value={}),
            patch("harness.read_file", return_value=canonical(schema_bundle(docker.m))),
        ):
            with self.assertRaisesRegex(RuntimeError, "preexisting"):
                docker.up(docker.m["run_id"])
        docker.command.assert_not_called()

    def test_exact_docker_absence_messages_do_not_mask_other_errors(self):
        docker = Docker(HERE, manifest())
        for kind, message in (
            ("container", "No such object: planned"),
            ("volume", "get planned: no such volume"),
            ("network", "network planned not found"),
            ("image", "No such image: planned"),
        ):
            docker.command = Mock(
                return_value=subprocess.CompletedProcess(
                    [], 1, "", "Error response from daemon: " + message
                )
            )
            self.assertIsNone(docker.inspect(kind, "planned"))
            for error in (
                "permission denied",
                message.replace("planned", "foreign"),
                "Cannot connect to Docker daemon",
            ):
                docker.command.return_value = subprocess.CompletedProcess(
                    [], 1, "", error
                )
                with self.assertRaises(RuntimeError):
                    docker.inspect(kind, "planned")

    def test_running_guard_requires_actual_not_just_configured_loopback_ports(self):
        m = manifest()
        docker = Docker(HERE, m)
        name = m["project"] + "-replica1"
        bindings = {
            "8123/tcp": [{"HostIp": "127.0.0.1", "HostPort": "42001"}],
            "9000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "42002"}],
        }
        row = {
            "Name": name,
            "Id": "owned-id",
            "Config": {
                "Labels": {
                    LABEL: m["run_id"],
                    TOKEN_LABEL: m["token"],
                    "com.docker.compose.project": m["project"],
                    "com.docker.compose.service": "replica1",
                }
            },
            "State": {"Running": True, "OOMKilled": False},
            "HostConfig": {
                "Memory": MEMORY["replica1"],
                "MemorySwap": MEMORY["replica1"],
                "PortBindings": bindings,
            },
            "NetworkSettings": {"Ports": {"8123/tcp": None, "9000/tcp": None}},
        }
        docker.resources = Mock(return_value=[("container", name)])
        docker.inspect = Mock(return_value=row)
        with self.assertRaisesRegex(RuntimeError, "loopback endpoints"):
            docker.owned(running=True)
        row["NetworkSettings"]["Ports"] = copy.deepcopy(bindings)
        self.assertEqual(len(docker.owned(running=True)), 1)
        row["NetworkSettings"]["Ports"]["8123/tcp"][0]["HostIp"] = "0.0.0.0"
        with self.assertRaisesRegex(RuntimeError, "loopback endpoints"):
            docker.owned(running=True)

    def test_headroom_rejects_uncapped_or_overbudget_existing_work_without_mutating_it(
        self,
    ):
        for memory in (0, 2 * GIB):
            with (
                self.subTest(memory=memory),
                tempfile.TemporaryDirectory(dir=HERE) as tmp,
            ):
                docker = Docker(Path(tmp), manifest())
                docker.inspect = Mock(return_value=None)
                outputs = [
                    json.dumps({"MemTotal": 8 * GIB}),
                    "preexisting-id\n",
                    json.dumps([{"HostConfig": {"Memory": memory}}]),
                ]
                docker.command = Mock(
                    side_effect=[
                        subprocess.CompletedProcess([], 0, output, "")
                        for output in outputs
                    ]
                )
                with (
                    patch("schema_probe.source_fingerprints", return_value={}),
                    patch(
                        "harness.read_file",
                        return_value=canonical(schema_bundle(docker.m)),
                    ),
                ):
                    with self.assertRaisesRegex(RuntimeError, "headroom unproven"):
                        docker.up(docker.m["run_id"])
                self.assertEqual(
                    [call.args[0][0] for call in docker.command.call_args_list],
                    ["info", "ps", "container"],
                )
                self.assertTrue((Path(tmp) / "headroom.json").is_file())

    def owned_rows(self, docker):
        labels = {
            LABEL: docker.m["run_id"],
            TOKEN_LABEL: docker.m["token"],
            "com.docker.compose.project": docker.m["project"],
        }
        return {
            (kind, name): {
                "Id": str(index) * 64,
                "Name": name,
                **(
                    {"Config": {"Labels": dict(labels)}}
                    if kind == "container"
                    else {"Labels": dict(labels)}
                ),
            }
            for index, (kind, name) in enumerate(docker.resources(), 1)
        }

    def test_cleanup_prevalidates_all_resources_before_removing_anything(self):
        for failure in ("foreign volume", "foreign container", "protected id"):
            with self.subTest(failure=failure):
                docker = Docker(HERE, manifest())
                rows = self.owned_rows(docker)
                if failure == "foreign volume":
                    rows[docker.resources()[-2]]["Labels"][TOKEN_LABEL] = "foreign"
                elif failure == "foreign container":
                    rows[docker.resources()[0]]["Config"]["Labels"][LABEL] = "foreign"
                else:
                    rows[docker.resources()[0]]["Id"] = "c2854450c0d8" + "0" * 52
                docker.inspect = Mock(
                    side_effect=lambda kind, name, rows=rows: rows.get((kind, name))
                )
                docker.command = Mock()
                with self.assertRaises(RuntimeError):
                    docker.cleanup()
                docker.command.assert_not_called()

    def test_cleanup_removes_only_validated_exact_ids_and_named_volumes(self):
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            docker = Docker(Path(tmp), manifest())
            rows = self.owned_rows(docker)
            docker.inspect = Mock(side_effect=lambda kind, name: rows.get((kind, name)))
            commands = []

            def command(args, **_kwargs):
                commands.append(args)
                target = args[-1]
                key = next(
                    key
                    for key, row in rows.items()
                    if key[1] == target or row.get("Id") == target
                )
                del rows[key]

            docker.command = command
            docker.cleanup()
            self.assertEqual(len(commands), 7)
            self.assertFalse(rows)
            self.assertFalse(
                any(
                    word in {"prune", "down", "image", "--volumes"}
                    for args in commands
                    for word in args
                )
            )


class SchemaTests(unittest.TestCase):
    def test_reuses_exact_schema_and_preserves_version_argument_and_keys(self):
        bundle = schema_bundle(manifest())
        self.assertEqual(bundle["production_required_replicas"], 3)
        self.assertFalse(bundle["production_admitted"])
        for family, expected in bundle["databases"].items():
            rows = [
                {
                    "database": expected["name"],
                    "name": table["name"],
                    "engine": table["engine"],
                    "create_table_query": table["sql"],
                }
                for table in expected["tables"]
            ]
            exact_schema(rows, expected)
            state = next(
                table
                for table in expected["tables"]
                if table["name"] == "property_catalog_source_streams"
            )
            self.assertIn("_version)", state["sql"])
            self.assertEqual(
                state["engine"],
                "ReplicatedReplacingMergeTree"
                if family == "replicated"
                else "ReplacingMergeTree",
            )
            broken = copy.deepcopy(rows)
            broken[0]["create_table_query"] = broken[0]["create_table_query"].replace(
                "catalog_revision", "foreign_revision"
            )
            with self.assertRaises(ValueError):
                exact_schema(broken, expected)
            for table in expected["tables"]:
                if family == "replicated":
                    self.assertIn(expected["name"], table["keeper_path"])
                    self.assertIn("/{shard}/", table["keeper_path"])
                    self.assertIn("'{replica}'", table["sql"])
                self.assertNotIn("ON CLUSTER", table["sql"])

    def test_actual_production_admission_rejects_two_replicas_without_patch(self):
        from tracer.services.clickhouse.v2 import catalog_prod_schema as schema

        client = Mock()
        client.query_rows.return_value = [(1, 1, "replica1"), (1, 2, "replica2")]
        with self.assertRaisesRegex(
            schema.CatalogProdSchemaError, "three distinct replicas"
        ):
            schema._prove_cluster(client, cluster="smoke_cluster")
        self.assertEqual(schema.REQUIRED_REPLICAS, 3)
        self.assertEqual(client.query_rows.call_count, 1)

    def test_one_of_one_and_split_keeper_paths_never_count_as_two_healthy_replicas(
        self,
    ):
        expected = schema_bundle(manifest())["databases"]["replicated"]
        rows = [
            {
                "table": table["name"],
                "zookeeper_path": table["keeper_path"].replace("{shard}", "1"),
                "replica_name": "replica1",
                "total_replicas": 2,
                "active_replicas": 2,
            }
            for table in expected["tables"]
        ]
        self.assertEqual(topology_errors(rows, expected, "replica1"), [])
        for change in (
            {"total_replicas": 1, "active_replicas": 1},
            {"zookeeper_path": "/independent/keeper"},
            {"replica_name": "replica2"},
        ):
            with self.subTest(change=change):
                altered = copy.deepcopy(rows)
                altered[0].update(change)
                self.assertTrue(topology_errors(altered, expected, "replica1"))
        self.assertTrue(topology_errors(rows[:-1], expected, "replica1"))


class FaultTests(unittest.TestCase):
    def test_unknown_response_is_not_retried(self):
        for fault in (
            "read error",
            "content length",
            "exception after 200",
            "too large",
        ):
            response = Mock(status=200)
            response.getheader.side_effect = lambda name, default=None, fault=fault: (
                "3"
                if fault == "content length" and name == "Content-Length"
                else default
            )
            response.read.return_value = (
                b"Code: 999. DB::Exception"
                if fault == "exception after 200"
                else b"x" * ((1 << 20) + 1)
                if fault == "too large"
                else b""
            )
            if fault == "read error":
                response.read.side_effect = http.client.IncompleteRead(b"partial")
            connection = Mock()
            connection.getresponse.return_value = response
            with (
                self.subTest(fault=fault),
                patch("http.client.HTTPConnection", return_value=connection),
            ):
                with self.assertRaises(UnknownWrite):
                    request_once(1, "/", b"INSERT", "isolated", write=True)
                self.assertEqual(connection.request.call_count, 1)
                connection.close.assert_called_once()

    def test_ack_loss_proxy_forwards_once_then_client_has_unknown_outcome(self):
        attempts = []

        class Upstream(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                attempts.append(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

        server = http.server.HTTPServer(("127.0.0.1", 0), Upstream)
        server.timeout = 3
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        try:
            result = lose_ack(
                server.server_port, "/?query_id=unit-only", b"one insert", "isolated"
            )
        finally:
            thread.join(timeout=5)
            server.server_close()
        self.assertEqual(attempts, [b"one insert"])
        self.assertEqual(result["forward_attempts"], 1)
        self.assertEqual(result["upstream"], {"outcome": "returned", "value": ""})
        self.assertEqual(result["client"]["type"], "UnknownWrite")

    def test_same_count_or_one_replica_does_not_prove_exact_row_visibility(self):
        expected = {"build_token": "a", "_version": 1}
        for observed in (
            {"replica1": [expected]},
            {"replica1": [expected], "replica2": []},
            {
                "replica1": [expected],
                "replica2": [{**expected, "build_token": "foreign"}],
            },
            {"replica1": [expected], "replica2": [expected, expected]},
        ):
            self.assertFalse(
                row_witness(expected, observed)["all_replica_visibility_proven"]
            )
        self.assertTrue(
            row_witness(
                expected, {node: [expected] for node in ("replica1", "replica2")}
            )["all_replica_visibility_proven"]
        )

    def test_fault_results_are_not_forced_to_pass_and_write_intents_cannot_replay(self):
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            runner = Runner.__new__(Runner)
            runner.directory, runner.m = Path(tmp), manifest()
            runner.bundle = schema_bundle(runner.m)
            runner.probe = Mock(
                return_value={"fixture_exact_topology_and_schema": True}
            )
            runner.verify_owned = Mock()
            runner.sql = Mock(return_value=[])
            runner.witness = Mock(
                return_value={
                    "exact_on_each_replica": {"replica1": True, "replica2": True},
                    "all_replica_visibility_proven": True,
                }
            )
            with (
                patch("run.reservation_row", return_value={"build_token": "unit"}),
                patch("run.request_once", return_value=b"") as request,
                patch(
                    "run.lose_ack",
                    return_value={
                        "forward_attempts": 1,
                        "client": {"type": "UnknownWrite"},
                    },
                ) as proxy,
            ):
                result = runner.scenarios(runner.m["run_id"])
                self.assertFalse(result["minimum_gap_demonstrated"])
                self.assertTrue(
                    all(
                        row["failure_result"] == "NOT_REPRODUCED_OR_DIFFERENT_FAILURE"
                        for row in result["scenarios"]
                    )
                )
                self.assertEqual(request.call_count, 1)
                self.assertEqual(proxy.call_count, 1)
                with self.assertRaises(FileExistsError):
                    runner.scenarios(runner.m["run_id"])
                self.assertEqual(request.call_count, 1)
                self.assertEqual(proxy.call_count, 1)
            commands = [call.args[1] for call in runner.sql.call_args_list]
            self.assertEqual(
                sum(command.startswith("SYSTEM START FETCHES") for command in commands),
                2,
            )


class AdapterAckTests(unittest.TestCase):
    def test_async_profile_only_creates_owned_standalone_user_with_exact_grants(self):
        m = manifest()
        database = "property_catalog_dev_standalone_" + m["run_id"]
        user, statements = profile_statements(m, database)
        self.assertEqual(user, "smoke_async_" + m["run_id"])
        self.assertIn("async_insert=1, wait_for_async_insert=0", statements[0])
        self.assertIn("async_insert_busy_timeout_ms=60000", statements[0])
        self.assertEqual(len(statements), 5)
        self.assertEqual(
            statements[1], f"GRANT SELECT, INSERT ON {database}.* TO {user}"
        )
        self.assertTrue(
            all(
                statement.startswith("GRANT SELECT ON system.")
                for statement in statements[2:]
            )
        )
        for foreign in (
            "default",
            "property_catalog_prod",
            database.replace("standalone", "replicated"),
        ):
            with self.assertRaises(ValueError):
                profile_statements(m, foreign)

    def test_ack_requires_exact_server_and_row_evidence_not_success_alone(self):
        profile = {"async_insert": "1", "wait_for_async_insert": "0"}
        cases = [
            {
                "token": token,
                "transport": {"outcome": "returned"},
                "exact_row_immediately_visible": True,
                "elapsed_seconds": 0.1,
            }
            for token in ("data", "control")
        ]
        logs = [
            {
                "token": token,
                "type": "QueryFinish",
                "async_insert": "0",
                "wait_for_async_insert": "0",
                "written_rows": 1,
                "exception_code": 0,
            }
            for token in ("data", "control")
        ]
        self.assertTrue(synchronous_evidence(profile, cases, logs))
        for change in (
            {"async_insert": "1"},
            {"async_insert": ""},
            {"written_rows": 0},
            {"exception_code": 1},
            {"type": "ExceptionWhileProcessing"},
        ):
            changed = copy.deepcopy(logs)
            changed[0].update(change)
            self.assertFalse(synchronous_evidence(profile, cases, changed))
        self.assertFalse(synchronous_evidence(profile, cases, logs[:1]))
        cases[0]["transport"] = {"outcome": "rejected", "type": "UnknownWrite"}
        self.assertFalse(synchronous_evidence(profile, cases, logs))
        self.assertFalse(synchronous_evidence({"async_insert": "0"}, cases, logs))

    def test_async_phase_requires_confirmation_and_never_replays_existing_intent(self):
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            runner = Mock()
            runner.directory, runner.m = Path(tmp), manifest()
            runner.bundle = schema_bundle(runner.m)
            with self.assertRaisesRegex(RuntimeError, "confirmation"):
                run_adapter_ack(runner, None)
            runner.verify_owned.assert_not_called()
            save_new(
                runner.directory / "async-profile-intent.json", {"outcome": "UNKNOWN"}
            )
            with self.assertRaises(FileExistsError):
                run_adapter_ack(runner, runner.m["run_id"])
            runner.sql.assert_not_called()


if __name__ == "__main__":
    unittest.main()
