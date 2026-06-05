"""
Lightweight stub layer for agentic_eval formal tests.

eval_type.py only imports stdlib enum — no stubs needed for it.
error_handler.py imports litellm and tfc — we stub those so the real
format_concise_error can be imported directly (instead of inlining it).
"""
import sys
import types


def _make_module(name):
    mod = types.ModuleType(name)
    sys.modules.setdefault(name, mod)
    return sys.modules[name]


# ── structlog ─────────────────────────────────────────────────────────────────

structlog = _make_module("structlog")


class _NullLogger:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def exception(self, *a, **k): pass
    def debug(self, *a, **k): pass


structlog.get_logger = lambda *a, **k: _NullLogger()

# ── litellm — stub the exception classes error_handler.py imports ─────────────

_litellm = _make_module("litellm")
for _exc_name in (
    "APIConnectionError", "APIError", "APIResponseValidationError",
    "AuthenticationError", "BadRequestError", "BudgetExceededError",
    "ContentPolicyViolationError", "ContextWindowExceededError",
    "InternalServerError", "InvalidRequestError", "JSONSchemaValidationError",
    "NotFoundError", "RateLimitError", "ServiceUnavailableError",
    "Timeout", "UnprocessableEntityError", "UnsupportedParamsError",
):
    setattr(_litellm, _exc_name, type(_exc_name, (Exception,), {}))

# ── tfc.utils.error_codes — stub get_error_message ───────────────────────────

_tfc = _make_module("tfc")
_tfc_utils = _make_module("tfc.utils")
_tfc.utils = _tfc_utils
_tfc_error_codes = _make_module("tfc.utils.error_codes")
_tfc_utils.error_codes = _tfc_error_codes
_tfc_error_codes.get_error_message = lambda code, **kw: code

# NOTE: Do NOT stub agentic_eval or its sub-packages here.
# eval_type.py depends only on stdlib enum and can be imported directly.
# Stubbing the package would prevent the real code from loading.
