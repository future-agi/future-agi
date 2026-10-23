#!/usr/bin/env python3
"""Helm/manifest contracts for opt-in storage; does not claim a Kubernetes run."""

import os
import subprocess
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

CHART = Path(__file__).resolve().parents[1]


def render(*overrides, cluster=True, external=False, success=True):
    args = [
        os.environ.get("HELM", "helm"),
        "template",
        "fixture",
        str(CHART),
        "-f",
        str(CHART / "examples/bundled.yaml"),
    ]
    if cluster:
        args += ["-f", str(CHART / "examples/clickhouse-cluster.yaml")]
    if external:
        args += ["-f", str(CHART / "examples/clickhouse-external-cluster.yaml")]
    for value in overrides:
        args += ["--set", value]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    if success:
        if result.returncode:
            raise AssertionError(result.stderr)
        return [obj for obj in yaml.safe_load_all(result.stdout) if obj]
    if result.returncode == 0:
        raise AssertionError("unsafe cluster configuration rendered")
    return result.stderr


class ClusterChartTests(unittest.TestCase):
    def test_single_node_is_unchanged(self):
        objects = render(cluster=False)
        data = [
            o
            for o in objects
            if o["kind"] == "StatefulSet" and "clickhouse" in o["metadata"]["name"]
        ]
        self.assertEqual(
            [(o["metadata"]["name"], o["spec"]["replicas"]) for o in data],
            [("fixture-futureagi-clickhouse", 1)],
        )
        self.assertFalse(any("keeper" in o["metadata"]["name"] for o in objects))

    def test_three_by_two_and_keeper_quorum(self):
        objects = render()
        data = [
            o
            for o in objects
            if o["kind"] == "StatefulSet" and "clickhouse" in o["metadata"]["name"]
        ]
        self.assertEqual(sorted(o["spec"]["replicas"] for o in data), [2, 2, 2, 3])
        for obj in data:
            self.assertEqual(obj["spec"]["updateStrategy"]["type"], "OnDelete")
            self.assertIn("volumeClaimTemplates", obj["spec"])
            pod = obj["spec"]["template"]["spec"]
            self.assertFalse(pod["automountServiceAccountToken"])
            self.assertIn(
                "requiredDuringSchedulingIgnoredDuringExecution",
                pod["affinity"]["podAntiAffinity"],
            )
        config = next(
            o["data"]
            for o in objects
            if o["kind"] == "ConfigMap" and "cluster.xml" in o.get("data", {})
        )
        xml = ET.fromstring(config["cluster.xml"])
        shards = xml.findall("remote_servers/futureagi_cluster/shard")
        self.assertEqual(len(shards), 3)
        hosts = []
        for shard in shards:
            self.assertEqual(shard.findtext("internal_replication"), "true")
            replicas = shard.findall("replica")
            self.assertEqual(len(replicas), 2)
            hosts.extend(r.findtext("host") for r in replicas)
            self.assertTrue(all(r.find("password").get("from_env") for r in replicas))
        self.assertEqual(len(set(hosts)), 6)
        users = ET.fromstring(config["users.xml"])
        self.assertEqual(users.findtext("profiles/default/insert_quorum"), "2")
        self.assertEqual(
            users.findtext("profiles/default/distributed_foreground_insert"), "1"
        )
        self.assertEqual(
            users.findtext("profiles/default/skip_unavailable_shards"), "0"
        )
        self.assertEqual(
            users.findtext("profiles/default/distributed_product_mode"), "global"
        )
        keeper = next(
            o
            for o in objects
            if o["kind"] == "NetworkPolicy"
            and o["metadata"]["name"].endswith("clickhouse-keeper")
        )
        self.assertEqual(keeper["spec"]["policyTypes"], ["Ingress"])

    def test_external_cluster_does_not_create_data_or_keeper_pods(self):
        objects = render(external=True)
        self.assertFalse(
            any(
                o["kind"] == "StatefulSet" and "clickhouse" in o["metadata"]["name"]
                for o in objects
            )
        )
        backend = next(
            o
            for o in objects
            if o["kind"] == "Deployment" and o["metadata"]["name"].endswith("backend")
        )
        env = {
            e["name"]: e.get("value")
            for e in backend["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        self.assertEqual(env["CH_CLUSTER_ENABLED"], "true")
        self.assertEqual(env["CH_HOST"], "clickhouse.internal.example")

    def test_invalid_profiles_fail_before_deployment(self):
        for override in (
            "clickhouse.cluster.insertQuorum=3",
            "clickhouse.database=default",
            "clickhouse.cluster.privateNetwork=false",
            "networkPolicy.enabled=false",
            "clickhouse.cluster.keeper.replicas=2",
            "clickhouse.cluster.name=bad-name",
            "clickhouse.cluster.replicas=1",
        ):
            with self.subTest(override=override):
                render(override, success=False)


if __name__ == "__main__":
    unittest.main()
