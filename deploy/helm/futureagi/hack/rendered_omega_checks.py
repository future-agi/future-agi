"""Invariants of the Omega objects (omega.*) in the rendered manifests.

Imported by hack/rendered_checks.py. Every render except `omega` must contain
nothing of Omega's (the flag is off by default); the `omega` render
(ci/omega.yaml) must contain each object with the settings the gateway's cloud
mode (services/omega-gateway/src/config.mjs) and the session exchange's key
check (futureagi/accounts/omega_session.py) need, and the revocation channel
(futureagi/accounts/host_events/config.py) on every pod that sends, with the
CA the gateway trusts EE with. READINESS L09's "renders the gateway objects
only when the flag is set". In every render, the pod label Omega EE's
NetworkPolicy admits senders by (omega.futureagi.ai/host-events-sender) is on
exactly the pods that send and on no other.
"""

from __future__ import annotations

from urllib.parse import urlparse

OMEGA_COMPONENTS = ("omega-gateway", "omega-internal-tls", "omega-ingress")
GATEWAY = "futureagi-omega-gateway"
BACKEND = "futureagi-backend"
KEY_DIR = "/var/run/secrets/futureagi/omega-issuer"
HOST_EVENTS_DIR = "/var/run/secrets/futureagi/omega-host-events"
HOST_EVENTS_CA_DIR = "/etc/futureagi/omega-host-events-ca"
GATEWAY_CA = "/etc/omega-gateway/ca/ca.crt"
OMEGA_VOLUMES = {"omega-issuer", "omega-tls", "omega-host-events", "omega-host-events-ca"}
# The gateway refuses every one of these unless it is an https URL to a
# non-loopback host (cloud mode).
GATEWAY_URLS = (
    "OMEGA_REMOTE_BASE_URL",
    "OMEGA_SESSION_EXCHANGE_URL",
    "OMEGA_ASSERTION_ISSUER",
    "OMEGA_CONSOLE_ORIGIN",
    "OMEGA_GATEWAY_OTLP_TRACES_URL",
)
LOOPBACK = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
# The Omega Hub (values `hub`, hack/rendered_hub_checks.py) has settings of its
# own (OMEGA_HUB_*) and shares the UI's switch for its Omega products
# (VITE_OMEGA_CONSOLE): the UI shows them where the gateway or the Hub runs.
# The Hub's checks hold both where the Hub is off, so here OMEGA_HUB_* are
# never the gateway's, and the switch counts only in a render without the
# Hub's checker. check.sh's negative controls hold both exceptions narrow.
HUB_PREFIX = "OMEGA_HUB_"
UI_SWITCH = "VITE_OMEGA_CONSOLE"
HUB_COMPONENT = "hub-checker"
# Omega EE's NetworkPolicy (omega-os values-futureagi.yaml) admits every pod with
# this label to its revocation receiver: the chart sets it on the senders alone.
SENDER_LABEL = "omega.futureagi.ai/host-events-sender"
# The variables only the chart sets on the API, worker and bootstrap pods
# (templates/omega/_validate.tpl). Where config.extraEnvFrom feeds a container,
# the chart pins each one it does not set to "" in the container's env, which
# Kubernetes prefers to envFrom: blank there is the guard, not Omega.
RESERVED_ENV = (
    "OMEGA_SQUAD_DELEGATION_ISSUER",
    "OMEGA_HOST_EVENTS_URL",
    "OMEGA_HOST_EVENTS_KEY_ID",
    "OMEGA_HOST_EVENTS_SECRET_FILE",
    "OMEGA_HOST_EVENTS_ALLOW_HTTP",
    "OMEGA_HOST_EVENTS_CA_FILE",
)


def _labels(doc: dict) -> dict:
    return doc.get("metadata", {}).get("labels") or {}


def _pod_spec(doc: dict) -> dict:
    return doc["spec"] if doc["kind"] == "Pod" else doc["spec"]["template"]["spec"]


def _containers(doc: dict) -> list[dict]:
    spec = _pod_spec(doc)
    return spec.get("initContainers", []) + spec.get("containers", [])


def _env(container: dict) -> dict:
    return {e["name"]: e.get("value") for e in container.get("env", [])}


def _find(docs: list[dict], kind: str, name: str) -> dict | None:
    for doc in docs:
        if doc["kind"] == kind and doc["metadata"]["name"] == name:
            return doc
    return None


def _workloads(docs: list[dict]) -> list[dict]:
    kinds = ("Deployment", "StatefulSet", "Job", "Pod")
    return [d for d in docs if d["kind"] in kinds]


def _pinned_blank(container: dict, key: str) -> bool:
    """A reserved variable the chart pinned to "" against config.extraEnvFrom."""
    return (
        key in RESERVED_ENV
        and bool(container.get("envFrom"))
        and _env(container).get(key) == ""
    )


def check_omega_absent(name: str, docs: list[dict]) -> list[str]:
    """With omega.enabled false: no Omega object, volume, port or variable anywhere
    (the Hub's own OMEGA_HUB_* and, where the Hub runs, the UI switch excepted)."""
    failed = []
    hub_on = any(
        _labels(d).get("app.kubernetes.io/component") == HUB_COMPONENT for d in docs
    )
    for doc in docs:
        where = f"{name}: {doc['kind']} {doc['metadata']['name']}"
        if _labels(doc).get("app.kubernetes.io/component") in OMEGA_COMPONENTS:
            failed.append(f"{where} renders with omega.enabled false")
        if doc["kind"] == "Service" and any(
            p.get("name") == "https-internal" for p in doc["spec"]["ports"]
        ):
            failed.append(f"{where} has the internal TLS port with omega off")
        if doc["kind"] == "Certificate":
            failed.append(f"{where}: a Certificate with omega off")
    for doc in _workloads(docs):
        where = f"{name}: {doc['kind']} {doc['metadata']['name']}"
        spec = _pod_spec(doc)
        volumes = {v["name"] for v in spec.get("volumes", [])}
        if volumes & OMEGA_VOLUMES:
            failed.append(f"{where} mounts an Omega volume with omega off")
        for container in _containers(doc):
            if container["name"] == "omega-tls":
                failed.append(f"{where} runs the TLS sidecar with omega off")
            stray = sorted(
                k
                for k in _env(container)
                if (k.startswith("OMEGA_") or k.startswith("VITE_OMEGA_"))
                and not k.startswith(HUB_PREFIX)
                and not (k == UI_SWITCH and hub_on)
                and not _pinned_blank(container, k)
            )
            if stray:
                failed.append(
                    f"{where}/{container['name']} gets {stray} with omega off"
                )
    return failed


def _https_remote(value: str | None) -> bool:
    if not value:
        return False
    url = urlparse(value)
    host = (url.hostname or "").lower()
    return (
        url.scheme == "https"
        and host not in LOOPBACK
        and not host.endswith((".local", ".localhost"))
        and not (url.username or url.password or url.query or url.fragment)
    )


def _check_gateway(docs: list[dict]) -> list[str]:
    failed = []
    deployment = _find(docs, "Deployment", GATEWAY)
    if deployment is None:
        return ["omega: no gateway Deployment"]
    for kind in ("Service", "HorizontalPodAutoscaler", "PodDisruptionBudget"):
        if _find(docs, kind, GATEWAY) is None:
            failed.append(f"omega: no gateway {kind}")
    (container,) = [c for c in _containers(deployment) if c["name"] == "omega-gateway"]
    env = _env(container)
    if env.get("OMEGA_GATEWAY_MODE") != "cloud":
        failed.append(
            f"omega: gateway mode {env.get('OMEGA_GATEWAY_MODE')!r}, not cloud"
        )
    for key in GATEWAY_URLS:
        if not _https_remote(env.get(key)):
            failed.append(
                f"omega: gateway {key}={env.get(key)!r} is not an https remote URL"
            )
    origins = (env.get("OMEGA_ALLOWED_ORIGINS") or "").split(",")
    if not origins or not all(_https_remote(o) for o in origins):
        failed.append(f"omega: gateway OMEGA_ALLOWED_ORIGINS={origins!r}")
    if env.get("OMEGA_GATEWAY_BASE_PATH") != "/omega/api":
        failed.append("omega: the gateway is not mounted at /omega/api")
    if env.get("OMEGA_UPSTREAM_TOKEN"):
        failed.append("omega: the gateway has a static upstream token")
    redis = next(
        (
            e
            for e in container["env"]
            if e["name"] == "OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL"
        ),
        None,
    )
    if not redis or "secretKeyRef" not in redis.get("valueFrom", {}):
        failed.append("omega: the rate-limit Redis URL does not come from a Secret")
    mounts = {m["mountPath"]: m for m in container.get("volumeMounts", [])}
    for path, key in (
        ("/etc/omega-gateway/jwks", "OMEGA_ASSERTION_JWKS_FILE"),
        ("/etc/omega-gateway/ca", "NODE_EXTRA_CA_CERTS"),
        (
            "/var/run/secrets/futureagi/omega-otlp",
            "OMEGA_GATEWAY_OTLP_CREDENTIALS_FILE",
        ),
    ):
        if path not in mounts or not mounts[path].get("readOnly"):
            failed.append(f"omega: gateway {path} is not mounted read-only")
        if not (env.get(key) or "").startswith(path + "/"):
            failed.append(f"omega: gateway {key}={env.get(key)!r} is not under {path}")
    security = container.get("securityContext", {})
    if not security.get("readOnlyRootFilesystem") or security.get(
        "allowPrivilegeEscalation"
    ):
        failed.append("omega: the gateway container is not read-only and unprivileged")
    spec = _pod_spec(deployment)
    if not spec["securityContext"].get("runAsNonRoot"):
        failed.append("omega: the gateway pod may run as root")
    # The internet-facing pod runs as its own ServiceAccount, never the API's
    # (which a cloud identity may let read the issuer key), with no token.
    account = spec.get("serviceAccountName")
    api = _find(docs, "Deployment", BACKEND)
    if account is None or (api is not None and account == _pod_spec(api).get("serviceAccountName")):
        failed.append(f"omega: the gateway runs as the API's ServiceAccount {account!r}")
    sa = _find(docs, "ServiceAccount", account or "")
    if sa is None or sa.get("automountServiceAccountToken") is not False:
        failed.append(f"omega: no token-less ServiceAccount {account!r} for the gateway")
    if spec.get("automountServiceAccountToken") is not False:
        failed.append("omega: the gateway pod mounts a ServiceAccount token")
    grace = spec.get("terminationGracePeriodSeconds", 30)
    drain = int(env.get("OMEGA_GATEWAY_DRAIN_MS", "10000")) / 1000
    if grace < drain + 5:
        failed.append(
            f"omega: terminationGracePeriodSeconds {grace} < drain {drain} s + 5 s"
        )
    probes = {
        p: container.get(p, {}).get("httpGet", {}).get("path")
        for p in ("readinessProbe", "livenessProbe")
    }
    if probes != {"readinessProbe": "/readyz", "livenessProbe": "/healthz"}:
        failed.append(f"omega: gateway probes {probes}")
    return failed


def _check_frontend(docs: list[dict]) -> list[str]:
    """The production UI turns its Omega tabs on at run time (frontend/docker-entrypoint.sh
    writes VITE_OMEGA_CONSOLE into /config.js), only where the gateway runs."""
    frontend = _find(docs, "Deployment", "futureagi-frontend")
    if frontend is None:
        return ["omega: no frontend Deployment"]
    container = next(c for c in _containers(frontend) if c["name"] == "frontend")
    if _env(container).get("VITE_OMEGA_CONSOLE") != "1":
        return ["omega: the UI's Omega tabs are off (VITE_OMEGA_CONSOLE is not 1)"]
    return []


def _check_backend(docs: list[dict]) -> list[str]:
    failed = []
    deployment = _find(docs, "Deployment", BACKEND)
    spec = _pod_spec(deployment)
    volumes = {v["name"]: v for v in spec["volumes"]}
    issuer = volumes.get("omega-issuer", {}).get("secret", {})
    # 0440 = 288: root-owned and readable through the fsGroup, never by others.
    if issuer.get("defaultMode") != 0o440:
        failed.append(
            f"omega: issuer key volume mode {issuer.get('defaultMode')!r}, not 0440"
        )
    if not spec["securityContext"].get("fsGroup"):
        failed.append(
            "omega: the API pods have no fsGroup, so the key would be unreadable"
        )
    (api,) = [c for c in spec["containers"] if c["name"] == "backend"]
    env = _env(api)
    if env.get("OMEGA_SQUAD_DELEGATION_PRIVATE_KEY_FILE") != f"{KEY_DIR}/private.pem":
        failed.append("omega: the session exchange's key path is not the mounted key")
    if not _https_remote(env.get("OMEGA_SQUAD_DELEGATION_ISSUER")):
        failed.append("omega: the session exchange's issuer is not https")
    if not any(
        m["mountPath"] == KEY_DIR and m.get("readOnly") for m in api["volumeMounts"]
    ):
        failed.append("omega: the API does not mount the issuer key read-only")
    sidecars = [c for c in spec["containers"] if c["name"] == "omega-tls"]
    if len(sidecars) != 1:
        return failed + ["omega: the API pods have no internal TLS sidecar"]
    args = sidecars[0]["args"]
    if "--target=127.0.0.1:8000" not in args:
        failed.append(f"omega: the TLS sidecar forwards beyond loopback: {args}")
    if not any(a.startswith("--listen=") for a in args) or "--unsafe-target" in args:
        failed.append(f"omega: the TLS sidecar args {args}")
    if "@sha256:" not in sidecars[0]["image"]:
        failed.append(
            f"omega: the TLS sidecar image is not pinned: {sidecars[0]['image']}"
        )
    service = _find(docs, "Service", BACKEND)
    if not any(p.get("name") == "https-internal" for p in service["spec"]["ports"]):
        failed.append("omega: the API Service has no https-internal port")
    for doc in _workloads(docs):
        if doc["metadata"]["name"] == BACKEND:
            continue
        if any(v["name"] == "omega-issuer" for v in _pod_spec(doc).get("volumes", [])):
            failed.append(
                f"omega: {doc['kind']} {doc['metadata']['name']} mounts the issuer key"
            )
    return failed


def _check_host_events(docs: list[dict]) -> list[str]:
    """The revocation channel on the API and every worker (accounts/host_events senders):
    its settings, and the shared secret mounted read-only with mode 0440 under the fsGroup
    (the issuer key's rule, which config.py applies). Where the gateway trusts a private CA
    for EE, each sender checks EE's certificate with it too (OMEGA_HOST_EVENTS_CA_FILE,
    mounted read-only), never through the pod-wide trust store. The bootstrap Job, which
    registers the 3 s drain, sees the issuer and holds no secret."""
    failed = []
    gateway = _find(docs, "Deployment", GATEWAY)
    private_ca = gateway is not None and any(
        _env(c).get("NODE_EXTRA_CA_CERTS") == GATEWAY_CA for c in _containers(gateway)
    )
    senders = []
    for doc in docs:
        if doc["kind"] != "Deployment":
            continue
        component = _labels(doc).get("app.kubernetes.io/component", "")
        if component == "backend" or component.startswith("worker"):
            name = "backend" if component == "backend" else "worker"
            senders += [(doc, c) for c in _pod_spec(doc)["containers"] if c["name"] == name]
    if not any(d["metadata"]["name"] == BACKEND for d, _ in senders) or len(senders) < 2:
        failed.append(f"omega: senders {[d['metadata']['name'] for d, _ in senders]}")
    for doc, container in senders:
        where = f"omega: {doc['metadata']['name']}"
        env = _env(container)
        url = env.get("OMEGA_HOST_EVENTS_URL")
        if not (_https_remote(url) or (env.get("OMEGA_HOST_EVENTS_ALLOW_HTTP") == "1" and url)):
            failed.append(f"{where} OMEGA_HOST_EVENTS_URL={url!r} is not an https remote URL")
        if not env.get("OMEGA_HOST_EVENTS_KEY_ID"):
            failed.append(f"{where} has no OMEGA_HOST_EVENTS_KEY_ID")
        if not _https_remote(env.get("OMEGA_SQUAD_DELEGATION_ISSUER")):
            failed.append(f"{where} has no https OMEGA_SQUAD_DELEGATION_ISSUER for its events")
        if env.get("OMEGA_HOST_EVENTS_SECRET_FILE") != f"{HOST_EVENTS_DIR}/secret":
            failed.append(f"{where}: the host-events secret path is not the mounted secret")
        spec = _pod_spec(doc)
        volume = next(
            (v for v in spec.get("volumes", []) if v["name"] == "omega-host-events"), {}
        ).get("secret", {})
        # 0440 = 288: root-owned and readable through the fsGroup, never by others.
        if volume.get("defaultMode") != 0o440 or not volume.get("secretName"):
            failed.append(
                f"{where} host-events secret volume mode {volume.get('defaultMode')!r}, not 0440"
            )
        if not any(
            m["mountPath"] == HOST_EVENTS_DIR and m.get("readOnly")
            for m in container.get("volumeMounts", [])
        ):
            failed.append(f"{where} does not mount the host-events secret read-only")
        if not spec["securityContext"].get("fsGroup"):
            failed.append(f"{where} has no fsGroup, so the host-events secret is unreadable")
        ca_file = env.get("OMEGA_HOST_EVENTS_CA_FILE")
        if private_ca and (
            ca_file != f"{HOST_EVENTS_CA_DIR}/ca.crt"
            or not any(
                m["mountPath"] == HOST_EVENTS_CA_DIR and m.get("readOnly")
                for m in container.get("volumeMounts", [])
            )
        ):
            failed.append(
                f"{where} does not check EE's certificate with the CA the gateway trusts"
                f" (OMEGA_HOST_EVENTS_CA_FILE={ca_file!r})"
            )
        for pod_wide in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE"):
            if (env.get(pod_wide) or "").startswith((HOST_EVENTS_CA_DIR, "/etc/omega-gateway")):
                failed.append(f"{where} makes EE's CA the whole pod's trust store ({pod_wide})")
    for doc in _workloads(docs):
        if _labels(doc).get("app.kubernetes.io/component") != "bootstrap":
            continue
        env = {k: v for c in _containers(doc) for k, v in _env(c).items()}
        if not _https_remote(env.get("OMEGA_SQUAD_DELEGATION_ISSUER")):
            failed.append(
                "omega: the bootstrap Job does not see the issuer: the host-events drain registers hourly"
            )
        if any(v["name"] == "omega-host-events" for v in _pod_spec(doc).get("volumes", [])):
            failed.append("omega: the bootstrap Job mounts the host-events secret")
    return failed


def _check_routes_and_policies(docs: list[dict]) -> list[str]:
    failed = []
    ingress = _find(docs, "Ingress", "futureagi-omega")
    if ingress is None:
        return ["omega: no Omega Ingress"]
    paths = {
        (rule["host"], p["path"], p["backend"]["service"]["port"]["name"])
        for rule in ingress["spec"]["rules"]
        for p in rule["http"]["paths"]
    }
    for expected in (
        ("futureagi.example.com", "/omega/api", "http"),
        ("console.futureagi.example.com", "/", "console"),
    ):
        if expected not in paths:
            failed.append(f"omega: the Omega Ingress lacks {expected}")
    for doc in docs:
        if doc["kind"] != "Ingress":
            continue
        for rule in doc["spec"]["rules"]:
            for p in rule["http"]["paths"]:
                if "omega-os" in p["backend"]["service"]["name"]:
                    failed.append(
                        f"omega: Ingress {doc['metadata']['name']} routes to EE"
                    )
    annotations = ingress["metadata"].get("annotations") or {}
    if annotations.get("nginx.ingress.kubernetes.io/proxy-buffering") != "off":
        failed.append("omega: event streams are buffered by the Omega Ingress")
    certificate = _find(docs, "Certificate", "futureagi-backend-omega-tls")
    if certificate is None:
        failed.append("omega: no Certificate for the internal TLS listener")
    elif "futureagi-backend.futureagi.svc" not in certificate["spec"]["dnsNames"]:
        failed.append(f"omega: certificate SANs {certificate['spec']['dnsNames']}")
    public = _find(docs, "NetworkPolicy", "futureagi-public")
    selected = public["spec"]["podSelector"]["matchExpressions"][0]["values"]
    if "omega-gateway" not in selected:
        failed.append("omega: no NetworkPolicy selects the gateway pods")
    listener = _find(docs, "NetworkPolicy", "futureagi-omega-internal-tls")
    if listener is None or listener["spec"]["ingress"][0]["ports"] != [
        {"protocol": "TCP", "port": "https-internal"}
    ]:
        failed.append("omega: EE is not admitted to the internal TLS port alone")
    return failed


def _template_labels(doc: dict) -> dict:
    if doc["kind"] == "Pod":
        return _labels(doc)
    return doc["spec"]["template"].get("metadata", {}).get("labels") or {}


def check_sender_label(name: str, docs: list[dict]) -> list[str]:
    """Every render: omega.futureagi.ai/host-events-sender: "true" on the pod template
    of exactly the Deployments that send host events (the API and every worker, which
    get OMEGA_HOST_EVENTS_URL), never on another pod (Omega EE would admit it), never
    in a selector (immutable: an upgrade that changes it fails) and never on an
    object's own labels."""
    failed = []
    for doc in docs:
        where = f"{name}: {doc['kind']} {doc['metadata']['name']}"
        if SENDER_LABEL in _labels(doc) and doc["kind"] != "Pod":
            failed.append(f"{where} carries {SENDER_LABEL} on its own labels")
        selector = (doc.get("spec") or {}).get("selector") or {}
        if SENDER_LABEL in (selector.get("matchLabels") or selector):
            failed.append(f"{where} has {SENDER_LABEL} in its selector")
    for doc in _workloads(docs) + [d for d in docs if d["kind"] == "DaemonSet"]:
        where = f"{name}: {doc['kind']} {doc['metadata']['name']}"
        labels = _template_labels(doc)
        component = labels.get("app.kubernetes.io/component", "")
        sends = (
            doc["kind"] == "Deployment"
            and (component == "backend" or component.startswith("worker"))
            and any(_env(c).get("OMEGA_HOST_EVENTS_URL") for c in _containers(doc))
        )
        if sends and labels.get(SENDER_LABEL) != "true":
            failed.append(
                f"{where}: its pods send host events (OMEGA_HOST_EVENTS_URL) but lack"
                f" {SENDER_LABEL}: \"true\", so Omega EE's NetworkPolicy drops them"
            )
        if not sends and SENDER_LABEL in labels:
            failed.append(
                f"{where}: its pods carry {SENDER_LABEL} but send no host events:"
                " Omega EE's NetworkPolicy would admit them"
            )
    return failed


def check_omega(docs: list[dict]) -> list[str]:
    return (
        _check_gateway(docs)
        + _check_frontend(docs)
        + _check_backend(docs)
        + _check_host_events(docs)
        + _check_routes_and_policies(docs)
    )
