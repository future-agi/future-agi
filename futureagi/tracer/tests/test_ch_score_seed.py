"""Unit guards for the disposable ClickHouse Score seeder."""

import uuid
from types import SimpleNamespace

import pytest

from tracer.tests._ch_seed import _SCORE_INSERT_COLUMNS, _score_row_from_django


def _score(*, source_project_id, tracer_project_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        source_type="OBSERVATION_SPAN",
        trace_id=uuid.uuid4(),
        observation_span_id="span-1",
        trace_session_id=None,
        call_execution_id=None,
        dataset_row_id=None,
        prototype_run_id=None,
        queue_item_id=None,
        # DevelopAI has a different id space and must never become the tracer
        # tenant fence merely because it is populated.
        project_id=uuid.uuid4(),
        tracer_project_id=tracer_project_id,
        trace=None,
        observation_span=SimpleNamespace(project_id=source_project_id),
        trace_session=None,
        call_execution=None,
        label_id=uuid.uuid4(),
        value={"text": "approved"},
        annotator_id=None,
        score_source="HUMAN",
        notes=None,
        organization_id=uuid.uuid4(),
        workspace_id=None,
        deleted=False,
        deleted_at=None,
        created_at=None,
        updated_at=None,
    )


@pytest.mark.unit
def test_score_seed_derives_tracer_project_from_tracer_source():
    source_project_id = uuid.uuid4()

    row = _score_row_from_django(_score(source_project_id=source_project_id))

    assert row[_SCORE_INSERT_COLUMNS.index("tracer_project_id")] == str(
        source_project_id
    )


@pytest.mark.unit
def test_score_seed_preserves_explicit_tracer_project_id():
    source_project_id = uuid.uuid4()
    explicit_project_id = uuid.uuid4()

    row = _score_row_from_django(
        _score(
            source_project_id=source_project_id,
            tracer_project_id=explicit_project_id,
        )
    )

    assert row[_SCORE_INSERT_COLUMNS.index("tracer_project_id")] == str(
        explicit_project_id
    )
