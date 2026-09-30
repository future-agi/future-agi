import pytest

from simulate.utils.scenario_keys import canonical_scenario_key


# Expected values are what the harness itself derives for the same input.
@pytest.mark.parametrize(
    ("value", "key"),
    [
        ("refund_missing_item", "refund-missing-item"),
        ("refund-missing-item", "refund-missing-item"),
        ("Refund missing item", "refund-missing-item"),
        ("  Cancel: Delivered Order!! ", "cancel-delivered-order"),
        ("Ünïcödé café", "n-c-d-caf"),
        ("रिफंड वापसी", "scenario-b69f27438621"),
        ("", ""),
        ("   ", ""),
        (None, ""),
    ],
)
def test_canonical_key_matches_the_harness(value, key):
    assert canonical_scenario_key(value) == key
