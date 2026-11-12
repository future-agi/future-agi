#!/usr/bin/env python3
"""Invariants of the Omega Hub objects (values `hub`, templates/hub/) in the
manifests hack/check.sh rendered:

    python3 hack/rendered_hub_checks.py <dir of rendered value sets>

- `hub` (ci/hub.yaml) and `hub-generated` (examples/hub.yaml over the bundled
  datastores): one checker Deployment whose environment is the registry, its
  token and the runner host, nothing else (no database, storage, provider or
  application secret); it reaches the runner over mutual TLS with a
  read-only client certificate and never mounts a Docker socket or a host
  path; it runs unprivileged, with no Kubernetes token. Every Python process
  shares the checker's token (same Secret, same key) and gets the Hub's
  settings; the UI turns its Omega products on.
- `hub`: the checker's NetworkPolicy takes no connections and reaches only
  DNS, the backend and the runner host's Docker port.
- `hub-generated`: the token is generated into the chart's Secret.
- Every other value set: no Hub object, setting or token at all. The UI's
  switch for its Omega products (VITE_OMEGA_CONSOLE) is shared with the Omega
  gateway (values `omega`, hack/rendered_omega_checks.py): in a render where
  the gateway runs, it is the gateway's, and the Omega checks hold it there.

Needs PyYAML.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

HUB_SETS = ("hub", "hub-generated")
WORKLOADS = ("Deployment", "StatefulSet", "Job", "DaemonSet")
CHECKER_ENV = {
    "OMEGA_HUB_CHECKER_ID",
    "OMEGA_HUB_CHECKER_TOKEN",
    "OMEGA_HUB_URL",
    "OMEGA_HUB_RUNNER_IMAGE",
    "OMEGA_HUB_RUNNER_RUNTIME",
    "OMEGA_HUB_RUNNER_WAIT",
    "DOCKER_HOST",
    "DOCKER_TLS_VERIFY",
    "DOCKER_CERT_PATH",
    "HOME",
}
PYTHON_HUB_ENV = ("OMEGA_HUB_STORAGE", "OMEGA_HUB_BUCKET", "OMEGA_HUB_APP_URL", "OMEGA_HUB_PUBLIC_API_URL")


def component(doc: dict) -> str:
    return doc["metadata"].get("labels", {}).get("app.kubernetes.io/component", "")


def pod_spec(doc: dict) -> dict:
    return doc["spec"]["template"]["spec"]


def containers(doc: dict) -> list[dict]:
    spec = pod_spec(doc)
    return spec.get("initContainers", []) + spec.get("containers", [])


def env_of(container: dict) -> dict[str, dict]:
    return {entry["name"]: entry for entry in container.get("env", [])}


def is_python(container: dict) -> bool:
    return "DJANGO_SETTINGS_MODULE" in env_of(container)


def secret_ref(entry: dict | None) -> tuple[str, str] | None:
    ref = (entry or {}).get("valueFrom", {}).get("secretKeyRef")
    return (ref["name"], ref["key"]) if ref else None


def check_hub(name: str, docs: list[dict]) -> list[str]:
    failed: list[str] = []
    workloads = [d for d in docs if d["kind"] in WORKLOADS]
    checkers = [d for d in workloads if component(d) == "hub-checker"]
    if len(checkers) != 1 or checkers[0]["kind"] != "Deployment":
        return [f"{name}: expected one hub-checker Deployment, found {[d['kind'] for d in checkers]}"]
    checker = checkers[0]
    spec = pod_spec(checker)
    where = f"{name}: Deployment {checker['metadata']['name']}"
    pods = spec["containers"]
    if len(pods) != 1 or spec.get("initContainers"):
        failed.append(f"{where}: expected one container and no init containers")
    container = pods[0]
    env = env_of(container)
    if set(env) != CHECKER_ENV:
        failed.append(f"{where}: env is {sorted(env)}, expected exactly {sorted(CHECKER_ENV)}")
    plain = {k: v.get("value") for k, v in env.items()}
    if not (plain.get("DOCKER_HOST") or "").startswith("tcp://"):
        failed.append(f"{where}: DOCKER_HOST {plain.get('DOCKER_HOST')!r} is not the runner host over TCP")
    if plain.get("DOCKER_TLS_VERIFY") != "1" or plain.get("DOCKER_CERT_PATH") != "/certs/client":
        failed.append(f"{where}: the runner connection is not mutual TLS from /certs/client")
    if plain.get("OMEGA_HUB_RUNNER_RUNTIME") != "runsc":
        failed.append(f"{where}: OMEGA_HUB_RUNNER_RUNTIME is not runsc")
    if plain.get("OMEGA_HUB_RUNNER_WAIT") != "0":
        failed.append(f"{where}: a checker that cannot sandbox must exit (OMEGA_HUB_RUNNER_WAIT=0)")
    if not (plain.get("OMEGA_HUB_URL") or "").startswith("http://") or "backend" not in plain.get("OMEGA_HUB_URL", ""):
        failed.append(f"{where}: OMEGA_HUB_URL {plain.get('OMEGA_HUB_URL')!r} is not the release's backend")
    if container.get("envFrom"):
        failed.append(f"{where}: envFrom would hand the checker more than its own settings")
    if spec.get("automountServiceAccountToken") is not False:
        failed.append(f"{where}: mounts a Kubernetes token")
    if spec.get("hostNetwork") or spec.get("hostPID") or spec.get("hostIPC"):
        failed.append(f"{where}: shares a host namespace")
    security = container.get("securityContext", {})
    if security.get("privileged") or security.get("allowPrivilegeEscalation") is not False:
        failed.append(f"{where}: may gain privileges")
    if security.get("readOnlyRootFilesystem") is not True or security.get("capabilities", {}).get("drop") != ["ALL"]:
        failed.append(f"{where}: root filesystem writable or capabilities kept")
    if not spec.get("securityContext", {}).get("runAsNonRoot"):
        failed.append(f"{where}: may run as root")
    volumes = {v["name"]: v for v in spec.get("volumes", [])}
    for volume in volumes.values():
        if "hostPath" in volume:
            failed.append(f"{where}: mounts the host path {volume['hostPath'].get('path')}")
    client = volumes.get("runner-client", {}).get("secret")
    mounts = {m["name"]: m for m in container.get("volumeMounts", [])}
    if not client or {i["key"] for i in client.get("items", [])} != {"ca.pem", "cert.pem", "key.pem"}:
        failed.append(f"{where}: the runner's client certificate is not mounted from a Secret (ca.pem, cert.pem, key.pem)")
    elif client.get("defaultMode") != 0o440 or not mounts.get("runner-client", {}).get("readOnly"):
        failed.append(f"{where}: the runner's client certificate is not read-only to the checker's group (0440)")
    if mounts.get("runner-client", {}).get("mountPath") != "/certs/client":
        failed.append(f"{where}: the client certificate is not at DOCKER_CERT_PATH")
    token = secret_ref(env.get("OMEGA_HUB_CHECKER_TOKEN"))
    if not token or token[1] != "OMEGA_HUB_CHECKER_TOKEN":
        failed.append(f"{where}: the token is not read from a Secret's OMEGA_HUB_CHECKER_TOKEN")

    pythons = [(d, c) for d in workloads for c in containers(d) if is_python(c)]
    if not any(component(d) == "backend" for d, _ in pythons):
        failed.append(f"{name}: no backend container to check")
    for doc, python in pythons:
        here = f"{name}: {doc['kind']} {doc['metadata']['name']}/{python['name']}"
        penv = env_of(python)
        if secret_ref(penv.get("OMEGA_HUB_CHECKER_TOKEN")) != token:
            failed.append(f"{here}: OMEGA_HUB_CHECKER_TOKEN is {secret_ref(penv.get('OMEGA_HUB_CHECKER_TOKEN'))}, the checker reads {token}")
        for key in PYTHON_HUB_ENV:
            if not penv.get(key, {}).get("value"):
                failed.append(f"{here}: {key} is not set")
        if penv.get("OMEGA_HUB_STORAGE", {}).get("value") != "object":
            failed.append(f"{here}: the Hub's storage is not object storage")
        for key, want in (("OMEGA_HUB_APP_URL", "/dashboard/hub"), ("OMEGA_HUB_PUBLIC_API_URL", "/omega-hub/v1")):
            if not penv.get(key, {}).get("value", "").endswith(want):
                failed.append(f"{here}: {key} does not end in {want}")
    frontends = [d for d in workloads if component(d) == "frontend"]
    for doc in frontends:
        fenv = {k: v.get("value") for k, v in env_of(doc["spec"]["template"]["spec"]["containers"][0]).items()}
        if fenv.get("VITE_OMEGA_CONSOLE") != "1":
            failed.append(f"{name}: the UI does not turn its Omega products on (VITE_OMEGA_CONSOLE)")
    return failed


def check_policy(name: str, docs: list[dict]) -> list[str]:
    policies = [d for d in docs if d["kind"] == "NetworkPolicy" and d["metadata"]["name"].endswith("-hub-checker")]
    if len(policies) != 1:
        return [f"{name}: expected one hub-checker NetworkPolicy, found {len(policies)}"]
    spec = policies[0]["spec"]
    failed = []
    if spec["podSelector"]["matchLabels"].get("app.kubernetes.io/component") != "hub-checker":
        failed.append(f"{name}: the hub-checker NetworkPolicy selects another component")
    if set(spec.get("policyTypes", [])) != {"Ingress", "Egress"} or spec.get("ingress"):
        failed.append(f"{name}: the hub-checker NetworkPolicy admits connections")
    egress = spec.get("egress", [])
    ports = sorted((p["port"], p.get("protocol")) for rule in egress for p in rule.get("ports", []))
    if ports != sorted([(53, "UDP"), (53, "TCP"), (8000, "TCP"), (2376, "TCP")]):
        failed.append(f"{name}: the hub-checker may reach ports {ports}, expected DNS, the backend's 8000 and the runner's 2376")
    for rule in egress:
        if not rule.get("ports"):
            failed.append(f"{name}: a hub-checker egress rule allows every port")
    backend = [r for r in egress if any(p["port"] == 8000 for p in r.get("ports", []))]
    if not backend or backend[0].get("to", [{}])[0].get("podSelector", {}).get("matchLabels", {}).get("app.kubernetes.io/component") != "backend":
        failed.append(f"{name}: the hub-checker's port 8000 reaches more than the backend")
    return failed


def check_generated(name: str, docs: list[dict]) -> list[str]:
    secret = next((d for d in docs if d["kind"] == "Secret" and d["metadata"]["name"].endswith("-secrets")), {})
    failed = []
    if "OMEGA_HUB_CHECKER_TOKEN" not in (secret.get("data") or {}):
        failed.append(f"{name}: the chart's Secret has no generated OMEGA_HUB_CHECKER_TOKEN")
    checker = next(d for d in docs if d["kind"] == "Deployment" and component(d) == "hub-checker")
    ref = secret_ref(env_of(pod_spec(checker)["containers"][0]).get("OMEGA_HUB_CHECKER_TOKEN"))
    if ref != (secret.get("metadata", {}).get("name"), "OMEGA_HUB_CHECKER_TOKEN"):
        failed.append(f"{name}: the checker reads its token from {ref}, not the chart's Secret")
    return failed


def check_off(name: str, docs: list[dict]) -> list[str]:
    failed = []
    gateway_on = any(component(d) == "omega-gateway" for d in docs)
    for doc in docs:
        if "hub" in component(doc) or doc["metadata"]["name"].endswith("-hub-checker"):
            failed.append(f"{name}: renders {doc['kind']} {doc['metadata']['name']} with hub.enabled off")
        if doc["kind"] == "Secret" and "OMEGA_HUB_CHECKER_TOKEN" in (doc.get("data") or {}):
            failed.append(f"{name}: Secret {doc['metadata']['name']} holds a Hub token with hub.enabled off")
        if doc["kind"] not in WORKLOADS:
            continue
        for container in containers(doc):
            names = [
                k
                for k in env_of(container)
                if k.startswith("OMEGA_HUB_") or (k == "VITE_OMEGA_CONSOLE" and not gateway_on)
            ]
            if names:
                failed.append(f"{name}: {doc['metadata']['name']}/{container['name']} sets {names} with hub.enabled off")
    return failed


def main() -> int:
    out = Path(sys.argv[1])
    renders = {
        path.stem: [d for d in yaml.safe_load_all(path.read_text()) if d]
        for path in sorted(out.glob("*.yaml"))
    }
    failed = []
    for name in HUB_SETS:
        if name not in renders:
            failed.append(f"{name}: not rendered")
    for name, docs in renders.items():
        if name in HUB_SETS:
            failed += check_hub(name, docs)
        else:
            failed += check_off(name, docs)
    if "hub" in renders:
        failed += check_policy("hub", renders["hub"])
    if "hub-generated" in renders:
        failed += check_generated("hub-generated", renders["hub-generated"])
    if failed:
        print("the Omega Hub objects break their invariants:", file=sys.stderr)
        for line in failed:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(f"ok   Hub invariants hold in {len(renders)} renders ({', '.join(n for n in HUB_SETS if n in renders)} with the Hub on)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
