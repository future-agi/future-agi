"""A total-spend budget threshold is stored in US dollars.

Budget enforcement compares the stored threshold_value directly with the
period's invoice preview total, which is in dollars, so the field's help text
must say dollars. Text that said cents would lead someone entering a threshold
through the admin to set it one hundred times too high.
"""

from ee.usage.models.usage import UsageBudget


def test_total_spend_threshold_help_text_says_us_dollars():
    help_text = str(UsageBudget._meta.get_field("threshold_value").help_text)

    assert "US dollars for total_spend" in help_text
    assert "cents" not in help_text.lower()


def test_threshold_storage_is_unchanged():
    field = UsageBudget._meta.get_field("threshold_value")

    assert (field.get_internal_type(), field.max_digits, field.decimal_places) == (
        "DecimalField",
        16,
        4,
    )
