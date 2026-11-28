"""Render every v2 ClickHouse schema file into statements for the integration job.

Stdlib only, like render_schema.py. Statements are split by the application's
own apply_schema_rewriter.split_statements, unrewritten (the integration
ClickHouse is a single node). Output: {"files": {"<file>.sql": [statement, ...]}}.
Usage: python3 render_v2_schema.py OUTPUT.json
"""

import importlib.util
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
v2 = root / "futureagi/tracer/services/clickhouse/v2"
spec = importlib.util.spec_from_file_location(
    "schema_rewriter", v2 / "apply_schema_rewriter.py"
)
rewriter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rewriter)

files = {
    path.name: rewriter.split_statements(path.read_text())
    for path in sorted((v2 / "schema").glob("*.sql"))
}
for required in ("002_spans_v2.sql", "040_trajectory_items.sql"):
    if not files.get(required):
        raise SystemExit(f"v2 schema file missing or empty: {required}")
if len(sys.argv) != 2:
    raise SystemExit("usage: render_v2_schema.py OUTPUT.json")
Path(sys.argv[1]).write_text(json.dumps({"files": files}))
print(f"Rendered {len(files)} v2 schema files")
