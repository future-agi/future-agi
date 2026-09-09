import { describe, expect, it, vi } from "vitest";

import { INTERACTIVE_MAX_PAGE_SIZE } from "src/config/runtime_limits";

import {
  EVAL_LOG_GRID_REQUEST_TIMEOUT_MS,
  createEvalLogReadScope,
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

  it("allows a slow exact page to complete beyond the former deadline", async () => {
    vi.useFakeTimers();
    try {
      let signal;
      let complete;
      const pending = readEvalLogGridPage(({ signal: requestSignal, timeout }) => {
        signal = requestSignal;
        expect(timeout).toBe(0);
        return new Promise((resolve) => { complete = resolve; });
      });
      await vi.advanceTimersByTimeAsync(120_000);
      expect(signal.aborted).toBe(false);
      complete(pageResponse());
      await expect(pending).resolves.toMatchObject({ totalRows: 1 });
    } finally {
      vi.useRealTimers();
    }
  });

  it("settles cancellation even when the exact transport ignores abort", async () => {
    const controller = new AbortController();
    let signal;
    const pending = readEvalLogGridPage(({ signal: requestSignal }) => {
      signal = requestSignal;
      return new Promise(() => {});
    }, { signal: controller.signal });
    const rejected = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    controller.abort();
    await rejected;
    expect(signal.aborted).toBe(true);
  });

  it("rejects a server page above the configured response bound", async () => {
    const oversized = pageResponse();
    oversized.data.result.metadata.page_size = INTERACTIVE_MAX_PAGE_SIZE + 1;

    await expect(
      readEvalLogGridPage(() => Promise.resolve(oversized)),
    ).rejects.toMatchObject({ code: "eval_log_invalid_page" });
  });
});


describe("evaluation log read scope", () => {
  it("cancels every old request and allows a replacement generation", async () => {
    const scope = createEvalLogReadScope();
    const before = scope.generation();
    const signals = [];
    const oldRead = () => scope.readPage(({ signal }) => {
      signals.push(signal);
      return new Promise(() => {});
    });
    const first = oldRead();
    const second = oldRead();
    const rejected = Promise.all([
      expect(first).rejects.toMatchObject({ name: "AbortError" }),
      expect(second).rejects.toMatchObject({ name: "AbortError" }),
    ]);
    scope.cancel();
    await rejected;
    expect(signals.every((signal) => signal.aborted)).toBe(true);
    expect(scope.isCurrent(before)).toBe(false);
    await expect(scope.readPage(() => Promise.resolve(pageResponse()))).resolves.toMatchObject({ totalRows: 1 });
  });

  it("rejects a late success after datasource destruction", async () => {
    const scope = createEvalLogReadScope();
    let finish;
    const pending = scope.readPage(() => new Promise((resolve) => { finish = resolve; }));
    const rejected = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    finish(pageResponse());
    scope.cancel();
    await rejected;
    scope.cancel(); // Repeated cleanup must not poison a later mount.
    await expect(scope.readPage(() => Promise.resolve(pageResponse()))).resolves.toMatchObject({ totalRows: 1 });
  });
});
