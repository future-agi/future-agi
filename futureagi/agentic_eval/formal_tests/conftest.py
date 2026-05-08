"""
Lightweight stub layer for agentic_eval formal tests.

eval_type.py only imports stdlib enum — no stubs needed for it.
error_handler.py imports litellm — we inline its pure functions instead.
conftest only stubs modules that would otherwise crash on import
(structlog, litellm internals referenced by conftest-unrelated code).
"""
import sys
import types


def _make_module(name):
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


# ── structlog ─────────────────────────────────────────────────────────────────

structlog = _make_module("structlog")


class _NullLogger:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def exception(self, *a, **k): pass
    def debug(self, *a, **k): pass


structlog.get_logger = lambda *a, **k: _NullLogger()

# NOTE: Do NOT stub agentic_eval or its sub-packages here.
# eval_type.py depends only on stdlib enum and can be imported directly.
# Stubbing the package would prevent the real code from loading.
