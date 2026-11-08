import json
from uuid import uuid4

import pytest

from tracer.utils.adapters.langfuse_skills import (
    external_trace_link,
    hub_runtime_metadata,
    normalize_langfuse_skills,
)


def test_availability_is_retained_never_synthesized_as_load():
    source = {
        "langfuse.project.id": "project-a",
        "langfuse.trace.id": "trace-a",
        "langfuse.observation.id": "observation-a",
        "langfuse.observation.metadata": json.dumps(
            {
                "available_skills": [{"name": "refund", "version": 4}],
                "private": "not retained",
            }
        ),
    }
    result = normalize_langfuse_skills(source)
    assert result["fi.external.langfuse.skills"]["available_skills"] == [
        {"name": "refund", "version": 4}
    ]
    assert "loaded_skills" not in json.dumps(result)
    assert "skill.load" not in json.dumps(result)
    assert "private" not in json.dumps(result)
    assert source["langfuse.trace.id"] == "trace-a"


def test_changed_hub_content_never_claims_old_external_version():
    resource, version = uuid4(), uuid4()
    result = hub_runtime_metadata(
        kind="agent_context",
        resource_id=resource,
        version_id=version,
        version=7,
        digest="sha256:" + "a" * 64,
        hub_origin="https://hub.example.test",
    )
    assert result["fi.context.version_id"] == str(version)
    assert result["fi.context.url"].endswith(f"/contexts/{resource}?version=7")
    assert not any("langfuse" in key for key in result)


def test_external_navigation_is_encoded_not_fetched():
    result = external_trace_link(
        origin="https://traces.example.test",
        project_id="private/project",
        trace_id="trace?query",
        observation_id="span#1",
    )
    assert (
        result
        == "https://traces.example.test/project/private%2Fproject/traces/trace%3Fquery?observation=span%231"
    )
    with pytest.raises(ValueError):
        external_trace_link(
            origin="https://name:secret@example.test",
            project_id="p",
            trace_id="t",
            observation_id="s",
        )


def test_metadata_bounds_and_flattened_fields():
    assert normalize_langfuse_skills({"langfuse.skills.available": "x" * 65537}) == {
        "fi.external.langfuse.skills_status": "too_large"
    }
    result = normalize_langfuse_skills(
        {
            "langfuse.observation.metadata.fi.context.version_id": "synthetic-id",
            "langfuse.observation.metadata.available_skills": [],
        }
    )
    assert result["fi.external.langfuse.skills"]["available_skills"] == []
    assert (
        result["fi.external.langfuse.skills"]["fi.context.version_id"] == "synthetic-id"
    )
