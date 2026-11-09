"""Bounded recovery for evaluation metadata reads, never evaluation side effects."""

import time
from collections.abc import Callable, Iterable
from typing import TypeVar
from uuid import UUID

from django.db import DEFAULT_DB_ALIAS, InterfaceError, OperationalError, connections
from psycopg.errors import ConnectionTimeout

from accounts.models.workspace import Workspace
from model_hub.models.develop_dataset import Column, Row

_T = TypeVar("_T")
_RETRY_DELAYS = (0.25, 0.5)


class EvaluationMetadataUnavailable(RuntimeError):
    """No evaluator or billing call started; metadata could not be loaded."""

    code = "EVALUATION_METADATA_UNAVAILABLE"

    def __init__(self):
        super().__init__(
            "Evaluation not started: database temporarily unavailable while loading "
            "evaluation metadata. Please retry the evaluation."
        )


def _is_connection_failure(error: Exception) -> bool:
    # Django wraps the driver's exception. Do not retry all OperationalErrors:
    # statement timeouts, invalid SQL and configuration failures are not outages.
    cause = error.__cause__
    if isinstance(cause, ConnectionTimeout):
        return True
    sqlstate = getattr(cause, "sqlstate", None)
    return bool(
        sqlstate
        and (
            sqlstate.startswith("08")
            or sqlstate
            in {
                "57P01",
                "57P02",
                "57P03",
            }
        )
    )


def _read_metadata(read: Callable[[], _T], *, using: str) -> _T:
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            return read()
        except (OperationalError, InterfaceError) as error:
            if not _is_connection_failure(error):
                raise
            connection = connections[using]
            # A caller-owned transaction cannot be repaired here. Closing it or
            # retrying queries inside it would leave the caller's work unusable.
            if connection.in_atomic_block:
                raise EvaluationMetadataUnavailable() from error
            connection.close()
            if attempt == len(_RETRY_DELAYS):
                raise EvaluationMetadataUnavailable() from error
            time.sleep(_RETRY_DELAYS[attempt])
    raise AssertionError("Unreachable metadata retry state")


def read_evaluation_column_types(column_ids: Iterable[str | UUID]) -> dict[str, str]:
    columns = Column.objects.filter(id__in=list(column_ids))
    # Keep routing on the queryset, not its iterable values. Each attempt builds
    # and materializes a fresh values query inside the retry boundary.
    return _read_metadata(
        lambda: {
            str(col["id"]): col["data_type"]
            for col in columns.values("id", "data_type")
        },
        using=columns.db,
    )


def read_evaluation_workspace(row: Row) -> Workspace | None:
    return _read_metadata(
        lambda: row.dataset.workspace,
        using=row._state.db or DEFAULT_DB_ALIAS,
    )
