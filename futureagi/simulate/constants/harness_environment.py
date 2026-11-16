"""The values the environments surface reports, shared by its read model and its serializers."""

from __future__ import annotations

from typing import Literal

# The four states the list's status pill knows. The pipeline reports far finer
# stages; the raw one travels alongside as ``stage`` so a caller that wants
# progress can still derive it.
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
