"""Regression coverage for mutation_lock.py's Windows-import bug.

`fcntl` is POSIX-only. A module-scope `import fcntl` in mutation_lock.py
broke every Windows Django test run, because this module is pulled in
transitively by migration 0093_register_eval_task_search_attributes on
every test-database setup -- regardless of which test was actually
targeted.

`test_module_has_no_top_level_fcntl_import` pins the fix statically: it
parses the module's AST and asserts no `import fcntl` sits at module
scope, so a future edit can't silently reintroduce the crash. The
POSIX-only tests below prove the deferred import didn't break the actual
locking behavior it exists to provide.
"""

from __future__ import annotations

import ast
import os
import threading
import time
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "services"
    / "clickhouse"
    / "v2"
    / "property_catalog"
    / "mutation_lock.py"
)


def _top_level_import_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_module_has_no_top_level_fcntl_import() -> None:
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    assert "fcntl" not in _top_level_import_names(tree), (
        "fcntl is POSIX-only; importing it at module scope breaks every "
        "Windows test run (this module loads on every Django migration "
        "via tfc.temporal). Import it inside the function that uses it."
    )


@pytest.mark.skipif(
    os.name != "posix",
    reason="FileCatalogMutationSerializer uses fcntl.flock, POSIX-only",
)
def test_file_serializer_returns_operation_result(tmp_path) -> None:
    from tracer.services.clickhouse.v2.property_catalog.mutation_lock import (
        FileCatalogMutationSerializer,
    )

    serializer = FileCatalogMutationSerializer(str(tmp_path))
    assert serializer.serialize("some-key", lambda: 42) == 42


@pytest.mark.skipif(
    os.name != "posix",
    reason="FileCatalogMutationSerializer uses fcntl.flock, POSIX-only",
)
def test_file_serializer_serializes_concurrent_callers(tmp_path) -> None:
    """Two threads racing on the same key must not run their operations
    concurrently -- proves flock() still gives real mutual exclusion after
    deferring the import, not just that the happy path returns a value."""
    from tracer.services.clickhouse.v2.property_catalog.mutation_lock import (
        FileCatalogMutationSerializer,
    )

    serializer = FileCatalogMutationSerializer(str(tmp_path))
    active = 0
    max_concurrent = 0
    guard = threading.Lock()

    def operation() -> None:
        nonlocal active, max_concurrent
        with guard:
            active += 1
            max_concurrent = max(max_concurrent, active)
        time.sleep(0.05)
        with guard:
            active -= 1

    threads = [
        threading.Thread(target=serializer.serialize, args=("same-key", operation))
        for _ in range(5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert max_concurrent == 1
