-- 022 — Bloom indexes on attribute map VALUES, for SPAN_ATTRIBUTE equality/IN
-- filters (key-only blooms don't prune when most spans carry the key).
--
-- idx_attrs_str_values indexes LOWERED values: text filters compare via
-- lower(), so it only engages alongside the companion predicate that
-- ClickHouseFilterBuilderV2._span_attr_inner emits — keep the two in step.
-- attrs_bool gets no value index ({0,1} never prunes).
--
-- ADD INDEX only covers parts written after it. Backfill existing parts once,
-- async, off the boot/request path — a MATERIALIZE here times out the 120s
-- boot applier and re-errors every boot (CORE-BACKEND-11KX):
--   ALTER TABLE spans MATERIALIZE INDEX idx_attrs_num_values;
--   ALTER TABLE spans MATERIALIZE INDEX idx_attrs_str_values;

ALTER TABLE spans
    ADD INDEX IF NOT EXISTS idx_attrs_str_values arrayMap(x -> lower(x), mapValues(attrs_string))
    TYPE bloom_filter(0.01) GRANULARITY 1;

ALTER TABLE spans
    ADD INDEX IF NOT EXISTS idx_attrs_num_values mapValues(attrs_number)
    TYPE bloom_filter(0.01) GRANULARITY 1;
