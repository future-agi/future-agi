from unittest.mock import Mock, patch

import pytest
import requests

from agentic_eval.core_evals.fi_evals.function.functions import (
    contains_valid_link,
    no_invalid_links,
)


@pytest.mark.parametrize("evaluator", [contains_valid_link, no_invalid_links])
def test_link_evaluator_accepts_redirecting_links(evaluator):
    response = Mock(status_code=200)

    with patch(
        "agentic_eval.core_evals.fi_evals.function.functions.requests.head",
        return_value=response,
    ) as head:
        result = evaluator("Read more at http://example.com")

    assert result["result"] is True
    head.assert_called_once_with(
        "http://example.com", timeout=5, allow_redirects=True
    )


@pytest.mark.parametrize("evaluator", [contains_valid_link, no_invalid_links])
def test_link_evaluator_falls_back_to_get_when_head_is_not_supported(evaluator):
    head_response = Mock(status_code=405)
    get_response = Mock(status_code=200)

    with (
        patch(
            "agentic_eval.core_evals.fi_evals.function.functions.requests.head",
            return_value=head_response,
        ) as head,
        patch(
            "agentic_eval.core_evals.fi_evals.function.functions.requests.get",
            return_value=get_response,
        ) as get,
    ):
        result = evaluator("Read more at http://example.com")

    assert result["result"] is True
    head.assert_called_once_with(
        "http://example.com", timeout=5, allow_redirects=True
    )
    get.assert_called_once_with(
        "http://example.com", timeout=5, allow_redirects=True, stream=True
    )


@pytest.mark.parametrize("evaluator", [contains_valid_link, no_invalid_links])
def test_link_evaluator_returns_false_on_request_timeout(evaluator):
    with patch(
        "agentic_eval.core_evals.fi_evals.function.functions.requests.head",
        side_effect=requests.Timeout,
    ):
        result = evaluator("Read more at http://example.com")

    assert result["result"] is False


def test_link_evaluator_does_not_swallow_unexpected_exceptions():
    with patch(
        "agentic_eval.core_evals.fi_evals.function.functions.requests.head",
        side_effect=RuntimeError("unexpected bug"),
    ):
        with pytest.raises(RuntimeError, match="unexpected bug"):
            contains_valid_link("Read more at http://example.com")
