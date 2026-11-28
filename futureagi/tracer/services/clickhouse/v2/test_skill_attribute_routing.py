"""Skill span wire-routing contracts; run on the cloud test host."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tracer.services.clickhouse.v2.adapter import CH_INSERT_COLUMNS, adapt, row_to_tuple

VERSION = "11111111-1111-4111-8111-111111111111"
SECOND_VERSION = "22222222-2222-4222-8222-222222222222"


def _span(attributes, **overrides):
    row = {
        "project_id": str(uuid4()),
        "trace_id": str(uuid4()),
        "id": "0123456789abcdef",
        "name": "skill.load",
        "start_time": datetime(2026, 11, 1, tzinfo=UTC),
        "span_attributes": attributes,
        **overrides,
    }
    return adapt(row)


@pytest.mark.parametrize("cached,root_file", [(True, False), (False, True)])
@pytest.mark.parametrize("encoded", [False, True])
def test_skill_attributes_keep_types_and_flat_dotted_array_keys(
    cached, root_file, encoded
):
    attributes = {
        "fi.skill.version_id": VERSION,
        "fi.skill.version": 3,
        "fi.skill.requested_version": 3,
        "fi.skill.load.cached": cached,
        "fi.skill.load.root_file": root_file,
        "fi.skills.version_ids": [VERSION, SECOND_VERSION],
        "fi.skills.ids": [VERSION],
        "fi.skills.refs": ["policy@v3"],
        "fi.skills.count": 2,
        "fi.skills.truncated": False,
        "fi.context.linked_skill_version_ids": [SECOND_VERSION],
    }
    row = _span(json.dumps(attributes) if encoded else attributes)
    assert row.attrs_string == {"fi.skill.version_id": VERSION}
    assert row.attrs_number == {
        "fi.skill.version": 3.0,
        "fi.skill.requested_version": 3.0,
        "fi.skills.count": 2.0,
    }
    assert row.attrs_bool == {
        "fi.skill.load.cached": int(cached),
        "fi.skill.load.root_file": int(root_file),
        "fi.skills.truncated": 0,
    }
    overflow = json.loads(row.attributes_extra)
    for key in (
        "fi.skills.version_ids",
        "fi.skills.ids",
        "fi.skills.refs",
        "fi.context.linked_skill_version_ids",
    ):
        assert overflow[key] == attributes[key]
        assert key not in row.attrs_string
        assert key not in row.attrs_number
        assert key not in row.attrs_bool
    assert "fi" not in overflow
    wire = dict(zip(CH_INSERT_COLUMNS, row_to_tuple(row), strict=True))
    assert isinstance(wire["attributes_extra"], str)
    assert json.loads(wire["attributes_extra"])["fi.skills.version_ids"] == [
        VERSION,
        SECOND_VERSION,
    ]
    assert "skill_version_ids" not in wire  # Scope stays in attributes_extra only.


def test_failed_load_keeps_scope_without_fabricating_verified_identity():
    attrs = {
        "fi.skill.name": "policy",
        "fi.skill.requested_label": "production",
        "fi.skill.load.via": "sdk.read_file",
        "error.type": "integrity",
        "gen_ai.agent.name": "reviewer",
        "fi.context.id": VERSION,
        "fi.context.version_id": SECOND_VERSION,
    }
    row = _span(attrs, status="ERROR")
    assert row.status == "ERROR"
    assert row.attrs_string == attrs
    for key in ("fi.skill.id", "fi.skill.version_id", "fi.skill.digest"):
        assert key not in row.attrs_string


def test_adapter_tombstone_retains_full_load_row():
    attrs = {"fi.skill.version_id": VERSION, "fi.skill.name": "policy"}
    row = _span(attrs, deleted=True)
    wire = dict(zip(CH_INSERT_COLUMNS, row_to_tuple(row), strict=True))
    assert wire["is_deleted"] == 1
    assert wire["name"] == "skill.load"
    assert wire["attrs_string"] == attrs
    assert wire["start_time"] == datetime(2026, 11, 1, tzinfo=UTC)
