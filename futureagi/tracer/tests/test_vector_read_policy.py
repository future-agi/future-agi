"""Contracts that vector workflow reads stay on their write-side client."""

from pathlib import Path

import pytest

_QUERIES_DIR = Path(__file__).resolve().parents[1] / "queries"


@pytest.mark.parametrize(
    ("module_name", "guarded_read_count"),
    [
        ("error_analysis.py", 1),
        ("error_clustering.py", 4),
        ("eval_clustering.py", 2),
        ("scan_clustering.py", 6),
    ],
)
def test_clustering_selects_use_the_workflow_vector_database_client(
    module_name, guarded_read_count
):
    source = (_QUERIES_DIR / module_name).read_text()

    assert source.count("db.execute_read(") == guarded_read_count
    assert "execute_vector_read" not in source
    assert "services.clickhouse.vector_reads" not in source
    assert "rows = db.client.execute(" not in source
    assert "result = db.client.execute(" not in source


def test_error_analysis_read_owns_and_closes_its_vector_client():
    source = (_QUERIES_DIR / "error_analysis.py").read_text()
    method = source.split("def _embedding_exists_for_detail(", 1)[1].split(
        "\n    def ", 1
    )[0]

    assert "db = ClickHouseVectorDB()" in method
    assert "result = db.execute_read(" in method
    assert "finally:" in method
    assert "db.close()" in method
