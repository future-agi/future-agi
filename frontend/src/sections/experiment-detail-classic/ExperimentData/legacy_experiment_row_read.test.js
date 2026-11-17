import { describe, expect, it, vi } from "vitest";
import { DATASET_ROW_ADJACENCY_MAX_ROWS } from "src/config/runtime_limits";

import {
  LEGACY_EXPERIMENT_ROW_REQUEST_TIMEOUT_MS,
  readLegacyExperimentRow,
} from "./legacy_experiment_row_read";

const rowId = "00000000-0000-4000-8000-000000000001";
const nextId = "00000000-0000-4000-8000-000000000002";

const response = (overrides = {}) => ({
  data: {
    status: true,
    result: {
      column_config: [],
      table: [{ row_id: rowId }],
      next_row_ids: [nextId],
      ...overrides,
    },
  },
});

describe("readLegacyExperimentRow", () => {
  it("reads the authoritative snake-case continuation under one wall", async () => {
    const requestRow = vi.fn(({ signal, timeout }) => {
      expect(signal).toBeInstanceOf(AbortSignal);
      expect(timeout).toBe(LEGACY_EXPERIMENT_ROW_REQUEST_TIMEOUT_MS);
      return Promise.resolve(response());
    });

    await expect(readLegacyExperimentRow(requestRow, rowId)).resolves.toEqual([
      nextId,
    ]);
    expect(requestRow).toHaveBeenCalledOnce();
  });

  it.each([
    [{ next_row_ids: null }, "missing continuation"],
    [{ nextRowIds: [nextId], next_row_ids: undefined }, "legacy key only"],
    [{ table: [{ row_id: nextId }] }, "wrong point row"],
    [{ next_row_ids: [nextId, nextId] }, "duplicate continuation"],
    [{ next_row_ids: [rowId] }, "self continuation"],
  ])("fails closed for %s (%s)", async (overrides) => {
    await expect(
      readLegacyExperimentRow(
        () => Promise.resolve(response(overrides)),
        rowId,
      ),
    ).rejects.toMatchObject({ code: "legacy_experiment_row_invalid_response" });
  });

  it("rejects a continuation above the configured response bound", async () => {
    const oversized = Array.from(
      { length: DATASET_ROW_ADJACENCY_MAX_ROWS + 1 },
      (_, index) => `row-${index}`,
    );

    await expect(
      readLegacyExperimentRow(
        () => Promise.resolve(response({ next_row_ids: oversized })),
        rowId,
      ),
    ).rejects.toMatchObject({
      code: "legacy_experiment_row_invalid_response",
    });
  });

  it("aborts a stalled request at the configured wall", async () => {
    vi.useFakeTimers();
    let requestSignal;
    const request = readLegacyExperimentRow(({ signal }) => {
      requestSignal = signal;
      return new Promise(() => {});
    }, rowId);
    const rejection = expect(request).rejects.toMatchObject({
      code: "aggregation_request_timeout",
    });

    await vi.advanceTimersByTimeAsync(LEGACY_EXPERIMENT_ROW_REQUEST_TIMEOUT_MS);

    await rejection;
    expect(requestSignal.aborted).toBe(true);
    vi.useRealTimers();
  });
});
