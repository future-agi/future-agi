import { describe, expect, it, vi } from "vitest";
import { DATASET_ROW_ADJACENCY_MAX_ROWS } from "src/config/runtime_limits";

import {
  DATASET_POINT_READ_TIMEOUT_MS,
  readDatasetCellRow,
  readDatasetRowAdjacency,
  runDatasetPointReadAction,
} from "./dataset_point_read";

const rowId = "00000000-0000-4000-8000-000000000001";
const nextId = "00000000-0000-4000-8000-000000000002";

const adjacencyResponse = (overrides = {}) => ({
  data: {
    status: true,
    result: {
      current: { row_id: rowId },
      next: { row_id: [nextId] },
      ...overrides,
    },
  },
});

describe("dataset point reads", () => {
  it("validates the authoritative row adjacency and passes a bounded signal", async () => {
    const requestRow = vi.fn(({ signal, timeout }) => {
      expect(signal).toBeInstanceOf(AbortSignal);
      expect(timeout).toBe(DATASET_POINT_READ_TIMEOUT_MS);
      return Promise.resolve(adjacencyResponse());
    });

    await expect(
      readDatasetRowAdjacency(requestRow, { row_id: rowId }),
    ).resolves.toEqual({
      current: { row_id: rowId },
      nextRowIds: [nextId],
    });
  });

  it.each([
    [{ current: null }, "missing current row"],
    [{ current: { row_id: nextId } }, "wrong current row"],
    [{ next: { rowId: [nextId] } }, "legacy continuation key"],
    [{ next: { row_id: [nextId, nextId] } }, "duplicate continuation"],
    [{ next: { row_id: [rowId] } }, "self continuation"],
  ])("fails closed for malformed adjacency: %s (%s)", async (overrides) => {
    await expect(
      readDatasetRowAdjacency(
        () => Promise.resolve(adjacencyResponse(overrides)),
        { row_id: rowId },
      ),
    ).rejects.toMatchObject({
      code: "dataset_row_adjacency_invalid_response",
    });
  });

  it("rejects an adjacency window above the configured response bound", async () => {
    const oversized = Array.from(
      { length: DATASET_ROW_ADJACENCY_MAX_ROWS + 1 },
      (_, index) => `row-${index}`,
    );

    await expect(
      readDatasetRowAdjacency(
        () =>
          Promise.resolve(adjacencyResponse({ next: { row_id: oversized } })),
        { row_id: rowId },
      ),
    ).rejects.toMatchObject({
      code: "dataset_row_adjacency_invalid_response",
    });
  });

  it("fails closed instead of publishing a missing cell row", async () => {
    await expect(
      readDatasetCellRow(
        () => Promise.resolve({ data: { status: true, result: {} } }),
        { row_ids: [rowId], column_ids: ["column-1"] },
      ),
    ).rejects.toMatchObject({ code: "dataset_cell_data_invalid_response" });
  });

  it("retains the requested row identity for the next adjacency read", async () => {
    await expect(
      readDatasetCellRow(
        () =>
          Promise.resolve({
            data: {
              status: true,
              result: { [rowId]: { "column-1": { cell_value: "value" } } },
            },
          }),
        { row_ids: [rowId], column_ids: ["column-1"] },
      ),
    ).resolves.toEqual({
      row_id: rowId,
      "column-1": { cell_value: "value" },
    });
  });

  it("uses one configured signal across sequential point reads", async () => {
    vi.useFakeTimers();
    let outerSignal;
    let secondReadStarted = false;
    const action = runDatasetPointReadAction(async (signal) => {
      outerSignal = signal;
      await new Promise((resolve) =>
        globalThis.setTimeout(resolve, DATASET_POINT_READ_TIMEOUT_MS - 500),
      );
      secondReadStarted = true;
      return new Promise(() => {});
    });
    const rejection = expect(action).rejects.toMatchObject({
      code: "aggregation_request_timeout",
    });

    await vi.advanceTimersByTimeAsync(DATASET_POINT_READ_TIMEOUT_MS);

    await rejection;
    expect(secondReadStarted).toBe(true);
    expect(outerSignal.aborted).toBe(true);
    vi.useRealTimers();
  });
});
