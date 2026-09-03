"""Lightweight unit test wrapper for fast CLI check without Django DB."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings")
try:
    import django
    django.setup()
except Exception:
    pass

from agentic_eval.core_evals.fi_evals.function.functions import (
    calculate_reasoning_faithfulness,
    calculate_citation_precision,
    calculate_citation_recall,
)

def test_all():
    assert calculate_reasoning_faithfulness("Paris capital France", "Paris is capital of France")["result"] == 1.0
    assert calculate_citation_precision("Paris [1]", ["Paris is capital"])["result"] == 1.0
    assert calculate_citation_recall("Paris [1]", ["Paris"], expected=[1])["result"] == 1.0
    print("test_faithfulrag_unit: PASS")

if __name__ == "__main__":
    test_all()
