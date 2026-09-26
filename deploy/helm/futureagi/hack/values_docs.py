#!/usr/bin/env python3
"""Keep the README values table and values.schema.json in step with values.yaml.

    python3 hack/values_docs.py          rewrite README.md's table and values.schema.json
    python3 hack/values_docs.py --check  fail when either is stale or a key is undocumented

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

# path pattern -> schema fragment merged over the one derived from the default.
RULES: list[tuple[str, dict]] = [
    ("*.mode", {"enum": ["external", "bundled"]}),
    ("objectStorage.backend", {"enum": ["s3", "gcs", "minio"]}),
    ("config.envType", {"enum": ["production", "local"]}),
    ("config.cdcMode", {"enum": ["outbox", "off"]}),
    ("config.logLevel", {"enum": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]}),
    ("bootstrap.installHook", {"enum": ["auto", "pre-install", "post-install"]}),
    (
        "postgres.external.sslMode",
        {"enum": ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]},
    ),
    ("*.service.type", {"enum": ["ClusterIP", "NodePort", "LoadBalancer"]}),
    ("*pullPolicy", {"enum": ["", "Always", "IfNotPresent", "Never"]}),
    ("*.tag", {"type": ["string", "number"]}),
    ("*.digest", {"pattern": "^(sha256:[a-f0-9]{64})?$"}),
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
    ("*.extraEnv", SCALAR_MAP),
    ("secrets.extra", SCALAR_MAP),
    ("*Annotations", STRING_MAP),
    ("*.annotations", STRING_MAP),
    ("*Labels", STRING_MAP),
    ("*.nodeSelector", STRING_MAP),
    ("nodeSelector", STRING_MAP),
    ("worker.allQueues.excludedQueues", {"items": {"type": "string"}}),
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
                },
            }
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
        rows.append(
            f"| `{path}` | {default_text(value).replace('|', '&#124;')} "
            f"| {description.replace('|', '&#124;')} |"
        )
    return "\n".join(rows)


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
    new_readme = (
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
            stale.append("README.md values table")
        if stale:
            print(
                f"stale: {', '.join(stale)}. Run: python3 {Path(__file__).relative_to(CHART.parent.parent.parent)}",
                file=sys.stderr,
            )
            return 1
        print(f"ok   {len(docs)} documented keys; schema and README table are current")
        return 0

    SCHEMA.write_text(schema)
    README.write_text(new_readme)
    print(f"wrote values.schema.json and the README table ({len(docs)} keys)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
