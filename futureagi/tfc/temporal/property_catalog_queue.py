"""Dedicated Temporal queue identity for one DEV property-catalog sidecar."""

from __future__ import annotations

import os
import re

DEFAULT_PROPERTY_CATALOG_TASK_QUEUE = "property_catalog_dev_sidecar"
_WORKSPACE_QUEUE_RE = re.compile(r"^property_catalog_dev_sidecar_[0-9a-f]{32}$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def workspace_property_catalog_task_queue(workspace_id: str) -> str:
    if not isinstance(workspace_id, str) or _UUID_RE.fullmatch(workspace_id) is None:
        raise ValueError("property catalog task queue requires one workspace UUID")
    return DEFAULT_PROPERTY_CATALOG_TASK_QUEUE + "_" + workspace_id.replace("-", "")


def configured_property_catalog_task_queue(
    value: str | None = None,
    *,
    reconcile_enabled: bool | None = None,
    workspace_allowlist: tuple[str, ...] | None = None,
) -> str:
    """Return the default queue or one exact workspace-isolated queue.

    Multiple catalog sidecars can coexist on a DEV Temporal namespace.  A
    shared queue would let a worker configured for one workspace consume a
    different workspace's activity, so steady-state sidecars must use the
    renderer-provided UUID-derived queue.
    """

    raw = os.getenv("PROPERTY_CATALOG_DEV_TASK_QUEUE", "") if value is None else value
    if not isinstance(raw, str):
        raise TypeError("property catalog task queue must be a string")
    queue = raw.strip()
    if reconcile_enabled is None:
        raw_enabled = (
            os.getenv("PROPERTY_CATALOG_DEV_RECONCILE_ENABLED", "false").strip().lower()
        )
        if raw_enabled not in {"true", "false"}:
            raise ValueError(
                "PROPERTY_CATALOG_DEV_RECONCILE_ENABLED must be exactly true or false"
            )
        reconcile_enabled = raw_enabled == "true"
    elif type(reconcile_enabled) is not bool:
        raise TypeError("reconcile_enabled must be a bool")
    if workspace_allowlist is None:
        workspace_allowlist = tuple(
            item.strip()
            for item in os.getenv("PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST", "").split(
                ","
            )
            if item.strip()
        )
    if not isinstance(workspace_allowlist, tuple) or any(
        not isinstance(item, str) for item in workspace_allowlist
    ):
        raise TypeError("workspace_allowlist must be a tuple of strings")
    if reconcile_enabled:
        if len(workspace_allowlist) != 1:
            raise ValueError(
                "enabled property catalog task queue requires one workspace"
            )
        expected = workspace_property_catalog_task_queue(workspace_allowlist[0])
        if queue and queue != expected:
            raise ValueError(
                "PROPERTY_CATALOG_DEV_TASK_QUEUE does not match the workspace"
            )
        return expected
    if not queue or queue == DEFAULT_PROPERTY_CATALOG_TASK_QUEUE:
        return DEFAULT_PROPERTY_CATALOG_TASK_QUEUE
    if _WORKSPACE_QUEUE_RE.fullmatch(queue) is None:
        raise ValueError(
            "PROPERTY_CATALOG_DEV_TASK_QUEUE must be the default queue or an "
            "exact workspace-isolated queue"
        )
    return queue


PROPERTY_CATALOG_TASK_QUEUE = configured_property_catalog_task_queue()

__all__ = [
    "DEFAULT_PROPERTY_CATALOG_TASK_QUEUE",
    "PROPERTY_CATALOG_TASK_QUEUE",
    "configured_property_catalog_task_queue",
    "workspace_property_catalog_task_queue",
]
