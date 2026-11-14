"""Assignment and source shapes shared by tag readers and write preconditions."""

from datetime import datetime
from typing import Literal, NotRequired, TypedDict
from uuid import UUID


class TagItem(TypedDict):
    name: str
    color: NotRequired[str]


class TagSourceRow(TypedDict, total=False):
    """Raw telemetry JSON is parsed by the shared tag normalization boundary."""

    tags: object
    attrs_string: object
    attributes_extra: object
    span_attributes: object


class EffectiveTags(TypedDict):
    tags: list[TagItem]
    source_tag_names: list[str]


class TagTargetReference(TypedDict):
    """Validated mutation selector, never a project authorization credential."""

    project_id: str | UUID
    trace_id: str
    span_id: str
    start_hour: datetime
    observation_type: str
    service_name: str
    expected_start_time: datetime
    expected_version: str


class TagTargetRow(TagSourceRow):
    """Identity and liveness checked by the physical reader before use."""

    project_id: str
    trace_id: str
    id: str
    start_hour: datetime
    start_time: datetime
    observation_type: str
    service_name: str
    _version: str
    is_deleted: int
    parent_span_id: str | None


TagAssignmentKey = tuple[str, Literal["trace", "span"], str, str, datetime, str, str]


class TagUpdateRequest(TagTargetReference):
    tags: list[TagItem]
    expected_tag_revision: int


class TagUpdateResult(EffectiveTags):
    id: str
    manual_tags: list[TagItem]
    tag_revision: int
