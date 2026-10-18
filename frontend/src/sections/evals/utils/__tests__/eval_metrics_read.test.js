import { describe, expect, it, vi } from "vitest";

import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";
import { EVAL_METRIC_MAX_WINDOW_DAYS } from "src/config/runtime_limits";

import {
  EVAL_METRICS_REQUEST_TIMEOUT_MS,
  readEvalMetrics,
} from "../eval_metrics_read";

const validResponse = () => ({
  data: {
    status: true,
    result: {
      base_eval_template_id: "eval-1",
      api_call_count: {
        api_call_count: 2,
        count_graph_data: [{ timestamp: "2026-08-14T00:00:00Z", value: 2 }],
      },
      average: {
        average: 75,
        avg_graph_data: [{ timestamp: "2026-08-14T00:00:00Z", value: 75 }],
      },
      metadata: {
        bucket_count: 1,
        query_complete: true,
        query_sampled: false,
        has_more: false,
        max_window_days: EVAL_METRIC_MAX_WINDOW_DAYS,
      },
    },
  },
});

describe("readEvalMetrics", () => {
  it("returns only a complete aligned graph and forwards the action signal", async () => {
    const requestMetrics = vi.fn().mockResolvedValue(validResponse());

    await expect(readEvalMetrics(requestMetrics)).resolves.toEqual(
      validResponse().data,
    );
    expect(requestMetrics).toHaveBeenCalledWith({
      signal: expect.any(AbortSignal),
      timeout: EVAL_METRICS_REQUEST_TIMEOUT_MS,
    });
  });

  it.each([
    undefined,
    { status: true, result: {} },
    (() => {
      const body = validResponse().data;
      body.result.metadata.query_complete = false;
      return body;
    })(),
    (() => {
      const body = validResponse().data;
      body.result.average.avg_graph_data[0].timestamp = "2026-08-15T00:00:00Z";
      return body;
    })(),
  ])(
    "rejects malformed or incomplete graphs instead of charting empty data",
    async (body) => {
      await expect(
        readEvalMetrics(() => Promise.resolve({ data: body })),
      ).rejects.toMatchObject({ code: "eval_metrics_invalid_response" });
    },
  );

  it("aborts a stalled graph request before ten seconds", async () => {
    vi.useFakeTimers();
    let signal;
    const pending = readEvalMetrics((options) => {
      signal = options.signal;
      return new Promise(() => {});
    });
    const rejection = expect(pending).rejects.toMatchObject({
      code: "aggregation_request_timeout",
    });

    await vi.advanceTimersByTimeAsync(EVAL_METRICS_REQUEST_TIMEOUT_MS);

    await rejection;
    expect(signal.aborted).toBe(true);
    expect(EVAL_METRICS_REQUEST_TIMEOUT_MS).toBe(
      INTERACTIVE_REQUEST_TIMEOUT_MS,
    );
    vi.useRealTimers();
  });

  it("rejects a server window above the configured response bound", async () => {
    const response = validResponse();
    response.data.result.metadata.max_window_days =
      EVAL_METRIC_MAX_WINDOW_DAYS + 1;

    await expect(
      readEvalMetrics(() => Promise.resolve(response)),
    ).rejects.toMatchObject({ code: "eval_metrics_invalid_response" });
  });
});
