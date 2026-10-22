"""Render test catalog DDL through the application's actual topology rewriter."""

import importlib.util
import json
from pathlib import Path

root = Path(__file__).resolve().parents[3]
v2 = root / "futureagi/tracer/services/clickhouse/v2"
spec = importlib.util.spec_from_file_location(
    "schema_rewriter", v2 / "apply_schema_rewriter.py"
)
rewriter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rewriter)
statements = rewriter.split_statements((v2 / "observed_catalog/schema.sql").read_text())
print(
    json.dumps(
        [
            rewriter.rewrite_for_replicated(
                statement,
                table_name=rewriter.extract_table_name(statement),
                cluster="observed_test_cluster",
                zk_prefix="/clickhouse/observed-test/{database}",
            )
            for statement in statements
        ]
    )
)
