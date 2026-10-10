"""The dataset-limit outcome and the one rule for who the limit binds."""

from model_hub.utils.dataset_limit import (
    DatasetLimitCheck,
    DatasetLimitOutcome,
    dataset_limit_binds,
)


def test_outcomes_are_the_three_states_the_check_can_end_in():
    assert {outcome.value for outcome in DatasetLimitOutcome} == {
        "allowed",
        "limit_reached",
        "unverified",
    }


def test_check_defaults_to_no_limit_value():
    assert DatasetLimitCheck(DatasetLimitOutcome.ALLOWED).limit == 0


def test_limit_binds_every_creation_but_sdk_uploads():
    assert dataset_limit_binds(sdk_source=False) is True
    assert dataset_limit_binds(sdk_source=True) is False
