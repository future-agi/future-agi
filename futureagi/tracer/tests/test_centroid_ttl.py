"""Behavioral regression test for #306 / PR #339.

cluster_centroids rows were never removed: high-error-rate projects accumulated
centroids indefinitely, growing HDBSCAN input until the clustering worker OOM'd or
timed out. The fix declares a row-level ClickHouse TTL (`TTL last_updated + INTERVAL N
DAY DELETE`) on table creation, adds expire_stale_centroids() to retrofit the TTL onto
pre-existing tables via ALTER TABLE MODIFY TTL (idempotent, metadata-only), and makes N
configurable (ErrorClusteringDB(centroid_ttl_days=...), default 90).

Unit half (CI's `-m unit` lane): captures the SQL the real ErrorClusteringDB emits
through ClickHouseVectorDB and asserts the TTL clause is present, configurable, and
retrofitted -- and that a failed ALTER stays non-fatal. Fails if the TTL is unwired
from either the CREATE or the ALTER path.

Integration half (repo convention for ClickHouse, `-m integration`, skipped when no
server is reachable): asserts against ClickHouse's OWN catalog that the created table
carries the TTL, and that a stale row is actually deleted by TTL materialization while
a fresh row survives -- the end-to-end behavior #306 asked for.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from tracer.queries.error_clustering import _DEFAULT_CENTROID_TTL_DAYS, ErrorClusteringDB


# --------------------------------------------------------------------------- unit
def _capture_executed_sql(fn):
    """Run fn with ClickHouseVectorDB stubbed in error_clustering's namespace; return
    every SQL string executed through the stub's client."""
    executed = []
    stub_db = MagicMock()
    stub_db.client.execute.side_effect = lambda sql, *a, **k: executed.append(sql)
    with patch("tracer.queries.error_clustering.ClickHouseVectorDB", return_value=stub_db):
        fn()
    return executed


@pytest.mark.unit
def test_centroid_table_ddl_carries_default_ttl():
    executed = _capture_executed_sql(lambda: ErrorClusteringDB().ensure_centroid_table())
    create = [s for s in executed if "CREATE TABLE" in s and "cluster_centroids" in s]
    assert create, f"no cluster_centroids CREATE TABLE executed: {executed!r}"
    assert (
        f"TTL last_updated + INTERVAL {_DEFAULT_CENTROID_TTL_DAYS} DAY DELETE" in create[0]
    ), "centroid table DDL lost its TTL clause -- centroids would accumulate forever (#306)"


@pytest.mark.unit
def test_centroid_ttl_days_is_configurable():
    executed = _capture_executed_sql(
        lambda: ErrorClusteringDB(centroid_ttl_days=7).ensure_centroid_table()
    )
    assert any("TTL last_updated + INTERVAL 7 DAY DELETE" in s for s in executed)


@pytest.mark.unit
def test_expire_stale_centroids_retrofits_ttl_via_alter():
    # Tables created before the fix have no TTL; each run must retrofit it.
    executed = _capture_executed_sql(
        lambda: ErrorClusteringDB(centroid_ttl_days=14).expire_stale_centroids()
    )
    alters = [s for s in executed if "ALTER TABLE cluster_centroids MODIFY TTL" in s]
    assert alters, f"expire_stale_centroids executed no ALTER: {executed!r}"
    assert "last_updated + INTERVAL 14 DAY DELETE" in alters[0]


@pytest.mark.unit
def test_expire_stale_centroids_failure_is_nonfatal():
    # First run: the table may not exist yet -- the retrofit must warn, not raise.
    stub_db = MagicMock()
    stub_db.client.execute.side_effect = RuntimeError("Table doesn't exist")
    with patch("tracer.queries.error_clustering.ClickHouseVectorDB", return_value=stub_db):
        ErrorClusteringDB().expire_stale_centroids()  # must not raise
    stub_db.close.assert_called_once()


# --------------------------------------------------------------------- integration
@pytest.fixture()
def ch_db():
    """A real ClickHouseVectorDB, or skip when no server is reachable (repo convention)."""
    from agentic_eval.core.database.ch_vector import ClickHouseVectorDB

    try:
        db = ClickHouseVectorDB()
        db.client.execute("SELECT 1")
    except Exception:
        pytest.skip("ClickHouse not available for integration tests")
    yield db
    db.close()


@pytest.mark.integration
def test_created_table_carries_ttl_in_clickhouse_catalog(ch_db):
    ch_db.client.execute("DROP TABLE IF EXISTS cluster_centroids")
    ErrorClusteringDB().ensure_centroid_table()
    engine_full = ch_db.client.execute(
        "SELECT engine_full FROM system.tables WHERE name = 'cluster_centroids'"
    )[0][0]
    # ClickHouse normalizes INTERVAL 90 DAY to toIntervalDay(90) in its catalog.
    assert "TTL" in engine_full and (
        "toIntervalDay(90)" in engine_full or "INTERVAL 90 DAY" in engine_full
    ), f"table exists without the TTL: {engine_full!r}"


@pytest.mark.integration
def test_stale_centroid_is_expired_and_fresh_survives(ch_db):
    import uuid

    ch_db.client.execute("DROP TABLE IF EXISTS cluster_centroids")
    ErrorClusteringDB().ensure_centroid_table()
    project = uuid.uuid4()
    stale = (datetime.utcnow() - timedelta(days=365)).replace(microsecond=0)
    fresh = datetime.utcnow().replace(microsecond=0)
    ch_db.client.execute(
        "INSERT INTO cluster_centroids "
        "(cluster_id, project_id, centroid, member_count, family, last_updated) VALUES",
        [
            ("stale-cluster", project, [0.0] * 8, 1, "test", stale),
            ("fresh-cluster", project, [1.0] * 8, 1, "test", fresh),
        ],
    )
    # Force TTL materialization now instead of waiting for a background merge.
    ch_db.client.execute("OPTIMIZE TABLE cluster_centroids FINAL")
    rows = ch_db.client.execute(
        "SELECT cluster_id FROM cluster_centroids WHERE project_id = %(p)s",
        {"p": project},
    )
    ids = {r[0] for r in rows}
    assert "stale-cluster" not in ids, "a year-old centroid survived the TTL (#306)"
    assert "fresh-cluster" in ids, "TTL must not expire active centroids"
