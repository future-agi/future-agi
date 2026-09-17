import { describe, expect, it, vi } from "vitest";

import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";

import {
  EVAL_TASK_LOG_REQUEST_TIMEOUT_MS,
  readEvalTaskLogs,
} from "./task_log_read";

const validResult = (overrides = {}) => ({
  success_count: 7,
  errors_count: 1,
  skipped_count: 1,
  warnings_count: 2,
  total_count: 10,
  error_groups: [],
  warning_groups: [],
  error_groups_truncated: false,
  warning_groups_truncated: false,
  status: "running",
  row_type: "spans",
  ...overrides,
});

describe("readEvalTaskLogs", () => {
  it("returns a valid bounded summary and forwards cancellation options", async () => {
    const requestLogs = vi.fn().mockResolvedValue({
      data: { status: true, result: validResult() },
    });

    await expect(readEvalTaskLogs(requestLogs)).resolves.toEqual(validResult());
    expect(requestLogs).toHaveBeenCalledWith({
      signal: expect.any(AbortSignal),
      timeout: EVAL_TASK_LOG_REQUEST_TIMEOUT_MS,
    });
  });

  it.each([
    undefined,
    validResult({ total_count: 1 }),
    validResult({ error_groups: null }),
    validResult({ status: "" }),
  ])(
    "rejects malformed summaries instead of publishing zero counts",
    async (result) => {
      await expect(
        readEvalTaskLogs(() =>
          Promise.resolve({ data: { status: true, result } }),
        ),
      ).rejects.toMatchObject({ code: "eval_task_logs_invalid_response" });
    },
  );

  it("aborts a stalled poll at the configured interactive wall", async () => {
    vi.useFakeTimers();
    let signal;
    const pending = readEvalTaskLogs((options) => {
      signal = options.signal;
      return new Promise(() => {});
    });
    const rejection = expect(pending).rejects.toMatchObject({
      code: "aggregation_request_timeout",
    });

    await vi.advanceTimersByTimeAsync(EVAL_TASK_LOG_REQUEST_TIMEOUT_MS);

    await rejection;
    expect(signal.aborted).toBe(true);
    expect(EVAL_TASK_LOG_REQUEST_TIMEOUT_MS).toBe(
      INTERACTIVE_REQUEST_TIMEOUT_MS,
    );
    vi.useRealTimers();
  });
});
