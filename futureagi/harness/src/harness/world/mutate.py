"""Breaking a world on purpose, to find out whether its checks would notice.

The checks that verify an environment are written by whoever built it. That is the right way
round: what makes a world usable is a judgement about this agent, and no fixed set of probes
written in advance can make it for every agent. But it leaves nothing independent confirming the
checks work, and a check that cannot fail reports a healthy world forever.

So the checks are put to a test they cannot talk their way out of. The world is damaged in ways
that are obviously wrong, and the checks have to go red. One that stays green through every
damaged world is not verifying anything, whatever it claims to inspect.

The damage is deliberately generic, because a mutation that needed to understand the agent would
need the same judgement the checks needed, and nothing would be gained:

- **emptied**: every collection loses its contents. A world with no data at all.
- **silenced**: every tool answers with nothing. Calls succeed and change nothing.

Any check worth keeping fails against at least one of those. Most fail against both.
"""

from __future__ import annotations

from typing import Any, Callable

from .runtime import GeneratedWorld

EMPTIED = "emptied"
# Not a kind of damage: how the report says the damage itself did not happen.
UNDAMAGED = "could not be damaged"
SILENCED = "silenced"

# What a silenced tool answers. Deliberately a plain empty string: it is the shape a handler
# returns when it has done nothing, which is exactly the failure being simulated.
_MUTE = "def handle(args, db):\n    return ''\n"


def _empty(world: GeneratedWorld) -> None:
    """Take the contents out of the world, leaving its shape intact.

    The store empties itself where it can, because only it knows what its engine needs: a
    relational one has to suspend foreign keys, or deleting a referenced table fails and most of
    the data stays. Dropping collection by collection through the world's own vocabulary is the
    fallback, for a store that has no opinion.
    """
    emptied = getattr(world.store, "clear", None)
    if callable(emptied):
        emptied()
    else:
        for name in list(world.state()):
            try:
                world.drop(name)
            except Exception:
                # One collection that will not empty is not a reason to abandon the mutation. What
                # survives is reported by `left`, so the gate can tell a check that failed to
                # notice from a mutation that never happened.
                continue
    # The agent's own state, where its code keeps what its tools act on. A world can have both.
    held = world.state_object
    if isinstance(held, dict):
        for name, group in held.items():
            if isinstance(group, dict):
                group.clear()
            elif isinstance(group, list):
                group.clear()
            else:
                held[name] = None


def left(world: GeneratedWorld) -> dict[str, int]:
    """What is still in the world after it was supposed to be empty.

    The gate accuses a check of verifying nothing when it stays green through damage. That
    accusation is only fair if the damage actually happened: a store that quietly refused to
    empty leaves every check reading real data and looking vacuous, and the person then rewrites
    a check that was right all along.
    """
    return {name: len(rows) for name, rows in world.state().items() if rows}


def _silence(world: GeneratedWorld) -> None:
    """Leave every tool answering with nothing, so no call has any effect."""
    for name in list(world.handlers):
        world.handlers[name] = _MUTE


def damage() -> dict[str, Callable[[GeneratedWorld], None]]:
    """Every way a world is broken on purpose, by name."""
    return {EMPTIED: _empty, SILENCED: _silence}


def unnoticed(
    world_root: Any,
    checks: list[tuple[str, str]],
    *,
    run: Callable[[str, GeneratedWorld], Any],
    restore: Callable[[Any], GeneratedWorld],
) -> dict[str, list[str]]:
    """Which checks fail to notice each kind of damage.

    Every mutation runs against its own restored copy, so one cannot inherit another's damage and
    a check is never blamed for a world some earlier mutation had already emptied.

    Returns damage name to the checks that stayed green through it. A check appearing under every
    kind of damage is one that cannot fail.
    """
    survived: dict[str, list[str]] = {}
    for name, apply in damage().items():
        broken = restore(world_root)
        try:
            apply(broken)
            if name == EMPTIED:
                remaining = left(broken)
                if remaining:
                    # The mutation did not land, so nothing can be concluded from it. Saying so is
                    # the point: reporting these checks as blind would have somebody rewrite a
                    # check that was reading the world correctly the whole time.
                    survived[name] = []
                    survived.setdefault(UNDAMAGED, []).append(
                        f"the world would not empty: {remaining}"
                    )
                    continue
            still_green = []
            for check_name, source in checks:
                outcome = run(source, broken)
                # A check that raises has not verified anything either, but that is a broken
                # check rather than a blind one, and it is reported separately by the caller.
                if getattr(outcome, "held", False):
                    still_green.append(check_name)
            survived[name] = still_green
        finally:
            broken.close()
    return survived


def blind(survived: dict[str, list[str]]) -> list[str]:
    """Checks that stayed green through every kind of damage."""
    if not survived:
        return []
    kinds = [names for kind, names in survived.items() if kind != UNDAMAGED]
    if not kinds:
        return []
    return sorted(set(kinds[0]).intersection(*kinds[1:])) if len(kinds) > 1 else sorted(kinds[0])
