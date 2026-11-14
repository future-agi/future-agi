"""What an evidence import stores beyond its sources' ids. A transcript has no
source to read again, so its redacted text, cut to the import's size, is kept
until the import is deleted. Nothing unredacted is stored. A dataset import
keeps only which columns to read."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

# What an import reads: Observe sessions or calls, transcripts or dataset rows.
ImportKind = Literal["session", "call", "transcript", "dataset"]
# How redaction found what it replaced: Presidio, or the basic patterns without it.
RedactionMethod = Literal["presidio", "basic"]


class StoredTurn(BaseModel):
    role: Literal["user", "agent"]
    text: str
    truncated: bool


class StoredTranscript(BaseModel):
    """One case's transcript (``EvidenceImportCase.transcript``)."""

    # The name its importer gave it, redacted.
    name: str
    turns: list[StoredTurn]
    turns_truncated: bool
    # Middle turns left out to fit; the first and last are kept.
    turns_omitted: int = Field(ge=0)
    # System and tool messages left out: only what the user and the agent said is kept.
    left_out: int = Field(ge=0)


class StoredRedaction(BaseModel):
    """What redaction replaced when the transcripts were stored
    (``EvidenceImport.redaction``)."""

    method: RedactionMethod
    # Why the method could not find everything; blank for Presidio.
    reason: str
    total: int = Field(ge=0)


class DatasetColumns(BaseModel):
    """Which of a dataset's columns its rows are read through
    (``EvidenceImport.dataset_columns``): ids, since names can be edited."""

    conversation: UUID
    labels: list[UUID]
    expected: UUID | None
