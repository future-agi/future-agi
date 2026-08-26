#!/usr/bin/env python3
"""Hard-retired planner for the deleted span-only catalog.

The file remains as a tombstone for saved operator commands. It never builds
or executes a rollout plan and has no database, network, or subprocess imports.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import NoReturn

REPLACEMENT_COMMAND = "python manage.py ch25_property_catalog_dev_rollout"
RETIRED_MESSAGE = (
    "scripts/fi_dev_rollout is retired and performs zero I/O; use "
    f"{REPLACEMENT_COMMAND} for the unified property catalog"
)


class RolloutSafetyError(RuntimeError):
    """The retired planner was invoked instead of the unified command."""


def build_plan(*args: object, **kwargs: object) -> NoReturn:
    """Reject stale imports without reconstructing the deleted schema plan."""

    del args, kwargs
    raise RolloutSafetyError(RETIRED_MESSAGE)


def main(argv: Sequence[str] | None = None) -> int:
    """Reject every CLI invocation before performing any external action."""

    del argv
    print(RETIRED_MESSAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
