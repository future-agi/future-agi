"""A call's worker events arranged for the Participants tab: who handed what to whom, when, and in parallel."""

from __future__ import annotations

import json
import re
from bisect import bisect_right
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

from simulate.services.harness_workers import check_again_at, read_pending
from simulate.types import (
    CallTurn,
    CallWorkersRecord,
    HandOffCounts,
    HandOffEnd,
    ShownText,
    TurnSummary,
    WorkerEvent,
    WorkerRelationship,
    WorkersState,
    WorkerStep,
    WorkerSteps,
    WorkerTimeline,
    WorkerTurn,
)

_HAND_OFF_TYPES = frozenset({"delegated", "transferred"})
# The ends that count as a hand-off that did not finish, in the headline count and on its row alike.
_NOT_FINISHED: frozenset[HandOffEnd] = frozenset({"failed", "no_return"})
# A printed dict opens on a key; a printed list on a quoted, nested or literal item. `…` ends a cut-off one.
_PRINTED_DICT = re.compile(r"^\{\s*(?:(['\"]).*?\1|\w+)\s*:")
_PRINTED_LIST = re.compile(
    r"^\[\s*(?:['\"{\[]|(?:-?\d[\d.]*|True|False|None)\s*(?:,|\]$)|\]$)"
)


def _seconds(at: object) -> float | None:
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


def _aware(at: str | None) -> str | None:
    # Records kept before timestamps carried their zone are served with it.
    moment = _seconds(at)
    return at if moment is None else _iso(moment)


def _shown(text: str | None) -> ShownText | None:
    """A text as data (JSON indented, or a printed dict or list as it came) or as prose."""
    if not text:
        return None
    body = text.strip()
    if body[:1] not in ("[", "{"):
        return {"format": "text", "text": text}
    try:
        return {
            "format": "code",
            "text": json.dumps(json.loads(body), indent=2, ensure_ascii=False),
        }
    except ValueError:
        printed = body[-1] in "}]…" and bool(
            _PRINTED_DICT.match(body) or _PRINTED_LIST.match(body)
        )
    return (
        {"format": "code", "text": body}
        if printed
        else {"format": "text", "text": text}
    )


def _served(event: WorkerEvent) -> WorkerEvent:
    """An event as the client reads it: its time with its zone, and its task and answer marked as data or prose."""
    out: WorkerEvent = {**event, "at": _aware(event.get("at"))}
    if event["type"] in _HAND_OFF_TYPES:
        out["task_shown"] = _shown(event.get("task"))
    if event["type"] == "returned":
        out["result_shown"] = _shown(event.get("result"))
    return out


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


def _tree(events: Iterable[WorkerEvent]) -> tuple[_Block, list[_Block], set[int]]:
    """The events as a tree, and the keys of events whose place was not recorded (kept at the top)."""
    root = _Block(-1, None, 0, None)
    unknown: set[int] = set()
    open_: dict[str | None, list[_Block]] = {}
    blocks: list[_Block] = []

    by_id: dict[str, _Block] = {}
    # A step may name its hand-off before it opens or after it returns (another clock), so it waits to join it.
    events = list(events)
    named = {
        str(event["delegation_id"])
        for event in events
        if event["type"] in _HAND_OFF_TYPES and event.get("delegation_id")
    }
    waiting: dict[str, list[WorkerEvent]] = {}
    waiting_blocks: dict[str, list[_Block]] = {}

    def open_for(event: WorkerEvent) -> _Block | None:
        """The open hand-off an event's recorded id names. Without one the record does not say, so the event stays
        at the top: never given to an open hand-off by its worker or its timing."""
        wanted = event.get("delegation_id")
        if not wanted:
            return None
        found = by_id.get(str(wanted))
        return found if found is not None and found.returned is None else None

    def holder(event: WorkerEvent) -> _Block | None:
        """The hand-off a new hand-off was made inside, as recorded: its parent's id, the top when the record says
        so or the coordinator made it, else none (unknown). Never guessed from the maker's open hand-offs.
        """
        parent = event.get("parent_delegation_id")
        if parent:
            return by_id.get(str(parent))
        if event.get("relationship") == "observed" or not event.get("by"):
            return root
        return None

    for event in events:
        at = _seconds(event.get("at"))
        worker = event.get("worker_id")
        if event["type"] in _HAND_OFF_TYPES:
            recorded_parent = str(event.get("parent_delegation_id") or "")
            # A hand-off recorded inside one that has not opened yet (no start, or another clock) waits for it.
            later = bool(
                recorded_parent
                and recorded_parent in named
                and recorded_parent not in by_id
            )
            parent = None if later else holder(event)
            if parent is None and not later:
                parent = root
                unknown.add(event.get("seq", len(blocks)))
            block = _Block(
                event.get("seq", len(blocks)), worker, (parent or root).depth + 1, at
            )
            block.handoff = event
            if later:
                waiting_blocks.setdefault(recorded_parent, []).append(block)
            else:
                (parent or root).items.append(block)
            blocks.append(block)
            open_.setdefault(worker, []).append(block)
            if event.get("delegation_id"):
                by_id[str(event["delegation_id"])] = block
                block.items.extend(waiting.pop(str(event["delegation_id"]), []))
                block.items.extend(waiting_blocks.pop(str(event["delegation_id"]), []))
        elif event["type"] == "returned":
            closed = open_for(event)
            if closed is not None:
                open_[closed.worker_id].remove(closed)
                closed.returned = event
                closed.reach(at)
            else:
                unknown.add(event.get("seq", len(blocks)))
                orphan = _Block(event.get("seq", len(blocks)), worker, 1, at)
                orphan.returned = event
                root.items.append(orphan)
                blocks.append(orphan)
        else:
            recorded = str(event.get("delegation_id") or "")
            if worker and recorded:
                if recorded not in named:
                    # It names a hand-off this record does not hold: where it ran is unknown.
                    unknown.add(event.get("seq", -1))
                    root.items.append(event)
                elif recorded in by_id:
                    by_id[recorded].items.append(event)
                    if by_id[recorded].returned is None:
                        by_id[recorded].reach(at)
                else:
                    waiting.setdefault(recorded, []).append(event)
                continue
            # A call with no worker is the coordinator's own, even with control passed back to it.
            running = open_for(event) if worker else None
            recorded_top = event.get("relationship") == "observed" and not event.get(
                "delegation_id"
            )
            if worker and running is None and open_.get(worker) and not recorded_top:
                unknown.add(event.get("seq", -1))
            (running or root).items.append(event)
            if running is not None:
                running.reach(at)
    _set_depths(root)
    return root, blocks, unknown


def _set_depths(node: _Block) -> None:
    """Each hand-off one deeper than the one it sits in, once every late-placed hand-off has joined its parent."""
    for item in node.items:
        if isinstance(item, _Block):
            item.depth = node.depth + 1
            _set_depths(item)


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


def _relationship(
    event: WorkerEvent | None, key: int, unknown: set[int]
) -> WorkerRelationship:
    """`unknown` when the record does not say where this step belongs; otherwise `observed`."""
    if key in unknown or (event is not None and event.get("relationship") == "unknown"):
        return "unknown"
    return "observed"


def _end(block: _Block, cut_off: bool) -> HandOffEnd:
    """A return decides it, even after a tool failed; a control pass never returns, so only a failed tool marks it."""
    if block.returned is not None:
        return "failed" if block.returned.get("status") == "error" else "finished"
    if block.handoff is not None and block.handoff.get("type") == "transferred":
        failed = any(
            not isinstance(item, _Block) and item.get("status") == "error"
            for item in block.items
        )
        return "failed" if failed else "passed"
    # A record cut at its limit, or read only in part, can't say a missing return never came.
    return "cut_off" if cut_off else "no_return"


def _flatten(
    node: _Block,
    parent: int | None,
    out: list[WorkerStep],
    unknown: set[int],
    cut_off: bool,
) -> None:
    for item in node.items:
        if isinstance(item, _Block):
            out.append(
                {
                    "kind": "handoff",
                    "key": item.key,
                    "parent": parent,
                    "depth": item.depth,
                    "worker_id": item.worker_id,
                    "transfer": item.handoff is not None
                    and item.handoff.get("type") == "transferred",
                    "handoff": _served(item.handoff)
                    if item.handoff is not None
                    else None,
                    "returned": _served(item.returned)
                    if item.returned is not None
                    else None,
                    "started_at": _iso(item.start),
                    "ended_at": _iso(item.end),
                    "parallel": item.parallel,
                    "end": _end(item, cut_off),
                    "did_not_finish": _end(item, cut_off) in _NOT_FINISHED,
                    "first_of_worker": False,
                    "children": [],
                    "relationship": _relationship(
                        item.handoff or item.returned, item.key, unknown
                    ),
                }
            )
            _flatten(item, item.key, out, unknown, cut_off)
        else:
            out.append(
                {
                    "kind": "tool_call",
                    "key": item.get("seq", len(out)),
                    "parent": parent,
                    "depth": node.depth,
                    "worker_id": item.get("worker_id"),
                    "call": _served(item),
                    "relationship": _relationship(item, item.get("seq", -1), unknown),
                }
            )


def _counts(steps: list[WorkerStep]) -> HandOffCounts:
    ends = [step.get("end") for step in steps if step["kind"] == "handoff"]
    return {
        "hand_offs": len(ends),
        "workers": len(
            {step["worker_id"] for step in steps if step["kind"] == "handoff"}
        ),
        "finished": ends.count("finished"),
        "did_not_finish": sum(1 for end in ends if end in _NOT_FINISHED),
        "passed": ends.count("passed"),
        "cut_off": ends.count("cut_off"),
    }


def _step_time(step: WorkerStep) -> float | None:
    if step["kind"] == "handoff":
        return _seconds(step.get("started_at"))
    call = step.get("call")
    return _seconds(call.get("at")) if call is not None else None


def _by_turn(
    steps: list[WorkerStep], turns: Iterable[CallTurn], *, cut: bool = False
) -> list[WorkerTurn]:
    """Each step's turn (n starts at the caller's nth message, 0 before they spoke; a step's children keep its turn;
    null with no time or past the last message kept when `cut`), and each turn's request, reply and top-level steps.
    """
    said = [one for one in turns if isinstance(one, Mapping)]
    asked = [
        (at, one)
        for one in said
        if one.get("role") == "user" and (at := _seconds(one.get("at"))) is not None
    ]
    replies = [
        (at, one)
        for one in said
        if one.get("role") == "agent" and (at := _seconds(one.get("at"))) is not None
    ]
    asked_at = [at for at, _ in asked]
    kept_until = max((at for at, _ in asked + replies), default=None) if cut else None
    turn_of: dict[int, int | None] = {}
    latest: dict[int | None, float] = {}
    order: dict[int | None, int] = {}
    for step in steps:
        parent = step["parent"]
        if parent is not None and parent in turn_of:
            turn = turn_of[parent]
        else:
            at = _step_time(step)
            beyond = kept_until is not None and at is not None and at > kept_until
            turn = None if at is None or beyond else bisect_right(asked_at, at)
        step["turn"] = turn
        # Reading order within the turn, stable for a given record: steps are already in that order.
        step["order"] = order[turn] = order.get(turn, 0) + 1
        if step["kind"] == "handoff":
            turn_of[step["key"]] = turn
        moment = _seconds(step.get("ended_at")) if step["kind"] == "handoff" else None
        moment = moment if moment is not None else _step_time(step)
        if moment is not None:
            latest[turn] = max(latest.get(turn, moment), moment)

    def summary(one: CallTurn) -> TurnSummary:
        text = one.get("text")
        return {"text": text, "at": _aware(one.get("at")), "shown": _shown(text)}

    top = [step for step in steps if step["parent"] is None]

    rounds: list[WorkerTurn] = []
    known = sorted({step["turn"] for step in steps if step["turn"] is not None})
    for turn in known:
        until = asked_at[turn] if turn < len(asked_at) else None
        after = latest.get(turn, asked_at[turn - 1] if turn else float("-inf"))
        reply = next(
            (
                one
                for at, one in replies
                if at >= after and (until is None or at < until)
            ),
            None,
        )
        rounds.append(
            {
                "turn": turn,
                "request": summary(asked[turn - 1][1]) if turn else None,
                "response": summary(reply) if reply is not None else None,
                "children": [],
                "calls_outside": 0,
                "handoffs": [],
                "expects_messages": True,
            }
        )
    if any(step["turn"] is None for step in steps):
        rounds.append(
            {
                "turn": None,
                "request": None,
                "response": None,
                "children": [],
                "calls_outside": 0,
                "handoffs": [],
                "expects_messages": False,
            }
        )
    # Work before the caller spoke has no request of its own, unless that is the whole call.
    if len(rounds) > 1 and rounds[0]["turn"] == 0:
        rounds[0]["expects_messages"] = False
    of_turn = {one["turn"]: one for one in rounds}
    for step in top:
        held = of_turn[step["turn"]]
        held["children"].append(step["key"])
        # A tool call outside every hand-off has no table row of its own.
        held["calls_outside"] += step["kind"] == "tool_call"
    for step in steps:
        if step["kind"] == "handoff":
            of_turn[step["turn"]]["handoffs"].append(step["key"])
    return rounds


def worker_steps(
    events: Iterable[WorkerEvent],
    turns: Iterable[CallTurn] = (),
    *,
    turns_cut: bool = False,
    cut_off: bool = False,
) -> WorkerSteps:
    """The events as steps in reading order, each hand-off with its parent, children, depth, span, end and parallel
    flag, and each turn with its request, reply and top-level steps; `cut_off` when the record is not whole."""
    root, blocks, unknown = _tree(events)
    _close(root)
    overlap_unknown = _mark_parallel(root)
    steps: list[WorkerStep] = []
    _flatten(root, None, steps, unknown, cut_off)
    held = {step["key"]: step for step in steps if step["kind"] == "handoff"}
    for step in steps:
        if step["parent"] is not None and step["parent"] in held:
            held[step["parent"]]["children"].append(step["key"])
    seen: set[str | None] = set()
    for step in held.values():
        step["first_of_worker"] = step["worker_id"] not in seen
        seen.add(step["worker_id"])
    rounds = _by_turn(steps, turns, cut=turns_cut)
    depth_of: dict[str, int] = {}
    for block in blocks:
        if block.worker_id is not None:
            depth_of.setdefault(block.worker_id, block.depth)
    depth = max((block.depth for block in blocks), default=0)
    parallel = sum(1 for block in blocks if block.parallel)
    return {
        "steps": steps,
        "turns": rounds,
        "depth": depth,
        "parallel": parallel,
        "overlap_unknown": overlap_unknown,
        "worker_depths": depth_of,
        "counts": _counts(steps),
        "cut_off": cut_off,
        # The supported boundary is one level of sequential hand-offs from the coordinator.
        "beyond_boundary": depth > 1 or parallel > 0,
    }


def served_record(
    timeline: WorkerTimeline, now: datetime, *, state: WorkersState = "recorded"
) -> CallWorkersRecord:
    """A kept record as the client reads it, old records included: its steps, times with their zone, and whether a
    later read is still due."""
    cut_off = bool(timeline.get("events_omitted")) or timeline.get("collection") in (
        "partial",
        "unavailable",
    )
    steps = worker_steps(
        timeline.get("events", []),
        timeline.get("messages", []),
        turns_cut=bool(timeline.get("messages_omitted")),
        cut_off=cut_off,
    )
    due = timeline.get("next_read_at")
    pending = read_pending(due, now)
    served: CallWorkersRecord = {
        **timeline,
        **steps,
        "events": [_served(one) for one in timeline.get("events", [])],
        "started_at": _aware(timeline.get("started_at")),
        "pending": pending,
        "state": state,
    }
    if due and pending:
        # The client reads again once, at this time; past it, the read has been and the record is final.
        served["next_read_at"] = check_again_at(due, now)
    return served
