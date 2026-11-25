"""Bounded single-sandbox sizing from operator-certified resource profiles."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


def configured_capacity(payload: Mapping[str, Any]) -> SandboxCapacity:
    from django.conf import settings

    from simulate.services.hosted_sandbox import sandbox_runtime_policy

    runtime = dict(payload.get("runtime") or {})
    policy = sandbox_runtime_policy()
    if policy.fixed_resources:
        cpu_units, memory_mb, disk_gb = policy.fixed_resources
        runtime.setdefault("cpu_units", cpu_units)
        runtime.setdefault("memory_mb", memory_mb)
        runtime.setdefault("disk_gb", disk_gb)
    enabled = getattr(settings, "HARNESS_PARALLELISM_ENABLED", False)
    unpinned = policy.permits_unpinned_parallelism
    if not enabled:
        runtime["parallelism"] = 1
    profiles = getattr(settings, "HARNESS_RESOURCE_PROFILES", [])
    if not isinstance(profiles, list) or any(
        not isinstance(p, Mapping) for p in profiles
    ):
        raise ValueError("resource profiles must be a JSON array of objects")
    allowed_digests = set(
        getattr(settings, "HARNESS_PARALLEL_SNAPSHOT_DIGESTS", ()) or ()
    )
    if profiles and not unpinned:
        # An unqualified image can only offer one slot, even if its hardware fits more.
        profiles = [
            {**profile, "max_parallelism": 1}
            if profile.get("snapshot_digest") not in allowed_digests
            else profile
            for profile in profiles
        ]
    agent = payload.get("agent") or {}
    connector = str(agent.get("connector") or "auto").strip().lower()
    if connector == "auto":
        config = agent.get("config") or {}
        config_keys = (
            {str(key).lower() for key in config}
            if isinstance(config, Mapping)
            else set()
        )
        aliases = {str(key).upper() for key in (agent.get("secret_refs") or {})}
        if "LIVEKIT_URL" in aliases or "livekit_url" in config_keys:
            connector = "livekit"
        elif "VAPI_API_KEY" in aliases:
            connector = "vapi"
        elif "RETELL_API_KEY" in aliases:
            connector = "retell"
    capacity = select_capacity(
        runtime,
        scenario_count=int(payload.get("scenario_count") or 1),
        connector=connector,
        profiles=profiles,
        ceiling=getattr(settings, "HARNESS_MAX_WORLD_SLOTS", 8),
        dockerfile=unpinned,
    )
    if not policy.supports_runtime_selection and (
        (capacity.snapshot_name and capacity.snapshot_name != policy.name)
        or (capacity.snapshot_digest and capacity.snapshot_digest != policy.digest)
    ):
        raise ValueError("selected sandbox profile does not match provider runtime")
    if policy.fixed_resources and any(
        required > available
        for required, available in zip(
            (capacity.cpu_units, capacity.memory_mb, capacity.disk_gb),
            policy.fixed_resources,
            strict=True,
        )
    ):
        raise ValueError("selected sandbox capacity exceeds provider runtime resources")
    return capacity


@dataclass(frozen=True)
class SandboxCapacity:
    name: str
    cpu_units: int
    memory_mb: int
    disk_gb: int
    parallelism: int
    snapshot_name: str | None = None
    snapshot_digest: str | None = None


def _resource_slots(cpu_units: int, memory_mb: int) -> int:
    """Match guest admission's control-process reserve and per-world budget."""
    return min(
        math.floor((cpu_units - 0.5) / 0.8),
        math.floor((memory_mb / 1024 - 1) / 0.95),
    )


def select_capacity(
    runtime: Mapping[str, Any],
    *,
    scenario_count: int,
    connector: str,
    profiles: Sequence[Mapping[str, Any]],
    ceiling: int = 8,
    dockerfile: bool = False,
) -> SandboxCapacity:
    """Profiles bound resource spending; no catalog means fixed-size execution."""
    requested = int(runtime.get("parallelism", 1))
    if requested < 1 or scenario_count < 1 or not 1 <= ceiling <= 8:
        raise ValueError("invalid single-sandbox capacity inputs")
    target = min(requested, scenario_count, ceiling)
    if not profiles:
        # Preserve the existing fixed-size lane until measured profiles are supplied.
        cpu = int(runtime.get("cpu_units", 4))
        memory = int(runtime.get("memory_mb", 8192))
        width = min(target, _resource_slots(cpu, memory))
        if width < 1:
            raise ValueError("insufficient sandbox resources")
        disk = int(runtime.get("disk_gb", 10))
        return SandboxCapacity("fixed", cpu, memory, disk, width)

    candidates: list[SandboxCapacity] = []
    for profile in profiles:
        try:
            connectors = profile.get("connectors", [])
            if (
                isinstance(connectors, (str, bytes))
                or not isinstance(connectors, Sequence)
                or any(type(item) is not str or not item for item in connectors)
            ):
                raise ValueError
            if connector not in connectors:
                continue
            cpu, memory, disk, width = (
                profile[key]
                for key in ("cpu_units", "memory_mb", "disk_gb", "max_parallelism")
            )
            if any(type(v) is not int or v < 1 for v in (cpu, memory, disk, width)):
                raise ValueError
            if memory < 1024 or width > min(cpu, 8):
                raise ValueError
            resource_slots = _resource_slots(cpu, memory)
            if resource_slots < 1:
                raise ValueError
            name = profile["name"]
            if not isinstance(name, str) or not name:
                raise ValueError
            snapshot = profile.get("snapshot_name")
            digest = profile.get("snapshot_digest")
            if not dockerfile and not (snapshot and digest):
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid certified sandbox resource profile") from exc
        candidates.append(
            SandboxCapacity(
                name,
                cpu,
                memory,
                disk,
                min(target, width, resource_slots),
                snapshot,
                digest,
            )
        )
    if not candidates:
        raise ValueError("no certified sandbox profile for this connector")
    # Meet as much demand as possible, then select the smallest certified profile.
    return min(
        candidates,
        key=lambda p: (-p.parallelism, p.cpu_units, p.memory_mb, p.disk_gb, p.name),
    )
