#!/usr/bin/env python3
"""Invariants of the rendered manifests, run by hack/check.sh.

    python3 hack/rendered_checks.py <directory of rendered value sets>

Each <name>.yaml in the directory is one `helm template` output. Needs PyYAML.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

WORKLOADS = ("Deployment", "StatefulSet", "Job", "Pod")
REFERENCE = re.compile(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)")
# The public URLs every Python container gets, per value set: the port-forward
# defaults, the ingress hosts, and urls.otlp winning over the ingress.
PORT_FORWARD_URLS = {
    "APP_URL": "http://localhost:3000",
    "FRONTEND_URL": "http://localhost:3000",
    "BASE_URL": "http://localhost:8000",
    "FI_COLLECTOR_PUBLIC_URL": "http://localhost:4318",
}
INGRESS_URLS = {
    "APP_URL": "https://futureagi.example.com",
    "FRONTEND_URL": "https://futureagi.example.com",
    "BASE_URL": "https://api.futureagi.example.com",
    "FI_COLLECTOR_PUBLIC_URL": "https://api.futureagi.example.com",
}
EXPECTED_URLS = {
    "bundled": PORT_FORWARD_URLS,
    # urls.app and urls.api, no OTLP URL: SDKs still go through the port-forward.
    "external": {**INGRESS_URLS, "FI_COLLECTOR_PUBLIC_URL": "http://localhost:4318"},
    "ingress": INGRESS_URLS,
    "bundled-ingress": INGRESS_URLS,
    "all-components": INGRESS_URLS,
    "overrides": {
        **PORT_FORWARD_URLS,
        "FI_COLLECTOR_PUBLIC_URL": "https://otlp.futureagi.example.com",
    },
}


def pod_spec(doc: dict) -> dict:
    return doc["spec"] if doc["kind"] == "Pod" else doc["spec"]["template"]["spec"]


def containers(doc: dict) -> list[dict]:
    spec = pod_spec(doc)
    return spec.get("initContainers", []) + spec["containers"]


def env_values(container: dict) -> dict:
    return {e["name"]: e.get("value") for e in container.get("env", [])}


def component(doc: dict) -> str:
    return doc["metadata"]["labels"].get("app.kubernetes.io/component", "")


def check_render(name: str, docs: list[dict]) -> list[str]:
    failed = []
    workloads = [d for d in docs if d["kind"] in WORKLOADS]
    job_hooks = [
        d["metadata"].get("annotations", {}).get("helm.sh/hook", "")
        for d in docs
        if d["kind"] == "Job"
    ]
    post_install = any("post-install" in hook for hook in job_hooks)

    for doc in workloads:
        where = f"{name}: {doc['kind']} {doc['metadata']['name']}"
        for container in containers(doc):
            env = container.get("env", [])
            names = [e["name"] for e in env]
            values = env_values(container)
            if len(names) != len(set(names)):
                failed.append(f"{where}: duplicate env names")
            # Kubernetes expands $(NAME) only from variables defined earlier.
            for index, entry in enumerate(env):
                for ref in REFERENCE.findall(entry.get("value") or ""):
                    if ref not in names[:index]:
                        failed.append(
                            f"{where}: {entry['name']} references $({ref}) before it is defined"
                        )
            if "NO_STARTUP_DB_MUTATIONS" not in values:
                continue
            # Only the bootstrap job may change the databases.
            if (values["NO_STARTUP_DB_MUTATIONS"] == "false") != (doc["kind"] == "Job"):
                failed.append(f"{where}/{container['name']}: NO_STARTUP_DB_MUTATIONS")
            # With its scheme: a bare host gets https links under ENV_TYPE=production.
            if not (values.get("APP_URL") or "").startswith(("http://", "https://")):
                failed.append(f"{where}: APP_URL is not a URL")
            if not (values.get("FI_COLLECTOR_PUBLIC_URL") or "").startswith("http"):
                failed.append(f"{where}: FI_COLLECTOR_PUBLIC_URL is not a URL")
            for key, expected in EXPECTED_URLS.get(name, {}).items():
                if values.get(key) != expected:
                    failed.append(
                        f"{where}: {key} is {values.get(key)!r}, expected {expected!r}"
                    )

        if doc["kind"] == "Deployment" and component(doc).startswith("worker"):
            spec = pod_spec(doc)
            if spec.get("initContainers"):
                # `helm install --wait` would wait on them before post-install hooks.
                failed.append(f"{where}: workers must not have initContainers")
            command = " ".join(spec["containers"][0]["command"])
            waits = "migrate --check" in command
            if waits != post_install:
                failed.append(
                    f"{where}: waits for migrations={waits}, bootstrap post-install={post_install}"
                )

    # A LoadBalancer or NodePort Service must stay reachable under NetworkPolicies.
    policies = [d for d in docs if d["kind"] == "NetworkPolicy"]
    for service in (d for d in docs if d["kind"] == "Service"):
        if not policies or service["spec"].get("type", "ClusterIP") == "ClusterIP":
            continue
        target = service["spec"]["selector"]["app.kubernetes.io/component"]
        wanted = {p["targetPort"] for p in service["spec"]["ports"]} - {"admin"}
        open_ports = set()
        for policy in policies:
            selector = policy["spec"]["podSelector"].get("matchLabels", {})
            if selector.get("app.kubernetes.io/component") != target:
                continue
            for rule in policy["spec"].get("ingress", []):
                if "from" not in rule:
                    open_ports |= {p["port"] for p in rule.get("ports", [])}
        if not wanted <= open_ports:
            failed.append(
                f"{name}: {service['spec']['type']} Service {service['metadata']['name']} "
                f"has ports {sorted(wanted - open_ports)} no NetworkPolicy opens to any source"
            )

    # The gateway listens where its probes and Service point.
    for doc in workloads:
        if component(doc) != "agentcc-gateway":
            continue
        container = pod_spec(doc)["containers"][0]
        port = next(
            p["containerPort"] for p in container["ports"] if p["name"] == "http"
        )
        if env_values(container).get("AGENTCC_PORT") != str(port):
            failed.append(
                f"{name}: gateway containerPort {port} differs from AGENTCC_PORT"
            )
        for config_map in (d for d in docs if d["kind"] == "ConfigMap"):
            if component(config_map) == "agentcc-gateway":
                config = yaml.safe_load(config_map["data"]["config.yaml"])
                if config["server"]["port"] != port:
                    failed.append(
                        f"{name}: gateway containerPort {port} differs from server.port"
                    )

    # The Secret comes back with `helm rollback`.
    for secret in (d for d in docs if d["kind"] == "Secret"):
        hook = secret["metadata"].get("annotations", {}).get("helm.sh/hook", "")
        if hook and "pre-rollback" not in hook:
            failed.append(
                f"{name}: Secret {secret['metadata']['name']} is not a pre-rollback hook"
            )
    return failed


def by_name(docs: list[dict], kind: str) -> dict[str, dict]:
    return {d["metadata"]["name"]: d for d in docs if d["kind"] == kind}


def check_overrides(bundled: list[dict], overrides: list[dict]) -> list[str]:
    """ci/overrides.yaml over examples/bundled.yaml."""
    failed = []
    # Neither a new LLM key nor a chart upgrade restarts a bundled datastore.
    before, after = by_name(bundled, "StatefulSet"), by_name(overrides, "StatefulSet")
    for name, statefulset in before.items():
        if statefulset["spec"]["template"] != after[name]["spec"]["template"]:
            failed.append(f"overrides: the pod template of StatefulSet {name} changed")

    deployments = by_name(overrides, "Deployment")
    queue = next(d for n, d in deployments.items() if n.endswith("-worker-tasks-s"))
    values = env_values(pod_spec(queue)["containers"][0])
    if values.get("TEMPORAL_MAX_CONCURRENT_ACTIVITIES") != "7":
        failed.append(
            "overrides: a queue's extraEnv does not win over the chart's TEMPORAL_*"
        )
    if values.get("OPENAI_API_KEY") != "sk-queue-ci":
        failed.append(
            "overrides: a queue's extraEnv does not replace the secret-backed OPENAI_API_KEY"
        )
    for doc in deployments.values():
        container = pod_spec(doc)["containers"][0]
        if (
            "REDIS_URL" in env_values(container)
            and env_values(container).get("REDIS_PASSWORD") != "override-ci"
        ):
            failed.append(
                f"overrides: {doc['metadata']['name']} ignores config.extraEnv.REDIS_PASSWORD"
            )

    mounts = {
        component(d): {
            m["mountPath"] for m in pod_spec(d)["containers"][0].get("volumeMounts", [])
        }
        for d in overrides
        if d["kind"] in ("Deployment", "Job")
    }
    for name in ("bootstrap", "fi-collector"):
        if "/etc/pg-ca" not in mounts.get(name, set()):
            failed.append(f"overrides: {name} does not mount its extraVolumeMounts")
    return failed


def main() -> int:
    out = Path(sys.argv[1])
    renders = {
        path.stem: [d for d in yaml.safe_load_all(path.read_text()) if d]
        for path in sorted(out.glob("*.yaml"))
    }
    failed = []
    for name, docs in renders.items():
        failed += check_render(name, docs)
    if "bundled" in renders and "overrides" in renders:
        failed += check_overrides(renders["bundled"], renders["overrides"])
    if failed:
        print("rendered manifests break the chart's invariants:", file=sys.stderr)
        for line in failed:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(f"ok   invariants hold in {len(renders)} renders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
