-- 039 — row-local trace names. The dictionary consumed too much memory (and
-- reloads need a second copy); spans are inserted before traces, so dictionary
-- enrichment cannot reliably supply the name on insert anyway.
-- MODIFY COLUMN changes metadata only: existing stored names remain intact.
-- Never run MATERIALIZE COLUMN trace_name after this migration: that would
-- overwrite existing explicit names and erase legacy child names.
ALTER TABLE spans
    MODIFY COLUMN trace_name String
        MATERIALIZED if(empty(parent_span_id), name, '');
