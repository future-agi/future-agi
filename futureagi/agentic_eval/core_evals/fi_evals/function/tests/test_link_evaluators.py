from unittest.mock import patch

import requests

from agentic_eval.core_evals.fi_evals.function.functions import (
    contains_valid_link,
    no_invalid_links,
)

# Reachable hosts for the fake transport. The reserved .invalid TLD (RFC 2606)
# never resolves, so it stands in for a broken link without real network I/O.
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

    Accepts *args/**kwargs so this stays valid if the evaluators later pass
    timeout=/allow_redirects= (issues #1945/#1953).
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

    Previously used re.search, so only the first match was validated and a
    broken later link incorrectly passed.
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
        # Reproducer for #2554: broken link is not first, so re.search missed it.
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

    Previously validated only the first match, so a valid later link was
    reported as absent.
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
        # Mirror of #2554: only valid link is not first; re.search returned False.
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
