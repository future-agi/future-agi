"""
v2 query builders — target the CH 25.3 `spans` table with typed Maps + JSON.

These are line-for-line ports of the legacy `tracer/services/clickhouse/query_builders/`
with column-name updates and typed-JSON path access. The legacy builders stay
in place during cutover so the dispatch layer can run them in SHADOW mode
(both v1 + v2 execute; results compared; v1 returned to the user). Once a
query type has logged zero shadow diffs for 24-48h, the dispatch layer is
flipped to v2-primary for that type. After all types are flipped, v1 is
deleted.

Column-name + access mapping (cheat sheet — full mapping in `columns.py`):

    v1 (legacy spans table)        →  v2 (spans CH 25.3)
    ─────────────────────────────────────────────────────────────
    span_attr_str                  →  attrs_string
    span_attr_num                  →  attrs_number
    span_attr_bool                 →  attrs_bool
    span_attributes_raw (String)   →  attributes_extra (typed JSON, path access)
    resource_attributes_raw        →  resource_attrs (typed JSON)
    metadata_map (Map)             →  metadata (typed JSON)
    _peerdb_is_deleted             →  is_deleted
    _peerdb_version                →  _version

JSON path access difference (the biggest gotcha):

    v1:  JSONExtractString(span_attributes_raw, 'gen_ai.request.model')
    v2:  attributes_extra.gen_ai.request.model.:String

CH 25.x auto-flattens dotted keys at write time, so the path syntax mirrors
the natural OTel attribute namespace. Documented in DECISIONS #018.
"""
