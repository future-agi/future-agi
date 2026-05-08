"""
Evaluator class registry.

Replaces the `from fi_evals import *` + `globals().get(eval_type_id)` pattern
used across 7+ files. All evaluator classes are registered here once and
looked up by their string type ID.

Lazy singleton design — see docs/adr/005-registry-lazy-singleton.md.
"""

import structlog

logger = structlog.get_logger(__name__)

_REGISTRY: dict[str, type] = {}
_BUILT = False


def _build_registry():
    """Populate the registry from fi_evals exports."""
    global _BUILT
    if _BUILT:
        return

    try:
        import agentic_eval.core_evals.fi_evals as _evals_module

        for name in getattr(_evals_module, "__all__", []):
            cls = getattr(_evals_module, name, None)
            if cls is not None:
                _REGISTRY[name] = cls

        _BUILT = True
        logger.debug("eval_registry_built", count=len(_REGISTRY))
    except ImportError:
        logger.error("eval_registry_import_failed")
        raise


def get_eval_class(eval_type_id: str) -> type:
    """
    Look up an evaluator class by its type ID string.

    Raises ValueError if the class is not found.
    """
    if not _BUILT:
        _build_registry()

    cls = _REGISTRY.get(eval_type_id)
    if cls is None:
        raise ValueError(f"Unknown evaluator type: '{eval_type_id}'")
    return cls


def is_registered(eval_type_id: str) -> bool:
    """Check if an evaluator type ID is registered."""
    if not _BUILT:
        _build_registry()
    return eval_type_id in _REGISTRY


def list_registered() -> list[str]:
    """Return all registered evaluator type IDs."""
    if not _BUILT:
        _build_registry()
    return list(_REGISTRY.keys())
