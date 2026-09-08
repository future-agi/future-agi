import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import useCursorGridPagination from "../useCursorGridPagination";

// goToPage() only moves when it can reach a live AG Grid API, and the pager
// state has to stay correct across those moves — that is the whole point of
// the page-aware flags below.
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
  let returned;
  act(() => {
    returned = result.current.publishPage({
      request: { startRow, endRow },
      rows: new Array(rows).fill({}),
      isLastPage,
      metadata,
    });
  });
  return returned;
};

describe("useCursorGridPagination", () => {
  it("keeps the AG Grid row count sentinel unchanged", () => {
    // REGRESSION GUARD: this value feeds params.success({ rowCount }). If it
    // stops being endRow + 1 on a non-terminal page, AG Grid concludes the
    // data ends here and forward navigation dies silently.
    const { result } = renderHook(() => useCursorGridPagination(null, null));

    expect(
      publish(result, {
        startRow: 0,
        endRow: 25,
        rows: 25,
        isLastPage: false,
        metadata: { total_rows: 26, has_more: true },
      }),
    ).toBe(26);

    expect(
      publish(result, {
        startRow: 25,
        endRow: 50,
        rows: 25,
        isLastPage: false,
        metadata: { total_rows: 51, has_more: true },
      }),
    ).toBe(51);
  });

  it("keeps pageCount as the navigation bound", () => {
    const { result } = renderHook(() => useCursorGridPagination(null, null));
    publish(result, {
      startRow: 0,
      endRow: 25,
      rows: 25,
      isLastPage: false,
      metadata: { total_rows: 26, has_more: true },
    });
    expect(result.current.pageCount).toBe(2);
    expect(result.current.page).toBe(1);
  });

  it("proves the next page when the metadata proves a further row", () => {
    const { result } = renderHook(() => useCursorGridPagination(null, null));
    publish(result, {
      startRow: 0,
      endRow: 25,
      rows: 25,
      isLastPage: false,
      metadata: {
        total_rows: 26,
        total_rows_is_lower_bound: true,
        has_more: true,
      },
    });
    expect(result.current.hasMore).toBe(true);
    expect(result.current.provenNext).toBe(true);
  });

  it("withholds the next page number when no further row is proven", () => {
    const { result } = renderHook(() => useCursorGridPagination(null, null));
    publish(result, {
      startRow: 0,
      endRow: 25,
      rows: 25,
      isLastPage: false,
      metadata: {
        total_count: 25,
        count_is_lower_bound: true,
        has_more: true,
      },
    });
    expect(result.current.hasMore).toBe(true);
    expect(result.current.provenNext).toBe(false);
  });

  it("clears both flags on a terminal page", () => {
    const { result } = renderHook(() => useCursorGridPagination(null, null));
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
    expect(result.current.hasMore).toBe(false);
    expect(result.current.provenNext).toBe(false);
    expect(result.current.page).toBe(3);
  });

  it("overrides contradictory metadata on a terminal page", () => {
    // REGRESSION GUARD: the server can legitimately report has_more/a higher
    // total on the same response that also sets isLastPage. isLastPage must
    // win regardless of what the metadata says, otherwise the pager could
    // draw a page number the cursor protocol can never serve.
    const { result } = renderHook(() => useCursorGridPagination(null, null));
    publish(result, {
      startRow: 50,
      endRow: 75,
      rows: 9,
      isLastPage: true,
      metadata: {
        total_rows: 100,
        total_rows_is_lower_bound: true,
        has_more: true,
      },
    });
    expect(result.current.hasMore).toBe(false);
    expect(result.current.provenNext).toBe(false);
  });

  it("keeps forward navigation alive after returning from the terminal page", () => {
    // REGRESSION GUARD (C1): the flags used to be last-write-wins, written
    // only from inside publishPage. Returning to a cached page never
    // re-invokes the datasource, so the terminal page's `false` stuck and
    // Next stayed disabled until a full refresh.
    const { result } = renderPagination();
    publish(result, {
      startRow: 0,
      endRow: 25,
      rows: 25,
      isLastPage: false,
      metadata: {
        total_rows: 26,
        total_rows_is_lower_bound: true,
        has_more: true,
      },
    });
    expect(result.current.hasMore).toBe(true);
    expect(result.current.provenNext).toBe(true);

    act(() => result.current.goToPage(2));
    publish(result, {
      startRow: 25,
      endRow: 50,
      rows: 9,
      isLastPage: true,
      metadata: { total_rows: 34, has_more: false },
    });
    expect(result.current.page).toBe(2);
    expect(result.current.hasMore).toBe(false);
    expect(result.current.provenNext).toBe(false);

    act(() => result.current.goToPage(1));

    expect(result.current.page).toBe(1);
    expect(result.current.hasMore).toBe(true);
    expect(result.current.provenNext).toBe(true);
  });

  it("reaches an overflow page the terminal response already buffered", () => {
    // REGRESSION GUARD (C3): isLastPage can only be false while the transport
    // reports no further window when the response overflowed and its extra
    // rows are already held for the next page. Those rows are proven by
    // construction; without this they were silently unreachable.
    const { result } = renderPagination();
    publish(result, {
      startRow: 0,
      endRow: 25,
      rows: 25,
      isLastPage: false,
      metadata: {
        total_rows: 25,
        total_rows_is_lower_bound: true,
        has_more: false,
      },
    });

    expect(result.current.hasMore).toBe(true);
    expect(result.current.provenNext).toBe(true);
    expect(result.current.pageCount).toBeGreaterThanOrEqual(2);
  });

  it("does not manufacture a phantom trailing page in legacy/numbered mode", () => {
    // REGRESSION GUARD (N1): hasBufferedOverflowPage()'s proof only holds when
    // the transport is in cursor mode and the response carries a `has_more`
    // field (listCursorPagination.js isLastPage(), :805-810). A legacy/
    // numbered-mode response has no such field, so isLastPage degrades to
    // `rowCount < pageSize`. A true, exactly-full final page (page 2 of a
    // 50-row legacy DRF list, 25 of 25 rows) must not be read as a buffered
    // overflow just because isLastPage came back false.
    const { result } = renderHook(() => useCursorGridPagination(null, null));
    publish(result, {
      startRow: 25,
      endRow: 50,
      rows: 25,
      isLastPage: false,
      metadata: { count: 50, total_pages: 2 },
    });
    expect(result.current.hasMore).toBe(false);
    expect(result.current.provenNext).toBe(false);
  });

  it("ignores a background block published for another page", () => {
    // beginPageLoad() already refuses to let a background block own the page
    // loader; the visible page and the pager flags need the same guard.
    const { result } = renderPagination();
    publish(result, {
      startRow: 0,
      endRow: 25,
      rows: 25,
      isLastPage: false,
      metadata: {
        total_rows: 26,
        total_rows_is_lower_bound: true,
        has_more: true,
      },
    });
    act(() => result.current.goToPage(2));

    publish(result, {
      startRow: 50,
      endRow: 75,
      rows: 5,
      isLastPage: true,
      metadata: { total_rows: 55, has_more: false },
    });

    expect(result.current.page).toBe(2);
    expect(result.current.hasMore).toBe(true);
    // Page 2 has not published its own state yet, so nothing beyond it is
    // proven. What matters is that the background terminal block did not get
    // to declare the list finished on page 2's behalf.
    expect(result.current.provenNext).toBe(false);
  });

  it("ignores a navigation to the page already on screen", () => {
    // Without this the transition's render check can never see the rows
    // change, so "Loading page…" spins until the transition times out.
    const { result } = renderPagination();
    publish(result, {
      startRow: 0,
      endRow: 25,
      rows: 25,
      isLastPage: false,
      metadata: {
        total_rows: 26,
        total_rows_is_lower_bound: true,
        has_more: true,
      },
    });

    act(() => result.current.goToPage(1));

    expect(result.current.isPageLoading).toBe(false);
  });

  it("clears both flags on reset", () => {
    const { result } = renderHook(() => useCursorGridPagination(null, null));
    publish(result, {
      startRow: 0,
      endRow: 25,
      rows: 25,
      isLastPage: false,
      metadata: { total_rows: 26, has_more: true },
    });
    act(() => result.current.resetPagination({ moveGrid: false }));
    expect(result.current.hasMore).toBe(false);
    expect(result.current.provenNext).toBe(false);
    expect(result.current.page).toBe(1);
  });

  it("collapses the frontier when a shallower page re-reads as terminal", () => {
    const { result } = renderHook(() => useCursorGridPagination(null, null));

    publish(result, {
      startRow: 0,
      endRow: 25,
      rows: 25,
      isLastPage: false,
      metadata: { total_rows: 26, has_more: true },
    });
    publish(result, {
      startRow: 25,
      endRow: 50,
      rows: 25,
      isLastPage: false,
      metadata: { total_rows: 51, has_more: true },
    });
    publish(result, {
      startRow: 50,
      endRow: 75,
      rows: 25,
      isLastPage: false,
      metadata: { total_rows: 76, has_more: true },
    });
    expect(result.current.frontierPage).toBe(3);
    expect(result.current.pageCount).toBe(4);

    // The list shrank under the walk (relative time range, deleted rows). AG
    // Grid evicted page 2's block (maxBlocksInCache), the user navigated back,
    // and the re-read came back terminal.
    publish(result, {
      startRow: 25,
      endRow: 50,
      rows: 10,
      isLastPage: true,
      metadata: { total_rows: 35, has_more: false },
    });

    expect(result.current.page).toBe(2);
    expect(result.current.pageCount).toBe(2);
    // Terminal proof beats the monotone guard: the deeper pages are disproven.
    expect(result.current.frontierPage).toBe(2);
    expect(result.current.hasMore).toBe(false);
    expect(result.current.provenNext).toBe(false);
    expect(result.current.endUnknown).toBe(false);
  });

  // The bug this split exists for: walk to the terminal page, go back, and the
  // trailing ellipsis returned — because `hasMore` is true again once you are
  // behind the frontier. Navigability and "is the end known" are different
  // questions and must not share a flag.
  it("keeps endUnknown false after the terminal page, while Next stays usable", () => {
    const { result } = renderHook(() => useCursorGridPagination(null, null));

    publish(result, {
      startRow: 0,
      endRow: 25,
      rows: 25,
      isLastPage: false,
      metadata: { total_rows: 26, has_more: true },
    });
    expect(result.current.endUnknown).toBe(true);

    publish(result, {
      startRow: 25,
      endRow: 50,
      rows: 11,
      isLastPage: true,
      metadata: {
        total_rows: 36,
        total_rows_is_lower_bound: false,
        has_more: false,
      },
    });
    expect(result.current.endUnknown).toBe(false);

    // Back on page 1: `hasMore` is true again (you can move forward to the
    // frontier), but the end is still known. This is the discriminating case —
    // deriving endUnknown from hasMore makes this assertion fail.
    publish(result, {
      startRow: 0,
      endRow: 25,
      rows: 25,
      isLastPage: false,
      metadata: { total_rows: 26, has_more: true },
    });
    expect(result.current.page).toBe(1);
    expect(result.current.hasMore).toBe(true);
    expect(result.current.endUnknown).toBe(false);
  });
});
