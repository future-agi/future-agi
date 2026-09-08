import { describe, expect, it } from "vitest";

import { getListPagerState, pickPagerMetadata, pagerMetadataEquals, windowedPageNumbers } from "../listPagerState";

describe("getListPagerState", () => {
  // Captured from the local API on 2026-09-07, project 501e948b.
  it("proves the next page when the reported total exceeds rows seen", () => {
    expect(
      getListPagerState({
        metadata: {
          total_rows: 26,
          total_rows_is_lower_bound: true,
          has_more: true,
        },
        startRow: 0,
        rowCount: 25,
      }),
    ).toEqual({ hasMore: true, seen: 25, provenNext: true, exactTotal: null });
  });

  it("does not prove a next page when the total equals rows seen", () => {
    // Users page 1: total_count 25, has_more true, but no extra row proven.
    expect(
      getListPagerState({
        metadata: {
          total_count: 25,
          count_is_lower_bound: true,
          has_more: true,
        },
        startRow: 0,
        rowCount: 25,
      }),
    ).toEqual({ hasMore: true, seen: 25, provenNext: false, exactTotal: null });
  });

  it("does not prove a next page for an empty page that still has more", () => {
    // Call Logs returned two of these in a row, each with a valid cursor.
    expect(
      getListPagerState({
        metadata: { count: 0, count_is_lower_bound: true, has_more: true },
        startRow: 0,
        rowCount: 0,
      }),
    ).toEqual({ hasMore: true, seen: 0, provenNext: false, exactTotal: null });
  });

  it("reports an exact total on a terminal page", () => {
    expect(
      getListPagerState({
        metadata: {
          total_count: 59,
          count_is_lower_bound: false,
          has_more: false,
        },
        startRow: 50,
        rowCount: 9,
      }),
    ).toEqual({ hasMore: false, seen: 59, provenNext: false, exactTotal: 59 });
  });

  it("distinguishes an exact zero total from absent metadata", () => {
    expect(
      getListPagerState({
        metadata: {
          total_rows: 0,
          total_rows_is_lower_bound: false,
          has_more: false,
        },
        startRow: 0,
        rowCount: 0,
      }),
    ).toEqual({ hasMore: false, seen: 0, provenNext: false, exactTotal: 0 });
  });

  it("counts pages from an exact total when the list is not cursor-backed", () => {
    // The agent-definition call-log endpoint (DRF ExtendedPageNumberPagination):
    // exact `count`, exact `total_pages`, no has_more and no cursor.
    expect(
      getListPagerState({
        metadata: { count: 137, total_pages: 6, current_page: 1, results: [] },
        startRow: 0,
        rowCount: 25,
      }),
    ).toEqual({ hasMore: true, seen: 25, provenNext: true, exactTotal: 137 });
  });

  it("ends a non-cursor list on the page its exact total runs out", () => {
    expect(
      getListPagerState({
        metadata: { count: 30, total_pages: 2, current_page: 2, results: [] },
        startRow: 25,
        rowCount: 5,
      }),
    ).toEqual({ hasMore: false, seen: 30, provenNext: false, exactTotal: 30 });
  });

  it("treats the users lower-bound flag as a lower bound", () => {
    // The users endpoint emits total_count_is_lower_bound, not
    // count_is_lower_bound; missing it reported a guess as an exact total.
    expect(
      getListPagerState({
        metadata: {
          total_count: 25,
          total_count_is_lower_bound: true,
          has_more: true,
        },
        startRow: 0,
        rowCount: 25,
      }).exactTotal,
    ).toBeNull();
  });

  it("tolerates absent metadata", () => {
    expect(getListPagerState({})).toEqual({
      hasMore: false,
      seen: 0,
      provenNext: false,
      exactTotal: null,
    });
  });
});

describe("windowedPageNumbers", () => {
  it("shows page 1 and the proven next page", () => {
    expect(windowedPageNumbers({ page: 1, provenNext: true })).toEqual([1, 2]);
  });

  it("stays contiguous while the window touches page 1", () => {
    expect(windowedPageNumbers({ page: 3, provenNext: true })).toEqual([
      1, 2, 3, 4,
    ]);
  });

  it("never exceeds four numbers once a gap opens", () => {
    expect(windowedPageNumbers({ page: 8, provenNext: true })).toEqual([
      1, 7, 8, 9,
    ]);
  });

  it("omits the next number when it is not proven", () => {
    expect(windowedPageNumbers({ page: 9, provenNext: false })).toEqual([
      1, 8, 9,
    ]);
  });

  it("clamps a bad page to 1", () => {
    expect(windowedPageNumbers({ page: 0, provenNext: false })).toEqual([1]);
  });

  describe("furthestPage boundary", () => {
    // Hand-worked examples from the design brief: walking to page 11 and back.
    it("draws the furthest page as a right-hand boundary after a walk-and-return", () => {
      expect(
        windowedPageNumbers({ page: 1, provenNext: true, furthestPage: 11 }),
      ).toEqual([1, 2, 11]);
    });

    it("opens a second gap for the boundary while keeping the leading gap", () => {
      expect(
        windowedPageNumbers({ page: 5, provenNext: true, furthestPage: 11 }),
      ).toEqual([1, 4, 5, 6, 11]);
    });

    it("draws no separate boundary once standing on the furthest page itself", () => {
      expect(
        windowedPageNumbers({ page: 11, provenNext: false, furthestPage: 11 }),
      ).toEqual([1, 10, 11]);
    });

    it("never exceeds five numbers", () => {
      expect(
        windowedPageNumbers({ page: 5, provenNext: true, furthestPage: 11 })
          .length,
      ).toBeLessThanOrEqual(5);
    });

    it("is absent (no-op) when the furthest page is not ahead of the window", () => {
      // The window already reaches page 9 (provenNext), so a furthest page of
      // 9 or lower adds nothing new.
      expect(
        windowedPageNumbers({ page: 8, provenNext: true, furthestPage: 9 }),
      ).toEqual([1, 7, 8, 9]);
    });

    it("never draws the boundary behind or equal to the current page", () => {
      // A stale/lower furthestPage (e.g. from a route that no longer exists,
      // or simply behind where the walk has since moved) must never appear.
      expect(
        windowedPageNumbers({ page: 8, provenNext: true, furthestPage: 3 }),
      ).toEqual([1, 7, 8, 9]);
      expect(
        windowedPageNumbers({ page: 8, provenNext: true, furthestPage: 8 }),
      ).toEqual([1, 7, 8, 9]);
    });

    it("does not flicker during the in-flight window right after Next is clicked", () => {
      // useCursorGridPagination.js sets `page` optimistically on click, so
      // `page === frontier.page + 1` for the whole in-flight request. A
      // furthestPage equal to the *previous* page must not draw a boundary
      // one click ahead of itself.
      expect(
        windowedPageNumbers({ page: 5, provenNext: true, furthestPage: 4 }),
      ).toEqual([1, 4, 5, 6]);
    });

    it("defaults to no boundary when furthestPage is omitted", () => {
      expect(windowedPageNumbers({ page: 8, provenNext: true })).toEqual([
        1, 7, 8, 9,
      ]);
    });
  });
});

describe("pickPagerMetadata", () => {
  it("plucks exactly the fields getListPagerState reads", () => {
    const picked = pickPagerMetadata({
      count: 5,
      count_is_lower_bound: true,
      total_count: 7,
      total_count_is_lower_bound: false,
      total_rows: 9,
      total_rows_is_lower_bound: true,
      has_more: true,
      rows: [{}],
      next_cursor: "abc",
    });
    expect(picked).toEqual({
      count: 5,
      count_is_lower_bound: true,
      total_count: 7,
      total_count_is_lower_bound: false,
      total_rows: 9,
      total_rows_is_lower_bound: true,
      has_more: true,
    });
  });

  it("keeps has_more's absence — the cursor-contract marker — observable", () => {
    const picked = pickPagerMetadata({ count: 3 });
    expect(Object.prototype.hasOwnProperty.call(picked, "has_more")).toBe(
      false,
    );
  });

  it("gives getListPagerState the same verdict as the raw response", () => {
    const response = { count: 10, count_is_lower_bound: true, has_more: true };
    const args = { startRow: 0, rowCount: 5 };
    expect(
      getListPagerState({ metadata: pickPagerMetadata(response), ...args }),
    ).toEqual(getListPagerState({ metadata: response, ...args }));
  });
});

describe("pagerMetadataEquals", () => {
  it("treats picks of pager-identical responses as equal", () => {
    expect(
      pagerMetadataEquals(
        pickPagerMetadata({ count: 1, has_more: true, rows: [{}] }),
        pickPagerMetadata({ count: 1, has_more: true, rows: [{}, {}] }),
      ),
    ).toBe(true);
  });

  it("distinguishes a missing has_more from a present one", () => {
    expect(
      pagerMetadataEquals(
        pickPagerMetadata({ count: 1 }),
        pickPagerMetadata({ count: 1, has_more: false }),
      ),
    ).toBe(false);
  });
});
