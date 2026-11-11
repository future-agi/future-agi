"""Typed error codes a client branches on, and the messages shown with them.

``get_error_message`` answers "An unknown error occurred." for a key it does
not know, so a misspelt key would ship silently; these pin every registered
code to its wire string and its message.
"""

import pytest

from tfc.utils.api_errors import ApiErrorCode
from tfc.utils.error_codes import get_error_message

UNKNOWN = "An unknown error occurred."


@pytest.mark.parametrize(
    ("code", "wire", "message_key", "message"),
    [
        (
            ApiErrorCode.USER_FILTER_REQUIRES_CURSOR,
            "user_filter_requires_cursor",
            "USER_FILTER_REQUIRES_CURSOR",
            "These user filters need cursor pagination. Retry with cursor_mode=true.",
        ),
        (
            ApiErrorCode.SCORE_PROJECT_MISMATCH,
            "score_project_mismatch",
            "SCORE_PROJECT_MISMATCH",
            "This queue item belongs to another project's copy of this source. "
            "Annotate it from that project.",
        ),
        (
            ApiErrorCode.SCORE_PROJECT_MISMATCH,
            "score_project_mismatch",
            "SCORE_PROJECT_MISMATCH_EXISTING_SCORE",
            "A score on this queue item belongs to another project's copy of "
            "this source. Annotate it from that project.",
        ),
        (
            ApiErrorCode.DATASET_LIMIT_CHECK_FAILED,
            "dataset_limit_check_failed",
            "DATASET_LIMIT_CHECK_FAILED",
            "Could not verify your plan's dataset limit. Please try again in a moment.",
        ),
        (
            ApiErrorCode.FILTER_VALUE_INVENTORY_TOO_BROAD,
            "filter_value_inventory_too_broad",
            "FILTER_VALUE_INVENTORY_TOO_BROAD",
            "Too many values to browse exactly. Enter a more specific search.",
        ),
    ],
)
def test_registered_code_and_message(code, wire, message_key, message):
    assert code == wire
    assert str(code) == wire
    assert get_error_message(message_key) == message != UNKNOWN
