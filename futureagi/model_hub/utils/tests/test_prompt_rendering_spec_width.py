"""A format spec in an f-string template has a bounded width and precision.

A width or precision above 1000 is refused with the plain message, so one
small field cannot make the renderer build a very large text. Widths and
precisions up to 1000 format as before, and so does a date or time pattern
that contains a number.
"""

import datetime
import decimal

import pytest

from model_hub.utils.prompt_rendering import render_template

REFUSED = "This template can only insert a named value or one of its keys."
WHEN = datetime.datetime(2026, 11, 10, 14, 5, 6)


def render(template, context):
    return render_template(template, context, "f-string")


@pytest.mark.parametrize(
    "template, context",
    [
        pytest.param("{score:>1001}", {"score": 5}, id="width-on-a-number"),
        pytest.param("{question:<5000}", {"question": "why"}, id="width-on-text"),
        pytest.param("{score:.1001f}", {"score": 0.5}, id="precision"),
        pytest.param("{score:>000001001}", {"score": 5}, id="leading-zeros"),
        pytest.param("{score:>99999999999}", {"score": 5}, id="many-digits"),
        pytest.param(
            "{price:.1001f}", {"price": decimal.Decimal("1.5")}, id="decimal"
        ),
        pytest.param("{when!s:>1001}", {"when": WHEN}, id="converted-date"),
    ],
)
def test_a_width_or_precision_above_the_limit_is_refused(template, context):
    with pytest.raises(ValueError, match=REFUSED):
        render(template, context)


def test_a_width_and_precision_at_the_limit_still_format():
    assert render("{score:>1000}", {"score": 5}) == format(5, ">1000")
    assert render("{score:.1000f}", {"score": 0.5}) == format(0.5, ".1000f")
    assert render("{score:>8.3f}|", {"score": 0.5}) == format(0.5, ">8.3f") + "|"


def test_a_number_in_a_date_or_time_pattern_is_literal_text():
    pattern = "%Y-%m-%d (week %W of 2026)"
    assert render("{when:" + pattern + "}", {"when": WHEN}) == format(WHEN, pattern)
    assert render("{day:%d.%m.%Y in 2026}", {"day": WHEN.date()}) == format(
        WHEN.date(), "%d.%m.%Y in 2026"
    )


def test_none_with_a_conversion_inserts_the_text_none():
    assert render("{x!r}|{x!s:>6}|{x!a}", {"x": None}) == "None|  None|None"


def test_none_with_a_bare_format_spec_is_refused_with_the_plain_message():
    with pytest.raises(ValueError, match=REFUSED):
        render("{x:>6}", {"x": None})
