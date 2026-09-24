#!/usr/bin/env python3
"""Render the DEV-only handoff from the unrouted bootstrap producer.

The bootstrap producer owns the reviewed image, Kafka destination, stream,
workspace, and shared runtime bind.  This renderer copies only that unified
catalog contract onto the existing application's ``fi-collector`` service.
It never copies the bootstrap producer's expired/source-only credentials or
listener addresses, and it cannot add a public port.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

APPLICATION_SERVICE = "fi-collector"
KAFKA_NETWORK_KEY = "property-catalog-kafka-existing"
KAFKA_NETWORK_NAME = "property-catalog-dev"
PRODUCER_SERVICE = "property-catalog-producer"
RUNTIME_DESTINATION = "/var/lib/property-catalog-runtime"
_IMAGE_RE = re.compile(r"sha256:[0-9a-f]{64}")
_RUNTIME_SOURCE_RE = re.compile(r"/home/ubuntu/property-catalog-kartik-[a-z0-9-]+/runtime")

_CATALOG_ENV_KEYS = (
    "FI_CATALOG_MODE",
    "FI_PROPERTY_CATALOG_DEV_ACK",
    "FI_PROPERTY_CATALOG_ENVIRONMENT",
    "FI_PROPERTY_CATALOG_EPOCH",
    "FI_PROPERTY_CATALOG_KAFKA_BROKERS",
    "FI_PROPERTY_CATALOG_KAFKA_TOPIC",
    "FI_PROPERTY_CATALOG_MODE",
    "FI_PROPERTY_CATALOG_PRODUCER_STREAM_ID",
    "FI_PROPERTY_CATALOG_PROJECTION_VERSION",
    "FI_PROPERTY_CATALOG_REPLAY_INTERVAL",
    "FI_PROPERTY_CATALOG_REVISION_FENCE_FILE",
    "FI_PROPERTY_CATALOG_SPOOL_DIR",
    "FI_PROPERTY_CATALOG_WORKSPACE_ALLOWLIST",
)


class LiveCollectorHandoffError(RuntimeError):
    """The reviewed bootstrap producer cannot safely hand off live traffic."""


class _UniqueSafeLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader, node, deep=False):
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise LiveCollectorHandoffError(f"duplicate YAML key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LiveCollectorHandoffError(f"{label} must be a mapping")
    return value


def _load_bootstrap(path: Path) -> Mapping[str, Any]:
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueSafeLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise LiveCollectorHandoffError(f"cannot read bootstrap Compose: {exc}") from exc
    top = _mapping(value, "bootstrap Compose")
    name = top.get("name")
    if not isinstance(name, str) or not name.startswith(
        "fi-property-catalog-kartik-"
    ):
        raise LiveCollectorHandoffError("bootstrap Compose is outside Kartik DEV")
    services = _mapping(top.get("services"), "bootstrap services")
    producer = _mapping(services.get(PRODUCER_SERVICE), "bootstrap producer")
    _producer_contract(producer)
    return top


def _runtime_bind(producer: Mapping[str, Any]) -> dict[str, str]:
    volumes = producer.get("volumes")
    if not isinstance(volumes, Sequence) or isinstance(volumes, (str, bytes)):
        raise LiveCollectorHandoffError("bootstrap producer volumes drifted")
    matches: list[dict[str, str]] = []
    for value in volumes:
        if not isinstance(value, Mapping):
            continue
        source = str(value.get("source", ""))
        target = str(value.get("target", ""))
        if (
            value.get("type") == "bind"
            and target == RUNTIME_DESTINATION
            and _RUNTIME_SOURCE_RE.fullmatch(source) is not None
        ):
            matches.append({"type": "bind", "source": source, "target": target})
    if len(matches) != 1:
        raise LiveCollectorHandoffError(
            "bootstrap producer must have one exact Kartik runtime bind"
        )
    return matches[0]


def _producer_contract(producer: Mapping[str, Any]) -> None:
    environment = _mapping(producer.get("environment"), "producer environment")
    image = str(producer.get("image", ""))
    if (
        _IMAGE_RE.fullmatch(image) is None
        or producer.get("pull_policy") != "never"
        or producer.get("networks")
        != ["application-existing", "kafka-existing"]
        or "ports" in producer
        or "expose" in producer
        or environment.get("FI_PROPERTY_CATALOG_MODE") != "kafka"
        or environment.get("FI_PROPERTY_CATALOG_ENVIRONMENT") != "development"
        or environment.get("FI_CATALOG_MODE") != "disabled"
        or environment.get("FI_GRPC_ADDR") != "127.0.0.1:4317"
        or environment.get("FI_HTTP_ADDR") != "127.0.0.1:4318"
    ):
        raise LiveCollectorHandoffError("bootstrap producer safety contract drifted")
    missing = [key for key in _CATALOG_ENV_KEYS if not str(environment.get(key, ""))]
    if missing:
        raise LiveCollectorHandoffError(
            "bootstrap producer is missing catalog contract keys: "
            + ", ".join(missing)
        )
    _runtime_bind(producer)


def render_overlay(bootstrap: Mapping[str, Any]) -> dict[str, Any]:
    services = _mapping(bootstrap.get("services"), "bootstrap services")
    producer = _mapping(services.get(PRODUCER_SERVICE), "bootstrap producer")
    _producer_contract(producer)
    producer_environment = _mapping(
        producer.get("environment"), "producer environment"
    )
    environment = {key: producer_environment[key] for key in _CATALOG_ENV_KEYS}
    service = {
        "image": producer["image"],
        "pull_policy": "never",
        "environment": environment,
        "volumes": [_runtime_bind(producer)],
        "networks": ["default", KAFKA_NETWORK_KEY],
        "labels": {
            "futureagi.component": "live-property-catalog-producer",
            "futureagi.environment": "development",
            "futureagi.property-catalog-handoff": "reviewed-v1",
        },
    }
    overlay = {
        "services": {APPLICATION_SERVICE: service},
        "networks": {
            KAFKA_NETWORK_KEY: {
                "external": True,
                "name": KAFKA_NETWORK_NAME,
            }
        },
    }
    validate_overlay(overlay, producer=producer)
    return overlay


def validate_overlay(overlay: Any, *, producer: Mapping[str, Any]) -> None:
    top = _mapping(overlay, "live collector handoff")
    if set(top) != {"services", "networks"}:
        raise LiveCollectorHandoffError("handoff may contain only services and networks")
    services = _mapping(top["services"], "handoff services")
    if set(services) != {APPLICATION_SERVICE}:
        raise LiveCollectorHandoffError("handoff may change only fi-collector")
    service = _mapping(services[APPLICATION_SERVICE], APPLICATION_SERVICE)
    if any(
        key in service
        for key in (
            "build",
            "command",
            "depends_on",
            "devices",
            "entrypoint",
            "env_file",
            "expose",
            "links",
            "network_mode",
            "ports",
            "privileged",
        )
    ):
        raise LiveCollectorHandoffError("handoff gained routing or privilege")
    producer_environment = _mapping(
        producer.get("environment"), "producer environment"
    )
    expected_environment = {
        key: producer_environment[key] for key in _CATALOG_ENV_KEYS
    }
    labels = _mapping(service.get("labels"), "handoff labels")
    if (
        service.get("image") != producer.get("image")
        or service.get("pull_policy") != "never"
        or service.get("environment") != expected_environment
        or service.get("volumes") != [_runtime_bind(producer)]
        or service.get("networks") != ["default", KAFKA_NETWORK_KEY]
        or labels.get("futureagi.environment") != "development"
        or labels.get("futureagi.property-catalog-handoff") != "reviewed-v1"
    ):
        raise LiveCollectorHandoffError("live collector handoff contract drifted")
    forbidden_credentials = {
        "FI_CH_PASSWORD",
        "FI_CH_USERNAME",
        "FI_PG_READ",
        "FI_PG_WRITE",
    }
    if forbidden_credentials & set(expected_environment):
        raise LiveCollectorHandoffError("bootstrap credentials leaked into handoff")
    networks = _mapping(top["networks"], "handoff networks")
    if networks != {
        KAFKA_NETWORK_KEY: {"external": True, "name": KAFKA_NETWORK_NAME}
    }:
        raise LiveCollectorHandoffError("handoff Kafka network drifted")


def render_yaml(bootstrap: Mapping[str, Any]) -> str:
    overlay = render_overlay(bootstrap)
    rendered = yaml.safe_dump(overlay, sort_keys=False, explicit_start=True)
    round_trip = yaml.load(rendered, Loader=_UniqueSafeLoader)
    producer = _mapping(
        bootstrap["services"][PRODUCER_SERVICE], "bootstrap producer"
    )
    validate_overlay(round_trip, producer=producer)
    if round_trip != overlay:
        raise LiveCollectorHandoffError("handoff YAML did not round-trip exactly")
    return rendered


def _write_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap-compose", required=True)
    parser.add_argument("--output")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.output and args.check:
        parser.error("--output and --check are mutually exclusive")
    try:
        bootstrap = _load_bootstrap(Path(args.bootstrap_compose))
        rendered = render_yaml(bootstrap)
        if args.output:
            _write_atomic(Path(args.output), rendered)
        elif not args.check:
            sys.stdout.write(rendered)
    except (OSError, LiveCollectorHandoffError) as exc:
        sys.stderr.write(f"property-catalog live handoff rejected: {exc}\n")
        return 2
    if args.check:
        sys.stdout.write(
            "validated one live DEV collector handoff; routes and credentials "
            "unchanged\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
