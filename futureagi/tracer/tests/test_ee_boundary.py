"""OSS-stripped boundary test (``_ee_available=False``).

A self-host / OSS deploy runs with ``ee.*`` absent, so every
``tracer.ee_boundary`` function must degrade to its no-op / passthrough fallback
without touching ``ee.*``. That path is otherwise unexercised — a regression
(e.g. dereferencing an ee symbol before the availability guard) would land
silently on self-host only. Pin all four fallbacks here.
"""

from unittest.mock import patch

from tracer import ee_boundary


def test_oss_path_returns_fallbacks_when_ee_absent():
    with patch.object(ee_boundary, "_ee_available", False):
        # LLM-backed helpers → None (callers default to medium / deterministic).
        assert ee_boundary.generate_scan_cluster_severity("cat", "brief") is None
        assert ee_boundary.generate_eval_cluster_meta("eval", "reasoning") is None

        # Passthroughs → the SAME object back, untouched (identity proves the
        # guard returned before any ee call / CH read).
        key_moments = [{"verbatim": "x"}]
        assert (
            ee_boundary.attribute_key_moments(key_moments, "trace-1", "proj-1")
            is key_moments
        )

        results: list = []
        assert ee_boundary.distill_eval_failure_phrases(results) is results
