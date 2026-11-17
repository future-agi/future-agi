"""Scoped module stand-ins that do not roll back unrelated lazy imports."""

import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from types import ModuleType

from pytest import MonkeyPatch


@contextmanager
def stand_in_modules(modules: Mapping[str, ModuleType | None]) -> Iterator[None]:
    """Restore only the entries this stand-in replaces, even on an exception.

    ``patch.dict(sys.modules, ...)`` snapshots the entire import registry. Its
    exit deletes modules first imported inside the block, even though Django
    keeps their URL callbacks and other modules keep their imported objects.
    A later string-target mock then patches a different module instance.

    Per-entry undo preserves unrelated lazy imports and their object identity.
    A value of None still models an unavailable module; nested contexts restore
    the immediately enclosing value, including an originally absent entry.
    """
    with MonkeyPatch.context() as patcher:
        for name, module in modules.items():
            patcher.setitem(sys.modules, name, module)
        yield
