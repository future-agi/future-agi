"""TH-4909 regression: agent CECs with incomplete templates must produce
a visible error row, not silently fail.

Background: a bulk-attach flow persisted CustomEvalConfig rows whose
linked agent template had empty config. At dispatch time, the eval
engine failed in non-ValueError ways and the upstream catch swallowed
the failure — no eval_logger row was written, customer saw a blank cell.

Fix lives in tracer/utils/eval.py::_execute_evaluation — a pre-dispatch
check that raises ValueError when an agent template lacks required
fields. The existing ValueError handler then writes an error row.
"""
from __future__ import annotations

from pathlib import Path


def test_execute_evaluation_contains_agent_template_check():
    """Source-level guard: the pre-dispatch check stays in _execute_evaluation."""
    source = Path(__file__).resolve().parents[1] / "utils" / "eval.py"
    text = source.read_text()
    assert "TH-4909" in text, "TH-4909 marker missing — check may have been removed"
    assert 'eval_type", None) == "agent"' in text, \
        "agent-template gate missing from _execute_evaluation"
    assert '"output"' in text and '"rule_prompt"' in text, \
        "required-field list missing from TH-4909 check"
    assert "Agent eval template" in text and "is missing required" in text, \
        "TH-4909 error message wording changed — update or restore"


def test_execute_evaluation_check_routes_through_value_error_handler():
    """Verify the check raises ValueError (not a custom exception) so the
    existing upstream ValueError handler in evaluate_observation_span_observe
    writes an error row via _create_error_eval_logger."""
    source = Path(__file__).resolve().parents[1] / "utils" / "eval.py"
    text = source.read_text()
    th_4909_section = text.split("TH-4909:", 1)[1].split("# Composite evals", 1)[0]
    assert "raise ValueError" in th_4909_section, \
        "TH-4909 block must raise ValueError to hit the existing error-row handler"


def test_check_reads_template_not_cec_config():
    """Karthik's review: don't read from cec.config — read from the template
    via the FK. This test locks that in."""
    source = Path(__file__).resolve().parents[1] / "utils" / "eval.py"
    text = source.read_text()
    th_4909_section = text.split("TH-4909:", 1)[1].split("# Composite evals", 1)[0]
    assert "eval_model.config" in th_4909_section, \
        "TH-4909 must read from the template (eval_model.config), not CEC.config"
    assert "custom_eval_config.config" not in th_4909_section, \
        "TH-4909 must NOT read from cec.config — that's the architectural issue Karthik flagged"


def test_eval_type_id_read_is_none_safe():
    """Bot caught: line 547 was dereferencing `.config.get(...)` without a
    None-guard. If a template has config=None (the exact targeted case),
    that raises AttributeError BEFORE the TH-4909 ValueError guard fires
    — and the upstream `except ValueError` handler doesn't catch it, so
    the silent failure persists. The fix uses `(config or {}).get(...)`."""
    source = Path(__file__).resolve().parents[1] / "utils" / "eval.py"
    text = source.read_text()
    assert "(custom_eval_config.eval_template.config or {}).get(\"eval_type_id\")" in text, \
        "eval_type_id read must be None-safe — see TH-4909 review comment at line 547"


def test_composite_templates_skipped_by_agent_check():
    """Bot caught: composite templates inherit eval_type='agent' from their
    children but don't store output/rule_prompt on the composite itself.
    The agent guard must skip composites so valid composite-agent evals
    aren't killed before reaching the composite dispatch branch."""
    source = Path(__file__).resolve().parents[1] / "utils" / "eval.py"
    text = source.read_text()
    th_4909_section = text.split("TH-4909:", 1)[1].split("Composite templates", 1)[1].split("# Composite evals", 1)[0]
    assert 'template_type", None) != "composite"' in th_4909_section, \
        "TH-4909 agent guard must exclude composite templates — they hold output/rule_prompt on children"
