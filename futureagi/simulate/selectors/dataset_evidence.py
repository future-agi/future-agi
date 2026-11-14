"""What a dataset holds now for the rows a person picked, read as evidence the
way Observe's is: live on every view, so a row deleted later reads as
unavailable and an edited one as changed. Nothing is copied out of the dataset.

A row's conversation is one column, read as a transcript is (JSON messages or
"User:" / "Agent:" text). Its label columns become labels, and its expected
outcome column says what should happen.
"""

from __future__ import annotations

from dataclasses import replace

from django.db.models import Case, TextField, When
from django.db.models.functions import Left, Length

from model_hub.models.choices import DataTypeChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from simulate.pydantic_schemas.evidence_import import DatasetColumns
from simulate.utils.transcript_parse import (
    MAX_TRANSCRIPT_CHARS,
    Format,
    ParsedTranscript,
    TranscriptError,
    parse_transcript,
)
from tracer.selectors.observe_evidence import (
    MAX_TURNS,
    TURN_CHARS,
    Label,
    SourceEvidence,
    head_and_tail,
    recorded_turn,
)

# Columns whose cells hold media, not text: a row whose conversation is only
# audio is out of scope.
MEDIA_TYPES = frozenset(
    {
        DataTypeChoices.AUDIO.value,
        DataTypeChoices.IMAGE.value,
        DataTypeChoices.IMAGES.value,
        DataTypeChoices.DOCUMENT.value,
    }
)
# Label columns one import reads.
MAX_LABEL_COLUMNS = 10


def text_evidence(source_id: str, parsed: ParsedTranscript) -> SourceEvidence:
    """A conversation read from text, as Observe evidence: each turn cut to
    TURN_CHARS, and MAX_TURNS turns kept, the first and last halves."""
    turns = [
        turn
        for one in parsed.turns
        if (turn := recorded_turn(one.role, one.text, "", None)) is not None
    ]
    return SourceEvidence(
        source_id=source_id,
        trace_ids=(),
        started_at=None,
        ended_at=None,
        turns=head_and_tail(turns, MAX_TURNS),
        turns_truncated=len(turns) > MAX_TURNS,
        turns_omitted=max(0, len(turns) - MAX_TURNS),
        transcript_unreadable=False,
        tool_calls=(),
        tool_calls_truncated=False,
        labels=(),
    )


def dataset_columns(dataset: Dataset, ids) -> dict[str, Column]:
    """The dataset's live columns among ``ids``, by id."""
    return {
        str(column.id): column
        for column in Column.no_workspace_objects.filter(
            dataset=dataset, id__in=list(ids)
        )
    }


def dataset_evidence(
    dataset: Dataset, columns: DatasetColumns, row_ids: list[str]
) -> dict[str, SourceEvidence]:
    """What ``dataset`` holds now for each of ``row_ids`` still in it, read
    through ``columns``. Nothing, once the dataset is deleted."""
    if dataset.deleted:
        return {}
    wanted = [columns.conversation, *columns.labels]
    if columns.expected is not None:
        wanted.append(columns.expected)
    live = dataset_columns(dataset, wanted)
    rows = list(
        Row.no_workspace_objects.filter(dataset=dataset, id__in=row_ids).values_list(
            "id", flat=True
        )
    )
    # Each cell's length, and only as much of it as is ever read: the
    # conversation up to a transcript's limit, every other cell up to a turn.
    values: dict[tuple[str, str], tuple[str, int]] = {
        (str(row_id), str(column_id)): (head or "", size or 0)
        for row_id, column_id, head, size in Cell.no_workspace_objects.filter(
            row_id__in=rows, column_id__in=list(live)
        )
        .annotate(
            size=Length("value"),
            head=Case(
                When(
                    column_id=columns.conversation,
                    then=Left("value", MAX_TRANSCRIPT_CHARS),
                ),
                default=Left("value", TURN_CHARS),
                output_field=TextField(),
            ),
        )
        .values_list("row_id", "column_id", "head", "size")
    }
    conversation = str(columns.conversation)
    labels = [live[str(one)] for one in columns.labels if str(one) in live]
    expected = str(columns.expected) if columns.expected is not None else None
    found: dict[str, SourceEvidence] = {}
    for row_id in map(str, rows):
        evidence = _conversation(
            row_id,
            values.get((row_id, conversation), ("", 0))
            if conversation in live
            else None,
        )
        picked = [
            (column, values[(row_id, str(column.id))])
            for column in labels
            if values.get((row_id, str(column.id)), ("", 0))[0]
        ]
        outcome = (
            values.get((row_id, expected), ("", 0))
            if expected is not None and expected in live
            else None
        )
        found[row_id] = replace(
            evidence,
            labels=tuple(
                Label(
                    name=column.name,
                    kind=column.data_type,
                    value=head,
                    notes="",
                    target="row",
                )
                for column, (head, _) in picked
            ),
            labels_truncated=any(size > TURN_CHARS for _, (_, size) in picked),
            expected=None if outcome is None else outcome[0],
            expected_truncated=outcome is not None and outcome[1] > TURN_CHARS,
        )
    return found


def _conversation(row_id: str, cell: tuple[str, int] | None) -> SourceEvidence:
    """A row's conversation cell (its text and length) read as a transcript;
    unreadable when it is neither JSON messages nor "User:" / "Agent:" text or
    is longer than a transcript may be, and empty when its column is gone."""
    empty = SourceEvidence(
        source_id=row_id,
        trace_ids=(),
        started_at=None,
        ended_at=None,
        turns=(),
        turns_truncated=False,
        turns_omitted=0,
        transcript_unreadable=False,
        tool_calls=(),
        tool_calls_truncated=False,
        labels=(),
    )
    if cell is None or not cell[0].strip():
        return empty
    text, size = cell
    if size > MAX_TRANSCRIPT_CHARS:
        return replace(empty, transcript_unreadable=True)
    form: Format = "json" if text.lstrip()[:1] in ("[", "{") else "text"
    try:
        return text_evidence(row_id, parse_transcript(text, form))
    except TranscriptError:
        return replace(empty, transcript_unreadable=True)
