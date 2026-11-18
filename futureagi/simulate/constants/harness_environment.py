"""The values the environments surface reports, shared by its read model and its serializers."""

from __future__ import annotations

from typing import Literal

# The list's four status-pill states; the pipeline's finer stage travels alongside as ``stage``.
STATUS_BUILDING = "building"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

AGENT_TYPE_VOICE = "voice"
AGENT_TYPE_CHAT = "chat"

RUN_KIND_ENVIRONMENT = "environment"
RUN_KIND_SIMULATE = "simulate"

# Whether the Actors tab shows: edited, listed read-only, or (null) not at all.
ActorsTab = Literal["edit", "list"]
ACTORS_TABS: tuple[ActorsTab, ...] = ("edit", "list")


# Why a worker row reads as it does, first rule first (`workers.worker_boundaries`).
WORKER_REASON_CODES = (
    "evidence_unreadable",
    "worker_channel_bypassed",
    "sealed_before_worker_limits",
    "tool_service_not_fronted",
    "in_process_unbound",
    "no_tool_service",
    "in_process_door",
    "shared_binding",
    "held_at_world_cooperative",
    "worker_not_sealed",
)
