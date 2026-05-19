"""
_eval_cluster_meta gating + fallback contract.

The cheap-LLM metadata (title / fix_layer / severity) is EE-only and
best-effort. The load-bearing guarantee: cluster creation NEVER breaks
on its account, and every field degrades independently.

  * EE present + full meta   -> use it
  * EE present + None        -> deterministic title, null fix_layer/severity
  * EE present + raises      -> same fallback
  * EE absent (OSS)          -> same fallback   (no crash)
  * EE present + partial     -> per-field fallback (title backfilled)
"""

import sys
from unittest.mock import patch

from tracer.queries.eval_clustering import _eval_cluster_meta, _extract_title
from tracer.types.eval_cluster_types import EvalClusterMeta

REASONING = (
    "The verdict is Fail because the speech has an unnatural, robotic "
    "rhythm and pacing throughout the call."
)
_PATCH = "ee.agenthub.trace_scanner.eval_cluster_title.generate_eval_cluster_meta"


def test_uses_full_meta_when_available():
    with patch(
        _PATCH,
        return_value=EvalClusterMeta(
            title="Robotic, unnatural speech rhythm",
            fix_layer="Prompt",
            severity="high",
        ),
    ):
        m = _eval_cluster_meta("prosody_and_intonation", REASONING)
    assert m.title == "Robotic, unnatural speech rhythm"
    assert m.fix_layer == "Prompt"
    assert m.severity == "high"


def test_partial_meta_backfills_title_only():
    """Null title -> deterministic title; fix_layer/severity stay as given."""
    with patch(
        _PATCH,
        return_value=EvalClusterMeta(
            title=None, fix_layer="Guardrails", severity="critical"
        ),
    ):
        m = _eval_cluster_meta("pii_leak", REASONING)
    assert m.title == _extract_title(REASONING)
    assert m.fix_layer == "Guardrails"
    assert m.severity == "critical"


def test_falls_back_when_meta_none():
    with patch(_PATCH, return_value=None):
        m = _eval_cluster_meta("prosody_and_intonation", REASONING)
    assert m == EvalClusterMeta(title=_extract_title(REASONING))


def test_falls_back_when_meta_raises():
    with patch(_PATCH, side_effect=RuntimeError("gateway down")):
        m = _eval_cluster_meta("prosody_and_intonation", REASONING)
    assert m.title == _extract_title(REASONING)
    assert m.fix_layer is None and m.severity is None


def test_oss_safety_no_ee_module_no_crash():
    # Module set to None in sys.modules makes `import` raise ImportError —
    # simulates an OSS deployment with no ee package.
    with patch.dict(sys.modules, {"ee.agenthub.trace_scanner.eval_cluster_title": None}):
        m = _eval_cluster_meta("prosody_and_intonation", REASONING)
    assert m == EvalClusterMeta(title=_extract_title(REASONING))
