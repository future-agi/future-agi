-- =============================================================================
-- 005 — Fix Nullable hot-key columns to actually return NULL on absent keys
-- =============================================================================
--
-- Rationale (codex P2 finding on 002_spans_v2.sql:230-233):
--   The original ADD COLUMN definitions for `streaming`, `temperature`, `top_p`,
--   `max_tokens` declared the columns Nullable but defined the MATERIALIZED
--   expression as `attrs_X['key']`. ClickHouse Map element access returns the
--   value type's default (0 for numeric, '' for string) when the key is absent —
--   NOT NULL. So:
--
--       SELECT temperature FROM spans WHERE attrs_number = map();
--       -- Returns 0.0, not NULL — indistinguishable from `temperature = 0.0`.
--       SELECT count() FROM spans WHERE temperature IS NULL;
--       -- Always 0 — the column can never be NULL even though declared so.
--
--   That breaks two downstream queries:
--     (a) "How many spans set temperature?" — counted as 100% (false).
--     (b) "Spans where temperature = 0" — includes both explicit-zero and absent.
--
-- Fix: wrap each expression with `if(mapContains(<map>, '<key>'), <map>['<key>'], NULL)`
-- so missing keys correctly materialize as NULL. `mapContains` is O(1) and the
-- compiler folds it into a single map probe, so there is no runtime cost.
--
-- This is an additive ALTER (MODIFY COLUMN). Per schema/README.md rule #4, we
-- do NOT edit 002_spans_v2.sql in place; we ship the correction here so the
-- hash chain remains intact and the change has its own audit trail.
--
-- Note on max_tokens: the original used `toInt32OrZero(toString(...))` which
-- conflates "absent" with "unparseable". The new version uses plain `toInt32`
-- because attrs_number is already Float64 — the cast can only fail on overflow
-- and we'd rather see that than silently zero.
-- =============================================================================

ALTER TABLE spans
    MODIFY COLUMN streaming   Nullable(UInt8)
        MATERIALIZED if(mapContains(attrs_bool,   'streaming'),
                        attrs_bool['streaming'], NULL),
    MODIFY COLUMN temperature Nullable(Float64)
        MATERIALIZED if(mapContains(attrs_number, 'gen_ai.request.temperature'),
                        attrs_number['gen_ai.request.temperature'], NULL),
    MODIFY COLUMN top_p       Nullable(Float64)
        MATERIALIZED if(mapContains(attrs_number, 'gen_ai.request.top_p'),
                        attrs_number['gen_ai.request.top_p'], NULL),
    MODIFY COLUMN max_tokens  Nullable(Int32)
        MATERIALIZED if(mapContains(attrs_number, 'gen_ai.request.max_tokens'),
                        toInt32(attrs_number['gen_ai.request.max_tokens']), NULL);
