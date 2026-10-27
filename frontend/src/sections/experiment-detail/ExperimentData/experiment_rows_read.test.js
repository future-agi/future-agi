import { describe, expect, it, vi } from "vitest";

import {
  createExperimentRowsActionBudget,
  EXPERIMENT_ROWS_REQUEST_TIMEOUT_MS,
  readExperimentColumnConfig,
  readExperimentRowsPage,
} from "./experiment_rows_read";

const validPage = (overrides = {}) => ({
  data: {
    status: true,
    result: {
      column_config: [{ id: "prompt" }],
      table: [{ row_id: "row-1", prompt: "hello" }],
      metadata: { total_rows: 11, total_pages: 2 },
      status: "Completed",
      ...overrides,
    },
  },
});

describe("experimentRowsRead", () => {
  it("returns one structurally valid bounded grid block", async () => {
    const requestPage = vi.fn().mockResolvedValue(validPage());

    await expect(
      readExperimentRowsPage(requestPage, undefined, { pageSize: 10 }),
    ).resolves.toMatchObject({
      table: [{ row_id: "row-1", prompt: "hello" }],
      metadata: { total_rows: 11, total_pages: 2 },
    });
    expect(requestPage).toHaveBeenCalledWith({
      signal: expect.any(AbortSignal),
      timeout: EXPERIMENT_ROWS_REQUEST_TIMEOUT_MS,
    });
  });

  it.each([
    undefined,
    { column_config: [], table: [], metadata: {}, status: "Completed" },
    {
      column_config: [],
      table: [{ row_id: "row-1" }, { row_id: "row-1" }],
      metadata: { total_rows: 2, total_pages: 1 },
      status: "Completed",
    },
    {
      column_config: [],
      table: [{ row_id: "row-1" }],
      metadata: { total_rows: 1, total_pages: 2 },
      status: "Completed",
    },
  ])(
    "rejects malformed blocks instead of reporting empty rows",
    async (result) => {
      await expect(
        readExperimentRowsPage(
          () => Promise.resolve({ data: { status: true, result } }),
          undefined,
          { pageSize: 10 },
        ),
      ).rejects.toMatchObject({ code: "experiment_rows_invalid_response" });
    },
  );

  it("validates the column-only form of the changed rows endpoint", async () => {
    await expect(
      readExperimentColumnConfig(() =>
        Promise.resolve({
          data: {
            status: true,
            result: { column_config: [], status: "Running" },
          },
        }),
      ),
    ).resolves.toMatchObject({
      result: { column_config: [], status: "Running" },
    });
  });

  it("shrinks every cached-block request against one refresh budget", () => {
    let currentTime = 1_000;
    const budget = createExperimentRowsActionBudget({
      now: () => currentTime,
    });

    expect(budget.remainingMs()).toBe(EXPERIMENT_ROWS_REQUEST_TIMEOUT_MS);
    currentTime += EXPERIMENT_ROWS_REQUEST_TIMEOUT_MS - 250;
    expect(budget.remainingMs()).toBe(250);
    currentTime += 250;
    expect(() => budget.remainingMs()).toThrowError(
      expect.objectContaining({ code: "aggregation_request_timeout" }),
    );
  });

  it("aborts a stalled block before ten seconds", async () => {
    vi.useFakeTimers();
    let signal;
    const pending = readExperimentRowsPage(
      (options) => {
        signal = options.signal;
        return new Promise(() => {});
      },
      undefined,
      { pageSize: 10 },
    );
    const rejection = expect(pending).rejects.toMatchObject({
      code: "aggregation_request_timeout",
    });

    await vi.advanceTimersByTimeAsync(EXPERIMENT_ROWS_REQUEST_TIMEOUT_MS);

    await rejection;
    expect(signal.aborted).toBe(true);
    vi.useRealTimers();
  });
});
