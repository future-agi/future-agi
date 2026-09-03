#!/usr/bin/env python3
"""Render the post-activation control worker for the Docker DEV catalog.

The bootstrap renderer deliberately keeps reconciliation disabled.  This
second-stage renderer accepts only that reviewed output and adds one dedicated
Temporal worker plus one opt-in schedule registrar.  It cannot change the
producer, consumer, source database, target database, or public routing.
"""

from __future__ import annotations

import argparse
import copy
import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

TASK_QUEUE_PREFIX = "property_catalog_dev_sidecar_"
SCHEDULED_RECONCILE_WALL_MS = "1200000"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_CATALOG_DATABASE_RE = re.compile(r"[a-z][a-z0-9_]{0,127}")
_RESERVED_CATALOG_DATABASES = frozenset(
    {"default", "futureagi", "information_schema", "property_catalog", "system"}
)
_PROJECT_NAME_RE = re.compile(r"fi-property-catalog-[a-z0-9][a-z0-9-]{2,39}")
_PRODUCTION_TOKEN_RE = re.compile(
    r"(?:^|[-._/:])(prod|production|live)(?:$|[-._/:])", re.I
)
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)


class SteadyStateRenderError(RuntimeError):
    """The bootstrap Compose file cannot safely become steady state."""


class _UniqueSafeLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader, node, deep=False):
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise SteadyStateRenderError(f"duplicate YAML key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SteadyStateRenderError(f"{label} must be a mapping")
    return value


def _load_base(path: Path) -> Mapping[str, Any]:
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueSafeLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise SteadyStateRenderError(f"cannot read bootstrap Compose: {exc}") from exc
    top = _mapping(raw, "bootstrap Compose")
    name = top.get("name")
    if (
        not isinstance(name, str)
        or _PROJECT_NAME_RE.fullmatch(name) is None
        or _PRODUCTION_TOKEN_RE.search(name)
    ):
        raise SteadyStateRenderError(
            "bootstrap Compose project is outside isolated DEV"
        )
    services = _mapping(top.get("services"), "bootstrap services")
    if set(services) != {
        "property-catalog-producer",
        "property-catalog-consumer",
        "property-catalog-operator",
    }:
        raise SteadyStateRenderError("bootstrap service inventory drifted")
    return top


def _validate_bootstrap_operator(operator: Mapping[str, Any]) -> None:
    environment = _mapping(operator.get("environment"), "operator environment")
    cloud_deployment = environment.get("CLOUD_DEPLOYMENT")
    target_database = str(environment.get("PROPERTY_CATALOG_DEV_TARGET_DATABASE", ""))
    if (
        operator.get("profiles") != ["operator"]
        or operator.get("restart") != "no"
        or environment.get("ENV_TYPE") != "development"
        or cloud_deployment not in {"", "DEV"}
        or environment.get("PROPERTY_CATALOG_DEV_CLOUD_DEPLOYMENT") != cloud_deployment
        or environment.get("PROPERTY_CATALOG_READ_MODE") != "off"
        or environment.get("PROPERTY_CATALOG_DEV_RECONCILE_ENABLED") != "false"
        or environment.get("PROPERTY_CATALOG_DEV_OTLP_TRAFFIC_AUTHORIZED") != "false"
        or environment.get("PROPERTY_CATALOG_DEV_SOURCE_DATABASE") != "futureagi"
        or _CATALOG_DATABASE_RE.fullmatch(target_database) is None
        or target_database in _RESERVED_CATALOG_DATABASES
        or environment.get("PROPERTY_CATALOG_DEV_SOURCE_DATABASE") == target_database
    ):
        raise SteadyStateRenderError("bootstrap operator safety contract drifted")
    if operator.get("networks") != ["application-existing"]:
        raise SteadyStateRenderError("control operator network scope drifted")
    if len(operator.get("env_file", [])) != 4:
        raise SteadyStateRenderError("control operator credential split drifted")
    if len(operator.get("volumes", [])) != 1:
        raise SteadyStateRenderError("control operator runtime bind drifted")


def _workspace_task_queue(operator: Mapping[str, Any]) -> str:
    environment = _mapping(operator.get("environment"), "operator environment")
    workspace_id = str(environment.get("PROPERTY_CATALOG_DEV_WORKSPACE_ID", ""))
    allowlist = str(environment.get("PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST", ""))
    if _UUID_RE.fullmatch(workspace_id) is None or allowlist != workspace_id:
        raise SteadyStateRenderError(
            "steady-state task queue requires one exact workspace UUID"
        )
    return TASK_QUEUE_PREFIX + workspace_id.replace("-", "")


def _control_service(
    operator: Mapping[str, Any], *, activation_sha256: str, task_queue: str
) -> dict[str, Any]:
    control = copy.deepcopy(dict(operator))
    control.pop("profiles", None)
    control["restart"] = "unless-stopped"
    control["entrypoint"] = ["python", "manage.py", "start_temporal_worker"]
    control["command"] = [
        "--task-queue",
        task_queue,
        "--max-concurrent-activities",
        "1",
        "--max-concurrent-workflow-tasks",
        "1",
        "--graceful-timeout",
        "180",
    ]
    control["environment"].update(
        {
            "PROPERTY_CATALOG_DEV_RECONCILE_ENABLED": "true",
            "PROPERTY_CATALOG_DEV_SCHEDULED_RECONCILE_WALL_MS": (
                SCHEDULED_RECONCILE_WALL_MS
            ),
            "PROPERTY_CATALOG_DEV_TASK_QUEUE": task_queue,
            "PROPERTY_CATALOG_DEV_BOOTSTRAP_ACTIVATION_SHA256": (activation_sha256),
            "TEMPORAL_HOST": "temporal:7233",
            "TEMPORAL_NAMESPACE": "default",
            "TEMPORAL_TASK_QUEUE": task_queue,
            "TEMPORAL_ALL_QUEUES": "false",
            "TEMPORAL_MAX_CONCURRENT_ACTIVITIES": "1",
            "TEMPORAL_MAX_CONCURRENT_WORKFLOW_TASKS": "1",
            "TEMPORAL_GRACEFUL_SHUTDOWN_TIMEOUT": "180",
            "TEMPORAL_RELOAD_DISPATCHER_ON_START": "false",
            "TEMPORAL_RESOURCE_TUNING_ENABLED": "false",
        }
    )
    control["labels"].update(
        {
            "futureagi.component": "scheduled-control-plane",
            "futureagi.schedule": "enabled",
            "futureagi.bootstrap-activation-sha256": activation_sha256,
        }
    )
    return control


def render_overlay(
    base: Mapping[str, Any], *, activation_sha256: str
) -> dict[str, Any]:
    if _SHA256_RE.fullmatch(activation_sha256) is None:
        raise SteadyStateRenderError("bootstrap activation must be a lowercase SHA-256")
    if len(set(activation_sha256)) < 4:
        raise SteadyStateRenderError("placeholder activation digest is forbidden")
    services = _mapping(base.get("services"), "bootstrap services")
    operator = _mapping(services.get("property-catalog-operator"), "bootstrap operator")
    _validate_bootstrap_operator(operator)
    task_queue = _workspace_task_queue(operator)
    control = _control_service(
        operator,
        activation_sha256=activation_sha256,
        task_queue=task_queue,
    )
    registrar = copy.deepcopy(control)
    registrar["restart"] = "no"
    registrar["profiles"] = ["registrar"]
    registrar["entrypoint"] = [
        "python",
        "manage.py",
        "register_temporal_schedules",
    ]
    registrar["command"] = ["--property-catalog-only"]
    registrar["labels"]["futureagi.component"] = "one-shot-schedule-registrar"
    overlay = {
        "services": {
            "property-catalog-control": control,
            "property-catalog-registrar": registrar,
        }
    }
    validate_overlay(overlay, operator=operator, activation_sha256=activation_sha256)
    return overlay


def validate_overlay(
    overlay: Any, *, operator: Mapping[str, Any], activation_sha256: str
) -> None:
    top = _mapping(overlay, "steady-state overlay")
    if set(top) != {"services"}:
        raise SteadyStateRenderError("overlay may contain only services")
    services = _mapping(top["services"], "steady-state services")
    if set(services) != {
        "property-catalog-control",
        "property-catalog-registrar",
    }:
        raise SteadyStateRenderError("steady-state service inventory drifted")
    task_queue = _workspace_task_queue(operator)
    for name, service_value in services.items():
        service = _mapping(service_value, name)
        if any(
            key in service
            for key in (
                "build",
                "ports",
                "expose",
                "privileged",
                "network_mode",
                "depends_on",
                "links",
                "devices",
            )
        ):
            raise SteadyStateRenderError(f"{name} gained routing or privilege")
        environment = _mapping(service.get("environment"), f"{name} environment")
        labels = _mapping(service.get("labels"), f"{name} labels")
        if (
            service.get("image") != operator.get("image")
            or service.get("env_file") != operator.get("env_file")
            or service.get("volumes") != operator.get("volumes")
            or service.get("networks") != ["application-existing"]
            or service.get("read_only") is not True
            or service.get("cap_drop") != ["ALL"]
            or environment.get("PROPERTY_CATALOG_DEV_RECONCILE_ENABLED") != "true"
            or environment.get("PROPERTY_CATALOG_DEV_SCHEDULED_RECONCILE_WALL_MS")
            != SCHEDULED_RECONCILE_WALL_MS
            or environment.get("PROPERTY_CATALOG_DEV_TASK_QUEUE") != task_queue
            or environment.get("PROPERTY_CATALOG_READ_MODE") != "off"
            or environment.get("PROPERTY_CATALOG_DEV_OTLP_TRAFFIC_AUTHORIZED")
            != "false"
            or environment.get("PROPERTY_CATALOG_DEV_BOOTSTRAP_ACTIVATION_SHA256")
            != activation_sha256
            or environment.get("TEMPORAL_TASK_QUEUE") != task_queue
            or labels.get("futureagi.public-routing") != "disabled"
            or labels.get("futureagi.schedule") != "enabled"
        ):
            raise SteadyStateRenderError(f"{name} safety contract drifted")
    control = services["property-catalog-control"]
    registrar = services["property-catalog-registrar"]
    if (
        control.get("restart") != "unless-stopped"
        or control.get("entrypoint") != ["python", "manage.py", "start_temporal_worker"]
        or control.get("command")
        != [
            "--task-queue",
            task_queue,
            "--max-concurrent-activities",
            "1",
            "--max-concurrent-workflow-tasks",
            "1",
            "--graceful-timeout",
            "180",
        ]
    ):
        raise SteadyStateRenderError("dedicated worker command drifted")
    if (
        registrar.get("restart") != "no"
        or registrar.get("profiles") != ["registrar"]
        or registrar.get("entrypoint")
        != ["python", "manage.py", "register_temporal_schedules"]
        or registrar.get("command") != ["--property-catalog-only"]
    ):
        raise SteadyStateRenderError("schedule registrar command drifted")


def render_yaml(base: Mapping[str, Any], *, activation_sha256: str) -> str:
    overlay = render_overlay(base, activation_sha256=activation_sha256)
    rendered = yaml.safe_dump(overlay, sort_keys=False, explicit_start=True)
    round_trip = yaml.load(rendered, Loader=_UniqueSafeLoader)
    operator = _mapping(base["services"]["property-catalog-operator"], "operator")
    validate_overlay(round_trip, operator=operator, activation_sha256=activation_sha256)
    if round_trip != overlay:
        raise SteadyStateRenderError("steady-state YAML did not round-trip exactly")
    return rendered


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
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
    parser.add_argument("--base-compose", required=True)
    parser.add_argument("--bootstrap-activation-sha256", required=True)
    parser.add_argument("--output")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.output and args.check:
        parser.error("--output and --check are mutually exclusive")
    try:
        base = _load_base(Path(args.base_compose))
        rendered = render_yaml(base, activation_sha256=args.bootstrap_activation_sha256)
        if args.output:
            _write_atomic(Path(args.output), rendered)
        elif not args.check:
            sys.stdout.write(rendered)
    except (OSError, SteadyStateRenderError) as exc:
        sys.stderr.write(f"property-catalog steady state rejected: {exc}\n")
        return 2
    if args.check:
        sys.stdout.write(
            "validated one dedicated worker and one opt-in registrar; "
            "source/public routing unchanged\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
