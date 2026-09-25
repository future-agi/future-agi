"""Raw column results and legacy eval scores use distinct sandbox contracts."""

import pytest

from agentic_eval.core_evals.fi_utils import sandbox


@pytest.mark.parametrize(
    ("returned", "expected"),
    [
        ("'HI THERE'", "HI THERE"),
        ("42", 42),
        ("{'score': 42, 'label': 'answer'}", {"score": 42, "label": "answer"}),
    ],
)
def test_raw_results_preserved_by_fallback(monkeypatch, returned, expected):
    monkeypatch.setattr(sandbox, "_call_executor_service", lambda *args: None)

    result = sandbox.execute_sandboxed_python(
        f"def main(**kwargs):\n    return {returned}", {}, raw_result=True
    )

    assert result == {"status": "success", "data": expected}


def test_eval_score_contract_unchanged(monkeypatch):
    monkeypatch.setattr(sandbox, "_call_executor_service", lambda *args: None)

    result = sandbox.execute_sandboxed_python("def main(**kwargs):\n    return 42", {})

    assert result == {
        "status": "success",
        "data": {"result": 1.0, "reason": "Numeric score"},
    }


def test_old_executor_cannot_silently_coerce_raw_result(monkeypatch):
    monkeypatch.setattr(
        sandbox,
        "_call_executor_service",
        lambda *args: {
            "status": "success",
            "data": {"result": 1.0, "reason": "numeric"},
        },
    )

    result = sandbox.execute_sandboxed_python(
        "def main(**kwargs):\n    return 42", {}, raw_result=True
    )

    assert result == {
        "status": "error",
        "data": "Code executor does not support raw results",
    }
