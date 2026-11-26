import { describe, expect, it, vi } from "vitest";

import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";

import {
  RUN_INSIGHT_LIST_REQUEST_TIMEOUT_MS,
  readRunInsightListPage,
} from "./run_insight_list_read";

describe("runInsightListRead", () => {
  it("passes a signal and the configured interactive timeout to the transport", async () => {
    const requestPage = vi.fn().mockResolvedValue({ data: { result: {} } });

    await expect(readRunInsightListPage(requestPage)).resolves.toEqual({
      data: { result: {} },
    });
    expect(requestPage).toHaveBeenCalledWith(
      expect.objectContaining({
        signal: expect.any(AbortSignal),
        timeout: RUN_INSIGHT_LIST_REQUEST_TIMEOUT_MS,
      }),
    );
    expect(RUN_INSIGHT_LIST_REQUEST_TIMEOUT_MS).toBe(
      INTERACTIVE_REQUEST_TIMEOUT_MS,
    );
  });
});
