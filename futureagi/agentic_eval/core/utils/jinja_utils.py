"""Shared helpers for Jinja-based prompt rendering across evaluators."""

from __future__ import annotations

from typing import Any


def nest_dotted_value(container: dict, parts: list[str], value: Any) -> None:
    """Nest ``value`` at ``parts`` path; numeric components become list indices.

    Jinja parses ``{{spans.0.kind}}`` as ``spans[0].kind``, so ``spans`` must
    be a list. e.g. ``["spans", "0", "kind"], "X"`` → ``{"spans": [{"kind": "X"}]}``.
    """
    target: Any = container

    # Walk every component except the leaf. Each iteration descends one
    # level, creating the right type of child container on the way down.
    for i, part in enumerate(parts[:-1]):
        # The CHILD container's type is decided by the NEXT component
        # (not the current one): numeric next → list, word next → dict.
        # That's because the next component is what will index into the
        # child we're about to create.
        next_is_numeric = parts[i + 1].isdigit()
        child_factory = list if next_is_numeric else dict

        if isinstance(target, list):
            # In a list: `part` is the integer index of our slot.
            idx = int(part)
            # Pad with None so out-of-order assignments work
            # (e.g. spans.2 set before spans.0).
            while len(target) <= idx:
                target.append(None)
            # Create or replace the slot so it's the right kind.
            if not isinstance(target[idx], child_factory):
                target[idx] = child_factory()
            target = target[idx]
        else:
            # In a dict: `part` is the string key.
            existing = target.get(part)
            if not isinstance(existing, child_factory):
                existing = child_factory()
                target[part] = existing
            target = existing

    # Drop `value` at the final position. Same dict-vs-list split as
    # above — depends on what `target` ended up as after the walk.
    leaf = parts[-1]
    if isinstance(target, list):
        idx = int(leaf)
        while len(target) <= idx:
            target.append(None)
        target[idx] = value
    else:
        target[leaf] = value
