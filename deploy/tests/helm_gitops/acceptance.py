#!/usr/bin/env python3
"""Real-controller acceptance on an explicitly disposable kind/Docker host.

Requires source-built backend/frontend/collector/gateway images at --image-tag.
Uses only a private registry, synthetic state and an ephemeral test signing key.
Never publishes to a production registry or qualifies a database version change.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from contextlib import ExitStack
from pathlib import Path

import yaml

if __package__:
    from .witnesses import PROGRAM
else:
    from witnesses import PROGRAM

ROOT = Path(__file__).resolve().parents[3]
CHART = ROOT / "deploy/helm/futureagi"
PINS = json.loads(Path(__file__).with_name("pins.json").read_text())
COMPONENTS = {
    "backend": "futureagi/future-agi",
    "frontend": "futureagi/frontend",
    "fiCollector": "futureagi/fi-collector",
    "agentccGateway": "futureagi/agentcc-gateway",
}
NS = "futureagi"
ADMIN_PASSWORD = "GitOps-Smoke-2026!x"


def sha(value):
    raw = (
        value
        if isinstance(value, bytes)
        else json.dumps(value, sort_keys=True).encode()
    )
    return hashlib.sha256(raw).hexdigest()


def flux_condition(doc, kind, expected):
    generation = doc.get("metadata", {}).get("generation")
    status = doc.get("status", {})
    return generation is not None and any(
        c.get("type") == kind
        and c.get("status") == expected
        and c.get("observedGeneration", status.get("observedGeneration")) == generation
        for c in status.get("conditions", [])
    )


def argo_at_digest(doc, digest, phase="Succeeded"):
    status = doc.get("status", {})
    operation = status.get("operationState", {})
    if phase == "Failed":
        return (
            operation.get("phase") in ("Failed", "Error")
            and operation.get("syncResult", {}).get("revision") == digest
        )
    return (
        operation.get("phase") == phase
        and operation.get("syncResult", {}).get("revision") == digest
        and status.get("sync", {}).get("revision") == digest
        and status.get("sync", {}).get("status") == "Synced"
        and status.get("health", {}).get("status") == "Healthy"
    )


def status_projection(status):
    """Keep outcome identity, not Argo history/comparedTo Helm values or messages."""
    allowed = (
        "observedGeneration",
        "lastAttemptedRevision",
        "lastAttemptedRevisionDigest",
        "failures",
        "installFailures",
        "upgradeFailures",
        "reconciledAt",
        "lastHandledReconcileAt",
    )
    output = {key: status[key] for key in allowed if key in status}
    output["conditions"] = [
        {
            key: condition[key]
            for key in (
                "type",
                "status",
                "reason",
                "observedGeneration",
                "lastTransitionTime",
            )
            if key in condition
        }
        for condition in status.get("conditions", [])
    ]
    if "sync" in status:
        output["sync"] = {
            key: status["sync"][key]
            for key in ("status", "revision")
            if key in status["sync"]
        }
    if "health" in status:
        output["health"] = {"status": status["health"].get("status")}
    if "operationState" in status:
        operation = status["operationState"]
        output["operationState"] = {
            key: operation[key]
            for key in ("phase", "startedAt", "finishedAt")
            if key in operation
        }
        output["operationState"]["revision"] = operation.get("syncResult", {}).get(
            "revision"
        )
    if "artifact" in status:
        output["artifact"] = {
            key: status["artifact"][key]
            for key in ("revision", "digest")
            if key in status["artifact"]
        }
    return output


class Refused(RuntimeError):
    pass


class Fixture:
    def __init__(self, controller, tag, output):
        self.controller, self.tag, self.output = (
            controller,
            tag,
            Path(output).absolute(),
        )
        self.output.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.work = Path(tempfile.mkdtemp(prefix="fagi-gitops-"))
        self.run_id = uuid.uuid4().hex[:12]
        self.cluster = "fagi-gitops-" + controller + "-" + self.run_id
        self.registry = self.cluster + "-registry"
        self.context = "kind-" + self.cluster
        self.env = {
            key: os.environ[key]
            for key in (
                "PATH",
                "LANG",
                "SSL_CERT_FILE",
                "SSL_CERT_DIR",
                "PLAYWRIGHT_BROWSERS_PATH",
            )
            if key in os.environ
        }
        self.env.update(
            HOME=str(self.work),
            KUBECONFIG=str(self.work / "kubeconfig"),
            DOCKER_CONFIG=str(self.work / "docker-config"),
            COSIGN_PASSWORD="",
        )
        self.commands, self.checkpoints = [], []
        self.cluster_attempted = self.registry_attempted = False
        self.app = self.source = self.release = None
        self.image_digests, self.artifacts = {}, {}
        self.token = uuid.uuid4().hex
        self.port = None
        self.baseline = None

    def save(self, name, data):
        (self.output / name).write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n"
        )

    def run(self, argv, *, timeout=300, input=None, check=True):
        start = time.monotonic()
        result = subprocess.run(
            list(map(str, argv)),
            input=input,
            capture_output=True,
            text=True,
            env=self.env,
            timeout=timeout,
            check=False,
        )
        # Do not retain stdin or arbitrary stdout: kubectl outputs can contain
        # credentials even on a synthetic fixture. Save explicit projections.
        row = {
            "argv": list(map(str, argv)),
            "exit_code": result.returncode,
            "seconds": round(time.monotonic() - start, 3),
        }
        self.commands.append(row)
        self.save("commands.json", self.commands)
        if check and result.returncode:
            raise Refused(
                f"{argv[0]} {argv[1]} failed ({result.returncode}): {result.stderr[-3000:]}"
            )
        return result

    def kubectl(self, *args, **kwargs):
        return self.run(["kubectl", "--context", self.context, *args], **kwargs)

    def apply(self, document):
        text = yaml.safe_dump_all(
            document if isinstance(document, list) else [document]
        )
        self.kubectl(
            "apply",
            "--server-side",
            "--field-manager=futureagi-gitops-test",
            "-f",
            "-",
            input=text,
        )

    def get(self, kind, name=None, namespace=NS):
        argv = ["-n", namespace, "get", kind]
        if name:
            argv.append(name)
        return json.loads(self.kubectl(*argv, "-o", "json").stdout)

    def wait(self, description, read, predicate, seconds=1500):
        deadline = time.monotonic() + seconds
        last = None
        while time.monotonic() < deadline:
            last = read()
            if predicate(last):
                print("PASS " + description, flush=True)
                return last
            time.sleep(5)
        status = last.get("status") if isinstance(last, dict) else None
        safe_status = status_projection(status) if isinstance(status, dict) else {}
        raise Refused(
            description
            + " exceeded "
            + str(seconds)
            + " seconds; last status="
            + json.dumps(safe_status)[-3000:]
        )

    def start(self):
        if os.environ.get("FAGI_GITOPS_DISPOSABLE") != "1":
            raise Refused(
                "set FAGI_GITOPS_DISPOSABLE=1 only on an owned disposable cloud/CI host"
            )
        existing = self.run(["kind", "get", "clusters"]).stdout.splitlines()
        if (
            self.cluster in existing
            or self.run(["docker", "inspect", self.registry], check=False).returncode
            == 0
        ):
            raise Refused("fixture name already exists; refusing to adopt it")
        # No externally exposed registry. kind nodes use a containerd mirror;
        # controller pods reach the same private registry through a Service.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.registry_attempted = True
        self.run(
            [
                "docker",
                "run",
                "-d",
                "--restart=no",
                "--name",
                self.registry,
                "--label",
                "futureagi.gitops.fixture=" + self.run_id,
                "-p",
                f"127.0.0.1:{self.port}:5000",
                PINS["registry_image"],
            ]
        )
        # Current kind already enables /etc/containerd/certs.d. Do not add
        # the retired containerd 1.x plugin stanza to a containerd 2.x node.
        config = {
            "kind": "Cluster",
            "apiVersion": "kind.x-k8s.io/v1alpha4",
            "networking": {"apiServerAddress": "127.0.0.1"},
        }
        path = self.work / "kind.yaml"
        path.write_text(yaml.safe_dump(config))
        self.cluster_attempted = True
        self.run(
            [
                "kind",
                "create",
                "cluster",
                "--name",
                self.cluster,
                "--config",
                path,
                "--wait",
                "180s",
            ],
            timeout=300,
        )
        self.run(["docker", "network", "connect", "kind", self.registry])
        host = f"localhost:{self.port}"
        for node in self.run(
            ["kind", "get", "nodes", "--name", self.cluster]
        ).stdout.splitlines():
            directory = "/etc/containerd/certs.d/" + host
            self.run(["docker", "exec", node, "mkdir", "-p", directory])
            hosts = f'[host."http://{self.registry}:5000"]\n  capabilities = ["pull", "resolve"]\n'
            self.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    node,
                    "sh",
                    "-c",
                    "cat > " + directory + "/hosts.toml",
                ],
                input=hosts,
            )
        registry = json.loads(self.run(["docker", "inspect", self.registry]).stdout)[0]
        ip = registry["NetworkSettings"]["Networks"]["kind"]["IPAddress"]
        self.apply(
            [
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {"name": "registry"},
                },
                {
                    "apiVersion": "v1",
                    "kind": "Service",
                    "metadata": {"name": "chart-registry", "namespace": "registry"},
                    "spec": {
                        "ports": [{"port": 5000, "targetPort": 5000}],
                        "type": "ClusterIP",
                    },
                },
                {
                    "apiVersion": "discovery.k8s.io/v1",
                    "kind": "EndpointSlice",
                    "metadata": {
                        "name": "chart-registry",
                        "namespace": "registry",
                        "labels": {"kubernetes.io/service-name": "chart-registry"},
                    },
                    "addressType": "IPv4",
                    "ports": [{"port": 5000, "protocol": "TCP"}],
                    "endpoints": [{"addresses": [ip], "conditions": {"ready": True}}],
                },
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {
                        "name": NS,
                        "labels": {"pod-security.kubernetes.io/enforce": "restricted"},
                    },
                },
            ]
        )
        self.registry_host = host
        self.chart_ref = host + "/charts/futureagi"
        self.controller_ref = (
            "chart-registry.registry.svc.cluster.local:5000/charts/futureagi"
        )
        for component, image in COMPONENTS.items():
            target = host + "/" + image + ":gitops-fixture"
            self.run(["docker", "image", "inspect", image + ":" + self.tag])
            self.run(["docker", "tag", image + ":" + self.tag, target])
            self.run(["docker", "push", target], timeout=600)
            digest = self.run(
                ["oras", "resolve", "--plain-http", target]
            ).stdout.strip()
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise Refused("registry returned invalid component digest")
            self.image_digests[component] = digest
        self.run(
            ["cosign", "generate-key-pair", "--output-key-prefix", self.work / "good"]
        )
        self.run(
            ["cosign", "generate-key-pair", "--output-key-prefix", self.work / "wrong"]
        )
        (self.output / "test-signing-public-key.pem").write_text(
            (self.work / "good.pub").read_text()
        )
        self.save(
            "fixture.json",
            {
                "controller": self.controller,
                "run_id": self.run_id,
                "context": self.context,
                "registry_binding": "127.0.0.1",
                "test_key_only": True,
                "controller_pin": PINS[self.controller],
                "product_image_digests": self.image_digests,
                "scope": "Controller reconciliation of the current product; no cross-version data-migration qualification",
            },
        )
        self.install_controller()
        self.create_secrets()
        self.artifacts["a"] = self.package("0.0.1", "a")
        self.artifacts["bad"] = self.package("0.0.2", "bad", fail_bootstrap=True)
        self.artifacts["b"] = self.package("0.0.3", "b")
        self.artifacts["untrusted"] = self.package("0.0.4", "untrusted", key="wrong")

    def install_controller(self):
        pin = PINS[self.controller]
        with urllib.request.urlopen(pin["url"], timeout=120) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
        if sha(raw) != pin["sha256"]:
            raise Refused("controller manifest checksum mismatch")
        namespace = "argocd" if self.controller == "argo" else "flux-system"
        self.apply(
            {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}}
        )
        manifest = self.work / (self.controller + "-install.yaml")
        manifest.write_bytes(raw)
        self.kubectl(
            "apply", "--server-side", "-n", namespace, "-f", manifest, timeout=300
        )
        self.kubectl(
            "-n",
            namespace,
            "wait",
            "deployment",
            "--all",
            "--for=condition=Available",
            "--timeout=600s",
            timeout=630,
        )
        if self.controller == "argo":
            self.kubectl(
                "-n",
                namespace,
                "rollout",
                "status",
                "statefulset/argocd-application-controller",
                "--timeout=600s",
                timeout=630,
            )
            self.apply(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {
                        "name": "fixture-oci",
                        "namespace": namespace,
                        "labels": {"argocd.argoproj.io/secret-type": "repository"},
                    },
                    "stringData": {
                        "url": "oci://" + self.controller_ref,
                        "type": "oci",
                        "insecureOCIForceHttp": "true",
                    },
                }
            )
            project = yaml.safe_load(
                (CHART / "examples/gitops/argocd-project.yaml").read_text()
            )
            project["spec"]["sourceRepos"] = ["oci://" + self.controller_ref]
            self.apply(project)
        else:
            self.apply(
                list(
                    yaml.safe_load_all(
                        (CHART / "examples/gitops/flux-rbac.yaml").read_text()
                    )
                )
            )
            self.apply(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {"name": "fixture-trust", "namespace": NS},
                    "stringData": {"cosign.pub": (self.work / "good.pub").read_text()},
                }
            )

    def create_secrets(self):
        keys = {
            name: secrets.token_hex(32)
            for name in [
                "SECRET_KEY",
                "AGENTCC_INTERNAL_API_KEY",
                "AGENTCC_ADMIN_TOKEN",
                "PROPERTY_CATALOG_API_PASSWORD",
                "PROPERTY_CATALOG_CONSUMER_PASSWORD",
                "AGENTCC_WEBHOOK_SECRET",
            ]
        }
        keys["INTEGRATION_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(
            secrets.token_bytes(32)
        ).decode()
        self.apply(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": "gitops-app", "namespace": NS},
                "stringData": keys,
            }
        )
        self.apply(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": "gitops-admin", "namespace": NS},
                "stringData": {
                    "email": "gitops-admin@example.com",
                    "name": "GitOps Smoke",
                    "password": ADMIN_PASSWORD,
                },
            }
        )
        # Choose loopback endpoints for this private fixture, not fixed shared
        # host ports. Port-forward bind failure is fatal; never adopt a listener.
        with socket.socket() as ui, socket.socket() as api:
            ui.bind(("127.0.0.1", 0))
            api.bind(("127.0.0.1", 0))
            self.ui_port, self.api_port = ui.getsockname()[1], api.getsockname()[1]
        self.values = yaml.safe_load((CHART / "examples/bundled.yaml").read_text())
        self.values.update(
            {
                "urls": {
                    "app": f"http://127.0.0.1:{self.ui_port}",
                    "api": f"http://127.0.0.1:{self.api_port}",
                },
                "secrets": {"existingSecret": "gitops-app"},
                "config": {"telemetry": False},
                "image": {"registry": self.registry_host},
                "bootstrap": {
                    "admin": {"existingSecret": "gitops-admin"},
                    "backoffLimit": 0,
                },
            }
        )
        for store in ("postgres", "clickhouse", "redis"):
            self.values[store]["password"] = "Fixture-" + self.token
        self.values["objectStorage"].update(
            accessKey="fixture-access", secretKey="Fixture-" + self.token
        )

    def package(self, version, marker, fail_bootstrap=False, key="good"):
        directory = self.work / ("chart-" + version + "-" + marker)
        shutil.copytree(CHART, directory)
        chart = yaml.safe_load((directory / "Chart.yaml").read_text())
        chart.update(version=version, appVersion="v" + version)
        (directory / "Chart.yaml").write_text(yaml.safe_dump(chart))
        values = yaml.safe_load((directory / "values.yaml").read_text())
        values["image"]["digests"] = dict(self.image_digests)
        # Optional serving/code-executor remain off. No coverage is claimed for
        # them; enabling them would require real fixture artifacts of their own.
        for component in COMPONENTS:
            values[component]["image"]["digest"] = self.image_digests[component]
            values[component]["image"]["tag"] = "gitops-fixture"
        values["backend"]["podAnnotations"]["futureagi.com/gitops-fixture"] = marker
        (directory / "values.yaml").write_text(yaml.safe_dump(values))
        if fail_bootstrap:
            path = directory / "templates/bootstrap/job.yaml"
            text = path.read_text()
            needle = 'command: ["python", "manage.py", "bootstrap_install"]'
            if text.count(needle) != 1:
                raise Refused(
                    "bootstrap fault seam changed; refusing a different injection"
                )
            injected = "command: [\"python\", \"-c\", \"import subprocess,sys; sys.exit(subprocess.call(['python','manage.py','bootstrap_install']) or 23)\"]"
            path.write_text(text.replace(needle, injected))
        packages = directory / "packages"
        packages.mkdir()
        self.run(["helm", "package", directory, "--destination", packages])
        archive = packages / ("futureagi-" + version + ".tgz")
        self.run(
            [
                "helm",
                "push",
                archive,
                "oci://" + self.registry_host + "/charts",
                "--plain-http",
            ]
        )
        digest = self.run(
            ["oras", "resolve", "--plain-http", self.chart_ref + ":" + version]
        ).stdout.strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise Refused("invalid chart digest")
        self.run(
            [
                "cosign",
                "sign",
                "--yes",
                "--key",
                self.work / (key + ".key"),
                "--tlog-upload=false",
                "--allow-insecure-registry",
                "--new-bundle-format=false",
                "--use-signing-config=false",
                self.chart_ref + "@" + digest,
            ]
        )
        verification = self.run(
            [
                "cosign",
                "verify",
                "--key",
                self.work / "good.pub",
                "--insecure-ignore-tlog",
                "--allow-insecure-registry",
                "--new-bundle-format=false",
                self.chart_ref + "@" + digest,
            ],
            check=key == "good",
        )
        if key != "good":
            if verification.returncode == 0:
                raise Refused("wrong-key artifact unexpectedly verifies")
            if not re.search(
                r"no matching signatures|invalid signature",
                verification.stderr,
                re.IGNORECASE,
            ):
                raise Refused(
                    "wrong-key check failed for an unclassified reason, not demonstrated signature refusal"
                )
        self.save(
            "signature-check-" + marker + ".json",
            {
                "test_key_only": True,
                "oci_digest": digest,
                "exit_code": verification.returncode,
                "verified_payload": json.loads(verification.stdout)
                if key == "good"
                else None,
                "refusal": verification.stderr[-2000:] if key != "good" else None,
            },
        )
        result = {
            "version": version,
            "digest": digest,
            "marker": marker,
            "archive_sha256": sha(archive.read_bytes()),
            "trusted": key == "good",
            "fault": fail_bootstrap,
        }
        self.save("artifact-" + marker + ".json", result)
        return result

    def select(self, artifact):
        if self.controller == "argo":
            if self.app is None:
                self.app = yaml.safe_load(
                    (CHART / "examples/gitops/argocd-application.yaml").read_text()
                )
                self.app["spec"]["source"]["repoURL"] = "oci://" + self.controller_ref
                self.app["spec"]["source"]["helm"]["valuesObject"] = self.values
            self.app["spec"]["source"]["targetRevision"] = artifact["digest"]
            self.apply(self.app)
        else:
            if self.source is None:
                self.source, self.release = list(
                    yaml.safe_load_all(
                        (CHART / "examples/gitops/flux-helmrelease.yaml").read_text()
                    )
                )
                self.source["spec"].update(
                    url="oci://" + self.controller_ref,
                    insecure=True,
                    interval="5s",
                    verify={
                        "provider": "cosign",
                        "secretRef": {"name": "fixture-trust"},
                    },
                )
                self.apply(
                    {
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "metadata": {"name": "futureagi-values", "namespace": NS},
                        "data": {"values.yaml": yaml.safe_dump(self.values)},
                    }
                )
                self.release["spec"]["interval"] = "5s"
            self.source["spec"]["ref"] = {"digest": artifact["digest"]}
            self.apply(self.source)
            self.apply(self.release)

    def successful(self, artifact):
        if self.controller == "argo":
            document = self.wait(
                "Argo current digest healthy " + artifact["marker"],
                lambda: self.get("application", "futureagi", "argocd"),
                lambda d: argo_at_digest(d, artifact["digest"]),
            )
            self.save(
                "controller-" + artifact["marker"] + ".json",
                {
                    "generation": document["metadata"]["generation"],
                    "status": status_projection(document["status"]),
                },
            )
        else:
            self.wait(
                "Flux exact source verified " + artifact["marker"],
                lambda: self.get("ocirepository", "futureagi"),
                lambda d: (
                    flux_condition(d, "Ready", "True")
                    and flux_condition(d, "SourceVerified", "True")
                    and artifact["digest"]
                    in d.get("status", {}).get("artifact", {}).get("revision", "")
                ),
                seconds=300,
            )
            document = self.wait(
                "Flux release healthy " + artifact["marker"],
                lambda: self.get("helmrelease", "futureagi"),
                lambda d: (
                    flux_condition(d, "Ready", "True")
                    and d.get("status", {}).get("lastAttemptedRevisionDigest")
                    == artifact["digest"]
                ),
            )
            self.save(
                "controller-" + artifact["marker"] + ".json",
                {
                    "generation": document["metadata"]["generation"],
                    "status": status_projection(document["status"]),
                },
            )
        for workload in self.get("deployments,statefulsets")["items"]:
            self.kubectl(
                "-n",
                NS,
                "rollout",
                "status",
                workload["kind"].lower() + "/" + workload["metadata"]["name"],
                "--timeout=300s",
                timeout=330,
            )
        job = self.get("job", "futureagi-bootstrap")
        if job.get("status", {}).get("succeeded") != 1:
            raise Refused("current bootstrap did not succeed")
        backend = self.get("deployment", "futureagi-backend")
        if (
            backend["spec"]["template"]["metadata"]["annotations"].get(
                "futureagi.com/gitops-fixture"
            )
            != artifact["marker"]
        ):
            raise Refused("controller health did not select the requested workload")

    def witnesses(self, name, seed=False):
        result = self.kubectl(
            "-n",
            NS,
            "exec",
            "-i",
            "deployment/futureagi-backend",
            "-c",
            "backend",
            "--",
            "python",
            "-",
            "seed" if seed else "check",
            self.token,
            input=PROGRAM,
            timeout=180,
        )
        lines = [
            line.partition("=")[2]
            for line in result.stdout.splitlines()
            if line.startswith("GITOPS_WITNESS=")
        ]
        if len(lines) != 1:
            raise Refused("missing unique product witness result")
        observed = json.loads(lines[0])
        secrets_doc = self.get("secret", "gitops-app")
        observed["app_secret_sha256"] = sha(secrets_doc["data"])
        observed["pvcs"] = {
            d["metadata"]["name"]: d["metadata"]["uid"]
            for d in self.get("pvc")["items"]
        }
        if seed:
            self.baseline = observed
        elif observed != self.baseline:
            raise Refused("product/store/key/PVC witness changed: " + name)
        self.save("witness-" + name + ".json", observed)
        self.checkpoints.append(name)
        print("PASS persistent product witnesses " + name, flush=True)

    def browser(self, name, *, first_login=False):
        def stop(process):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)

        with ExitStack() as stack:
            for service, local, remote in (
                ("frontend", self.ui_port, 80),
                ("backend", self.api_port, 8000),
            ):
                log = stack.enter_context(tempfile.TemporaryFile(mode="w+"))
                process = subprocess.Popen(
                    [
                        "kubectl",
                        "--context",
                        self.context,
                        "-n",
                        NS,
                        "port-forward",
                        "--address=127.0.0.1",
                        "svc/futureagi-" + service,
                        f"{local}:{remote}",
                    ],
                    env=self.env,
                    stdout=log,
                    stderr=log,
                    text=True,
                )
                stack.callback(stop, process)
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise Refused("owned browser port-forward failed: " + service)
                    log.seek(0)
                    if f"Forwarding from 127.0.0.1:{local} ->" in log.read():
                        break
                    time.sleep(0.2)
                else:
                    raise Refused(
                        "browser port-forward did not become ready: " + service
                    )
            result = self.run(
                [sys.executable, "-m", "deploy.tests.helm_gitops.browser"],
                timeout=180,
                input=json.dumps(
                    {
                        "ui": self.values["urls"]["app"],
                        "api": self.values["urls"]["api"],
                        "email": "gitops-admin@example.com",
                        "name": "GitOps Smoke",
                        "password": ADMIN_PASSWORD,
                        "first_login": first_login,
                        "output": str(self.output / ("browser-" + name + ".json")),
                    }
                ),
                check=False,
            )
            if result.returncode:
                report_path = self.output / ("browser-" + name + ".json")
                report = (
                    json.loads(report_path.read_text()) if report_path.is_file() else {}
                )
                # Only the browser's sanitized stage/type, never entered values
                # or raw Playwright exception text, belongs in the command log.
                detail = {
                    key: report.get(key)
                    for key in ("stage", "error_type", "page_errors")
                }
                detail["outcome_file_present"] = report_path.is_file()
                raise Refused(
                    "real browser login/profile check failed: "
                    + name
                    + " "
                    + json.dumps(detail)
                )
        print("PASS real browser login and retained profile " + name, flush=True)

    def deployment_templates(self):
        return {
            d["metadata"]["name"]: sha(d["spec"]["template"])
            for d in self.get("deployments")["items"]
        }

    def pinned_digest_ignores_moved_tag(self):
        prior = self.deployment_templates()
        pinned = self.artifacts["a"]["digest"]
        moved = self.package("0.0.1", "moved-tag", key="wrong")
        if moved["digest"] == pinned:
            raise Refused("moving-tag negative control did not change the artifact")
        if self.controller == "argo":
            previous = (
                self.get("application", "futureagi", "argocd")
                .get("status", {})
                .get("reconciledAt")
            )
            self.kubectl(
                "-n",
                "argocd",
                "annotate",
                "application/futureagi",
                "argocd.argoproj.io/refresh=hard",
                "--overwrite",
            )
            self.wait(
                "Argo rechecks original digest after tag moves",
                lambda: self.get("application", "futureagi", "argocd"),
                lambda d: (
                    d.get("status", {}).get("reconciledAt") != previous
                    and argo_at_digest(d, pinned)
                ),
                seconds=300,
            )
        else:
            requested = self.run_id + "-moved-tag"
            self.kubectl(
                "-n",
                NS,
                "annotate",
                "ocirepository/futureagi",
                "reconcile.fluxcd.io/requestedAt=" + requested,
                "--overwrite",
            )
            self.wait(
                "Flux rechecks original digest after tag moves",
                lambda: self.get("ocirepository", "futureagi"),
                lambda d: (
                    d.get("status", {}).get("lastHandledReconcileAt") == requested
                    and flux_condition(d, "Ready", "True")
                    and pinned
                    in d.get("status", {}).get("artifact", {}).get("revision", "")
                ),
                seconds=180,
            )
        if prior != self.deployment_templates():
            raise Refused("moving a tag changed a digest-pinned application")
        self.save(
            "moved-tag.json",
            {
                "pinned_digest": pinned,
                "tag_now_resolves_to": moved["digest"],
                "reconciliation_observed": True,
                "application_templates_unchanged": True,
            },
        )
        self.witnesses("after-moved-tag")

    def missing_digest(self):
        prior = self.deployment_templates()
        job_uid = self.get("job", "futureagi-bootstrap")["metadata"]["uid"]
        missing = {"digest": "sha256:" + secrets.token_hex(32), "marker": "missing"}
        self.select(missing)
        if self.controller == "argo":
            self.wait(
                "Argo refuses missing immutable artifact",
                lambda: self.get("application", "futureagi", "argocd"),
                lambda d: any(
                    c.get("type") == "ComparisonError"
                    and missing["digest"] in c.get("message", "")
                    for c in d.get("status", {}).get("conditions", [])
                ),
                seconds=300,
            )
        else:
            self.wait(
                "Flux refuses missing immutable artifact",
                lambda: self.get("ocirepository", "futureagi"),
                lambda d: flux_condition(d, "Ready", "False"),
                seconds=180,
            )
        if (
            prior != self.deployment_templates()
            or job_uid != self.get("job", "futureagi-bootstrap")["metadata"]["uid"]
        ):
            raise Refused(
                "missing artifact triggered bootstrap or application mutation"
            )
        self.save(
            "missing-digest.json",
            {
                "requested_digest": missing["digest"],
                "bootstrap_unchanged": True,
                "application_templates_unchanged": True,
            },
        )
        self.witnesses("after-missing-digest")
        self.select(self.artifacts["a"])
        self.successful(self.artifacts["a"])

    def failed_bootstrap(self):
        prior = self.deployment_templates()
        self.select(self.artifacts["bad"])
        if self.controller == "argo":
            doc = self.wait(
                "Argo surfaces failed bootstrap",
                lambda: self.get("application", "futureagi", "argocd"),
                lambda d: argo_at_digest(d, self.artifacts["bad"]["digest"], "Failed"),
            )
        else:
            doc = self.wait(
                "Flux surfaces failed bootstrap",
                lambda: self.get("helmrelease", "futureagi"),
                lambda d: (
                    flux_condition(d, "Ready", "False")
                    and d.get("status", {}).get("lastAttemptedRevisionDigest")
                    == self.artifacts["bad"]["digest"]
                    and d.get("status", {}).get("upgradeFailures", 0) >= 1
                ),
            )
        job = self.get("job", "futureagi-bootstrap")
        if not any(
            c.get("type") == "Failed" and c.get("status") == "True"
            for c in job.get("status", {}).get("conditions", [])
        ):
            raise Refused("failure was not the injected bootstrap Job")
        if prior != self.deployment_templates():
            raise Refused("application templates changed despite failed bootstrap")
        self.save(
            "bootstrap-failure.json",
            {
                "job_uid": job["metadata"]["uid"],
                "job_status": job["status"],
                "controller_status": status_projection(doc["status"]),
                "old_application_templates_unchanged": True,
            },
        )
        self.witnesses("after-failed-bootstrap")
        # Explicit operator decision to repair forward; no automatic retry or
        # database restore is inferred from a failed Helm/Argo operation.
        self.pause()
        self.select(self.artifacts["b"])
        self.resume()
        self.successful(self.artifacts["b"])
        self.witnesses("after-forward-repair")
        self.browser("after-forward-repair")

    def pause(self):
        if self.controller == "argo":
            self.app["spec"]["syncPolicy"]["automated"]["enabled"] = False
            self.apply(self.app)
            current = self.get("application", "futureagi", "argocd")
            if current["spec"]["syncPolicy"]["automated"]["enabled"] is not False:
                raise Refused("Argo pause not observed")
            if (
                current.get("operation")
                or current.get("status", {}).get("operationState", {}).get("phase")
                == "Running"
            ):
                raise Refused("Argo operation still active; refusing manual recovery")
        else:
            self.release["spec"]["suspend"] = True
            self.apply(self.release)
            if self.get("helmrelease", "futureagi")["spec"].get("suspend") is not True:
                raise Refused("Flux pause not observed")
        job = self.get("job", "futureagi-bootstrap")
        if job.get("status", {}).get("active", 0):
            raise Refused("bootstrap still active during handoff")
        print("PASS pause with terminal bootstrap", flush=True)

    def resume(self):
        if self.controller == "argo":
            self.app["spec"]["syncPolicy"]["automated"]["enabled"] = True
            self.apply(self.app)
        else:
            self.release["spec"]["suspend"] = False
            self.apply(self.release)

    def untrusted_source(self):
        if self.controller != "flux":
            # Argo's boundary is external approval, not an invented controller
            # verifier. The real cosign wrong-key check ran while packaging.
            self.save(
                "untrusted-source.json",
                {
                    "boundary": "external verification before Git approval",
                    "wrong_key_cosign_refused": True,
                    "untrusted_digest_applied_to_argo": False,
                },
            )
            return
        prior = self.deployment_templates()
        self.select(self.artifacts["untrusted"])
        source = self.wait(
            "Flux rejects wrong-key chart",
            lambda: self.get("ocirepository", "futureagi"),
            lambda d: flux_condition(d, "SourceVerified", "False"),
            seconds=180,
        )
        if prior != self.deployment_templates():
            raise Refused("untrusted chart changed application templates")
        self.save(
            "untrusted-source.json",
            {
                "source_status": status_projection(source["status"]),
                "old_application_templates_unchanged": True,
            },
        )
        self.witnesses("after-untrusted-source")
        self.select(self.artifacts["b"])
        self.successful(self.artifacts["b"])

    def image_only_rollback(self):
        # Both test releases contain the same source-built application images.
        # The migration inventory witness proves this fixture's schema did not
        # change. This is NOT rollback across an arbitrary data migration.
        self.pause()
        self.select(self.artifacts["a"])
        self.resume()
        self.successful(self.artifacts["a"])
        self.witnesses("after-reviewed-image-config-rollback")
        self.browser("after-reviewed-image-config-rollback")

    def permissions(self):
        pods = self.get("pods")["items"]
        application = [
            d
            for d in pods
            if d.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/instance")
            == "futureagi"
        ]
        if not application or any(
            d["spec"].get("automountServiceAccountToken") is not False
            for d in application
        ):
            raise Refused("application pod has implicit API token access")
        if self.controller == "flux":
            identity = "system:serviceaccount:futureagi:futureagi-reconciler"
            for args in [
                ("create", "clusterroles"),
                ("create", "rolebindings", "-n", NS),
                ("get", "secrets", "-n", "kube-system"),
            ]:
                result = self.kubectl(
                    "auth", "can-i", *args, "--as", identity, check=False
                )
                if result.stdout.strip() != "no":
                    raise Refused("reconciler authority exceeds namespace boundary")
        self.save(
            "permissions.json",
            {
                "application_pods": len(application),
                "api_token_automount": False,
                "flux_namespace_denials_checked": self.controller == "flux",
            },
        )

    def cleanup(self):
        results = {}
        if self.cluster_attempted:
            # Namespace diagnostics exclude Secrets and raw Helm values.
            for kind in ("deployments,statefulsets,jobs,pvc", "events"):
                result = self.kubectl(
                    "-n", NS, "get", kind, "-o", "json", check=False, timeout=60
                )
                if result.returncode == 0:
                    self.save(
                        "final-" + kind.replace(",", "-") + ".json",
                        json.loads(result.stdout),
                    )
            results["cluster_delete_exit"] = self.run(
                ["kind", "delete", "cluster", "--name", self.cluster],
                check=False,
                timeout=180,
            ).returncode
            remaining_nodes = self.run(
                [
                    "docker",
                    "ps",
                    "-a",
                    "--filter",
                    "label=io.x-k8s.kind.cluster=" + self.cluster,
                    "--format",
                    "{{.ID}}",
                ]
            )
            results["cluster_absent"] = (
                not remaining_nodes.stdout.strip()
                and self.cluster
                not in self.run(["kind", "get", "clusters"]).stdout.splitlines()
            )
        if self.registry_attempted:
            check = self.run(["docker", "inspect", self.registry], check=False)
            if check.returncode == 0:
                actual = json.loads(check.stdout)[0]
                if (
                    actual.get("Config", {})
                    .get("Labels", {})
                    .get("futureagi.gitops.fixture")
                    != self.run_id
                ):
                    raise Refused("registry ownership changed; refusing deletion")
                results["registry_delete_exit"] = self.run(
                    ["docker", "rm", "-f", self.registry], check=False
                ).returncode
            remaining_registry = self.run(
                [
                    "docker",
                    "ps",
                    "-a",
                    "--filter",
                    "name=^/" + self.registry + "$",
                    "--format",
                    "{{.ID}}",
                ]
            )
            results["registry_absent"] = not remaining_registry.stdout.strip()
        self.save("cleanup.json", results)
        shutil.rmtree(self.work)
        if (self.cluster_attempted and not results.get("cluster_absent")) or (
            self.registry_attempted and not results.get("registry_absent")
        ):
            raise Refused("owned fixture cleanup is incomplete")

    def exercise(self):
        error = None
        try:
            self.start()
            self.select(self.artifacts["a"])
            self.successful(self.artifacts["a"])
            self.witnesses("installed-a", seed=True)
            self.browser("installed-a", first_login=True)
            self.permissions()
            self.pinned_digest_ignores_moved_tag()
            self.missing_digest()
            self.failed_bootstrap()
            self.untrusted_source()
            self.image_only_rollback()
        except Exception as exc:
            error = str(exc)
            print("FAIL " + error, flush=True)
        finally:
            try:
                self.cleanup()
            except Exception as exc:
                error = (error + "; " if error else "") + "cleanup: " + str(exc)
            self.save(
                "summary.json",
                {
                    "status": "failed" if error is not None else "passed",
                    "error": error,
                    "controller": self.controller,
                    "checkpoints": self.checkpoints,
                    "test_key_only": True,
                    "database_migration_rollback_qualified": False,
                    "artifacts": self.artifacts,
                    "controller_version": PINS[self.controller]["version"],
                },
            )
        return error is None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", choices=("argo", "flux"), required=True)
    parser.add_argument("--image-tag", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", args.image_tag):
        parser.error("image tag is invalid")
    return 0 if Fixture(args.controller, args.image_tag, args.output).exercise() else 1


if __name__ == "__main__":
    raise SystemExit(main())
