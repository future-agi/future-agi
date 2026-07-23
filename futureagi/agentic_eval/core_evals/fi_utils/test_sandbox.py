import os
import importlib
from futureagi.agentic_eval.core_evals.fi_utils import sandbox

def test_default_safe_modules_unchanged():
    """Ensure default modules remain intact when no env var is set."""
    if "CODE_EVAL_ADDITIONAL_SAFE_MODULES" in os.environ:
        del os.environ["CODE_EVAL_ADDITIONAL_SAFE_MODULES"]
    importlib.reload(sandbox)
    
    assert "json" in sandbox.SAFE_MODULES
    assert "requests" not in sandbox.SAFE_MODULES

def test_additional_safe_modules_loaded(monkeypatch):
    """Ensure additional modules are appended via environment variable."""
    monkeypatch.setenv("CODE_EVAL_ADDITIONAL_SAFE_MODULES", "requests, bs4 , httpx")
    importlib.reload(sandbox)
    
    # Check that new modules were added
    assert "requests" in sandbox.SAFE_MODULES
    assert "bs4" in sandbox.SAFE_MODULES
    assert "httpx" in sandbox.SAFE_MODULES
    
    # Check that defaults were preserved
    assert "json" in sandbox.SAFE_MODULES
    
    # Cleanup to avoid polluting other tests
    monkeypatch.delenv("CODE_EVAL_ADDITIONAL_SAFE_MODULES")
    importlib.reload(sandbox)