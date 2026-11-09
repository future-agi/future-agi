"""Connector lineage is bound to the real transport, never the destination name."""

import json
from copy import deepcopy

import pytest
import requests

from integrations.models import SyncStatus
from integrations.temporal.activities import sync_integration_connection
from integrations.transformers.langfuse_transformer import LangfuseTransformer
from tracer.models.observation_span import ObservationSpan


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("external_project", ["external-project-42", None])
def test_connector_retains_only_provider_project_identity(
    integration_connection, raw_langfuse_trace, monkeypatch, external_project
):
    trace = deepcopy(raw_langfuse_trace)
    if external_project is not None:
        trace["projectId"] = external_project
    else:
        trace.pop("projectId", None)
    trace["metadata"] = {"fi.external.langfuse": {"origin": "https://forged.test"}}
    for observation in trace["observations"]:
        observation["metadata"] = {
            "available_skills": ["refund"],
            "fi.external.langfuse": {"origin": "https://forged.test"},
        }
    calls = []

    def request(*, method, url, **kwargs):
        calls.append((method, url))
        root = integration_connection.host_url + "/api/public/traces"
        assert method == "GET"
        if url == root:
            body = {"data": [{"id": trace["id"]}], "meta": {"totalPages": 1}}
        else:
            assert url == root + "/" + trace["id"]
            body = trace
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(body).encode()
        return response

    monkeypatch.setattr("requests.request", request)
    sync_integration_connection(str(integration_connection.pk))
    assert integration_connection.sync_logs.get().status == SyncStatus.SUCCESS
    assert len(calls) == 2
    rows = ObservationSpan.no_workspace_objects.filter(
        project_id=integration_connection.project_id,
        id__in=[row["id"] for row in trace["observations"]],
    )
    assert rows.count() == len(trace["observations"])
    for row in rows:
        assert row.metadata["available_skills"] == ["refund"]
        if external_project is None:
            assert "fi.external.langfuse" not in row.metadata
        else:
            lineage = row.metadata["fi.external.langfuse"]
            assert lineage["project_id"] == external_project
            assert lineage["project_id"] != str(integration_connection.project_id)
            assert lineage["origin"] == integration_connection.host_url
            assert lineage["trace_id"] == trace["id"]
            assert lineage["observation_id"] == row.id
            assert lineage["trust"] == "connector_recorded"
            assert "forged" not in lineage["url"]


@pytest.mark.parametrize(
    "origin", ["http://private.example.test", "https://selfhost.example.test/langfuse"]
)
def test_unsupported_navigation_origin_keeps_trace_without_false_lineage(
    raw_langfuse_trace, origin
):
    rows = LangfuseTransformer().transform_observations(
        raw_langfuse_trace,
        "trace",
        "hub",
        source_origin=origin,
        source_project_id="external",
    )
    assert len(rows) == len(raw_langfuse_trace["observations"])
    for row in rows:
        assert "fi.external.langfuse" not in row["metadata"]
        assert (
            row["metadata"]["fi.external.langfuse.status"]
            == "unsupported_source_identity"
        )


def test_public_ingestion_cannot_assert_connector_origin(raw_langfuse_trace):
    raw = deepcopy(raw_langfuse_trace)
    raw["metadata"] = {"fi.external.langfuse": {"trust": "connector_recorded"}}
    for observation in raw["observations"]:
        observation["metadata"] = dict(raw["metadata"])
    transformer = LangfuseTransformer()
    assert (
        "fi.external.langfuse"
        not in transformer.transform_trace(raw, "hub")["metadata"]
    )
    for row in transformer.transform_observations(raw, "trace", "hub"):
        assert "fi.external.langfuse" not in row["metadata"]
