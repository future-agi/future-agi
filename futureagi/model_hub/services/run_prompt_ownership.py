"""Serialize prompt ownership changes and result writes on the prompt row.

The Redis lock limits execution concurrency. The database row lock ensures a
worker cannot publish a result after a successor has claimed its Redis token,
including when the old worker resumes after a long process pause.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from uuid import uuid4

from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from model_hub.models.choices import CellStatus, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Row
from model_hub.models.run_prompt import RunPrompter
from tfc.utils.distributed_state import RunningTaskInfo


class OwnershipLostError(Exception):
    """The attempt no longer owns the prompt; another attempt may retry it."""


class PromptSupersededError(Exception):
    """The prompt was edited, deleted or finished while this attempt ran."""


@contextmanager
def lock_prompt(prompt_id: str, *, nowait: bool = False) -> Iterator[RunPrompter]:
    """Lock the prompt before claiming, recovering or writing its results."""
    with transaction.atomic():
        prompt = (
            RunPrompter.objects.select_for_update(of=("self",), nowait=nowait)
            .filter(id=prompt_id)
            .first()
        )
        if prompt is None:
            raise PromptSupersededError(str(prompt_id))
        yield prompt


def queue_prompt_rows(
    prompt_ids: list[str],
    row_ids: list[str],
    *,
    request_id: str | None = None,
    scheduled_at: datetime | None = None,
) -> dict[str, str]:
    """Record each requested rerun before dispatch, sharing the write lock.

    The revision travels with the task so a retry neither resets completed
    cells again nor claims a later edit. Existing updated_at is the revision;
    execution and completion deliberately do not change it.
    """
    revisions = {}
    request_id = request_id or uuid4().hex
    with transaction.atomic():
        for prompt_id in sorted(set(map(str, prompt_ids))):
            with lock_prompt(prompt_id) as prompt:
                if scheduled_at is not None:
                    # Old Temporal payloads have no revision. Bind their stable
                    # activity ID once, without resetting cells on retries or
                    # adopting a newer user request.
                    if prompt.queued_request_id == request_id:
                        revisions[str(prompt.id)] = prompt.updated_at.isoformat()
                        continue
                    if prompt.updated_at > scheduled_at:
                        continue
                requested_rows = set(map(str, row_ids))
                if prompt.status == StatusType.RUNNING.value:
                    cells = Cell.objects.filter(
                        dataset_id=prompt.dataset_id,
                        row_id=OuterRef("id"),
                        column__source=SourceChoices.RUN_PROMPT.value,
                        column__source_id=str(prompt.id),
                        deleted=False,
                    )
                    unfinished = Row.objects.filter(
                        dataset_id=prompt.dataset_id, deleted=False
                    )
                    if prompt.queued_row_ids is not None:
                        unfinished = unfinished.filter(id__in=prompt.queued_row_ids)
                    unfinished = unfinished.filter(
                        Exists(
                            cells.filter(
                                status__in=[
                                    CellStatus.RUNNING.value,
                                    StatusType.RUNNING.value,
                                ]
                            )
                        )
                        | Exists(cells.filter(value__isnull=True))
                        | ~Exists(cells)
                    )
                    requested_rows.update(
                        map(str, unfinished.values_list("id", flat=True))
                    )
                prompt.queued_row_ids = sorted(requested_rows)
                prompt.queued_request_id = request_id
                prompt.status = StatusType.RUNNING.value
                prompt.save(
                    update_fields=[
                        "status",
                        "updated_at",
                        "queued_row_ids",
                        "queued_request_id",
                    ]
                )
                Cell.objects.filter(
                    dataset_id=prompt.dataset_id,
                    row_id__in=row_ids,
                    column__source=SourceChoices.RUN_PROMPT.value,
                    column__source_id=str(prompt.id),
                    deleted=False,
                ).update(
                    status=CellStatus.RUNNING.value,
                    value=None,
                    value_infos={},
                    updated_at=timezone.now(),
                )
                revisions[str(prompt.id)] = prompt.updated_at.isoformat()
    return revisions


@contextmanager
def guard_prompt_write(
    prompt_id: str,
    run_token: str,
    read_lease: Callable[[str], RunningTaskInfo | None],
    *,
    updated_at: datetime | None = None,
) -> Iterator[RunPrompter]:
    """Validate ownership inside the same transaction as the protected write.

    Claims and recovery take this row lock too. Redis read errors propagate: an
    unknown owner is never permission to write. A new configuration supersedes
    the old attempt even when its worker still holds the execution lock.
    """
    with lock_prompt(prompt_id) as prompt:
        lease = read_lease(prompt_id)
        if lease is None or (lease.metadata or {}).get("run_token") != run_token:
            raise OwnershipLostError(str(prompt_id))
        claimed_revision = (lease.metadata or {}).get("revision")
        if claimed_revision and prompt.updated_at != parse_datetime(claimed_revision):
            raise PromptSupersededError(str(prompt_id))
        if prompt.status != StatusType.RUNNING.value or (
            updated_at is not None and prompt.updated_at != updated_at
        ):
            raise PromptSupersededError(str(prompt_id))
        yield prompt
