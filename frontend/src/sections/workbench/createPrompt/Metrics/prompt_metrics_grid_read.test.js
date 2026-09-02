import { describe, expect, it, vi } from "vitest";

import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";

import {
  PROMPT_METRICS_REQUEST_TIMEOUT_MS,
  readPromptMetricsGridPage,
} from "./prompt_metrics_grid_read";

describe("promptMetricsGridRead", () => {
  it("returns a bounded, structurally valid page", async () => {
    const requestPage = vi.fn().mockResolvedValue({
      data: {
        result: {
          config: [{ id: "metric" }],
          table: [{ prompt_version_id: "version-1" }],
          metadata: { total_rows: 1 },
        },
      },
    });

    await expect(readPromptMetricsGridPage(requestPage)).resolves.toEqual({
      columns: [{ id: "metric" }],
      rowData: [{ prompt_version_id: "version-1" }],
      totalRows: 1,
    });
    expect(requestPage).toHaveBeenCalledWith(
      expect.objectContaining({
        signal: expect.any(AbortSignal),
        timeout: PROMPT_METRICS_REQUEST_TIMEOUT_MS,
      }),
    );
  });

  it.each([
    undefined,
    { config: [], table: [], metadata: {} },
    { config: [], table: [{}], metadata: { total_rows: 0 } },
  ])("fails closed for a malformed page", async (result) => {
    await expect(
      readPromptMetricsGridPage(() =>
        Promise.resolve({ data: result === undefined ? {} : { result } }),
      ),
    ).rejects.toMatchObject({ code: "prompt_metrics_invalid_page" });
  });

  it("aborts and rejects a stalled page before the interactive wall", async () => {
    vi.useFakeTimers();
    let signal;
    const pending = readPromptMetricsGridPage((options) => {
      signal = options.signal;
      return new Promise(() => {});
    });
    const rejection = expect(pending).rejects.toMatchObject({
      code: "aggregation_request_timeout",
    });

    await vi.advanceTimersByTimeAsync(PROMPT_METRICS_REQUEST_TIMEOUT_MS);
    await rejection;
    expect(signal.aborted).toBe(true);
    expect(PROMPT_METRICS_REQUEST_TIMEOUT_MS).toBe(
      INTERACTIVE_REQUEST_TIMEOUT_MS,
    );
    vi.useRealTimers();
  });
});
