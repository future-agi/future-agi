import { describe, expect, it } from "vitest";

import {
  getListPagerState,
  windowedPageNumbers,
} from "../listPagerState";

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
});
