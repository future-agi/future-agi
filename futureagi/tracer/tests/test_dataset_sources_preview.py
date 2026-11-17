"""Preview filters use scoped persisted telemetry and never write datasets."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.utils import timezone

from model_hub.models.choices import ModelTypes
from tracer.models.observation_span import EvalLogger
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.services.dataset_sources import preview_sources, validate_import_mapping


@pytest.mark.django_db
@pytest.mark.parametrize(
    "kind, expected",
    [("traces", ["root"]), ("sessions", ["root", "passing"]), ("spans", [])],
)
@pytest.mark.parametrize(
    "verdict, failed",
    [
        ({"output_bool": False}, True),
        ({"output_str": "False"}, True),
        ({"output_float": 0.2}, True),
        ({"output_bool": True, "output_float": 0.2}, False),
    ],
)
def test_failure_preview_scopes_candidates_and_filters_results(
    organization, workspace, kind, expected, verdict, failed
):
    project = Project.no_workspace_objects.create(
        name="preview",
        trace_type="observe",
        organization=organization,
        workspace=workspace,
        model_type=ModelTypes.GENERATIVE_LLM.value,
    )
    trace, passing = uuid4(), uuid4()
    Trace.no_workspace_objects.create(id=trace, project=project, name="failed trace")
    EvalLogger.no_workspace_objects.create(
        trace_id=trace,
        observation_span_id="failed-child",
        status="completed",
        **verdict,
    )
    now = timezone.now()
    reader = MagicMock()
    reader.__enter__.return_value = reader
    reader._client.query.return_value = SimpleNamespace(
        result_rows=[
            (
                "root",
                str(trace),
                "11111111-1111-4111-8111-111111111111",
                "failed trace",
                now,
            ),
            (
                "passing",
                str(passing),
                "11111111-1111-4111-8111-111111111111",
                "passing trace",
                now,
            ),
        ]
    )
    with patch("tracer.services.dataset_sources.get_reader", return_value=reader):
        result = preview_sources(project, {"source_kind": kind, "failures_only": True})
    assert [item["span_id"] for item in result["items"]] == (expected if failed else [])
    sql = reader._client.query.call_args.args[0]
    params = reader._client.query.call_args.kwargs["parameters"]
    assert "org_id = %(org)s" in sql
    assert "project_id = %(project)s" in sql
    assert "start_time >= %(since)s" in sql
    assert "LIMIT 201" in sql
    assert params["org"] == str(organization.id)
    assert (
        now - timedelta(days=7, seconds=1)
        < params["since"]
        <= timezone.now() - timedelta(days=7)
    )


@pytest.mark.parametrize(
    "mapping",
    [
        [{"col_name": "", "data_type": "text"}],
        [{"col_name": "a", "data_type": "invalid"}],
        [{"col_name": "input", "data_type": "text"}] * 2,
        [{"col_name": "input", "span_field": "unavailable", "data_type": "text"}],
    ],
)
def test_invalid_mapping_is_rejected(mapping):
    with pytest.raises(ValueError):
        validate_import_mapping(mapping)
