from io import BytesIO
from unittest.mock import Mock, patch

import pytest
import requests
from requests.adapters import BaseAdapter

from agentic_eval.core_evals.fi_evals.function.functions import (
    contains_valid_link,
    no_invalid_links,
)


def _response(status_code):
    response = requests.Response()
    response.status_code = status_code
    response._content = b""
    response.raw = BytesIO()
    return response


@pytest.mark.parametrize("evaluator", [contains_valid_link, no_invalid_links])
def test_link_evaluator_accepts_redirecting_links(evaluator):
    response = _response(200)

    with patch(
        "agentic_eval.core_evals.fi_evals.function.functions.requests.Session.head",
        return_value=response,
    ) as head:
        result = evaluator("Read more at http://example.com")

    assert result["result"] is True
    head.assert_called_once_with("http://example.com", timeout=5, allow_redirects=True)


@pytest.mark.parametrize("evaluator", [contains_valid_link, no_invalid_links])
@pytest.mark.parametrize("head_status", [403, 405])
def test_link_evaluator_falls_back_to_get_when_head_is_not_supported(
    evaluator, head_status
):
    head_response = _response(head_status)
    get_response = _response(200)
    head_response.close = Mock(wraps=head_response.close)
    get_response.close = Mock(wraps=get_response.close)

    with (
        patch(
            "agentic_eval.core_evals.fi_evals.function.functions.requests.Session.head",
            return_value=head_response,
        ) as head,
        patch(
            "agentic_eval.core_evals.fi_evals.function.functions.requests.Session.get",
            return_value=get_response,
        ) as get,
    ):
        result = evaluator("Read more at http://example.com")

    assert result["result"] is True
    head.assert_called_once_with("http://example.com", timeout=5, allow_redirects=True)
    get.assert_called_once_with(
        "http://example.com", timeout=5, allow_redirects=True, stream=True
    )
    head_response.close.assert_called_once_with()
    get_response.close.assert_called_once_with()


@pytest.mark.parametrize("evaluator", [contains_valid_link, no_invalid_links])
def test_link_evaluator_returns_false_on_request_timeout(evaluator):
    with patch(
        "agentic_eval.core_evals.fi_evals.function.functions.requests.Session.head",
        side_effect=requests.Timeout,
    ):
        result = evaluator("Read more at http://example.com")

    assert result["result"] is False


def test_link_evaluator_does_not_swallow_unexpected_exceptions():
    with patch(
        "agentic_eval.core_evals.fi_evals.function.functions.requests.Session.head",
        side_effect=RuntimeError("unexpected bug"),
    ):
        with pytest.raises(RuntimeError, match="unexpected bug"):
            contains_valid_link("Read more at http://example.com")


@pytest.mark.parametrize("evaluator", [contains_valid_link, no_invalid_links])
@pytest.mark.parametrize("head_status", [None, 403, 405])
@pytest.mark.parametrize("redirects, expected", [(5, True), (6, False), (None, False)])
def test_link_evaluator_bounds_redirect_chains(
    evaluator, head_status, redirects, expected, monkeypatch
):
    class RedirectAdapter(BaseAdapter):
        def __init__(self):
            self.calls = []

        def send(self, request, **kwargs):
            self.calls.append(request.method)
            response = _response(200)
            response.request = request
            response.url = request.url
            if request.method == "HEAD" and head_status is not None:
                response.status_code = head_status
            else:
                hop = int(request.url.rsplit("/", 1)[-1])
                response.status_code = 200 if hop == redirects else 302
                response.headers["Location"] = (
                    "/0" if redirects is None else f"/{hop + 1}"
                )
            return response

        def close(self):
            pass

    adapter = RedirectAdapter()
    monkeypatch.setattr(requests.Session, "get_adapter", lambda self, url: adapter)

    result = evaluator("Read more at http://example.com/0")

    assert result["result"] is expected
    method = "HEAD" if head_status is None else "GET"
    assert adapter.calls.count(method) == 6
    if head_status is not None:
        assert adapter.calls.count("HEAD") == 1
