"""The eval engine never runs an external metric (SRV-5)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from evaluations.engine.runner import EvalRequest, run_eval


def test_run_eval_refuses_an_external_template_before_resolving_a_class():
    template = SimpleNamespace(
        name="external-abc", eval_type="external", config={"eval_type_id": "Contains"}
    )
    with pytest.raises(ValueError, match="external metric"):
        run_eval(EvalRequest(eval_template=template, inputs={"output": "x"}))
