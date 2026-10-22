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
from urllib.parse import urlparse

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
    "MINIO_URL": "http://localhost:9005",
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
        # objectStorage.bundled.service.downloadPort
        "MINIO_URL": "http://localhost:9100",
    },
    "gateway-api": INGRESS_URLS,
    "local": PORT_FORWARD_URLS,
    "ingress-traefik": INGRESS_URLS,
    "digests": PORT_FORWARD_URLS,
    "digests-unpinned": PORT_FORWARD_URLS,
}
CHART = Path(__file__).resolve().parent.parent
APP_VERSION = str(yaml.safe_load((CHART / "Chart.yaml").read_text())["appVersion"])
# ci/digests.yaml: the digest stamped for each published repository.
STAMPED = {
    "futureagi/future-agi": "sha256:" + "1" * 64,
    "futureagi/frontend": "sha256:" + "2" * 64,
    "futureagi/fi-collector": "sha256:" + "3" * 64,
    "futureagi/agentcc-gateway": "sha256:" + "4" * 64,
    "futureagi/serving": "sha256:" + "5" * 64,
    "futureagi/code-executor": "sha256:" + "6" * 64,
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


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def is_public(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return bool(host) and host not in LOCAL_HOSTS and not host.endswith(".localhost")


def check_python_env(where: str, values: dict) -> list[str]:
    """Wiring every Python container needs (backend, workers, bootstrap)."""
    failed = []
    collector = (
        f"{values.get('FI_COLLECTOR_HOST')}:{values.get('FI_COLLECTOR_OTLP_PORT')}"
    )
    # Simulation and voice spans go to this release's collector, over gRPC.
    if values.get("SIM_COLLECTOR_OTLP_ENDPOINT") != collector:
        failed.append(
            f"{where}: SIM_COLLECTOR_OTLP_ENDPOINT is "
            f"{values.get('SIM_COLLECTOR_OTLP_ENDPOINT')!r}, expected {collector!r}"
        )
    # The license's minimum-version check reads APP_VERSION.
    if not values.get("APP_VERSION") or values.get("APP_VERSION") != values.get(
        "FUTURE_AGI_VERSION"
    ):
        failed.append(f"{where}: APP_VERSION is not the running version")
    # The bootstrap job (the only one allowed to migrate) connects directly.
    if values.get("NO_STARTUP_DB_MUTATIONS") == "false" and (
        values.get("PGBOUNCER_HOST") != values.get("PG_HOST")
        or "PGBOUNCER_READ_HOST" in values
    ):
        failed.append(f"{where}: the bootstrap job goes through a pooler or replica")
    # Public URLs: no wildcard CORS with credentials, no wildcard Host.
    app, api = values.get("APP_URL") or "", values.get("BASE_URL") or ""
    if is_public(app) and not values.get("CORS_ALLOWED_ORIGINS"):
        failed.append(f"{where}: the UI is public ({app}) but CORS allows every origin")
    cors = (values.get("CORS_ALLOWED_ORIGINS") or "").split(",")
    if (
        is_public(app)
        and f"{urlparse(app).scheme}://{urlparse(app).netloc}" not in cors
    ):
        failed.append(f"{where}: CORS_ALLOWED_ORIGINS {cors} misses the UI origin")
    hosts = (values.get("ALLOWED_HOSTS") or "").split(",")
    if is_public(api) and (
        "*" in hosts or urlparse(api).hostname not in hosts or "localhost" not in hosts
    ):
        failed.append(
            f"{where}: ALLOWED_HOSTS {hosts} should list the API host and localhost, not *"
        )
    return failed


def check_gateway_redis(name: str, docs: list[dict]) -> list[str]:
    """A gateway that can run more than one replica shares its state in Redis,
    never in the app's Redis over TLS (the gateway has no Redis TLS)."""
    failed = []
    scaled = {
        d["spec"]["scaleTargetRef"]["name"]
        for d in docs
        if d["kind"] == "HorizontalPodAutoscaler"
    }
    app = next(
        (
            env_values(pod_spec(d)["containers"][0])
            for d in docs
            if d["kind"] == "Deployment" and component(d) == "backend"
        ),
        {},
    )
    app_tls_redis = (
        f"{app.get('REDIS_HOST')}:{app.get('REDIS_PORT')}"
        if (app.get("REDIS_URL") or "").startswith("rediss://")
        else None
    )
    for doc in docs:
        if doc["kind"] != "Deployment" or component(doc) != "agentcc-gateway":
            continue
        values = env_values(pod_spec(doc)["containers"][0])
        multi = doc["metadata"]["name"] in scaled or doc["spec"].get("replicas", 1) > 1
        if multi and not values.get("AGENTCC_REDIS_ADDRESS"):
            failed.append(f"{name}: the gateway runs several replicas without Redis")
        if app_tls_redis and values.get("AGENTCC_REDIS_ADDRESS") == app_tls_redis:
            failed.append(
                f"{name}: the gateway, which has no Redis TLS, uses the app's Redis over TLS"
            )
        if values.get("AGENTCC_REDIS_DB") in {"0", "1", "2", "3"}:
            failed.append(
                f"{name}: the gateway shares Redis database {values['AGENTCC_REDIS_DB']} with the app"
            )
    return failed


def prestop_seconds(container: dict) -> int:
    pre = container.get("lifecycle", {}).get("preStop", {})
    if "sleep" in pre:
        return int(pre["sleep"]["seconds"])
    command = pre.get("exec", {}).get("command", [])
    return int(command[1]) if command[:1] == ["sleep"] else 0


def check_scheduling(name: str, docs: list[dict]) -> list[str]:
    """Spread constraints select their own component; worker grace periods
    cover the preStop sleep and the graceful shutdown; the exact-aggregation
    worker stays one single-slot replica."""
    failed = []
    scaled = {
        d["spec"]["scaleTargetRef"]["name"]
        for d in docs
        if d["kind"] == "HorizontalPodAutoscaler"
    }
    for doc in docs:
        if doc["kind"] != "Deployment":
            continue
        where = f"{name}: Deployment {doc['metadata']['name']}"
        spec = pod_spec(doc)
        own = component(doc)
        for constraint in spec.get("topologySpreadConstraints", []):
            labels = constraint.get("labelSelector", {}).get("matchLabels", {})
            if labels.get("app.kubernetes.io/component") != own:
                failed.append(f"{where}: a spread constraint does not select {own}")
        container = spec["containers"][0]
        grace = spec.get("terminationGracePeriodSeconds", 30)
        if prestop_seconds(container) >= grace:
            failed.append(f"{where}: the preStop sleep uses up the grace period")
        if not own.startswith("worker"):
            continue
        values = env_values(container)
        graceful = int(values["TEMPORAL_GRACEFUL_SHUTDOWN_TIMEOUT"])
        if grace != prestop_seconds(container) + graceful + 30:
            failed.append(
                f"{where}: grace {grace} is not preStop + graceful shutdown + 30"
            )
        if own == "worker-exact-aggregation" and (
            doc["metadata"]["name"] in scaled
            or doc["spec"].get("replicas") != 1
            or values.get("TEMPORAL_MAX_CONCURRENT_ACTIVITIES") != "1"
        ):
            failed.append(
                f"{where}: the exact-aggregation worker must be one single-slot replica"
            )
    return failed


def check_worker_queues(docs: list[dict]) -> list[str]:
    """ci/worker-queues.yaml over examples/bundled.yaml."""
    failed = []
    deployments = {component(d): d for d in docs if d["kind"] == "Deployment"}
    hpas = {component(d): d for d in docs if d["kind"] == "HorizontalPodAutoscaler"}
    pdbs = {component(d): d for d in docs if d["kind"] == "PodDisruptionBudget"}
    generic = env_values(pod_spec(deployments["worker"])["containers"][0])
    if (
        generic.get("TEMPORAL_EXCLUDED_QUEUES")
        != "simulation_runner,tasks_xl,agent_compass"
    ):
        failed.append(
            "worker-queues: the all-queues worker still polls the dedicated queues "
            f"({generic.get('TEMPORAL_EXCLUDED_QUEUES')!r})"
        )
    xl = pod_spec(deployments["worker-tasks-xl"])
    if xl.get("terminationGracePeriodSeconds") != 30 + 900 + 30:
        failed.append(
            "worker-queues: tasks_xl grace is not preStop 30 + graceful 900 + 30"
        )
    if xl.get("nodeSelector") != {"workload": "batch"} or not xl.get("tolerations"):
        failed.append("worker-queues: tasks_xl is not on its own pool")
    if xl.get("priorityClassName") != "futureagi-batch":
        failed.append("worker-queues: tasks_xl ignores its priorityClassName")
    policies = {
        name: pod_spec(deployments[name])["containers"][0]["imagePullPolicy"]
        for name in ("worker", "worker-tasks-xl")
    }
    if policies != {"worker": "IfNotPresent", "worker-tasks-xl": "Always"}:
        failed.append(
            f"worker-queues: pull policies {policies}: tasks_xl sets its own, Always"
        )
    hpa = hpas.get("worker-tasks-xl")
    metrics = {
        m["resource"]["name"]: m for m in (hpa or {}).get("spec", {}).get("metrics", [])
    }
    if not hpa or set(metrics) != {"memory"} or hpa["spec"]["maxReplicas"] != 3:
        failed.append(
            "worker-queues: tasks_xl HPA is not memory-only with maxReplicas 3"
        )
    if (hpa or {}).get("spec", {}).get("behavior", {}).get("scaleDown", {}).get(
        "stabilizationWindowSeconds"
    ) != 600:
        failed.append(
            "worker-queues: tasks_xl HPA does not inherit allQueues.autoscaling.behavior"
        )
    if pdbs.get("worker-tasks-xl", {}).get("spec", {}).get("maxUnavailable") != 2:
        failed.append("worker-queues: tasks_xl PDB ignores its own maxUnavailable")
    if "worker-agent-compass" in pdbs:
        failed.append(
            "worker-queues: agent_compass has a PDB although it turned it off"
        )
    backend_spread = pod_spec(deployments["backend"]).get(
        "topologySpreadConstraints", []
    )
    if not any(
        c["whenUnsatisfiable"] == "DoNotSchedule" and c.get("minDomains") == 2
        for c in backend_spread
    ):
        failed.append(
            "worker-queues: preset hard does not spread the two backend replicas"
        )
    if pod_spec(deployments["frontend"]).get("topologySpreadConstraints"):
        failed.append("worker-queues: a single-replica frontend got a spread preset")
    return failed


def check_pooler(docs: list[dict]) -> list[str]:
    """ci/pooler.yaml over examples/external.yaml: Django pooled, CDC direct."""
    failed = []
    for doc in docs:
        if doc["kind"] != "Deployment" or not component(doc).startswith(
            ("backend", "worker")
        ):
            continue
        values = env_values(pod_spec(doc)["containers"][0])
        expected = {
            "PGBOUNCER_HOST": "pgbouncer.db.svc",
            "PGBOUNCER_PORT": "6432",
            "PG_HOST": "postgres.example.internal",
            "PGBOUNCER_READ_HOST": "pgbouncer-read.db.svc",
            "PG_READ_DB": "futureagi",
            "READ_REPLICA_OPT_IN": "Dashboard,feature:dashboard_render",
        }
        for key, value in expected.items():
            if values.get(key) != value:
                failed.append(
                    f"pooler: {doc['metadata']['name']} {key} is {values.get(key)!r}, expected {value!r}"
                )
    return failed


def check_gateway_api(docs: list[dict]) -> list[str]:
    """examples/gateway-api.yaml and ci/gateway-api.yaml over bundled.yaml."""
    failed = []
    routes = {
        d["metadata"]["name"]: d
        for d in docs
        if d["kind"] in ("HTTPRoute", "GRPCRoute")
    }
    api = next((r for n, r in routes.items() if n.endswith("-api")), None)
    if not api:
        return ["gateway-api: no API HTTPRoute"]
    otlp = [
        rule
        for rule in api["spec"]["rules"]
        if {m["path"]["value"] for m in rule["matches"]}
        == {"/v1/traces", "/tracer/v1/traces"}
    ]
    if (
        not otlp
        or any(m["path"]["type"] != "Exact" for m in otlp[0]["matches"])
        or not otlp[0]["backendRefs"][0]["name"].endswith("-fi-collector")
        or otlp[0]["backendRefs"][0]["port"] != 4318
    ):
        failed.append(
            "gateway-api: OTLP/HTTP is not routed on exact paths to the collector's 4318"
        )
    ws = [r for r in api["spec"]["rules"] if r["matches"][0]["path"]["value"] == "/ws/"]
    if not ws or ws[0].get("timeouts", {}).get("request") != "24h":
        failed.append("gateway-api: the WebSocket rule has no long timeout")
    grpc = [r for r in routes.values() if r["kind"] == "GRPCRoute"]
    if not grpc or grpc[0]["spec"]["rules"][0]["backendRefs"][0]["port"] != 4317:
        failed.append("gateway-api: OTLP/gRPC is not routed to the collector's 4317")
    llm = next((r for n, r in routes.items() if n.endswith("-llm-gateway")), None)
    if not llm or llm["spec"]["hostnames"] != ["llm.futureagi.example.com"]:
        failed.append("gateway-api: no LLM gateway route")
    public = next(
        d
        for d in docs
        if d["kind"] == "NetworkPolicy" and d["metadata"]["name"].endswith("-public")
    )
    if (
        "agentcc-gateway"
        not in public["spec"]["podSelector"]["matchExpressions"][0]["values"]
    ):
        failed.append(
            "gateway-api: the routed LLM gateway is not in the public NetworkPolicy"
        )
    return failed


def check_health_check_policies(name: str, docs: list[dict]) -> list[str]:
    """GKE HealthCheckPolicies (gatewayApi.gke.healthChecks): one for each
    Service a route uses, probing what the pods' readiness probe probes (path,
    Host header, container port), because GKE's load balancer ignores the
    probes and would send GET / with the pod's IP as the Host."""
    failed = []
    policies = {
        d["spec"]["targetRef"]["name"]: d
        for d in docs
        if d["kind"] == "HealthCheckPolicy"
    }
    if not policies:
        return failed
    services = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Service"}
    deployments = {component(d): d for d in docs if d["kind"] == "Deployment"}
    routed: dict[str, set[int]] = {}
    for route in (d for d in docs if d["kind"] in ("HTTPRoute", "GRPCRoute")):
        for rule in route["spec"]["rules"]:
            for ref in rule["backendRefs"]:
                routed.setdefault(ref["name"], set()).add(ref["port"])
    if set(policies) != set(routed):
        failed.append(
            f"{name}: HealthCheckPolicies target {sorted(policies)}, the routes use {sorted(routed)}"
        )
    for target, policy in policies.items():
        where = f"{name}: HealthCheckPolicy {policy['metadata']['name']}"
        service = services.get(target)
        deployment = service and deployments.get(
            service["spec"]["selector"]["app.kubernetes.io/component"]
        )
        if not deployment:
            failed.append(f"{where}: no Service and Deployment {target}")
            continue
        container = pod_spec(deployment)["containers"][0]
        ports = {p["name"]: p["containerPort"] for p in container["ports"]}
        probe = container["readinessProbe"]["httpGet"]
        probe_host = next(
            (h["value"] for h in probe.get("httpHeaders", []) if h["name"] == "Host"),
            None,
        )
        config = policy["spec"]["default"]["config"]
        check = config.get("httpHealthCheck", {})
        if config.get("type") != "HTTP" or (
            check.get("requestPath"),
            check.get("host"),
        ) != (
            probe["path"],
            probe_host,
        ):
            failed.append(
                f"{where}: probes {check.get('requestPath')!r} with Host {check.get('host')!r}, "
                f"the readiness probe {probe['path']!r} with Host {probe_host!r}"
            )
        # The port the load balancer probes: a fixed one, or the routed
        # Service port's target (the endpoint's port).
        if check.get("portSpecification") == "USE_FIXED_PORT":
            probed = {check.get("port")}
        else:
            targets = {p["port"]: p["targetPort"] for p in service["spec"]["ports"]}
            probed = {
                ports.get(targets.get(port), targets.get(port))
                for port in routed.get(target, ())
            }
        if probed != {ports.get(probe["port"], probe["port"])}:
            failed.append(
                f"{where}: probes port {sorted(probed, key=str)}, the readiness probe {probe['port']!r}"
            )
    return failed


def check_alb_health_checks(name: str, docs: list[dict]) -> list[str]:
    """An ALB Ingress (AWS Load Balancer Controller) health-checks each target
    group itself: its healthcheck-path (default /) and healthcheck-port
    (default the traffic port), from the Service's annotations over the
    Ingress's. They must be what the pods' readiness probe checks."""
    failed = []
    prefix = "alb.ingress.kubernetes.io/"
    services = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Service"}
    deployments = {component(d): d for d in docs if d["kind"] == "Deployment"}
    for ingress in (d for d in docs if d["kind"] == "Ingress"):
        if ingress["spec"].get("ingressClassName") != "alb":
            continue
        for rule in ingress["spec"].get("rules", []):
            for path in rule["http"]["paths"]:
                ref = path["backend"]["service"]
                service = services[ref["name"]]
                port_ref = ref["port"].get("number", ref["port"].get("name"))
                where = f"{name}: ALB target group {ref['name']}:{port_ref}"
                annotations = {
                    **ingress["metadata"].get("annotations", {}),
                    **service["metadata"].get("annotations", {}),
                }
                deployment = deployments[
                    service["spec"]["selector"]["app.kubernetes.io/component"]
                ]
                container = pod_spec(deployment)["containers"][0]
                ports = {p["name"]: p["containerPort"] for p in container["ports"]}
                probe = container["readinessProbe"]["httpGet"]
                target = next(
                    p["targetPort"]
                    for p in service["spec"]["ports"]
                    if p["port"] == ref["port"].get("number")
                    or p["name"] == ref["port"].get("name")
                )
                port = annotations.get(prefix + "healthcheck-port", "traffic-port")
                probed = (
                    ports.get(target, target) if port == "traffic-port" else int(port)
                )
                checked = annotations.get(prefix + "healthcheck-path", "/")
                if (checked, probed) != (
                    probe["path"],
                    ports.get(probe["port"], probe["port"]),
                ):
                    failed.append(
                        f"{where}: checks {checked} on {probed}, the readiness probe "
                        f"{probe['path']} on {probe['port']}"
                    )
    return failed


def check_local(docs: list[dict]) -> list[str]:
    """examples/local.yaml: LoadBalancer Services on the localhost ports the
    default URLs use, so nothing needs a port-forward."""
    failed = []
    wanted = {"frontend": 3000, "backend": 8000, "fi-collector": 4318, "minio": 9005}
    services = {component(d): d for d in docs if d["kind"] == "Service"}
    for name, port in wanted.items():
        service = services.get(name, {}).get("spec", {})
        if service.get("type") != "LoadBalancer" or port not in {
            p["port"] for p in service.get("ports", [])
        }:
            failed.append(f"local: the {name} Service is not a LoadBalancer on {port}")
    return failed


def check_render(name: str, docs: list[dict]) -> list[str]:
    failed = check_gateway_redis(name, docs) + check_scheduling(name, docs)
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
            failed += check_python_env(where, values)
            # Load balancers that health-check pods directly (an AWS ALB with
            # target-type ip, GKE) send the pod's IP as the Host: a restricted
            # ALLOWED_HOSTS lists $(POD_IP), the pod's IP defined before it.
            hosts = (values.get("ALLOWED_HOSTS") or "").split(",")
            if "*" not in hosts:
                for pod_host in ("$(POD_IP)", "[$(POD_IP)]"):
                    if pod_host not in hosts:
                        failed.append(
                            f"{where}: ALLOWED_HOSTS {hosts} misses {pod_host}"
                        )
                pod_ip = next((e for e in env if e["name"] == "POD_IP"), {})
                if pod_ip.get("valueFrom", {}).get("fieldRef", {}).get(
                    "fieldPath"
                ) != "status.podIP" or names.index("POD_IP") > names.index(
                    "ALLOWED_HOSTS"
                ):
                    failed.append(
                        f"{where}: POD_IP is not the pod's IP (status.podIP) defined before ALLOWED_HOSTS"
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


# ---------------------------------------------------------------------------
# Enterprise and hardened operations (license, SSO, proxy, CA, air-gap,
# ExternalSecrets, Reloader, OpenShift)
# ---------------------------------------------------------------------------
PYTHON_COMPONENTS = ("backend", "bootstrap")
CA_PATH = "/etc/futureagi/ca/ca.crt"
PROXY_VARS = (
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "NO_PROXY",
    "no_proxy",
)
# Variables only an opted-in feature may set: a default render has none.
OPT_IN_VARS = PROXY_VARS + (
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    "PGSSLROOTCERT",
    "EE_LICENSE_KEY",
    "FUTURE_AGI_ENTERPRISE_HEARTBEAT_DISABLED",
    "FUTURE_AGI_LICENSE_URL",
    "LITELLM_LOCAL_MODEL_COST_MAP",
    "HF_HUB_OFFLINE",
    "TRANSFORMERS_OFFLINE",
    "FAGI_ADMIN_EMAIL",
)
IDS = ("runAsUser", "runAsGroup", "fsGroup", "seccompProfile")


def is_python(doc: dict) -> bool:
    comp = component(doc)
    return comp in PYTHON_COMPONENTS or comp.startswith("worker")


def secret_refs(container: dict) -> dict[str, tuple[str, str]]:
    refs = {}
    for entry in container.get("env", []):
        ref = entry.get("valueFrom", {}).get("secretKeyRef")
        if ref:
            refs[entry["name"]] = (ref["name"], ref["key"])
    return refs


def app_workloads(docs: list[dict]) -> list[dict]:
    """The Future AGI pods (not the datastores, the sandbox or the test pod)."""
    return [
        d
        for d in docs
        if d["kind"] in ("Deployment", "Job")
        and component(d) not in ("code-executor",)
        and d["metadata"].get("annotations", {}).get("helm.sh/hook") != "test"
    ]


def chart_secret(docs: list[dict]) -> dict:
    return next(
        (
            d
            for d in docs
            if d["kind"] == "Secret" and d["metadata"]["name"].endswith("-secrets")
        ),
        {"data": {}},
    )


def check_defaults_opt_in(name: str, docs: list[dict]) -> list[str]:
    """Without the enterprise values, none of their variables, annotations or
    objects appear, and the pods keep their fixed IDs."""
    failed = []
    for doc in (d for d in docs if d["kind"] in WORKLOADS):
        where = f"{name}: {doc['kind']} {doc['metadata']['name']}"
        for container in containers(doc):
            extra = sorted(set(env_values(container)) & set(OPT_IN_VARS))
            if extra:
                failed.append(f"{where}/{container['name']}: sets {extra} by default")
        spec = pod_spec(doc)
        if any(v.get("name") == "ca-bundle" for v in spec.get("volumes", [])):
            failed.append(f"{where}: mounts a CA bundle by default")
        if spec.get("securityContext") and "runAsUser" not in spec["securityContext"]:
            failed.append(f"{where}: pod securityContext lost its runAsUser")
        if "reloader.stakater.com/auto" in doc["metadata"].get("annotations", {}):
            failed.append(f"{where}: Reloader annotation by default")
    if any(d["kind"] == "ExternalSecret" for d in docs):
        failed.append(f"{name}: renders an ExternalSecret by default")
    return failed


def check_enterprise(docs: list[dict]) -> list[str]:
    """examples/enterprise.yaml + ci/enterprise.yaml: the license, SSO, email and
    first admin reach the right processes from the right Secrets."""
    failed = []
    secret = chart_secret(docs)
    python = [d for d in docs if d["kind"] in ("Deployment", "Job") and is_python(d)]
    if not any(component(d) == "bootstrap" for d in python):
        failed.append("enterprise: no bootstrap job")
    for doc in python:
        where = f"enterprise: {doc['kind']} {doc['metadata']['name']}"
        for container in containers(doc):
            refs, values = secret_refs(container), env_values(container)
            expected = {
                "EE_LICENSE_KEY": ("futureagi-license", "EE_LICENSE_KEY"),
                "AUTH0_CLIENT_ID": ("futureagi-google-oauth", "AUTH0_CLIENT_ID"),
                "AUTH0_CLIENT_SECRET": (
                    "futureagi-google-oauth",
                    "AUTH0_CLIENT_SECRET",
                ),
                "GITHUB_CLIENT_SECRET": (
                    secret["metadata"]["name"],
                    "GITHUB_CLIENT_SECRET",
                ),
                "MAILGUN_API_KEY": ("futureagi-mailgun", "MAILGUN_API_KEY"),
            }
            for var, ref in expected.items():
                if refs.get(var) != ref:
                    failed.append(
                        f"{where}/{container['name']}: {var} from {refs.get(var)}, expected {ref}"
                    )
            if values.get("GITHUB_CLIENT_ID") != "github-client-id":
                failed.append(
                    f"{where}/{container['name']}: GITHUB_CLIENT_ID is {values.get('GITHUB_CLIENT_ID')!r}"
                )
            if "MICROSOFT_CLIENT_ID" in values or "MICROSOFT_CLIENT_ID" in refs:
                failed.append(
                    f"{where}/{container['name']}: Microsoft sign-in is not configured"
                )
            if values.get("EE_LICENSE_CLOCK_SKEW_SECONDS") != "300":
                failed.append(
                    f"{where}/{container['name']}: EE_LICENSE_CLOCK_SKEW_SECONDS"
                )
            for var in PROXY_VARS:
                if var not in values:
                    failed.append(f"{where}/{container['name']}: {var} missing")
    data = secret.get("data") or {}
    if "EE_LICENSE_KEY" in data:
        failed.append(
            "enterprise: license.existingSecret set, yet the chart stores EE_LICENSE_KEY"
        )
    if "GITHUB_CLIENT_SECRET" not in data or "AUTH0_CLIENT_SECRET" in data:
        failed.append(
            "enterprise: the chart Secret should hold the inline GitHub secret only"
        )
    bootstrap = next(
        d for d in docs if d["kind"] == "Job" and component(d) == "bootstrap"
    )
    container = pod_spec(bootstrap)["containers"][0]
    refs = secret_refs(container)
    for var, key in (
        ("FAGI_ADMIN_EMAIL", "email"),
        ("FAGI_ADMIN_NAME", "name"),
        ("FAGI_ADMIN_PASSWORD", "password"),
    ):
        if refs.get(var) != ("futureagi-admin", key):
            failed.append(f"enterprise: bootstrap {var} from {refs.get(var)}")
    # The first admin is a step of bootstrap_install (FAGI_ADMIN_*): the
    # startup guard refuses `manage.py shell` in the bootstrap process.
    if container["command"] != ["python", "manage.py", "bootstrap_install"]:
        failed.append(
            f"enterprise: the bootstrap job runs {container['command']}, not bootstrap_install alone"
        )
    if container.get("args", [])[:1] != ["--wait-timeout"]:
        failed.append("enterprise: bootstrap_install lost its arguments")
    for doc in (d for d in docs if d["kind"] == "Deployment"):
        if (
            doc["metadata"].get("annotations", {}).get("reloader.stakater.com/auto")
            != "true"
        ):
            failed.append(
                f"enterprise: Deployment {doc['metadata']['name']} has no Reloader annotation"
            )
    return failed


def check_license_legacy(docs: list[dict]) -> list[str]:
    """The deprecated secrets.eeLicenseKey still licenses every Python process."""
    failed = []
    secret = chart_secret(docs)
    if "EE_LICENSE_KEY" not in (secret.get("data") or {}):
        failed.append("license-legacy: secrets.eeLicenseKey is not stored")
    for doc in (d for d in docs if d["kind"] in ("Deployment", "Job") and is_python(d)):
        for container in containers(doc):
            if secret_refs(container).get("EE_LICENSE_KEY") != (
                secret["metadata"]["name"],
                "EE_LICENSE_KEY",
            ):
                failed.append(
                    f"license-legacy: {doc['metadata']['name']}/{container['name']} has no EE_LICENSE_KEY"
                )
    return failed


def check_proxy_ca(docs: list[dict]) -> list[str]:
    """ci/proxy-ca.yaml: every Future AGI pod gets the proxy, and all but the
    frontend the CA bundle; the code sandbox gets neither."""
    failed = []
    services = {d["metadata"]["name"] for d in docs if d["kind"] == "Service"}
    for doc in app_workloads(docs):
        comp = component(doc)
        where = f"proxy-ca: {doc['kind']} {doc['metadata']['name']}"
        spec = pod_spec(doc)
        for container in containers(doc):
            values = env_values(container)
            if values.get(
                "HTTPS_PROXY"
            ) != "http://proxy.corp.example:3128" or values.get(
                "https_proxy"
            ) != values.get("HTTPS_PROXY"):
                failed.append(f"{where}/{container['name']}: HTTPS_PROXY")
            no_proxy = set((values.get("NO_PROXY") or "").split(","))
            wanted = {
                "localhost",
                "127.0.0.1",
                ".svc",
                ".cluster.local",
                ".futureagi.svc",
                ".corp.example",
                "10.0.0.0/8",
                "postgres.example.internal",
                "clickhouse.example.internal",
                "redis.example.internal",
            } | services
            if not wanted <= no_proxy or values.get("no_proxy") != values.get(
                "NO_PROXY"
            ):
                failed.append(
                    f"{where}/{container['name']}: NO_PROXY misses {sorted(wanted - no_proxy)}"
                )
            # A public S3 endpoint may only be reachable through the proxy.
            if any(h.startswith("s3.") or "amazonaws" in h for h in no_proxy):
                failed.append(
                    f"{where}/{container['name']}: NO_PROXY bypasses the proxy for object storage"
                )
            if comp == "frontend":
                continue
            if values.get("SSL_CERT_FILE") != CA_PATH:
                failed.append(f"{where}/{container['name']}: SSL_CERT_FILE")
            if not any(
                m.get("mountPath") == "/etc/futureagi/ca" and m.get("readOnly")
                for m in container.get("volumeMounts", [])
            ):
                failed.append(
                    f"{where}/{container['name']}: CA bundle not mounted read-only"
                )
            if is_python(doc) or comp == "serving":
                for var in (
                    "REQUESTS_CA_BUNDLE",
                    "CURL_CA_BUNDLE",
                    "NODE_EXTRA_CA_CERTS",
                ):
                    if values.get(var) != CA_PATH:
                        failed.append(f"{where}/{container['name']}: {var}")
            if (is_python(doc) or comp == "fi-collector") and values.get(
                "PGSSLROOTCERT"
            ) != CA_PATH:
                failed.append(
                    f"{where}/{container['name']}: PGSSLROOTCERT with verify-full"
                )
        if comp != "frontend":
            volume = next(
                (v for v in spec.get("volumes", []) if v["name"] == "ca-bundle"), None
            )
            if (
                not volume
                or volume.get("secret", {}).get("secretName") != "corp-ca"
                or volume["secret"].get("items")
                != [{"key": "bundle.pem", "path": "ca.crt"}]
            ):
                failed.append(f"{where}: ca-bundle volume is {volume}")
    for doc in (
        d for d in docs if component(d) == "code-executor" and d["kind"] == "Deployment"
    ):
        for container in containers(doc):
            if set(env_values(container)) & set(OPT_IN_VARS):
                failed.append("proxy-ca: the code sandbox gets proxy or CA variables")
    return failed


def check_airgap(docs: list[dict]) -> list[str]:
    failed = []
    for doc in app_workloads(docs):
        for container in containers(doc):
            values = env_values(container)
            where = f"airgap: {doc['metadata']['name']}/{container['name']}"
            if is_python(doc):
                for var, value in (
                    ("FUTURE_AGI_TELEMETRY_DISABLED", "true"),
                    ("FUTURE_AGI_ENTERPRISE_HEARTBEAT_DISABLED", "true"),
                    ("LITELLM_LOCAL_MODEL_COST_MAP", "True"),
                ):
                    if values.get(var) != value:
                        failed.append(f"{where}: {var} is {values.get(var)!r}")
            if component(doc) == "serving":
                for var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
                    if values.get(var) != "1":
                        failed.append(f"{where}: {var}")
    if not any(component(d) == "serving" for d in docs if d["kind"] == "Deployment"):
        failed.append("airgap: examples/airgap.yaml should run serving")
    for doc in (d for d in docs if d["kind"] in WORKLOADS):
        for container in containers(doc):
            if not container["image"].startswith(
                "registry.example.com/futureagi-mirror/"
            ):
                failed.append(
                    f"airgap: {doc['metadata']['name']} pulls {container['image']}"
                )
    return failed


def check_external_secrets(docs: list[dict]) -> list[str]:
    """ExternalSecrets create exactly the Secrets the chart reads, before the
    bootstrap job, and the chart generates none of them."""
    failed = []
    external = {
        d["spec"]["target"]["name"]: d for d in docs if d["kind"] == "ExternalSecret"
    }
    # examples/external-secrets.yaml, then ci/external-secrets.yaml: one per group.
    wanted = {
        "futureagi-app",
        "futureagi-llm",
        "futureagi-license",
        "futureagi-postgres",
        "futureagi-s3",
        "futureagi-email",
        "futureagi-google",
        "futureagi-github",
        "futureagi-microsoft",
        "futureagi-admin",
        "futureagi-clickhouse",
        "futureagi-redis",
    }
    if set(external) != wanted:
        failed.append(
            f"external-secrets: targets {sorted(external)}, expected {sorted(wanted)}"
        )
    for target, doc in external.items():
        annotations = doc["metadata"].get("annotations", {})
        if (
            "pre-install" not in annotations.get("helm.sh/hook", "")
            or int(annotations.get("helm.sh/hook-weight", "0")) >= -20
        ):
            failed.append(
                f"external-secrets: {target} is not created before the chart Secret and the bootstrap job"
            )
        if doc["spec"]["target"].get("creationPolicy") != "Orphan":
            failed.append(f"external-secrets: {target} would be deleted with its hook")
        if doc["spec"]["secretStoreRef"] != {
            "name": "vault",
            "kind": "ClusterSecretStore",
        }:
            failed.append(
                f"external-secrets: {target} store {doc['spec']['secretStoreRef']}"
            )
    read = set()
    for doc in (d for d in docs if d["kind"] in WORKLOADS):
        for container in containers(doc):
            read |= {ref[0] for ref in secret_refs(container).values()}
    if not wanted <= read:
        failed.append(f"external-secrets: no pod reads {sorted(wanted - read)}")
    data = chart_secret(docs).get("data") or {}
    for key in (
        "SECRET_KEY",
        "EE_LICENSE_KEY",
        "PG_PASSWORD",
        "S3_ACCESS_KEY",
        "MAILGUN_API_KEY",
        "AUTH0_CLIENT_SECRET",
        "GITHUB_CLIENT_SECRET",
        "MICROSOFT_CLIENT_SECRET",
        "CH_PASSWORD",
        "REDIS_PASSWORD",
    ):
        if key in data:
            failed.append(f"external-secrets: the chart still generates {key}")
    return failed


def check_openshift(name: str, docs: list[dict]) -> list[str]:
    """No fixed IDs or seccomp profile anywhere; datastore overrides apply."""
    failed = []
    for doc in (d for d in docs if d["kind"] in WORKLOADS):
        spec = pod_spec(doc)
        contexts = [("pod", spec.get("securityContext") or {})] + [
            (c["name"], c.get("securityContext") or {}) for c in containers(doc)
        ]
        for where, context in contexts:
            fixed = sorted(set(context) & set(IDS))
            if fixed:
                failed.append(
                    f"{name}: {doc['metadata']['name']}/{where} keeps {fixed}"
                )
    if name == "openshift":
        statefulsets = by_name(docs, "StatefulSet")
        postgres = next(d for n, d in statefulsets.items() if n.endswith("-postgres"))
        redis = next(d for n, d in statefulsets.items() if n.endswith("-redis"))
        if (
            pod_spec(postgres)["containers"][0]["securityContext"].get(
                "readOnlyRootFilesystem"
            )
            is not False
        ):
            failed.append(
                "openshift: postgres.bundled.containerSecurityContext is ignored"
            )
        if (
            pod_spec(redis)["securityContext"].get("fsGroupChangePolicy")
            != "OnRootMismatch"
        ):
            failed.append("openshift: redis.bundled.podSecurityContext is ignored")
    return failed


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

    # bootstrap.ttlSecondsAfterFinished: 0 deletes the job at once; it is not unset.
    for render, docs, ttl in (("bundled", bundled, 86400), ("overrides", overrides, 0)):
        job = next(
            d for d in docs if d["kind"] == "Job" and component(d) == "bootstrap"
        )
        if job["spec"].get("ttlSecondsAfterFinished") != ttl:
            failed.append(
                f"{render}: the bootstrap job's ttlSecondsAfterFinished is "
                f"{job['spec'].get('ttlSecondsAfterFinished')!r}, expected {ttl}"
            )
    # objectStorage.bundled.service.downloadPort: the Service port MINIO_URL names.
    minio = next(
        d for d in overrides if d["kind"] == "Service" and component(d) == "minio"
    )
    if 9100 not in {
        p["port"] for p in minio["spec"]["ports"] if p["name"] == "downloads"
    }:
        failed.append("overrides: the MinIO Service does not publish downloadPort 9100")

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


def all_images(docs: list[dict]) -> list[str]:
    return [c["image"] for d in docs if d["kind"] in WORKLOADS for c in containers(d)]


def check_digests(name: str, docs: list[dict], pinned: bool) -> list[str]:
    """ci/digests.yaml: stamped digests apply only to the appVersion tag of
    the published repository path, under any registry, and not at all with
    pinDigests=false."""
    failed = []
    seen = set()
    for image in all_images(docs):
        ref, _, digest = image.partition("@")
        path, _, tag = ref.rpartition(":")
        repository = path.split("/", 1)[1] if "/" in path else path
        if repository not in STAMPED and not repository.startswith("acme/"):
            continue
        seen.add(repository)
        expected = STAMPED.get(repository, "") if pinned and tag == APP_VERSION else ""
        if digest != expected:
            failed.append(f"{name}: {image} should carry digest {expected!r}")
    for repository in (
        "futureagi/future-agi",
        "futureagi/serving",
        "acme/code-executor",
    ):
        if repository not in seen:
            failed.append(f"{name}: no {repository} image rendered")
    # The registry is not compared: a mirror keeps the digest.
    if not any(
        i.startswith("mirror.example.com/futureagi/frontend:") for i in all_images(docs)
    ):
        failed.append(f"{name}: the frontend does not run from its own registry")
    if not any(i.endswith(":v0.0.0-queue") for i in all_images(docs)):
        failed.append(f"{name}: the tasks_s queue does not run its own tag")
    return failed


# The digests the chart pins its bundled datastores' default tags to
# (futureagi.datastorePins in templates/_helpers.tpl).
DATASTORE_PINS = {
    "docker.io/library/postgres:16.15-trixie": "sha256:1a6ab3f5345eb6dbe04a1349529caabdb0ab09293a09590fad07b2246bfa4b54",
    "docker.io/library/redis:7.4.11-alpine": "sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499",
    "ghcr.io/coollabsio/minio:RELEASE.2025-10-15T17-29-55Z": "sha256:69b55a1c1c5dc285ce04db96689f5b2102317fc77a50680a1874ca6efd1c87f9",
}
POSTGRES, REDIS, MINIO = (
    "docker.io/library/postgres",
    "docker.io/library/redis",
    "ghcr.io/coollabsio/minio",
)


def check_datastore_pins(name: str, docs: list[dict], expected: dict) -> list[str]:
    """The bundled PostgreSQL, Redis and MinIO run pinned on their default
    tags only: another tag (an existing <datastore>.bundled.image.tag override)
    or image.pinDigests=false runs by tag. expected: repository -> pinned?"""
    failed = []
    images = {
        image.partition("@")[0].rpartition(":")[0]: image
        for image in all_images(docs)
        if image.partition("@")[0].rpartition(":")[0] in expected
    }
    for repository, pinned in expected.items():
        image = images.get(repository)
        if image is None:
            failed.append(f"{name}: no {repository} image rendered")
            continue
        ref, _, digest = image.partition("@")
        want = DATASTORE_PINS.get(ref, "") if pinned else ""
        if pinned and not want:
            failed.append(f"{name}: {ref} is not a tag the chart pins")
        elif digest != want:
            failed.append(f"{name}: {image} should carry digest {want!r}")
    return failed


def check_token_mounts(name: str, docs: list[dict], mounted: bool) -> list[str]:
    """serviceAccount.automountServiceAccountToken reaches the ServiceAccount
    and the pods that run as it, but never the code sandbox; every other pod
    (datastores, the bootstrap job, the test pod) keeps the token out."""
    failed = []
    accounts = {
        d["metadata"]["name"]: d
        for d in docs
        if d["kind"] == "ServiceAccount"
        and "helm.sh/hook" not in d["metadata"].get("annotations", {})
    }
    for account in accounts.values():
        if account.get("automountServiceAccountToken") is not mounted:
            failed.append(
                f"{name}: ServiceAccount {account['metadata']['name']} automount is not {mounted}"
            )
    for doc in (d for d in docs if d["kind"] in WORKLOADS):
        spec = pod_spec(doc)
        want = (
            mounted
            and spec.get("serviceAccountName") in accounts
            and component(doc) != "code-executor"
        )
        if spec.get("automountServiceAccountToken") is not want:
            failed.append(
                f"{name}: {doc['kind']} {doc['metadata']['name']} automountServiceAccountToken "
                f"is {spec.get('automountServiceAccountToken')!r}, expected {want}"
            )
    return failed


def check_all_components(docs: list[dict]) -> list[str]:
    """ci/all-components.yaml: the chart-wide scheduling reaches every pod, the
    helm test pod included, and an empty bootstrap TTL leaves the field out."""
    failed = []
    for doc in (d for d in docs if d["kind"] in WORKLOADS):
        spec = pod_spec(doc)
        placed = (
            spec.get("nodeSelector") == {"kubernetes.io/os": "linux"},
            any(t.get("key") == "dedicated" for t in spec.get("tolerations", [])),
            spec.get("priorityClassName") == "futureagi",
        )
        if not all(placed):
            failed.append(
                f"all-components: {doc['kind']} {doc['metadata']['name']} misses the chart-wide "
                "nodeSelector, tolerations or priorityClassName"
            )
    job = next(d for d in docs if d["kind"] == "Job" and component(d) == "bootstrap")
    if "ttlSecondsAfterFinished" in job["spec"]:
        failed.append(
            "all-components: an empty bootstrap.ttlSecondsAfterFinished still sets one"
        )
    return failed


def check_dockerhub_mirror(docs: list[dict]) -> list[str]:
    """ci/dockerhub-mirror.yaml: a Docker Hub pull-through cache named per
    image; the bundled MinIO stays on ghcr.io; the digest pins stay."""
    failed = []
    for image in all_images(docs):
        ref = image.partition("@")[0]
        if ref.startswith("ghcr.io/coollabsio/minio:"):
            continue
        if not ref.startswith("registry.example.com/dockerhub/"):
            failed.append(f"dockerhub-mirror: {image} does not come through the mirror")
    if not any(i.startswith("ghcr.io/coollabsio/minio:") for i in all_images(docs)):
        failed.append("dockerhub-mirror: the bundled MinIO left ghcr.io")
    pins = {
        ref.replace("docker.io/", "registry.example.com/dockerhub/"): digest
        for ref, digest in DATASTORE_PINS.items()
    }
    for image in all_images(docs):
        ref, _, digest = image.partition("@")
        if ref in pins and digest != pins[ref]:
            failed.append(f"dockerhub-mirror: {image} lost its digest pin")
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
        failed += check_render(name, docs) + check_health_check_policies(name, docs)
        failed += check_bootstrap_order(name, docs)
        failed += check_alb_health_checks(name, docs)
        # ci/all-components.yaml turns serviceAccount.automountServiceAccountToken on.
        failed += check_token_mounts(name, docs, mounted=name == "all-components")
    # examples/cloud/gke.yaml: the UI, API and collector; ci/gateway-api.yaml
    # adds the LLM gateway.
    for render, wanted in (("cloud-gke", 3), ("gateway-api", 4)):
        count = sum(d["kind"] == "HealthCheckPolicy" for d in renders.get(render, []))
        if render in renders and count != wanted:
            failed.append(f"{render}: {count} HealthCheckPolicies, expected {wanted}")
    # examples/cloud/eks.yaml: the ALB check above had an Ingress to look at.
    if "cloud-eks" in renders and not any(
        d["kind"] == "Ingress" and d["spec"].get("ingressClassName") == "alb"
        for d in renders["cloud-eks"]
    ):
        failed.append("cloud-eks: no ALB Ingress")
    for render, pinned in (("digests", True), ("digests-unpinned", False)):
        if render in renders:
            failed += check_digests(render, renders[render], pinned)
    for render, expected in (
        ("bundled", {POSTGRES: True, REDIS: True, MINIO: True}),
        # ci/digests.yaml moves Redis and MinIO to other tags.
        ("digests", {POSTGRES: True, REDIS: False, MINIO: False}),
        ("digests-unpinned", {POSTGRES: False, REDIS: False, MINIO: False}),
    ):
        if render in renders:
            failed += check_datastore_pins(render, renders[render], expected)
    if "local" in renders:
        failed += check_local(renders["local"])
    if "gateway-api" in renders:
        failed += check_gateway_api(renders["gateway-api"])
    if "pooler" in renders:
        failed += check_pooler(renders["pooler"])
    if "worker-queues" in renders:
        failed += check_worker_queues(renders["worker-queues"])
    if "all-components" in renders:
        failed += check_all_components(renders["all-components"])
    if "dockerhub-mirror" in renders:
        failed += check_dockerhub_mirror(renders["dockerhub-mirror"])
    for render in ("bundled", "external", "local"):
        if render in renders:
            failed += check_defaults_opt_in(render, renders[render])
    for render, check in (
        ("enterprise", check_enterprise),
        ("license-legacy", check_license_legacy),
        ("proxy-ca", check_proxy_ca),
        ("airgap", check_airgap),
        ("external-secrets", check_external_secrets),
    ):
        if render in renders:
            failed += check(renders[render])
    for render in ("openshift", "openshift-auto"):
        if render in renders:
            failed += check_openshift(render, renders[render])
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
