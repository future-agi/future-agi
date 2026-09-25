"""The code-executor service preserves raw column values without changing evals."""

import pytest
import server
from falcon import testing


@pytest.mark.parametrize(
    ("returned", "expected"),
    [
        ("'HI THERE'", "HI THERE"),
        ("42", 42),
        ("{'score': 42, 'label': 'answer'}", {"score": 42, "label": "answer"}),
    ],
)
def test_raw_results_preserved_by_executor(returned, expected):
    result = server._execute_python_fallback(
        f"def main(**kwargs):\n    return {returned}", {}, 5, raw_result=True
    )

    assert result == {"status": "success", "data": expected, "raw_result": True}


def test_executor_eval_score_contract_unchanged():
    result = server._execute_python_fallback(
        "def main(**kwargs):\n    return 42", {}, 5
    )

    assert result == {"status": "success", "data": {"result": 1.0, "reason": "numeric"}}


def test_executor_http_raw_result():
    client = testing.TestClient(server.app)
    response = client.simulate_post(
        "/execute",
        json={
            "code": "def main(**kwargs):\n    return kwargs['question'].upper()",
            "input_data": {"question": "hi there"},
            "language": "python",
            "raw_result": True,
        },
    )

    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert response.json["data"] == "HI THERE"
    assert response.json["raw_result"] is True
