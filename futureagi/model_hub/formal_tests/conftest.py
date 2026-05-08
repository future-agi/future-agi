"""
Minimal stubs so model_hub pure-function modules can be imported
without a running Django / structlog stack.
"""
import sys
import types

# Stub structlog (eval_validators.py does `import structlog` at module level)
_structlog = types.ModuleType("structlog")
_structlog.get_logger = lambda *a, **kw: types.SimpleNamespace(
    warning=lambda *a, **kw: None,
    info=lambda *a, **kw: None,
    debug=lambda *a, **kw: None,
    error=lambda *a, **kw: None,
)
sys.modules.setdefault("structlog", _structlog)
