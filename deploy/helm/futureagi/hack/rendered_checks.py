#!/usr/bin/env python3
"""Invariants of the rendered manifests, run by hack/check.sh.

    python3 hack/rendered_checks.py <directory of rendered value sets> \
        [--compose <repository>/docker-compose.distributed.yml]

Each <name>.yaml in the directory is one `helm template` output. With
--compose, the Python services of the bundled and all-components renders must
also default the behaviour settings as that compose file does. Needs PyYAML.
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


# Behaviour every setup gives the backend, workers and serving: not addresses
# or credentials. docker-compose.distributed.yml's ${VAR:-default} counts as
# its default.
COMPOSE_BACKEND_DEFAULTS = (
    "AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS",
    "CH25_DROP_LEGACY_CDC_CHAIN",
    "CH25_EVAL_LOGGER_TABLE",
    "CH25_QUERY_TYPES_V2_ONLY",
    "CHANNEL_LAYER_BACKEND",
    "CH_USE_REPLICATED_ENGINES",
    "CODE_EXECUTOR_LOCAL_FALLBACK",
    "DJANGO_SETTINGS_MODULE",
    "EXACT_AGGREGATION_TASK_QUEUE",
    "FI_SKIP_CH25_MIGRATION",
    "NO_STARTUP_DB_MUTATIONS",
    "PROPERTY_CATALOG_CH_USER",
    "PROPERTY_CATALOG_DATABASE",
    "USAGE_EVENTS_ENABLED",
)
COMPOSE_DEFAULT = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?-([^${}]*)\}")

# Argo CD's sync order, as gitops-engine computes it (pkg/sync: hook/hook.go,
# hook/helm/type.go, syncwaves/waves.go, sync_phase.go, sync_tasks.go). Its own
# hook annotation replaces the Helm one; without it, every Helm install and
# upgrade hook applies on every sync, since Argo CD does not tell them apart.
ARGO_HOOK = "argocd.argoproj.io/hook"
ARGO_PHASES = {"PreSync": -1, "Sync": 0, "PostSync": 1}
ARGO_HOOK_TYPES = (*ARGO_PHASES, "SyncFail", "Skip")
HELM_HOOK_PHASES = {
    "pre-install": "PreSync",
    "pre-upgrade": "PreSync",
    "post-install": "PostSync",
    "post-upgrade": "PostSync",
}
DATASTORES = ("postgres", "clickhouse", "redis", "temporal", "minio")


def pod_spec(doc: dict) -> dict:
    return doc["spec"] if doc["kind"] == "Pod" else doc["spec"]["template"]["spec"]


def containers(doc: dict) -> list[dict]:
    spec = pod_spec(doc)
    return spec.get("initContainers", []) + spec["containers"]


def env_values(container: dict) -> dict:
    return {e["name"]: e.get("value") for e in container.get("env", [])}


def component(doc: dict) -> str:
    return doc["metadata"]["labels"].get("app.kubernetes.io/component", "")


def annotation_csv(doc: dict, key: str) -> list[str]:
    value = (doc["metadata"].get("annotations") or {}).get(key, "")
    items = (item.strip() for item in value.split(","))
    return list(dict.fromkeys(item for item in items if item))


def argo_steps(doc: dict) -> set[tuple[str, int]]:
    """The (phase, wave) steps of an Argo CD sync that apply this manifest,
    leaving out SyncFail, which runs only when a sync fails."""
    annotations = doc["metadata"].get("annotations") or {}
    types = [t for t in annotation_csv(doc, ARGO_HOOK) if t in ARGO_HOOK_TYPES]
    if types == ["Skip"]:
        return set()
    if not types:
        helm = annotation_csv(doc, "helm.sh/hook")
        types = [HELM_HOOK_PHASES[t] for t in helm if t in HELM_HOOK_PHASES]
    helm_hook = annotations.get("helm.sh/hook", "crd-install") != "crd-install"
    hook = ARGO_HOOK in annotations or helm_hook
    phases = {t for t in types if t in ARGO_PHASES} if hook else {"Sync"}
    wave = 0
    for key in ("argocd.argoproj.io/sync-wave", "helm.sh/hook-weight"):
        try:
            wave = int(annotations[key])
            break
        except (KeyError, ValueError):
            continue
    return {(phase, wave) for phase in phases}


def argo_order(step: tuple[str, int]) -> tuple[int, int]:
    return ARGO_PHASES[step[0]], step[1]


def references(spec: dict) -> set[tuple[str, str]]:
    """The Secrets, ConfigMaps and ServiceAccount a pod spec reads."""
    found = set()
    if spec.get("serviceAccountName"):
        found.add(("ServiceAccount", spec["serviceAccountName"]))

    def walk(node):
        if isinstance(node, dict):
            for key, kind in (
                ("secretKeyRef", "Secret"),
                ("secretRef", "Secret"),
                ("configMapKeyRef", "ConfigMap"),
                ("configMapRef", "ConfigMap"),
                ("configMap", "ConfigMap"),
            ):
                if isinstance(node.get(key), dict) and node[key].get("name"):
                    found.add((kind, node[key]["name"]))
            secret = node.get("secret")
            if isinstance(secret, dict) and secret.get("secretName"):
                found.add(("Secret", secret["secretName"]))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(spec)
    return found


def check_bootstrap_order(name: str, docs: list[dict]) -> list[str]:
    """Helm runs the bootstrap job before an upgrade's pods; Argo CD runs it once
    per sync, after what it reads and the bundled datastores it waits for, and
    before the other pods, which need its schema."""
    failed = []
    for job in (d for d in docs if d["kind"] == "Job" and component(d) == "bootstrap"):
        where = f"{name}: Job {job['metadata']['name']}"
        if "pre-upgrade" not in annotation_csv(job, "helm.sh/hook"):
            failed.append(f"{where} is not a pre-upgrade hook")
        steps = argo_steps(job)
        if len(steps) != 1:
            failed.append(f"{where}: Argo CD runs it {len(steps)} times per sync")
        reads = references(pod_spec(job))
        for step in sorted(steps, key=argo_order):
            early, late = [], []
            for doc in docs:
                ref = (doc["kind"], doc["metadata"]["name"])
                needed = ref in reads or component(doc) in DATASTORES
                waiting = doc["kind"] in WORKLOADS and doc is not job and not needed
                for other in argo_steps(doc):
                    at = f"{' '.join(ref)} ({other[0]} wave {other[1]})"
                    if needed and argo_order(other) >= argo_order(step):
                        early.append(at)
                    if waiting and argo_order(other) <= argo_order(step):
                        late.append(at)
            when = f"{where}: Argo CD runs it in {step[0]} wave {step[1]}"
            if early:
                failed.append(f"{when}, not after {', '.join(sorted(early))}")
            if late:
                failed.append(f"{when}, not before {', '.join(sorted(late))}")
    return failed


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

    # The collector writes observed attributes (property suggestions) into the
    # index the bootstrap job provisions: its database, as its writer.
    by_component = {component(d): pod_spec(d)["containers"][0] for d in workloads}
    collector, bootstrap = (
        by_component.get("fi-collector"),
        by_component.get("bootstrap"),
    )
    if collector and bootstrap:
        writes, provisions = env_values(collector), env_values(bootstrap)

        def secret_key(container: dict, variable: str) -> dict | None:
            for entry in container.get("env", []):
                if entry["name"] == variable:
                    return entry.get("valueFrom", {}).get("secretKeyRef")
            return None

        writer = secret_key(collector, "FI_OBSERVED_CATALOG_CH_PASSWORD")
        if (
            writes.get("FI_OBSERVED_CATALOG_MODE") != "direct"
            or writes.get("FI_OBSERVED_CATALOG_CH_URL") != writes.get("FI_CH_URL")
            or writes.get("FI_OBSERVED_CATALOG_CH_DATABASE")
            != provisions.get("PROPERTY_CATALOG_DATABASE")
            or writes.get("FI_OBSERVED_CATALOG_CH_USERNAME")
            != "observed_catalog_writer"
            or not writer
            or writer != secret_key(bootstrap, "PROPERTY_CATALOG_CONSUMER_PASSWORD")
        ):
            failed.append(
                f"{name}: fi-collector does not write the observed-attribute index "
                "the bootstrap job provisions"
            )

    # The gateway's request logs reach the backend only with the secret it
    # checks them against, and the chart's Secret holds one unless it is given.
    webhook = {}
    for doc in workloads:
        for container in containers(doc):
            for entry in container.get("env", []):
                if entry["name"] == "AGENTCC_WEBHOOK_SECRET":
                    ref = entry.get("valueFrom", {}).get("secretKeyRef", {})
                    webhook[component(doc)] = (ref.get("name"), ref.get("key"))
    if "agentcc-gateway" in {component(d) for d in workloads} and (
        webhook.get("agentcc-gateway") is None
        or webhook.get("agentcc-gateway") != webhook.get("backend")
    ):
        failed.append(
            f"{name}: the gateway and the backend do not share AGENTCC_WEBHOOK_SECRET"
        )
    # The gateway and the backend refuse or allow private provider URLs alike.
    private_urls = {
        component(doc): env_values(container).get("AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS")
        for doc in workloads
        for container in containers(doc)
        if component(doc) in ("agentcc-gateway", "backend")
    }
    if len(private_urls) == 2 and len(set(private_urls.values())) != 1:
        failed.append(
            f"{name}: gateway and backend disagree on AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS"
        )
    secret_keys = {
        (d["metadata"]["name"], key)
        for d in docs
        if d["kind"] == "Secret"
        for key in d.get("data") or {}
    }
    rendered_secrets = {d["metadata"]["name"] for d in docs if d["kind"] == "Secret"}
    holders = {ref for ref in secret_keys if ref[1] == "AGENTCC_WEBHOOK_SECRET"}
    for ref in set(webhook.values()):
        # Pods may read it from a Secret the chart does not render
        # (secrets.existingSecret) only when the chart renders no copy of it.
        if ref not in secret_keys and (holders or ref[0] in rendered_secrets):
            failed.append(f"{name}: no Secret holds AGENTCC_WEBHOOK_SECRET {ref}")

    # The Secret comes back with `helm rollback`.
    for secret in (d for d in docs if d["kind"] == "Secret"):
        hook = secret["metadata"].get("annotations", {}).get("helm.sh/hook", "")
        if hook and "pre-rollback" not in hook:
            failed.append(
                f"{name}: Secret {secret['metadata']['name']} is not a pre-rollback hook"
            )
    return failed


def check_compose_defaults(name: str, docs: list[dict], compose: Path) -> list[str]:
    """The long-running Python containers against the compose service of the
    same name (x-backend-env for one it does not have)."""
    config = yaml.safe_load(compose.read_text())
    failed = []
    for doc in (d for d in docs if d["kind"] == "Deployment"):
        service = config["services"].get(component(doc), {})
        compose_env = service.get("environment") or config["x-backend-env"]
        for container in containers(doc):
            values = env_values(container)
            if "DJANGO_SETTINGS_MODULE" not in values:
                continue
            for key in COMPOSE_BACKEND_DEFAULTS:
                expected = str(compose_env[key])
                while COMPOSE_DEFAULT.search(expected):
                    expected = COMPOSE_DEFAULT.sub(r"\1", expected)
                if values.get(key) != expected:
                    failed.append(
                        f"{name}: {doc['metadata']['name']}/{container['name']} sets {key}="
                        f"{values.get(key)!r}; {compose.name} defaults it to {expected!r}"
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


def check_gitops(docs: list[dict]) -> list[str]:
    """ci/gitops.yaml: the application keys in secrets.existingSecret.

    Argo CD renders without `lookup`, so a generated value would change
    on every sync: every pod reads AGENTCC_WEBHOOK_SECRET from the existing
    Secret, and no Secret the chart renders holds one.
    """
    failed = []
    for doc in (d for d in docs if d["kind"] in WORKLOADS):
        for container in containers(doc):
            for entry in container.get("env", []):
                if entry["name"] != "AGENTCC_WEBHOOK_SECRET":
                    continue
                ref = entry.get("valueFrom", {}).get("secretKeyRef", {})
                if (ref.get("name"), ref.get("key")) != (
                    "futureagi-app",
                    "AGENTCC_WEBHOOK_SECRET",
                ):
                    failed.append(
                        f"gitops: {doc['kind']} {doc['metadata']['name']} reads "
                        f"AGENTCC_WEBHOOK_SECRET from {ref}, not secrets.existingSecret"
                    )
    for secret in (d for d in docs if d["kind"] == "Secret"):
        if "AGENTCC_WEBHOOK_SECRET" in (secret.get("data") or {}):
            failed.append(
                f"gitops: Secret {secret['metadata']['name']} generates AGENTCC_WEBHOOK_SECRET"
            )
    return failed


def main() -> int:
    out = Path(sys.argv[1])
    compose = Path(sys.argv[3]) if sys.argv[2:3] == ["--compose"] else None
    renders = {
        path.stem: [d for d in yaml.safe_load_all(path.read_text()) if d]
        for path in sorted(out.glob("*.yaml"))
    }
    failed = []
    for name, docs in renders.items():
        failed += check_render(name, docs)
        failed += check_bootstrap_order(name, docs)
    if "bundled" in renders and "overrides" in renders:
        failed += check_overrides(renders["bundled"], renders["overrides"])
    if "gitops" in renders:
        failed += check_gitops(renders["gitops"])
    for name in ("bundled", "all-components"):
        if compose and name in renders:
            failed += check_compose_defaults(name, renders[name], compose)
    if failed:
        print("rendered manifests break the chart's invariants:", file=sys.stderr)
        for line in failed:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(f"ok   invariants hold in {len(renders)} renders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
