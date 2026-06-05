# ADR 005 — Evaluator registry is a lazy singleton

**Status**: Accepted
**Evidence**: `evaluations/engine/registry.py:13-35` (full `_build_registry` implementation); commit `ce5bb32`

## Context

Before the registry existed, every call site resolved evaluator classes with:

```python
from agentic_eval.core_evals.fi_evals import *
cls = globals().get(eval_type_id)
```

This pattern was scattered across 7+ files. It imported the entire `fi_evals` namespace into
local scope on every call, and `globals().get()` silently returned `None` for unknown type IDs
instead of raising.

## Decision

A module-level `_REGISTRY: dict[str, type]` is populated once on first access via `_build_registry()`,
guarded by a `_BUILT` bool. Subsequent calls skip the build entirely.

```python
_REGISTRY: dict[str, type] = {}
_BUILT = False

def _build_registry():
    global _BUILT
    if _BUILT:
        return
    import agentic_eval.core_evals.fi_evals as _evals_module
    for name in getattr(_evals_module, "__all__", []):
        ...
    _BUILT = True
```

## Why lazy, not module-level

`fi_evals` imports evaluator classes which import LiteLLM, sentence-transformers, and other
heavy dependencies. Importing at module load time would slow Django startup and cause circular
import failures in test environments where only parts of the app are loaded. Lazy init defers
the cost to first use.

## Why `_BUILT` bool, not `if not _REGISTRY`

An empty registry (e.g. if `__all__` were empty) is technically valid. `_BUILT` records whether
the build was *attempted*, not whether it produced results. This prevents silent infinite-rebuild
loops if `fi_evals` exports nothing.

## Consequences

- The registry is process-global. In tests, if `fi_evals.__all__` changes between test runs
  (e.g. via monkeypatching), the cached registry will be stale. Reset with
  `registry._BUILT = False; registry._REGISTRY.clear()`.
- `list_registered()` returns names in `__all__` insertion order (base → LLM → string →
  scoring → image), not alphabetical. This is a reflection of `__all__` structure, not sorted.
- If `fi_evals` fails to import, `_build_registry` re-raises. The registry never silently
  returns an empty set on import failure.
