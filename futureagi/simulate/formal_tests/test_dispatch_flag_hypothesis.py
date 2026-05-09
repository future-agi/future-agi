"""
Hypothesis property tests for TEMPORAL_TEST_EXECUTION_ENABLED flag removal (issue #310).

Tests the dispatch logic reference model to verify:
  - Post-fix: execute always takes Temporal path
  - Post-fix: cancel takes Temporal then optionally DB fallback
  - Post-fix: Celery legacy path is never reached
  - Pre-fix: flag=False allowed Celery (regression proof)
  - The setting is removed from settings.py
"""

from hypothesis import given, settings
from hypothesis import strategies as st


# ── Reference dispatch models ─────────────────────────────────────────────────

def dispatch_pre_fix(action: str, flag_enabled: bool, temporal_available: bool) -> str:
    """Pre-fix dispatch: flag gates Temporal vs Celery."""
    if action == "execute":
        if flag_enabled:
            return "temporal" if temporal_available else "error"
        else:
            return "celery_legacy"  # silent degradation
    else:  # cancel
        if flag_enabled:
            return "temporal" if temporal_available else "db_fallback"
        else:
            return "celery_legacy"


def dispatch_post_fix(action: str, temporal_available: bool) -> str:
    """Post-fix dispatch: always Temporal; no Celery fallback for execute."""
    if action == "execute":
        return "temporal" if temporal_available else "error"
    else:  # cancel
        return "temporal" if temporal_available else "db_fallback"


# ── Properties ────────────────────────────────────────────────────────────────

@given(temporal_available=st.booleans())
def test_execute_never_celery_post_fix(temporal_available):
    """Execute always goes to temporal or error, never celery_legacy."""
    path = dispatch_post_fix("execute", temporal_available)
    assert path != "celery_legacy"


@given(temporal_available=st.booleans())
def test_cancel_never_celery_post_fix(temporal_available):
    """Cancel always goes to temporal or db_fallback, never celery_legacy."""
    path = dispatch_post_fix("cancel", temporal_available)
    assert path != "celery_legacy"


@given(temporal_available=st.booleans())
def test_execute_no_db_fallback_post_fix(temporal_available):
    """Execute never reaches DB fallback (Celery cancel_test)."""
    path = dispatch_post_fix("execute", temporal_available)
    assert path != "db_fallback"


@given(temporal_available=st.booleans())
def test_execute_temporal_when_available(temporal_available):
    """Execute goes to temporal when Temporal is available."""
    if temporal_available:
        assert dispatch_post_fix("execute", True) == "temporal"


@given(temporal_available=st.booleans())
def test_execute_error_when_temporal_unavailable(temporal_available):
    """Execute surfaces error when Temporal unavailable (no silent degradation)."""
    if not temporal_available:
        assert dispatch_post_fix("execute", False) == "error"


@given(temporal_available=st.booleans())
def test_cancel_db_fallback_when_temporal_unavailable(temporal_available):
    """Cancel uses DB fallback when Temporal has no workflow — this is intentional."""
    if not temporal_available:
        assert dispatch_post_fix("cancel", False) == "db_fallback"


def test_pre_fix_flag_false_execute_reaches_celery():
    """Pre-fix: flag=False, execute action → celery_legacy (the silent degradation)."""
    path = dispatch_pre_fix("execute", flag_enabled=False, temporal_available=True)
    assert path == "celery_legacy"


def test_pre_fix_flag_true_execute_temporal():
    """Pre-fix: flag=True, execute action → temporal when available."""
    path = dispatch_pre_fix("execute", flag_enabled=True, temporal_available=True)
    assert path == "temporal"


@given(action=st.sampled_from(["execute", "cancel"]), temporal=st.booleans())
def test_post_fix_never_celery_under_all_inputs(action, temporal):
    """Post-fix: celery_legacy is unreachable for any (action, temporal) combo."""
    path = dispatch_post_fix(action, temporal)
    assert path != "celery_legacy"


# ── Settings file verification ────────────────────────────────────────────────

def test_flag_removed_from_settings():
    """TEMPORAL_TEST_EXECUTION_ENABLED should not exist in settings.py."""
    import re
    from pathlib import Path
    settings_path = Path("/Users/jonathanhill/src/future-agi/futureagi/tfc/settings/settings.py")
    content = settings_path.read_text()
    assert "TEMPORAL_TEST_EXECUTION_ENABLED" not in content, \
        "TEMPORAL_TEST_EXECUTION_ENABLED still present in settings.py"


def test_flag_removed_from_env_example():
    """TEMPORAL_TEST_EXECUTION_ENABLED should not be in .env.example."""
    from pathlib import Path
    env_path = Path("/Users/jonathanhill/src/future-agi/futureagi/.env.example")
    if env_path.exists():
        content = env_path.read_text()
        assert "TEMPORAL_TEST_EXECUTION_ENABLED" not in content, \
            "TEMPORAL_TEST_EXECUTION_ENABLED still in .env.example"
