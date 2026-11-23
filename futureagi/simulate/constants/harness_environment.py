"""The values the environments surface reports, shared by its read model and its serializers."""

from __future__ import annotations

from typing import Literal

# The list's status-pill states; the pipeline's finer stage travels alongside as ``stage``.
# An adaptation in review is awaiting approval until the person approves its design.
STATUS_AWAITING_APPROVAL = "awaiting_approval"
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
