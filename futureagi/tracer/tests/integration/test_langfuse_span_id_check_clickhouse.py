"""The Langfuse upsert's span id check, run against the test ClickHouse.

The other upsert tests answer the check with the conftest stand-in. This one
writes a span id of its own into the test ClickHouse ``spans`` table, held by
another organization's project, and lets the upsert run its real statement
through the v2 read client.
"""

import uuid
from datetime import UTC, datetime

import pytest

from tracer.models.observation_span import ObservationSpan
from tracer.services.clickhouse.v2.query_service import get_v2_query_client
from tracer.tests import _ch_seed
from tracer.tests.integration.test_langfuse_upsert_project_scope import (
    _observation,
    _organization,
    _refused,
    _trace,
    _upsert,
)
from tracer.utils import langfuse_upsert

pytestmark = pytest.mark.django_db


def _hold_in_clickhouse(span_id, project_id):
    """Write one span row with ``span_id`` into ClickHouse under ``project_id``."""
    client = _ch_seed._get_ch_client()
    try:
        client.insert(
            "spans",
            [
                [
                    uuid.UUID(str(project_id)),
                    "llm",
                    datetime.now(UTC),
                    # The table reads trace_id as a UUID (a materialized
                    # trace-name column looks it up), so it must be one.
                    str(uuid.uuid4()),
                    span_id,
                    "held",
                ]
            ],
            column_names=[
                "project_id",
                "observation_type",
                "start_time",
                "trace_id",
                "id",
                "name",
            ],
        )
    finally:
        client.close()


def test_the_check_refuses_an_id_another_project_holds_in_clickhouse(
    monkeypatch,
):
    owner = _organization("chcheck-owner")
    sender = _organization("chcheck-sender")
    held = f"lfch-held-{uuid.uuid4().hex}"
    own = f"lfch-own-{uuid.uuid4().hex}"
    fresh = f"lfch-fresh-{uuid.uuid4().hex}"
    # One id the owner holds only in ClickHouse, one the sender already holds
    # there in its own project, and one nobody holds.
    _hold_in_clickhouse(held, owner.project.id)
    _hold_in_clickhouse(own, sender.project.id)
    monkeypatch.setattr(langfuse_upsert, "get_v2_query_client", get_v2_query_client)

    trace_id = f"lfch-trace-{uuid.uuid4().hex}"
    result = _upsert(
        sender,
        _trace(
            trace_id,
            "real check",
            [
                _observation(held, "held"),
                _observation(own, "own"),
                _observation(fresh, "fresh"),
            ],
        ),
    )

    # The owner's id is refused and counted; no Postgres span takes it.
    refused = _refused(result)
    assert refused.observation_ids == (held,)
    assert not ObservationSpan._base_manager.filter(id=held).exists()
    # The sender's own id and the new id are stored in the sender's project.
    stored = set(
        ObservationSpan.no_workspace_objects.filter(
            project_id=sender.project.id, id__in=[own, fresh]
        ).values_list("id", flat=True)
    )
    assert stored == {own, fresh}
