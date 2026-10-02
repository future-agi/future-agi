import { describe, expect, it, vi } from "vitest";

import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";

import {
  GROUND_TRUTH_DATASET_ACTION_TIMEOUT_MS,
  GROUND_TRUTH_DATASET_PAGE_SIZE,
  appendGroundTruthDatasetPage,
  createEmptyGroundTruthDatasetRead,
  readNextGroundTruthDatasetPage,
} from "./ground_truth_dataset_pagination";

const rows = (start, count) =>
  Array.from({ length: count }, (_, index) => ({
    row_id: `row-${start + index}`,
  }));

const resultPage = ({
  pageIndex,
  totalRows,
  table,
  hasMore,
  nextPageIndex,
  nextCursor = hasMore ? `cursor-${pageIndex + 1}` : null,
  columns = ["column-a"],
}) => ({
  metadata: {
    dataset_name: "Reference answers",
    total_rows: totalRows,
    total_pages:
      totalRows === 0
        ? 0
        : Math.ceil(totalRows / GROUND_TRUTH_DATASET_PAGE_SIZE),
    page_size: GROUND_TRUTH_DATASET_PAGE_SIZE,
    current_page_index: pageIndex,
    has_more: hasMore,
    next_page_index: nextPageIndex,
    next_cursor: nextCursor,
    is_exact: true,
    snapshot_bound: true,
    error_messages: [],
  },
  column_config: columns.map((id) => ({ id, name: id })),
  table,
});

describe("ground-truth dataset pagination", () => {
  it("accumulates explicit pages and marks complete only at exact exhaustion", () => {
    const first = appendGroundTruthDatasetPage(
      createEmptyGroundTruthDatasetRead(),
      resultPage({
        pageIndex: 0,
        totalRows: 101,
        table: rows(0, 100),
        hasMore: true,
        nextPageIndex: 1,
      }),
      0,
    );

    expect(first).toMatchObject({
      totalRows: 101,
      nextPageIndex: 1,
      hasMore: true,
      complete: false,
    });
    expect(first.rows).toHaveLength(100);

    const second = appendGroundTruthDatasetPage(
      first,
      resultPage({
        pageIndex: 1,
        totalRows: 101,
        table: rows(100, 1),
        hasMore: false,
        nextPageIndex: null,
      }),
      1,
    );

    expect(second).toMatchObject({
      totalRows: 101,
      nextPageIndex: null,
      hasMore: false,
      complete: true,
    });
    expect(second.rows).toHaveLength(101);
  });

  it.each([
    [
      "changed totals",
      { totalRows: 502 },
      "ground_truth_dataset_count_mismatch",
    ],
    [
      "changed columns",
      { columns: ["column-b"] },
      "ground_truth_dataset_columns_changed",
    ],
    [
      "duplicate rows",
      { table: [{ row_id: "row-0" }] },
      "ground_truth_dataset_duplicate_row",
    ],
  ])("fails closed for %s", (_label, override, expectedCode) => {
    const first = appendGroundTruthDatasetPage(
      createEmptyGroundTruthDatasetRead(),
      resultPage({
        pageIndex: 0,
        totalRows: 101,
        table: rows(0, 100),
        hasMore: true,
        nextPageIndex: 1,
      }),
      0,
    );
    const secondPage = resultPage({
      pageIndex: 1,
      totalRows: 101,
      table: rows(100, 1),
      hasMore: false,
      nextPageIndex: null,
      ...override,
    });

    expect(() => appendGroundTruthDatasetPage(first, secondPage, 1)).toThrow(
      expect.objectContaining({ code: expectedCode }),
    );
  });

  it("rejects duplicate source column names before any import can collapse them", () => {
    expect(() =>
      appendGroundTruthDatasetPage(
        createEmptyGroundTruthDatasetRead(),
        resultPage({
          pageIndex: 0,
          totalRows: 1,
          table: rows(0, 1),
          hasMore: false,
          nextPageIndex: null,
          columns: ["duplicate", "duplicate"],
        }),
        0,
      ),
    ).toThrow(
      expect.objectContaining({
        code: "ground_truth_dataset_duplicate_column",
      }),
    );
  });

  it("rejects a non-exact server page instead of retaining it", () => {
    const page = resultPage({
      pageIndex: 0,
      totalRows: 1,
      table: rows(0, 1),
      hasMore: false,
      nextPageIndex: null,
    });
    page.metadata.is_exact = false;

    expect(() =>
      appendGroundTruthDatasetPage(
        createEmptyGroundTruthDatasetRead(),
        page,
        0,
      ),
    ).toThrow(
      expect.objectContaining({ code: "ground_truth_dataset_inexact_page" }),
    );
  });

  it("rejects a continuation cursor that does not advance", () => {
    const first = appendGroundTruthDatasetPage(
      createEmptyGroundTruthDatasetRead(),
      resultPage({
        pageIndex: 0,
        totalRows: 201,
        table: rows(0, 100),
        hasMore: true,
        nextPageIndex: 1,
        nextCursor: "signed-cursor-1",
      }),
      0,
    );

    expect(() =>
      appendGroundTruthDatasetPage(
        first,
        resultPage({
          pageIndex: 1,
          totalRows: 201,
          table: rows(100, 100),
          hasMore: true,
          nextPageIndex: 2,
          nextCursor: "signed-cursor-1",
        }),
        1,
      ),
    ).toThrow(
      expect.objectContaining({ code: "ground_truth_dataset_cursor_mismatch" }),
    );
  });

  it("makes one bounded request per action and forwards the exact page contract", async () => {
    const requestPage = vi.fn().mockResolvedValue({
      data: {
        result: resultPage({
          pageIndex: 0,
          totalRows: 1,
          table: rows(0, 1),
          hasMore: false,
          nextPageIndex: null,
        }),
      },
    });

    const loaded = await readNextGroundTruthDatasetPage({
      previous: createEmptyGroundTruthDatasetRead(),
      requestPage,
    });

    expect(loaded.complete).toBe(true);
    expect(requestPage).toHaveBeenCalledTimes(1);
    expect(requestPage).toHaveBeenCalledWith({
      pageIndex: 0,
      pageSize: 100,
      cursor: null,
      signal: expect.any(AbortSignal),
      timeout: GROUND_TRUTH_DATASET_ACTION_TIMEOUT_MS,
    });
  });

  it("aborts and rejects below the 9.5 second action wall", async () => {
    vi.useFakeTimers();
    try {
      let requestSignal;
      const pending = readNextGroundTruthDatasetPage({
        previous: createEmptyGroundTruthDatasetRead(),
        requestPage: ({ signal }) => {
          requestSignal = signal;
          return new Promise(() => {});
        },
      });
      const rejection = expect(pending).rejects.toMatchObject({
        code: "ground_truth_dataset_timeout",
      });

      await vi.advanceTimersByTimeAsync(GROUND_TRUTH_DATASET_ACTION_TIMEOUT_MS);
      await rejection;
      expect(requestSignal.aborted).toBe(true);
      expect(GROUND_TRUTH_DATASET_ACTION_TIMEOUT_MS).toBe(
        INTERACTIVE_REQUEST_TIMEOUT_MS,
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("retains the prior exact prefix when a changed snapshot returns 409", async () => {
    const first = appendGroundTruthDatasetPage(
      createEmptyGroundTruthDatasetRead(),
      resultPage({
        pageIndex: 0,
        totalRows: 101,
        table: rows(0, 100),
        hasMore: true,
        nextPageIndex: 1,
        nextCursor: "signed-revision-cursor",
      }),
      0,
    );
    const changed = Object.assign(new Error("Restart the import."), {
      response: { status: 409 },
    });

    await expect(
      readNextGroundTruthDatasetPage({
        previous: first,
        requestPage: vi.fn().mockRejectedValue(changed),
      }),
    ).rejects.toBe(changed);
    expect(first.rows).toHaveLength(100);
    expect(first.nextCursor).toBe("signed-revision-cursor");
    expect(first.complete).toBe(false);
  });
});
