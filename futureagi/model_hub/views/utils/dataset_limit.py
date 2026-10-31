"""Responses for a dataset creation refused by the plan's dataset limit."""

from typing import Protocol

from rest_framework import status
from rest_framework.response import Response

from model_hub.utils import dataset_limit
from tfc.constants.api_calls import APICallStatusChoices
from tfc.utils.api_errors import ApiErrorCode
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods

_gm = GeneralMethods()


class DatasetAddUsageEntry(Protocol):
    """The DATASET_ADD usage entry a create path gets from the usage log."""

    status: str


class DatasetLimitCheckFailed(Exception):
    """The plan's dataset limit could not be verified, so nothing was created."""


class DatasetLimitReached(Exception):
    """The plan's dataset limit is reached, so nothing was created."""

    def __init__(self, limit: int = 0):
        super().__init__(f"dataset limit reached ({limit})")
        self.limit = limit


def dataset_limit_check_failed_response() -> Response:
    # 503: the refusal is transient, like the other retryable read failures.
    # The typed code lets the frontend show this message despite the 5xx.
    return _gm.custom_error_response(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        get_error_message("DATASET_LIMIT_CHECK_FAILED"),
        code=ApiErrorCode.DATASET_LIMIT_CHECK_FAILED,
    )


def dataset_add_refusal(
    call_log_row_entry: DatasetAddUsageEntry | None, sdk_source: bool = False
) -> Response | None:
    """Return the response refusing a DATASET_ADD usage entry, or None to proceed.

    No entry means the limit was never verified, so the user is asked to retry
    instead of being told to upgrade. A reached limit refuses only a creation
    the limit binds (``dataset_limit_binds``: not SDK uploads).
    """
    if call_log_row_entry is None:
        return dataset_limit_check_failed_response()
    if (
        call_log_row_entry.status == APICallStatusChoices.RESOURCE_LIMIT.value
        and dataset_limit.dataset_limit_binds(sdk_source)
    ):
        return dataset_limit_reached_response()
    return None


def dataset_limit_reached_response() -> Response:
    # 429: the frontend shows it through the upgrade alert the caller sends
    # (the usage entry, or send_dataset_limit_alert), not as a toast.
    return _gm.too_many_requests(get_error_message("DATASET_CREATE_LIMIT_REACHED"))
