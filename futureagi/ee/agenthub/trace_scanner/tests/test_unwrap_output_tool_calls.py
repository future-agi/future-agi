"""Regression tests for unwrap_output over malformed tool_calls.

`_txt` type-checks before descending on every branch except the tool_calls one,
which read `(tc or {}).get("function")`. That guards None and empty but not a str,
and some producers emit tool_calls as a list of strings. It raised AttributeError
instead of degrading.

The raise is the damaging part rather than the missing text. The scanner fails open
on exception, so the trace came back has_issues=False and was indistinguishable from
a genuinely clean one. Measured on a 2,107-trace multi-tenant re-scan: 203 traces
(10.2%) died this way, all of them here, and they were not a random sample — median
123 spans against 11 for traces that scanned, and every one from the two
highest-volume tenants. Long conversational traces were being silently dropped from
the feed.

These pin that a malformed entry is skipped rather than fatal, and that a well-formed
entry alongside it is still extracted.
"""

from ee.agenthub.trace_scanner.compress import unwrap_output

_GOOD = {"function": {"name": "get_quote", "arguments": '{"ticker":"NVDA"}'}}


def _out(tool_calls):
    return {"choices": [{"message": {"content": None, "tool_calls": tool_calls}}]}


def test_string_tool_call_does_not_raise():
    """The exact production shape: tool_calls as a list of strings."""
    assert unwrap_output(_out(["get_quote", "lookup"])) is not None


def test_string_tool_call_is_skipped_not_fatal():
    """A malformed sibling must not cost us the well-formed one."""
    assert "NVDA" in unwrap_output(_out(["get_quote", _GOOD]))


def test_none_and_empty_entries_still_tolerated():
    """The original guard handled these; keep them working."""
    assert "NVDA" in unwrap_output(_out([None, {}, _GOOD]))


def test_non_dict_function_is_skipped():
    """`function` itself can be a string, not just the tool_call wrapper."""
    assert unwrap_output(_out([{"function": "get_quote"}])) is not None


def test_well_formed_tool_call_still_extracted():
    """The behaviour the branch exists for."""
    out = unwrap_output(_out([_GOOD]))
    assert "get_quote" in out and "NVDA" in out
