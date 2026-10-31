"""The plan's dataset-limit check: its outcome, and who the limit binds.

``check_if_dataset_creation_is_allowed`` (EE usage entries) answers with a
``DatasetLimitCheck``. Every dataset-create path branches on its outcome, and
asks ``dataset_limit_binds`` whether a refusal applies to the caller. Both live
here, outside ``ee``, so the open-source views can branch on them too.
"""

from dataclasses import dataclass
from enum import StrEnum


class DatasetLimitOutcome(StrEnum):
    # The plan allows another dataset.
    ALLOWED = "allowed"
    # The plan's dataset limit is reached.
    LIMIT_REACHED = "limit_reached"
    # The limit could not be checked (the entitlement check errored on cloud).
    # Not a reached limit: the caller is asked to retry, never to upgrade.
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class DatasetLimitCheck:
    outcome: DatasetLimitOutcome
    # The plan's limit when it is reached; 0 when the plan reports none.
    limit: int = 0


def dataset_limit_binds(sdk_source: bool) -> bool:
    """Whether a reached or unverified dataset limit refuses this creation.

    SDK uploads are not held to the dataset limit. This is the one place that
    says so: the usage-entry dispatcher (unverified limit) and the refusal
    helper (reached limit) both ask here.
    """
    return not sdk_source
