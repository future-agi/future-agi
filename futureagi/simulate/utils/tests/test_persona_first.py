"""Behavioral regression test for #311 / PR #344.

persona_first normalises a persona attribute that may be None, a plain string, or a
list of strings. The pre-fix code called values[0] unconditionally, so a plain string
like "male" silently returned its first CHARACTER "m". The fix handles all three cases
and emits a warning when a multi-value list is truncated to its first element.
"""
import logging

import pytest

from simulate.utils.persona_utils import persona_first


@pytest.mark.unit
def test_persona_first_string_returns_full_value_not_first_char():
    # The core bug: a plain string must return itself, not values[0] == "m".
    assert persona_first("male", "gender", "unknown") == "male"


@pytest.mark.unit
def test_persona_first_none_returns_default_and_list_returns_first():
    assert persona_first(None, "gender", "unknown") == "unknown"
    assert persona_first([], "gender", "unknown") == "unknown"
    assert persona_first(["female", "male"], "gender", "unknown") == "female"


@pytest.mark.unit
def test_persona_first_warns_on_multi_value_truncation(caplog):
    with caplog.at_level(logging.WARNING):
        result = persona_first(["female", "male"], "gender", "unknown")
    assert result == "female"
    assert "persona_attribute_multi_value_truncated" in caplog.text
