-- Reduce false positives for batched trace ID lookups.
-- Existing parts need a separate, partition-scoped MATERIALIZE INDEX operation.
-- Keep historical materialization out of the schema applier.

ALTER TABLE spans
    ADD INDEX IF NOT EXISTS idx_trace_id_bloom_strict trace_id
    TYPE bloom_filter(0.00001) GRANULARITY 1;
