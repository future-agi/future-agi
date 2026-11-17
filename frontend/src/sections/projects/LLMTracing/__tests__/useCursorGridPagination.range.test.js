import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import useCursorGridPagination from "../useCursorGridPagination";

// What the Traces, Spans and Sessions grids pass to CursorGridPagination for
// its range: the rows on the page on screen (`visibleRowCount`) and the exact
// total the list API reported (`exactTotal`), never a lower bound.
//
// goToPage() only moves when it can reach a live AG Grid API.
const renderPagination = () => {
  let currentPage = 0;
  const gridRef = {
    current: {
      api: {
        getRenderedNodes: () => [],
        paginationGetCurrentPage: () => currentPage,
        paginationGoToFirstPage: () => {
          currentPage = 0;
        },
        paginationGoToPage: (nextPage) => {
          currentPage = nextPage;
        },
      },
    },
  };
  return renderHook(() => useCursorGridPagination(gridRef, null));
};

const publish = (result, { startRow, endRow, rows, isLastPage, metadata }) => {
  act(() => {
    result.current.publishPage({
      request: { startRow, endRow },
      rows: new Array(rows).fill({}),
      isLastPage,
      metadata,
    });
  });
};

// A cursor page with more rows behind it: the API's total is a lower bound
// (list_cursor.py cursor_page_metadata: total_rows_is_lower_bound = has_more).
const lowerBoundPage = (seen) => ({
  total_rows: seen + 1,
  total_rows_is_lower_bound: true,
  has_more: true,
});

describe("useCursorGridPagination visible range", () => {
  it("reports the rows on the page on screen and no total while the API reports a lower bound", () => {
    const { result } = renderPagination();
    publish(result, {
      startRow: 0,
      endRow: 25,
      rows: 25,
      isLastPage: false,
      metadata: lowerBoundPage(25),
    });
    expect(result.current.page).toBe(1);
    expect(result.current.visibleRowCount).toBe(25);
    expect(result.current.exactTotal).toBeNull();
  });

  it("reports the exact total the final cursor page carries, and keeps it on a cached return to page 1", () => {
    const { result } = renderPagination();
    publish(result, {
      startRow: 0,
      endRow: 25,
      rows: 25,
      isLastPage: false,
      metadata: lowerBoundPage(25),
    });
    act(() => result.current.goToPage(2));
    // Not yet published: the rows on page 2 are unknown.
    expect(result.current.page).toBe(2);
    expect(result.current.visibleRowCount).toBeNull();

    publish(result, {
      startRow: 25,
      endRow: 50,
      rows: 25,
      isLastPage: false,
      metadata: lowerBoundPage(50),
    });
    expect(result.current.visibleRowCount).toBe(25);
    expect(result.current.exactTotal).toBeNull();

    act(() => result.current.goToPage(3));
    publish(result, {
      startRow: 50,
      endRow: 75,
      rows: 9,
      isLastPage: true,
      metadata: {
        total_rows: 59,
        total_rows_is_lower_bound: false,
        has_more: false,
      },
    });
    expect(result.current.page).toBe(3);
    expect(result.current.visibleRowCount).toBe(9);
    expect(result.current.exactTotal).toBe(59);

    // Page 1 is cached: AG Grid does not call the datasource again.
    act(() => result.current.goToPage(1));
    expect(result.current.page).toBe(1);
    expect(result.current.visibleRowCount).toBe(25);
    expect(result.current.exactTotal).toBe(59);
  });

  it("reports no total for a final page whose response still marks its total a lower bound", () => {
    const { result } = renderPagination();
    publish(result, {
      startRow: 0,
      endRow: 25,
      rows: 12,
      isLastPage: true,
      metadata: {
        total_rows: 12,
        total_rows_is_lower_bound: true,
        has_more: false,
      },
    });
    expect(result.current.visibleRowCount).toBe(12);
    expect(result.current.exactTotal).toBeNull();
  });

  it("reports the exact count a numbered (non-cursor) list returns from its first page", () => {
    const { result } = renderPagination();
    publish(result, {
      startRow: 0,
      endRow: 25,
      rows: 25,
      isLastPage: false,
      metadata: { total_rows: 312 },
    });
    expect(result.current.visibleRowCount).toBe(25);
    expect(result.current.exactTotal).toBe(312);
  });

  it("clears the rows and the total when pagination resets", () => {
    const { result } = renderPagination();
    publish(result, {
      startRow: 0,
      endRow: 25,
      rows: 7,
      isLastPage: true,
      metadata: {
        total_rows: 7,
        total_rows_is_lower_bound: false,
        has_more: false,
      },
    });
    expect(result.current.visibleRowCount).toBe(7);
    expect(result.current.exactTotal).toBe(7);

    act(() => result.current.resetPagination());
    expect(result.current.visibleRowCount).toBeNull();
    expect(result.current.exactTotal).toBeNull();
  });
});
