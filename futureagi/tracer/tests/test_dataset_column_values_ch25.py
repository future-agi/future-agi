"""Real-ClickHouse proof for the dataset-column value readers.

The table below is the shape PeerDB creates for the ``model_hub_cell`` mirror
(dev, 2026-09-25): ``ORDER BY id``, so a column's cells are scattered across the
whole table. FINAL never moves a non-key WHERE predicate to PREWHERE, so a
reader that scopes (dataset_id, column_id) only in WHERE reads and merges every
cell's value. On dev that was 14.3M rows / 19.5 GB and 22-24 s for a 12-cell
column. ``index_granularity`` is lowered only to keep this fixture small.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from conftest import _ch_test_native_client, _ch_test_owned_database

pytestmark = pytest.mark.integration

DATASET = "00000000-0000-4000-8000-000000000701"
COLUMN = "00000000-0000-4000-8000-000000000702"
OTHER_DATASET = "00000000-0000-4000-8000-000000000703"
OTHER_COLUMN = "00000000-0000-4000-8000-000000000704"
NOISE_CELLS = 65_536
NOISE_VALUE_BYTES = 512


@pytest.fixture(scope="module")
def ch_database():
    with _ch_test_owned_database("test_dataset_column_values_") as database:
        yield database


@pytest.fixture(scope="module")
def ch_client(ch_database):
    with _ch_test_native_client(database=ch_database) as client:
        yield client


@pytest.fixture()
def cell_table(ch_client):
    ch_client.execute(
        """
        CREATE TABLE model_hub_cell (
            created_at DateTime64(6),
            updated_at DateTime64(6),
            deleted Bool,
            deleted_at DateTime64(6),
            id UUID,
            value String,
            column_id UUID,
            dataset_id UUID,
            row_id UUID,
            status String,
            value_infos String,
            feedback_info String,
            column_metadata String,
            completion_tokens Int32,
            prompt_tokens Int32,
            response_time Float64,
            _peerdb_synced_at DateTime64(9) DEFAULT now64(),
            _peerdb_is_deleted UInt8,
            _peerdb_version UInt64
        ) ENGINE = ReplacingMergeTree(_peerdb_version, _peerdb_is_deleted)
        PRIMARY KEY id
        ORDER BY id
        SETTINGS index_granularity = 256
        """
    )
    try:
        yield
    finally:
        ch_client.execute("DROP TABLE model_hub_cell SYNC")


def _seed(ch_client, render):
    """Twelve target cells among fat noise; three get a later CDC version."""

    ch_client.execute(
        "INSERT INTO model_hub_cell "
        "(id, value, column_id, dataset_id, row_id, value_infos, _peerdb_version) "
        "SELECT generateUUIDv4(number), "
        f"randomPrintableASCII({NOISE_VALUE_BYTES}), "
        "toUUID(%(column)s), toUUID(%(dataset)s), generateUUIDv4(number), '{}', 1 "
        f"FROM numbers({NOISE_CELLS})",
        {"column": OTHER_COLUMN, "dataset": OTHER_DATASET},
    )
    ids = [str(uuid.uuid4()) for _ in range(12)]
    rows = [
        (cell_id, render(f"target-{index:02d}"), 1, 0)
        for index, cell_id in enumerate(ids)
    ]
    ch_client.execute(
        "INSERT INTO model_hub_cell "
        "(id, value, column_id, dataset_id, row_id, value_infos, "
        "_peerdb_version, _peerdb_is_deleted) VALUES",
        [
            (
                uuid.UUID(cell_id),
                value,
                uuid.UUID(COLUMN),
                uuid.UUID(DATASET),
                uuid.uuid4(),
                "{}",
                version,
                is_deleted,
            )
            for cell_id, value, version, is_deleted in rows
        ],
    )
    # Later versions land in their own part, so FINAL has to merge them.
    updates = [
        (ids[0], render("target-00-edited"), 2, 0),
        (ids[1], render("target-01"), 2, 1),
        (ids[2], "", 2, 0),
    ]
    ch_client.execute(
        "INSERT INTO model_hub_cell "
        "(id, value, column_id, dataset_id, row_id, value_infos, "
        "_peerdb_version, _peerdb_is_deleted) VALUES",
        [
            (
                uuid.UUID(cell_id),
                value,
                uuid.UUID(COLUMN),
                uuid.UUID(DATASET),
                uuid.uuid4(),
                "{}",
                version,
                is_deleted,
            )
            for cell_id, value, version, is_deleted in updates
        ],
    )
    return ["target-00-edited"] + [f"target-{index:02d}" for index in range(3, 12)]


class _NativeAnalytics:
    """Run the reader's statement on the test server and keep its read cost."""

    def __init__(self, client):
        self.client = client
        self.read_bytes = None

    def __call__(self):
        return self

    def execute_ch_query(self, sql, params, *, timeout_ms, settings):
        rows, columns = self.client.execute(
            sql,
            params,
            with_column_types=True,
            settings={
                **settings,
                "max_execution_time": max(1, timeout_ms // 1000),
                # Pin the server default the dev mirror runs with.
                "optimize_move_to_prewhere_if_final": 0,
            },
        )
        self.read_bytes = self.client.last_query.progress.bytes
        names = [name for name, _type in columns]
        return NS(data=[dict(zip(names, row, strict=True)) for row in rows])


@pytest.mark.parametrize(
    "data_type,source,render",
    [
        ("text", "OTHERS", lambda label: label),
        ("array", "evaluation", lambda label: repr([label])),
    ],
    ids=["text", "evaluation-choice"],
)
def test_filter_values_reads_only_the_column_and_keeps_latest_state(
    ch_client, cell_table, data_type, source, render
):
    from tracer.services.clickhouse.read_budget import ReadDeadline
    from tracer.views.dashboard import DashboardViewSet

    expected = _seed(ch_client, render)
    analytics = _NativeAnalytics(ch_client)
    column = NS(data_type=data_type, source=source)
    with (
        patch("tracer.views.dashboard.AnalyticsQueryService", analytics),
        patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True),
        patch(
            "tracer.views.dashboard._run_filter_value_pg_read",
            side_effect=lambda deadline, read: read(),
        ),
        patch("model_hub.models.develop_dataset.Column.objects") as columns,
    ):
        columns.select_related.return_value.get.return_value = column
        response = DashboardViewSet()._filter_values_dataset_column(
            NS(workspace=NS(organization_id="owned-org")),
            dataset_id=DATASET,
            column_id=COLUMN,
            query_params={"search": ""},
            deadline=ReadDeadline.start(20_000),
        )

    assert response.status_code == 200
    assert [option["value"] for option in response.data["result"]["values"]] == (
        expected
    )
    assert analytics.read_bytes < NOISE_CELLS * NOISE_VALUE_BYTES // 4


def test_ai_filter_grounding_reads_only_the_column_and_keeps_latest_state(
    ch_client, cell_table
):
    from model_hub.views import ai_filter

    expected = _seed(ch_client, lambda label: label)
    analytics = _NativeAnalytics(ch_client)
    with (
        patch(
            "tracer.services.clickhouse.query_service.AnalyticsQueryService",
            analytics,
        ),
        patch(
            "tracer.services.clickhouse.client.is_clickhouse_enabled",
            return_value=True,
        ),
        patch("model_hub.models.develop_dataset.Column.objects") as columns,
    ):
        columns.only.return_value.get.return_value = NS(data_type="text")
        values = ai_filter._fetch_dataset_column_values(
            DATASET, COLUMN, search_query="target"
        )

    assert values == expected
    assert analytics.read_bytes < NOISE_CELLS * NOISE_VALUE_BYTES // 4
