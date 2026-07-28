"""Unit tests for the P3b id-remap SQL helpers (``id_remap_sql``).

These assert the EMITTED SQL STRUCTURE — the survivor-collapse contract that
fixes the gate-C2 many-old→one-new fan-out (a post-flip ``new_X`` span fanning
across every duplicate remap row → user split + double-count). The end-to-end
behavioural proof (gate C2 reads ONE row / 450 tok, gate C straddler still 514,
gate B no-op on the real 1:1 baseline) is the ch_rehearsal harness; this file is
the fast regression guard so the structure can't silently revert.
"""

from __future__ import annotations

import unittest

from tracer.services.clickhouse.v2.id_remap_sql import (
    NIL_UUID,
    REMAP_ALIAS,
    remap_left_join,
    resolved_id_expr,
)


class ResolvedIdExprTests(unittest.TestCase):
    def test_resolves_to_survivor_id_not_old_id(self):
        # The resolved value is the survivor map's `survivor_id` (the canonical
        # old id of the span's group), NOT the raw remap `old_id` — that switch
        # is what collapses a consolidation group to ONE row.
        expr = resolved_id_expr("rs.end_user_id", "eu_remap")
        self.assertIn("eu_remap.survivor_id", expr)
        self.assertNotIn("eu_remap.old_id", expr)
        self.assertNotIn("eu_remap.new_id", expr)

    def test_zero_uuid_guard_preserved(self):
        # Unmatched LEFT JOIN fills a non-nullable UUID with the zero-uuid (not
        # NULL); the guard must fall back to the span's own id (gate-B no-op).
        expr = resolved_id_expr("rs.end_user_id", "eu_remap")
        self.assertIn(f"toUUID('{NIL_UUID}')", expr)
        self.assertIn("IS NULL", expr)
        # Falls back to the span's own id when there is no survivor.
        self.assertIn("rs.end_user_id", expr)

    def test_default_alias(self):
        self.assertIn(f"{REMAP_ALIAS}.survivor_id", resolved_id_expr("end_user_id"))


class RemapLeftJoinTests(unittest.TestCase):
    def test_joins_survivor_map_on_any_id(self):
        # The join target is the derived survivor map (NOT the raw table), and
        # the ON key is `any_id` — the union of {every old id} ∪ {every new id},
        # each appearing exactly once, so a span matches AT MOST one row → the
        # fan-out is structurally impossible.
        join = remap_left_join("rs.end_user_id", "end_user_id_remap", "eu_remap")
        self.assertIn("LEFT JOIN (", join)
        self.assertTrue(join.rstrip().endswith("ON rs.end_user_id = eu_remap.any_id"))
        # NOT the legacy direct `... ON span = remap.new_id` single-table join.
        self.assertNotIn("AS eu_remap FINAL ON", join)

    def test_survivor_map_has_both_arms(self):
        join = remap_left_join("s.end_user_id", "end_user_id_remap")
        # Window arm: every old_id → its group's survivor (folds non-survivor olds).
        self.assertIn("OVER (PARTITION BY new_id)", join)
        # Group arm: every new_id → its group's survivor (post-flip new ids).
        self.assertIn("GROUP BY new_id", join)
        self.assertIn("UNION ALL", join)
        # Survivor selector = lexicographically-smallest UUID *string* (NOT
        # min(uuid): CH's native UUID order is byte-swapped & non-reproducible).
        self.assertIn("argMin(old_id, toString(old_id))", join)
        # FINAL collapses the ReplacingMergeTree on both arms.
        self.assertEqual(join.count("end_user_id_remap FINAL"), 2)

    def test_outer_dedup_guards_carveout_identity_rows(self):
        # The outer `GROUP BY any_id` (+ `min(survivor_id)`) guarantees ONE row
        # per id — the carve-out fan-out guard: the Session §3.1 carve-out is an
        # identity remap row (old_id == new_id == K) that BOTH arms emit; without
        # the dedup a span carrying K matches both → fan-out → double-count (the
        # gate-C2 bug reintroduced). With it, K resolves once, to itself.
        join = remap_left_join(
            "rs.trace_session_id", "trace_session_id_remap", "ts_remap"
        )
        self.assertIn("min(survivor_id) AS survivor_id", join)
        self.assertIn("GROUP BY any_id", join)
        # The dedup wraps the two-arm union (any_id appears before the arms).
        self.assertLess(join.index("GROUP BY new_id"), join.index("GROUP BY any_id"))

    def test_distinct_aliases_do_not_collide(self):
        # A dual eu+ts read joins both remaps off the same row; the aliases and
        # their table names must be independent.
        eu = remap_left_join("rs.end_user_id", "end_user_id_remap", "eu_remap")
        ts = remap_left_join(
            "rs.trace_session_id", "trace_session_id_remap", "ts_remap"
        )
        self.assertIn("AS eu_remap", eu)
        self.assertIn("AS ts_remap", ts)
        self.assertIn("end_user_id_remap FINAL", eu)
        self.assertNotIn("trace_session_id_remap", eu)
        self.assertIn("trace_session_id_remap FINAL", ts)

    def test_table_name_is_unqualified(self):
        # DB-agnostic: the table is unqualified so CH25_DATABASE is the single
        # dev/test/prod switch (no hard-coded `default.`).
        join = remap_left_join("s.end_user_id", "end_user_id_remap")
        self.assertNotIn("default.", join)


if __name__ == "__main__":
    unittest.main()
