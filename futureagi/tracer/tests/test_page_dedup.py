"""Cross-page correctness of the prefix-dedup pagination helper.

These pin the invariants that replaced ``LIMIT 1 BY <key>`` in the span/trace
Phase-1 queries: pages are disjoint slices of one globally de-duplicated
stream — no key on two pages, no key skipped, first occurrence kept.
"""

import pytest

from tracer.services.clickhouse.page_dedup import paginate_deduped


def _rows(keys):
    return [{"id": k, "seq": i} for i, k in enumerate(keys)]


@pytest.mark.unit
class TestPaginateDeduped:
    def test_clean_page_matches_plain_slice(self):
        """No duplicates → identical to plain LIMIT/OFFSET pagination."""
        rows = _rows([f"s{i}" for i in range(10)])
        page, has_more = paginate_deduped(rows, "id", 0, 4)
        assert [r["id"] for r in page] == ["s0", "s1", "s2", "s3"]
        assert has_more is True

    def test_duplicate_straddling_page_boundary_not_repeated(self):
        """An un-merged duplicate at the page boundary must not appear on
        both pages. Page 1's prefix includes page 0's rows, so it knows the
        key was already surfaced."""
        # key 'dup' occupies raw positions 3 (page 0) and 4 (page 1 boundary)
        keys = ["a", "b", "c", "dup", "dup", "e", "f", "g", "h", "i"]
        page0, _ = paginate_deduped(_rows(keys), "id", 0, 4)
        page1, _ = paginate_deduped(_rows(keys), "id", 1, 4)
        assert [r["id"] for r in page0] == ["a", "b", "c", "dup"]
        assert [r["id"] for r in page1] == ["e", "f", "g", "h"]
        assert not {r["id"] for r in page0} & {r["id"] for r in page1}

    def test_multi_root_trace_far_apart_shown_once(self):
        """A multi-root trace whose second root sorts pages later must not
        resurface (the trace-list regression `LIMIT 1 BY trace_id` guarded)."""
        keys = ["t1", "t2", "t3", "t4", "t5", "t6", "t7", "t1", "t8", "t9"]
        page0, _ = paginate_deduped(_rows(keys), "id", 0, 4)
        page1, _ = paginate_deduped(_rows(keys), "id", 1, 4)
        assert [r["id"] for r in page0] == ["t1", "t2", "t3", "t4"]
        # raw position 7 (the second 't1' root) is skipped; 't9' fills the page
        assert [r["id"] for r in page1] == ["t5", "t6", "t7", "t8"]

    def test_no_key_skipped_across_pages(self):
        """Union of all pages == all distinct keys, exactly once each."""
        keys = ["a", "a", "b", "c", "c", "c", "d", "e", "f", "g", "h", "h", "i"]
        distinct = list(dict.fromkeys(keys))
        seen: list[str] = []
        page_number = 0
        while True:
            page, has_more = paginate_deduped(_rows(keys), "id", page_number, 3)
            seen.extend(r["id"] for r in page)
            if not has_more:
                break
            page_number += 1
        assert seen == distinct

    def test_first_occurrence_kept(self):
        """The surviving row per key is the FIRST in sort order — the same
        row ``LIMIT 1 BY`` (applied after ORDER BY) kept."""
        rows = [{"id": "x", "v": "new"}, {"id": "x", "v": "old"}]
        page, _ = paginate_deduped(rows, "id", 0, 5)
        assert page == [{"id": "x", "v": "new"}]

    def test_empty_and_out_of_range_pages(self):
        assert paginate_deduped([], "id", 0, 10) == ([], False)
        page, has_more = paginate_deduped(_rows(["a", "b"]), "id", 5, 10)
        assert page == [] and has_more is False

    def test_has_more_false_on_exact_boundary(self):
        page, has_more = paginate_deduped(_rows(["a", "b", "c"]), "id", 0, 3)
        assert len(page) == 3 and has_more is False
