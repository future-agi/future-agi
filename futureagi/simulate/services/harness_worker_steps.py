"""A call's worker events arranged for the Participants tab: who handed what to whom, when, and in parallel."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from simulate.types import WorkerEvent, WorkerStep, WorkerSteps

_HAND_OFF_TYPES = frozenset({"delegated", "transferred"})


def _seconds(at: Any) -> float | None:
    """Epoch seconds of a stored timestamp, read as UTC when it carries no zone."""
    if not isinstance(at, str) or not at:
        return None
    try:
        moment = datetime.fromisoformat(at)
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).timestamp()


def _iso(seconds: float | None) -> str | None:
    return None if seconds is None else datetime.fromtimestamp(seconds, UTC).isoformat()


class _Block:
    def __init__(
        self, key: int, worker_id: str | None, depth: int, start: float | None
    ) -> None:
        self.key = key
        self.worker_id = worker_id
        self.depth = depth
        self.start = start
        self.end = start
        self.handoff: WorkerEvent | None = None
        self.returned: WorkerEvent | None = None
        self.parallel = False
        self.items: list[_Block | WorkerEvent] = []

    def reach(self, at: float | None) -> None:
        if at is not None:
            self.end = at if self.end is None else max(self.end, at)


def _tree(events: Iterable[WorkerEvent]) -> tuple[_Block, list[_Block]]:
    root = _Block(-1, None, 0, None)
    open_: dict[str | None, list[_Block]] = {}
    latest: dict[str | None, _Block] = {}
    blocks: list[_Block] = []

    def open_for(event: WorkerEvent) -> _Block | None:
        waiting = open_.get(event.get("worker_id"), [])
        wanted = event.get("delegation_id")
        if not wanted:
            return waiting[-1] if waiting else None
        return next(
            (
                one
                for one in waiting
                if (one.handoff or {}).get("delegation_id") == wanted
            ),
            None,
        )

    def holder(worker: str | None, at: float | None) -> _Block:
        if not worker:
            return root
        containing = next(
            (
                one
                for one in reversed(open_.get(worker, []))
                if at is None or one.start is None or one.start <= at
            ),
            None,
        )
        return containing or latest.get(worker) or root

    for event in events:
        at = _seconds(event.get("at"))
        worker = event.get("worker_id")
        if event["type"] in _HAND_OFF_TYPES:
            parent = holder(event.get("by"), at)
            block = _Block(event.get("seq", len(blocks)), worker, parent.depth + 1, at)
            block.handoff = event
            parent.items.append(block)
            blocks.append(block)
            open_.setdefault(worker, []).append(block)
            latest[worker] = block
        elif event["type"] == "returned":
            block = open_for(event)
            if block is not None:
                open_[worker].remove(block)
                block.returned = event
                block.reach(at)
            else:
                orphan = _Block(event.get("seq", len(blocks)), worker, 1, at)
                orphan.returned = event
                root.items.append(orphan)
                blocks.append(orphan)
        else:
            # A call with no worker is the coordinator's own, even with control passed back to it.
            block = open_for(event) if worker else None
            (block or root).items.append(event)
            if block is not None:
                block.reach(at)
    return root, blocks


def _close(node: _Block) -> None:
    """A block still open ends with its last step or child; one with no start starts at its first."""
    for item in node.items:
        if isinstance(item, _Block):
            _close(item)
            node.reach(item.end)
    if node.start is None:
        for item in node.items:
            first = item.start if isinstance(item, _Block) else _seconds(item.get("at"))
            if first is not None:
                node.start = first
                break


def _mark_parallel(node: _Block) -> bool:
    """Mark sibling hand-offs whose spans overlap as parallel; return whether some start is unknown."""
    unknown = False
    siblings = [item for item in node.items if isinstance(item, _Block)]
    for index, one in enumerate(siblings):
        for other in siblings[index + 1 :]:
            if one.start is None or other.start is None:
                unknown = True
                continue
            if other.start < (
                one.end if one.end is not None else one.start
            ) and one.start < (other.end if other.end is not None else other.start):
                one.parallel = other.parallel = True
        unknown = _mark_parallel(one) or unknown
    return unknown


def _flatten(node: _Block, parent: int | None, out: list[WorkerStep]) -> None:
    for item in node.items:
        if isinstance(item, _Block):
            out.append(
                {
                    "kind": "handoff",
                    "key": item.key,
                    "parent": parent,
                    "depth": item.depth,
                    "worker_id": item.worker_id,
                    "transfer": (item.handoff or {}).get("type") == "transferred",
                    "handoff": item.handoff,
                    "returned": item.returned,
                    "started_at": _iso(item.start),
                    "ended_at": _iso(item.end),
                    "parallel": item.parallel,
                }
            )
            _flatten(item, item.key, out)
        else:
            out.append(
                {
                    "kind": "tool_call",
                    "key": item.get("seq", len(out)),
                    "parent": parent,
                    "depth": node.depth,
                    "worker_id": item.get("worker_id"),
                    "call": item,
                }
            )


def worker_steps(events: Iterable[WorkerEvent]) -> WorkerSteps:
    """The events as steps in reading order, each hand-off with its parent, depth, span and parallel flag."""
    root, blocks = _tree(events)
    _close(root)
    overlap_unknown = _mark_parallel(root)
    steps: list[WorkerStep] = []
    _flatten(root, None, steps)
    depth_of: dict[str, int] = {}
    for block in blocks:
        if block.worker_id is not None:
            depth_of.setdefault(block.worker_id, block.depth)
    return {
        "steps": steps,
        "depth": max((block.depth for block in blocks), default=0),
        "parallel": sum(1 for block in blocks if block.parallel),
        "overlap_unknown": overlap_unknown,
        "worker_depths": depth_of,
    }
