import { describe, expect, it, vi } from "vitest";

import { ANALYTICS_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";
import { INTERACTIVE_MAX_PAGE_SIZE } from "src/config/runtime_limits";

import {
  EVAL_LOG_GRID_REQUEST_TIMEOUT_MS,
  readEvalLogGridPage,
} from "../eval_log_grid_read";

const pageResponse = (overrides = {}) => ({
  data: {
    result: {
      column_config: [{ id: "column1", name: "Evaluation ID" }],
      table: [
        {
          row_id: "11111111-1111-4111-8111-111111111111",
          log_id: "11111111-1111-4111-8111-111111111111",
        },
      ],
      metadata: {
        total_rows: 1,
        total_pages: 1,
        current_page_index: 0,
        page_size: 10,
        query_complete: true,
        query_status: "complete",
        query_sampled: false,
      },
      ...overrides,
    },
  },
});

describe("evalLogGridRead", () => {
  it("returns one structurally complete page with stable grid ids", async () => {
    const requestPage = vi.fn().mockResolvedValue(pageResponse());

    await expect(
      readEvalLogGridPage(requestPage, { currentPageIndex: 0, pageSize: 10 }),
    ).resolves.toEqual({
      columns: [{ id: "column1", name: "Evaluation ID" }],
      rows: [
        expect.objectContaining({
          rowId: "11111111-1111-4111-8111-111111111111",
          logId: "11111111-1111-4111-8111-111111111111",
        }),
      ],
      totalRows: 1,
    });
    expect(requestPage).toHaveBeenCalledWith(
      expect.objectContaining({
        signal: expect.any(AbortSignal),
        timeout: EVAL_LOG_GRID_REQUEST_TIMEOUT_MS,
      }),
    );
  });

  it.each([
    { column_config: null },
    { metadata: { total_rows: 0 } },
    {
      table: [],
      metadata: {
        total_rows: 0,
        total_pages: 0,
        current_page_index: 0,
        page_size: 10,
        query_complete: false,
        query_status: "complete",
        query_sampled: false,
      },
    },
    {
      table: [
        { row_id: "duplicate", log_id: "first" },
        { row_id: "duplicate", log_id: "second" },
      ],
      metadata: {
        total_rows: 2,
        total_pages: 1,
        current_page_index: 0,
        page_size: 10,
        query_complete: true,
        query_status: "complete",
        query_sampled: false,
      },
    },
  ])(
    "fails closed instead of fabricating an empty success",
    async (overrides) => {
      await expect(
        readEvalLogGridPage(() => Promise.resolve(pageResponse(overrides)), {
          currentPageIndex: 0,
          pageSize: 10,
        }),
      ).rejects.toMatchObject({ code: "eval_log_invalid_page" });
    },
  );

  it("aborts a stalled page before the interactive browser wall", async () => {
    vi.useFakeTimers();
    let signal;
    const pending = readEvalLogGridPage(({ signal: requestSignal }) => {
      signal = requestSignal;
      return new Promise(() => {});
    });
    const rejection = expect(pending).rejects.toMatchObject({
      code: "aggregation_request_timeout",
    });

    await vi.advanceTimersByTimeAsync(EVAL_LOG_GRID_REQUEST_TIMEOUT_MS);
    await rejection;
    expect(signal.aborted).toBe(true);
    expect(EVAL_LOG_GRID_REQUEST_TIMEOUT_MS).toBe(ANALYTICS_REQUEST_TIMEOUT_MS);
    vi.useRealTimers();
  });

  it("rejects a server page above the configured response bound", async () => {
    const oversized = pageResponse();
    oversized.data.result.metadata.page_size = INTERACTIVE_MAX_PAGE_SIZE + 1;

    await expect(
      readEvalLogGridPage(() => Promise.resolve(oversized)),
    ).rejects.toMatchObject({ code: "eval_log_invalid_page" });
  });
});
