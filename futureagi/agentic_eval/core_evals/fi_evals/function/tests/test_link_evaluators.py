from unittest.mock import patch

import requests

from agentic_eval.core_evals.fi_evals.function.functions import (
    contains_valid_link,
    no_invalid_links,
)

# Hosts the fake transport treats as reachable. Anything under the reserved
# .invalid TLD (RFC 2606) can never resolve, so it stands in for a broken link.
_REACHABLE = {
    "http://example.com",
    "https://example.com",
    "http://ok.com",
    "https://ok.com",
}


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


def _fake_head(url, *args, **kwargs):
    """Stand-in for requests.head.

    Accepts *args/**kwargs so this stays valid once the evaluators start
    passing timeout=/allow_redirects= (issue #1945).
    """
    if url in _REACHABLE:
        return _FakeResponse(200)
    if url.endswith(".invalid"):
        raise requests.RequestException("name resolution failed")
    return _FakeResponse(404)


def _patch_head():
    return patch(
        "agentic_eval.core_evals.fi_evals.function.functions.requests.head",
        side_effect=_fake_head,
    )


class TestNoInvalidLinks:
    """Regression tests for no_invalid_links (issue #2554).

    The evaluator located links with re.search, so only the first match was
    ever validated and a broken later link passed.
    """

    def test_no_links_is_valid(self):
        with _patch_head():
            result = no_invalid_links(text="no links here at all")
        assert result["result"] is True

    def test_all_links_valid(self):
        with _patch_head():
            result = no_invalid_links(text="http://ok.com and https://example.com")
        assert result["result"] is True

    def test_invalid_link_after_a_valid_one_fails(self):
        # The reproducer from issue #2554: the broken link is not first, so
        # re.search never reached it and the evaluator wrongly returned True.
        text = "Docs: https://example.com and mirror: http://broken-domain-xyz.invalid"
        with _patch_head():
            result = no_invalid_links(text=text)
        assert result["result"] is False
        assert "http://broken-domain-xyz.invalid" in result["reason"]

    def test_invalid_link_before_a_valid_one_fails(self):
        text = "http://broken-domain-xyz.invalid then https://example.com"
        with _patch_head():
            result = no_invalid_links(text=text)
        assert result["result"] is False

    def test_non_200_status_after_a_valid_link_fails(self):
        text = "https://example.com then http://missing.com"
        with _patch_head():
            result = no_invalid_links(text=text)
        assert result["result"] is False
        assert "http://missing.com" in result["reason"]

    def test_every_link_is_checked(self):
        text = "https://example.com http://ok.com"
        with _patch_head() as head:
            no_invalid_links(text=text)
        checked = {call.args[0] for call in head.call_args_list}
        assert checked == {"https://example.com", "http://ok.com"}


class TestContainsValidLink:
    """Regression tests for contains_valid_link (issue #2554).

    The evaluator validated only the first match, so a valid link later in
    the text was reported as absent.
    """

    def test_no_links_is_not_valid(self):
        with _patch_head():
            result = contains_valid_link(text="no links here at all")
        assert result["result"] is False

    def test_first_link_valid(self):
        with _patch_head():
            result = contains_valid_link(text="see https://example.com")
        assert result["result"] is True

    def test_valid_link_after_a_broken_one_passes(self):
        # The mirror of the issue #2554 defect: the only valid link is not
        # first, so re.search checked the broken one and returned False.
        text = "Bad: http://broken-domain-xyz.invalid good: http://ok.com"
        with _patch_head():
            result = contains_valid_link(text=text)
        assert result["result"] is True
        assert "http://ok.com" in result["reason"]

    def test_valid_link_after_a_non_200_passes(self):
        text = "http://missing.com then https://ok.com"
        with _patch_head():
            result = contains_valid_link(text=text)
        assert result["result"] is True

    def test_all_links_broken_fails(self):
        text = "http://a-xyz.invalid and http://b-xyz.invalid"
        with _patch_head():
            result = contains_valid_link(text=text)
        assert result["result"] is False

    def test_stops_at_the_first_valid_link(self):
        text = "http://missing.com http://ok.com https://example.com"
        with _patch_head() as head:
            contains_valid_link(text=text)
        checked = [call.args[0] for call in head.call_args_list]
        assert checked == ["http://missing.com", "http://ok.com"]
