#!/usr/bin/env python3
"""Keep the README values table and values.schema.json in step with values.yaml.

    python3 hack/values_docs.py          rewrite README.md's generated content and values.schema.json
    python3 hack/values_docs.py --check  fail when either is stale or a key is undocumented

The webhook custody prose and its envFrom table note are generated here as
well: they describe template policy, not a new values default or schema rule.

Every key in values.yaml has a ``# -- description`` comment on the line
above it, or is a section whose keys all do. A documented key whose value is
a map (resources, annotations, extraEnv, the gateway config, ...) is
free-form: its entries are not documented or validated one by one.

The schema is derived from the defaults' types, with the enums, ranges and
list shapes below. Needs PyYAML.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

import yaml

CHART = Path(__file__).resolve().parent.parent
VALUES = CHART / "values.yaml"
SCHEMA = CHART / "values.schema.json"
README = CHART / "README.md"
TABLE_START = "<!-- values-table:start -->"
TABLE_END = "<!-- values-table:end -->"
KEY = re.compile(r"([A-Za-z0-9_.-]+):(?:\s+(.*))?$")

SCALAR = {"type": ["string", "number", "boolean"]}
# Environment maps: any scalar (the chart quotes it).
SCALAR_MAP = {"type": "object", "additionalProperties": SCALAR}
# Kubernetes wants strings here: `--set-string` for a numeric value.
STRING_MAP = {"type": "object", "additionalProperties": {"type": "string"}}
PORT = {"type": "integer", "minimum": 1, "maximum": 65535}
REPLICAS = {"type": "integer", "minimum": 0}
PERCENT_OR_EMPTY = {
    "anyOf": [{"type": "integer", "minimum": 1}, {"type": "string", "maxLength": 0}]
}

# Empty means the chart/release default. Keep these in the schema as well as
# validate.yaml: Helm lint ignores template fail(), even in --strict mode.
DNS_LABEL_OR_EMPTY = {
    "pattern": "^([a-z0-9]([-a-z0-9]*[a-z0-9])?)?$",
    "maxLength": 63,
}

# path pattern -> schema fragment merged over the one derived from the default.
RULES: list[tuple[str, dict]] = [
    ("nameOverride", DNS_LABEL_OR_EMPTY),
    ("fullnameOverride", DNS_LABEL_OR_EMPTY),
    ("*.mode", {"enum": ["external", "bundled"]}),
    ("objectStorage.backend", {"enum": ["s3", "gcs", "minio"]}),
    ("config.envType", {"enum": ["production", "local"]}),
    ("config.cdcMode", {"enum": ["outbox", "off"]}),
    ("config.logLevel", {"enum": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]}),
    ("bootstrap.installHook", {"enum": ["auto", "pre-install", "post-install"]}),
    ("hosted.migration.phase", {"enum": ["", "schema", "backfill", "check"]}),
    (
        "postgres.external.sslMode",
        {"enum": ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]},
    ),
    ("*.service.type", {"enum": ["ClusterIP", "NodePort", "LoadBalancer"]}),
    ("*pullPolicy", {"enum": ["", "Always", "IfNotPresent", "Never"]}),
    ("*.tag", {"type": ["string", "number"]}),
    ("*.digest", {"pattern": "^(sha256:[a-f0-9]{64})?$"}),
    (
        "image.digests",
        {
            "additionalProperties": False,
            "properties": {
                k: {"type": "string", "pattern": "^(sha256:[a-f0-9]{64})?$"}
                for k in (
                    "backend",
                    "frontend",
                    "fiCollector",
                    "agentccGateway",
                    "serving",
                    "codeExecutor",
                )
            },
        },
    ),
    ("*replicas", REPLICAS),
    ("*.minReplicas", {"type": "integer", "minimum": 1}),
    ("*.maxReplicas", {"type": "integer", "minimum": 1}),
    ("*.maxUnavailable", {"type": ["integer", "string"]}),
    ("*.targetCPUUtilizationPercentage", PERCENT_OR_EMPTY),
    ("*.targetMemoryUtilizationPercentage", PERCENT_OR_EMPTY),
    ("*port", PORT),
    ("*Port", PORT),
    ("*.service.port", PORT),
    ("bootstrap.ttlSecondsAfterFinished", {"type": ["integer", "string"]}),
    ("*Seconds", {"type": "integer", "minimum": 1}),
    # The operator's 900 s schema deadline plus 30 s process grace
    # (agentcc.hosted_migration_progress; deploy/tests/test_hosted_deployment.py).
    ("hosted.migration.activeDeadlineSeconds", {"minimum": 930}),
    ("*.extraEnv", SCALAR_MAP),
    ("secrets.extra", SCALAR_MAP),
    ("*Annotations", STRING_MAP),
    ("*.annotations", STRING_MAP),
    ("*Labels", STRING_MAP),
    ("*.nodeSelector", STRING_MAP),
    ("nodeSelector", STRING_MAP),
    ("worker.allQueues.excludedQueues", {"items": {"type": "string"}}),
    (
        "agentccGateway.redis.enabled",
        {
            "anyOf": [
                {"type": "boolean"},
                {"type": "string", "enum": ["auto", "true", "false"]},
            ]
        },
    ),
    ("agentccGateway.redis.db", {"type": "integer", "minimum": 0}),
    # Variable names, each a key of providerCredentials.existingSecret.
    (
        "agentccGateway.providerCredentials.keys",
        {"items": {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_]*$"}},
    ),
    (
        "worker.queues",
        {
            "items": {
                "type": "object",
                "required": ["name"],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "pattern": "^[a-z0-9_]+$"},
                    "replicas": REPLICAS,
                    "maxConcurrentActivities": {"type": "integer", "minimum": 1},
                    "maxConcurrentWorkflowTasks": {"type": "integer", "minimum": 1},
                    "resources": {"type": "object"},
                    "image": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            k: {"type": ["string", "number"]}
                            for k in (
                                "registry",
                                "repository",
                                "tag",
                                "digest",
                                "pullPolicy",
                            )
                        },
                    },
                    "extraEnv": SCALAR_MAP,
                    "autoscaling": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "enabled": {"type": "boolean"},
                            "minReplicas": {"type": "integer", "minimum": 1},
                            "maxReplicas": {"type": "integer", "minimum": 1},
                            "targetCPUUtilizationPercentage": PERCENT_OR_EMPTY,
                            "targetMemoryUtilizationPercentage": PERCENT_OR_EMPTY,
                            "behavior": {"type": "object"},
                        },
                    },
                    "gracefulShutdownSeconds": {"type": "integer", "minimum": 1},
                    "preStopSleepSeconds": {"type": "integer", "minimum": 0},
                    "pdb": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "enabled": {"type": "boolean"},
                            "maxUnavailable": {"type": ["integer", "string"]},
                        },
                    },
                    "nodeSelector": STRING_MAP,
                    "tolerations": {"type": "array"},
                    "affinity": {"type": "object"},
                    "topologySpreadConstraints": {"type": "array"},
                    "priorityClassName": {"type": "string"},
                    "podAnnotations": STRING_MAP,
                    "podLabels": STRING_MAP,
                },
            }
        },
    ),
    ("topologySpread.preset", {"enum": ["soft", "hard", "none"]}),
    # Gateway API durations (HTTPRoute timeouts), or empty.
    ("gatewayApi.timeouts.*", {"pattern": "^(([0-9]{1,5}(h|m|s|ms)){1,4})?$"}),
    ("gatewayApi.llmGateway.timeout", {"pattern": "^(([0-9]{1,5}(h|m|s|ms)){1,4})?$"}),
    ("gatewayApi.parentRefs", {"items": {"type": "object", "required": ["name"]}}),
    ("*.preStopSleepSeconds", {"minimum": 0}),
    # Omega gateway (services/omega-gateway/src/config.mjs bounds the same).
    ("omega.issuer.url", {"pattern": "^(https://[^/?#@ ]+(/[^?#@ ]*)?)?$"}),
    ("omega.gateway.ee.url", {"pattern": "^(https://[^/?#@ ]+(/[^?#@ ]*)?)?$"}),
    (
        "omega.gateway.sessionExchangeUrl",
        {"pattern": "^(https://[^/?#@ ]+(/[^?#@ ]*)?)?$"},
    ),
    ("omega.gateway.otlp.tracesUrl", {"pattern": "^(https://[^/?#@ ]+(/[^?#@ ]*)?)?$"}),
    (
        "omega.gateway.allowedOrigins",
        {"items": {"type": "string", "pattern": "^https://[^/?#@ ]+$"}},
    ),
    ("omega.gateway.drainMs", {"minimum": 0, "maximum": 30000}),
    ("omega.gateway.trustedProxyHops", {"minimum": 0, "maximum": 5}),
    ("omega.gateway.maxBodyBytes", {"minimum": 1024, "maximum": 10485760}),
    ("omega.gateway.maxBundleBytes", {"minimum": 1048576, "maximum": 268435456}),
    ("omega.gateway.console.trustedTypes", {"enum": ["report", "enforce"]}),
    ("omega.gateway.console.staticDir", {"pattern": "^(/.*)?$"}),
    ("omega.ingress.timeout", {"pattern": "^(([0-9]{1,5}(h|m|s|ms)){1,4})?$"}),
    ("omega.internalTls.reloadInterval", {"pattern": "^([0-9]{1,5}(h|m|s)){1,3}$"}),
    ("omega.internalTls.allowFrom", {"items": {"type": "object"}}),
    # Enterprise and hardened operations; validate.yaml enforces the same.
    ("edition", {"enum": ["oss", "ee"]}),
    (
        "billing.enforcement",
        {
            "anyOf": [
                {"type": "boolean"},
                {"type": "string", "enum": ["auto", "true", "false"]},
            ]
        },
    ),
    (
        "global.compatibility.openshift.adaptSecurityContext",
        {"enum": ["auto", "force", "disabled"]},
    ),
    (
        "license.heartbeat",
        {
            "anyOf": [
                {"type": "null"},
                {"type": "boolean"},
                {"type": "string", "enum": ["", "true", "false"]},
            ]
        },
    ),
    ("global.proxy.httpProxy", {"pattern": "^((http|https|socks5h?)://[^/]+/?)?$"}),
    ("global.proxy.httpsProxy", {"pattern": "^((http|https|socks5h?)://[^/]+/?)?$"}),
    (
        "externalSecrets.secrets",
        {
            "propertyNames": {
                "enum": [
                    "app",
                    "llm",
                    "license",
                    "email",
                    "google",
                    "github",
                    "microsoft",
                    "admin",
                    "postgres",
                    "clickhouse",
                    "redis",
                    "objectStorage",
                    "providerCredentials",
                ]
            },
            "additionalProperties": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "data": {"type": "object"},
                    "dataFrom": {"type": "array"},
                    "secretStoreRef": {"type": "object"},
                    "refreshInterval": {"type": "string"},
                    "target": {"type": "object"},
                },
            },
        },
    ),
    (
        "ingress.tls",
        {
            "items": {
                "type": "object",
                "properties": {
                    "secretName": {"type": "string"},
                    "hosts": {"type": "array", "items": {"type": "string"}},
                },
            }
        },
    ),
    # Omega Hub (templates/hub/_hub.tpl refuses the same when hub.enabled).
    (
        "hub.maintainerOrgId",
        {"pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})?$"},
    ),
    (
        "hub.trustedProxyDepth",
        {"anyOf": [{"type": "integer", "minimum": 0}, {"type": "string", "pattern": "^[0-9]*$"}]},
    ),
    ("hub.checker.runner.dockerHost", {"pattern": "^(tcp://[^/:]+(:[0-9]+)?/?)?$"}),
    ("hub.checker.runner.networkPolicyTo", {"items": {"type": "object"}}),
]


def rules_for(path: str) -> dict:
    merged: dict = {}
    for pattern, fragment in RULES:
        if fnmatch.fnmatchcase(path, pattern):
            merged.update(fragment)
    return merged


def parse_docs(text: str) -> tuple[dict[str, str], list[str]]:
    """(documented path -> description, undocumented leaf paths)."""
    docs: dict[str, str] = {}
    missing: list[str] = []
    stack: list[tuple[int, str]] = []
    pending: str | None = None
    skip_below: int | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            pending = None
            continue
        indent = len(line) - len(line.lstrip())
        if skip_below is not None:
            if indent > skip_below:
                continue
            skip_below = None
        if stripped.startswith("# -- "):
            pending = stripped[5:].strip()
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            continue
        match = KEY.match(stripped)
        if not match:
            continue
        key, rest = match.group(1), (match.group(2) or "").strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = ".".join([k for _, k in stack] + [key])
        if pending is not None:
            docs[path] = pending
            pending = None
            skip_below = indent
            continue
        if rest and not rest.startswith("#"):
            missing.append(path)
            continue
        stack.append((indent, key))
    return docs, missing


def lookup(values: dict, path: str):
    node = values
    for part in path.split("."):
        node = node[part]
    return node


def default_text(value) -> str:
    text = json.dumps(value, separators=(", ", ": "))
    if len(text) > 70:
        return "see values.yaml"
    return f"`{text}`"


def table(values: dict, docs: dict[str, str]) -> str:
    rows = ["| Key | Default | Description |", "| --- | --- | --- |"]
    for path, description in docs.items():
        value = lookup(values, path)
        if path == "config.extraEnvFrom":
            description += (
                " WEBHOOKS_ENCRYPTION_KEYS comes only from secrets.existingSecret;"
                " envFrom is not a supported webhook-key source. An explicit empty"
                " entry masks it when no existingSecret is configured or live lookup"
                " finds that Secret without the key. See [webhook encryption at rest]"
                "(#webhook-encryption-at-rest) for optional-reference lookup limits."
            )
        rows.append(
            f"| `{path}` | {default_text(value).replace('|', '&#124;')} "
            f"| {description.replace('|', '&#124;')} |"
        )
    return "\n".join(rows)


def webhook_docs(readme: str) -> str:
    """Keep template custody policy in the existing generated-docs path.

    These unique prose anchors also delimit the old policy on first generation;
    subsequent generations are idempotent. No values/schema changes are needed.
    """
    start = "The shared Python environment"
    end = "Keep the keys in the environment's secret store"
    if readme.count(start) != 1 or readme.count(end) != 1:
        raise SystemExit("webhook documentation anchors changed; update values_docs.py")
    first, last = readme.index(start), readme.index(end)
    if first >= last:
        raise SystemExit("webhook documentation anchors are out of order")
    policy = """The shared Python environment uses only the named Secret for webhook keys,
with an **optional** `secretKeyRef` when the key is available or lookup cannot
see that Secret. All consumers of `INTEGRATION_ENCRYPTION_KEY` receive the
webhook entry: the backend API and its collectstatic init container, the
all-queues worker, exact-aggregation worker and each dedicated queue worker,
and the bootstrap Job. Non-backend components do not receive it.

If `secrets.existingSecret` is unset, every Python consumer gets exactly one
explicit `WEBHOOKS_ENCRYPTION_KEYS` entry with `value: ""`. Kubernetes explicit
`env` takes precedence over `envFrom`, so opaque sources cannot supply webhook
keys in this case. The application drops empty entries and treats the empty
value exactly like an unset variable: webhook secret storage fails closed.
The chart never generates this key, stores it in its own Secret, or supplies
a values default. Inline `extraEnv` and `secrets.extra` entries for this name
are refused with a value-free message.

When `secrets.existingSecret` is set, a live `lookup` reads that named Secret
in the release namespace and checks **key-name membership only**. If the
Secret exists but its data lacks `WEBHOOKS_ENCRYPTION_KEYS`, the same explicit
empty mask is rendered. Otherwise the optional named-Secret reference is
retained. Lookup errors fail the render; live rendering needs GET permission
on the named Secret. No key value is copied into the manifest by this check.

`WEBHOOKS_ENCRYPTION_KEYS` must come only from `secrets.existingSecret`;
`config.extraEnvFrom` is not a supported way to supply it. Unprefixed
ConfigMap and Secret envFrom sources for other variables continue to be
accepted.
**Residual limitation:** offline/client-side rendering (including GitOps)
or a named Secret that does not yet exist cannot establish key membership,
so the optional reference remains. If its key is absent at pod startup,
Kubernetes skips it and an envFrom binding can still supply the variable.
Operators must therefore exclude this name from opaque sources; do not rely
on offline rendering to enforce custody. Deleting the key after rendering a
reference has the same fallback risk. Adding it after an empty mask was
rendered requires re-rendering and rolling out the workloads. Out-of-band
pod changes are outside this chart guarantee.

"""
    return readme[:first] + policy + readme[last:]


def type_of(value) -> dict:
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        return {"type": "array"}
    if isinstance(value, dict):
        return {"type": "object"}
    return {}


def schema_node(path: str, value, docs: dict[str, str]) -> dict:
    if path in docs:
        node = type_of(value)
        rule = rules_for(path)
        if "anyOf" in rule:
            node.pop("type", None)
        node.update(rule)
        node["description"] = docs[path]
        return node
    if isinstance(value, dict):
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                key: schema_node(f"{path}.{key}" if path else key, child, docs)
                for key, child in value.items()
            },
        }
    raise SystemExit(f"{path}: not documented")


def build_schema(values: dict, docs: dict[str, str]) -> dict:
    root = schema_node("", values, docs)
    return {
        "$schema": "https://json-schema.org/draft-07/schema#",
        "title": "Future AGI Helm chart values",
        "description": "Generated from values.yaml by hack/values_docs.py; edit values.yaml or the rules there.",
        **root,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    text = VALUES.read_text()
    values = yaml.safe_load(text)
    docs, missing = parse_docs(text)
    if missing:
        print(
            "undocumented keys in values.yaml (add a `# --` line above):",
            file=sys.stderr,
        )
        for path in missing:
            print(f"  {path}", file=sys.stderr)
        return 1

    schema = json.dumps(build_schema(values, docs), indent=2) + "\n"
    readme = README.read_text()
    start, end = readme.index(TABLE_START), readme.index(TABLE_END)
    new_readme = webhook_docs(
        readme[: start + len(TABLE_START)]
        + "\n"
        + table(values, docs)
        + "\n"
        + readme[end:]
    )

    if args.check:
        stale = []
        if SCHEMA.read_text() != schema:
            stale.append("values.schema.json")
        if readme != new_readme:
            stale.append("README.md generated content")
        if stale:
            print(
                f"stale: {', '.join(stale)}. Run: python3 {os.path.relpath(Path(__file__).resolve())}",
                file=sys.stderr,
            )
            return 1
        print(f"ok   {len(docs)} documented keys; schema and README table are current")
        return 0

    SCHEMA.write_text(schema)
    README.write_text(new_readme)
    print(f"wrote values.schema.json and README generated content ({len(docs)} keys)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
