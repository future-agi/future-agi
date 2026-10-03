import { describe, expect, it, vi } from "vitest";
import {
  AGGREGATION_POLL_MAX_ATTEMPTS,
  AGGREGATION_POLL_MAX_CONSECUTIVE_FAILURES,
  AGGREGATION_POLL_TIMEOUT_MS,
  AGGREGATION_REQUEST_TIMEOUT_MS,
  awaitAggregationRequestWithDeadline,
  createAggregationPollController,
  failServerSideGridRead,
  getAttributeLookupMessage,
  getAggregationPollDelay,
  getAggregationRefreshState,
  getExactAggregationReadState,
  getExactGraphData,
  getFilterValueReadMessage,
  getFilterValueReadState,
  getQueryCompletedAt,
  getQueryReadMessage,
  getQueryReadState,
  getRenderableGraphData,
  isAggregationPollBudgetExhausted,
  QUERY_FAILED_RETRY_MESSAGE,
  QUERY_READ_RETRY_MESSAGE,
  QUERY_READ_SAMPLED_MESSAGE,
} from "../queryReadState";

describe("queryReadState", () => {
  it("keeps bounded-read UI copy free of sampling and incompleteness claims", () => {
    const customerMessages = [
      getQueryReadMessage("sampled"),
      getQueryReadMessage("degraded"),
      getQueryReadMessage("error"),
      getFilterValueReadMessage("sampled"),
      getFilterValueReadMessage("degraded"),
      getAttributeLookupMessage("sampled"),
      getAttributeLookupMessage("degraded"),
    ].filter(Boolean);

    expect(customerMessages.join(" ")).not.toMatch(
      /sampled|sample-limited|incomplete|estimat/i,
    );
  });

  it("reads only the backend aggregation completion time", () => {
    expect(
      getQueryCompletedAt({
        result: { query_completed_at: "2026-08-03T02:00:00Z" },
      }),
    ).toEqual(new Date("2026-08-03T02:00:00Z"));

    expect(getQueryCompletedAt({ result: {} })).toBeNull();
    expect(
      getQueryCompletedAt({
        result: { query_completed_at: "not-a-timestamp" },
      }),
    ).toBeNull();
  });

  it("reads status and completion time through an Axios response wrapper", () => {
    const response = {
      data: {
        result: {
          query_complete: false,
          query_status: "degraded",
          query_completed_at: "2026-08-03T02:00:00Z",
        },
      },
    };

    expect(getQueryReadState(response)).toBe("degraded");
    expect(getQueryCompletedAt(response)).toEqual(
      new Date("2026-08-03T02:00:00Z"),
    );
  });

  it("preserves legacy behaviour when bounded-read metadata is absent", () => {
    expect(getQueryReadState({ result: { table: [] } })).toBe("complete");
  });

  it("keeps list compatibility while failing metadata-less aggregates closed", () => {
    const payload = { result: { data: [{ value: 1 }] } };
    expect(getQueryReadState(payload)).toBe("complete");
    expect(getExactAggregationReadState(payload)).toBe("degraded");
  });

  it("recognizes a queued exact aggregation as pending but non-chartable", () => {
    const payload = {
      data: [],
      query_complete: false,
      query_status: "pending",
      query_sampled: false,
      query_refreshing: true,
    };

    expect(getQueryReadState(payload)).toBe("degraded");
    expect(getExactAggregationReadState(payload)).toBe("pending");
    expect(getExactGraphData(payload)).toEqual([]);
    expect(getAggregationRefreshState(payload)).toEqual({
      isRefreshing: true,
      refreshFailed: false,
    });
  });

  it("keeps a cached exact snapshot chartable while its replacement is queued", () => {
    const payload = {
      data: [{ timestamp: "2026-08-03T00:00:00Z", value: 2 }],
      query_complete: true,
      query_status: "complete",
      query_sampled: false,
      query_refreshing: true,
    };

    expect(getExactAggregationReadState(payload)).toBe("complete");
    expect(getExactGraphData(payload)).toEqual(payload.data);
    expect(getAggregationRefreshState(payload).isRefreshing).toBe(true);
  });

  it("recognizes terminal refresh failure without making prior exact data unsafe", () => {
    const payload = {
      result: {
        data: [],
        query_complete: true,
        query_status: "complete",
        query_sampled: false,
        query_refreshing: false,
        query_refresh_failed: true,
      },
    };

    expect(getExactAggregationReadState(payload)).toBe("complete");
    expect(getAggregationRefreshState(payload)).toEqual({
      isRefreshing: false,
      refreshFailed: true,
    });
  });

  it("supports exactness metadata on each item of an aggregation array", () => {
    expect(
      getExactAggregationReadState({
        result: [
          {
            data: [],
            query_complete: true,
            query_status: "complete",
            query_sampled: false,
          },
        ],
      }),
    ).toBe("complete");
  });

  it("backs aggregation polling off to a bounded maximum", () => {
    expect([0, 1, 2, 3, 99].map(getAggregationPollDelay)).toEqual([
      1000, 2000, 4000, 8000, 8000,
    ]);
  });

  it("bounds exact aggregation polling by both attempts and elapsed time", () => {
    expect(AGGREGATION_POLL_TIMEOUT_MS).toBeGreaterThan(
      AGGREGATION_REQUEST_TIMEOUT_MS,
    );
    expect(AGGREGATION_REQUEST_TIMEOUT_MS).toBe(30_000);
    expect(
      isAggregationPollBudgetExhausted({
        attempt: AGGREGATION_POLL_MAX_ATTEMPTS - 1,
        startedAt: 1000,
        now: 1000 + AGGREGATION_POLL_TIMEOUT_MS - 1,
      }),
    ).toBe(false);
    expect(
      isAggregationPollBudgetExhausted({
        attempt: AGGREGATION_POLL_MAX_ATTEMPTS,
        startedAt: 1000,
        now: 1001,
      }),
    ).toBe(true);
    expect(
      isAggregationPollBudgetExhausted({
        attempt: 1,
        startedAt: 1000,
        now: 1000 + AGGREGATION_POLL_TIMEOUT_MS,
      }),
    ).toBe(true);
  });

  it("charges pending polls to the first visible action and resets only for retry", () => {
    let now = 1_000;
    const controller = createAggregationPollController({ now: () => now });

    expect(controller.start()).toBe(true);
    expect(controller.recordAttempt()).toBe(true);
    expect(controller.remainingMs(AGGREGATION_REQUEST_TIMEOUT_MS)).toBe(
      AGGREGATION_REQUEST_TIMEOUT_MS,
    );
    now += 1_000;
    expect(controller.remainingMs(AGGREGATION_REQUEST_TIMEOUT_MS)).toBe(
      AGGREGATION_REQUEST_TIMEOUT_MS,
    );
    now += AGGREGATION_POLL_TIMEOUT_MS - 1_500;

    // Even the shortest poll delay would cross the action wall.
    expect(controller.nextDelay()).toBe(false);
    expect(controller.isExhausted()).toBe(true);
    expect(controller.start()).toBe(false);

    // The user-facing Refresh/Retry action owns a fresh wall.
    controller.reset();
    expect(controller.start()).toBe(true);
    expect(controller.remainingMs(AGGREGATION_REQUEST_TIMEOUT_MS)).toBe(
      AGGREGATION_REQUEST_TIMEOUT_MS,
    );
    expect(controller.nextDelay()).toBe(1_000);
  });

  it("uses one finite polling lifecycle until an explicit reset", () => {
    let now = 1_000;
    const controller = createAggregationPollController({ now: () => now });

    expect(controller.start()).toBe(true);
    for (let index = 0; index < AGGREGATION_POLL_MAX_ATTEMPTS; index += 1) {
      expect(controller.nextDelay()).not.toBe(false);
      expect(controller.recordAttempt()).toBe(true);
    }
    expect(controller.nextDelay()).toBe(false);
    expect(controller.isExhausted()).toBe(true);
    expect(controller.getTerminationReason()).toBe("poll_budget");
    expect(controller.start()).toBe(false);

    controller.reset();
    expect(controller.getTerminationReason()).toBeNull();
    expect(controller.start()).toBe(true);
    now += AGGREGATION_POLL_TIMEOUT_MS;
    expect(controller.nextDelay()).toBe(false);
    expect(controller.getTerminationReason()).toBe("poll_budget");
  });

  it("terminates a polling lifecycle after bounded consecutive failures", () => {
    const controller = createAggregationPollController();
    controller.start();

    for (
      let index = 1;
      index < AGGREGATION_POLL_MAX_CONSECUTIVE_FAILURES;
      index += 1
    ) {
      expect(controller.recordFailure()).toBe(true);
    }
    expect(controller.recordFailure()).toBe(false);
    expect(controller.isActive()).toBe(false);
    expect(controller.isExhausted()).toBe(true);
    expect(controller.getTerminationReason()).toBe("transport_failures");
  });

  it("aborts the underlying aggregation request at its transport deadline and ignores late completion", async () => {
    vi.useFakeTimers();
    let requestSignal;
    let resolveLate;
    const request = awaitAggregationRequestWithDeadline(
      (signal) => {
        requestSignal = signal;
        return new Promise((resolve) => {
          resolveLate = resolve;
        });
      },
      { timeoutMs: 25 },
    );
    const rejection = expect(request).rejects.toMatchObject({
      code: "aggregation_request_timeout",
    });

    await vi.advanceTimersByTimeAsync(25);

    expect(requestSignal.aborted).toBe(true);
    resolveLate("too late");
    await vi.advanceTimersByTimeAsync(0);
    await rejection;
    vi.useRealTimers();
  });

  it("links caller cancellation to the underlying aggregation transport", async () => {
    const upstream = new AbortController();
    let requestSignal;
    const request = awaitAggregationRequestWithDeadline(
      (signal) => {
        requestSignal = signal;
        return new Promise(() => {});
      },
      { timeoutMs: 1000, signal: upstream.signal },
    );
    const rejection = expect(request).rejects.toMatchObject({
      name: "AbortError",
    });

    upstream.abort();

    expect(requestSignal.aborted).toBe(true);
    await rejection;
  });

  it("recognizes explicit complete metadata", () => {
    expect(
      getQueryReadState({
        result: {
          metadata: { query_complete: true, query_status: "complete" },
        },
      }),
    ).toBe("complete");
  });

  it.each([
    ["only a completion flag", { query_complete: true }],
    ["only a status flag", { query_status: "complete" }],
    [
      "an error code without a status pair",
      { query_error_code: "query_failed" },
    ],
    [
      "sampling coverage without a status pair",
      {
        query_sampling_strategy: "time_stratified_latest_state",
        query_sampling_strata: 8,
        query_sampling_strata_completed: 8,
      },
    ],
    [
      "a complete status marked incomplete",
      { query_complete: false, query_status: "complete" },
    ],
    [
      "a degraded status marked complete",
      { query_complete: true, query_status: "degraded" },
    ],
    [
      "an exact result with an active error code",
      {
        query_complete: true,
        query_status: "complete",
        query_error_code: "read_budget_exceeded",
      },
    ],
    [
      "an exact result marked sampled",
      {
        query_complete: true,
        query_status: "complete",
        query_sampled: true,
      },
    ],
  ])("fails closed for %s", (_, payload) => {
    expect(getQueryReadState(payload)).toBe("degraded");
  });

  it("rejects a sampled status that contradicts its completion flag", () => {
    expect(
      getQueryReadState({
        query_complete: true,
        query_status: "sampled",
        query_sampling_strategy: "time_stratified_latest_state",
        query_sampling_strata: 8,
        query_sampling_strata_completed: 8,
      }),
    ).toBe("degraded");
  });

  it.each([
    { query_complete: false },
    { query_status: "degraded" },
    { result: { query_complete: false, query_status: "degraded" } },
    { result: { metadata: { query_status: "degraded" } } },
  ])("recognizes degraded metadata at every API response level", (payload) => {
    expect(getQueryReadState(payload)).toBe("degraded");
    expect(getQueryReadMessage("degraded")).toBe(QUERY_READ_RETRY_MESSAGE);
  });

  it("recognizes an explicitly sampled graph without treating it as a failure", () => {
    const payload = {
      query_complete: false,
      query_status: "sampled",
      query_sampling_strategy: "time_stratified_latest_state",
      query_sampling_strata: 8,
      query_sampling_strata_completed: 8,
    };

    expect(getQueryReadState(payload)).toBe("sampled");
    expect(getQueryReadMessage("sampled")).toBe(QUERY_READ_SAMPLED_MESSAGE);
  });

  it("uses picker-specific language for bounded attribute suggestions", () => {
    expect(getAttributeLookupMessage("sampled")).toMatch(
      /Recent matching attributes/i,
    );
    expect(getAttributeLookupMessage("error")).toMatch(
      /Enter an exact attribute name/i,
    );
    expect(getAttributeLookupMessage("complete")).toBeNull();
  });

  it("recognizes the endpoint-specific sampled filter-value contract", () => {
    const payload = {
      values: [{ value: "completed", label: "completed" }],
      query_complete: false,
      query_status: "sampled",
      query_error_code: "sample_limit",
    };

    // Graph reads still fail closed without graph coverage metadata.
    expect(getQueryReadState(payload)).toBe("degraded");
    expect(getFilterValueReadState(payload)).toBe("sampled");
    expect(getFilterValueReadMessage("sampled")).toBe(
      "Showing configured or recent suggestions only. Enter an exact value.",
    );
  });

  it("keeps malformed filter-value sampling metadata retryable", () => {
    expect(
      getFilterValueReadState({
        values: ["completed"],
        query_complete: false,
        query_status: "sampled",
      }),
    ).toBe("degraded");
    expect(getFilterValueReadMessage("degraded")).toMatch(/retry/i);
  });

  it("recognizes sampled metadata on public chart-series arrays", () => {
    const series = [
      {
        name: "quality",
        data: [],
        query_complete: false,
        query_status: "sampled",
        query_sampling_strategy: "time_stratified_latest_state",
        query_sampling_strata: 8,
        query_sampling_strata_completed: 8,
      },
    ];

    expect(getQueryReadState({ result: series })).toBe("sampled");
  });

  it("recognizes the bounded dashboard sample contract inside metric results", () => {
    const payload = {
      metrics: [
        {
          query_complete: false,
          query_status: "sampled",
          query_error_code: "sample_limit",
          query_sampling_strategy: "bounded_physical_rows_per_time_bucket",
          query_sampling_interval_seconds: 86400,
          query_sample_limit: 8192,
          query_sample_per_bucket: 128,
        },
      ],
    };

    expect(getQueryReadState(payload)).toBe("sampled");
  });

  const validDashboardSample = {
    query_complete: false,
    query_status: "sampled",
    query_error_code: "sample_limit",
    query_sampling_strategy: "bounded_physical_rows_per_time_bucket",
    query_sampling_interval_seconds: 86400,
    query_sample_limit: 8192,
    query_sample_per_bucket: 128,
  };

  it.each([
    ["missing completion flag", { query_complete: undefined }],
    ["contradictory completion flag", { query_complete: true }],
    ["missing error code", { query_error_code: undefined }],
    ["wrong error code", { query_error_code: "query_failed" }],
    ["wrong strategy", { query_sampling_strategy: "full_scan" }],
    ["missing interval", { query_sampling_interval_seconds: undefined }],
    ["zero interval", { query_sampling_interval_seconds: 0 }],
    ["missing sample limit", { query_sample_limit: undefined }],
    ["zero sample limit", { query_sample_limit: 0 }],
    ["missing per-bucket limit", { query_sample_per_bucket: undefined }],
    ["zero per-bucket limit", { query_sample_per_bucket: 0 }],
    ["per-bucket above total limit", { query_sample_per_bucket: 8193 }],
  ])("fails closed for a dashboard sample with %s", (_, invalidFields) => {
    expect(
      getQueryReadState({
        result: {
          metrics: [{ ...validDashboardSample, ...invalidFields }],
        },
      }),
    ).toBe("degraded");
  });

  it("applies the strictest state across nested dashboard metrics", () => {
    const complete = { query_complete: true, query_status: "complete" };
    const degraded = {
      query_complete: false,
      query_status: "degraded",
      query_error_code: "read_budget_exceeded",
    };

    expect(
      getQueryReadState({
        result: { metrics: [complete, validDashboardSample] },
      }),
    ).toBe("sampled");
    expect(
      getQueryReadState({
        result: { metrics: [complete, validDashboardSample, degraded] },
      }),
    ).toBe("degraded");
    expect(
      getQueryReadState({
        result: {
          metrics: [
            complete,
            validDashboardSample,
            { ...validDashboardSample, query_sample_per_bucket: 0 },
          ],
        },
      }),
    ).toBe("degraded");
  });

  it("uses a generic message for request failures", () => {
    const rawError = "Code: 159 DB::Exception: Timeout exceeded";
    expect(getQueryReadState({ result: rawError }, { isError: true })).toBe(
      "error",
    );
    expect(getQueryReadMessage("error")).toBe(QUERY_FAILED_RETRY_MESSAGE);
    expect(getQueryReadMessage("error")).not.toContain(rawError);
  });

  it("returns graph points only for explicitly complete responses", () => {
    const points = [{ timestamp: "2026-08-03T00:00:00Z", value: 2 }];

    expect(
      getExactGraphData({
        data: points,
        query_complete: true,
        query_status: "complete",
        query_sampled: false,
      }),
    ).toEqual(points);
    expect(getExactGraphData({ result: { data: points } })).toEqual([]);
  });

  it.each([{ query_complete: false }, { query_status: "degraded" }])(
    "refuses to chart points from an incomplete backend response",
    (metadata) => {
      const sampledPoints = [{ timestamp: "2026-08-03T00:00:00Z", value: 999 }];

      expect(getExactGraphData({ ...metadata, data: sampledPoints })).toEqual(
        [],
      );
      expect(
        getExactGraphData({
          ...metadata,
          result: { data: sampledPoints },
        }),
      ).toEqual([]);
    },
  );

  it("renders only explicitly labelled samples", () => {
    const points = [{ timestamp: "2026-08-03T00:00:00Z", value: 2 }];

    expect(
      getRenderableGraphData({
        data: points,
        query_complete: false,
        query_status: "sampled",
        query_sampling_strategy: "time_stratified_latest_state",
        query_sampling_strata: 8,
        query_sampling_strata_completed: 8,
      }),
    ).toEqual(points);
    expect(
      getRenderableGraphData({
        data: points,
        query_complete: false,
        query_status: "degraded",
      }),
    ).toEqual([]);
  });

  it.each([
    {},
    { query_sampling_strata: 8, query_sampling_strata_completed: 0 },
    { query_sampling_strata: 8, query_sampling_strata_completed: 1 },
  ])("refuses a sampled graph without full temporal coverage", (coverage) => {
    const payload = {
      data: [{ timestamp: "2026-08-03T00:00:00Z", value: 999 }],
      query_complete: false,
      query_status: "sampled",
      query_sampling_strategy: "time_stratified_latest_state",
      ...coverage,
    };

    expect(getQueryReadState(payload)).toBe("degraded");
    expect(getRenderableGraphData(payload)).toEqual([]);
  });

  it("preserves server-side pagination failure semantics", () => {
    const params = {
      fail: vi.fn(),
      success: vi.fn(),
      api: { showNoRowsOverlay: vi.fn() },
    };

    failServerSideGridRead(params);

    expect(params.fail).toHaveBeenCalledOnce();
    expect(params.success).not.toHaveBeenCalled();
    expect(params.api.showNoRowsOverlay).toHaveBeenCalledOnce();
  });

  it("does not report a late failure to a destroyed grid", () => {
    const params = {
      fail: vi.fn(),
      api: {
        isDestroyed: () => true,
        showNoRowsOverlay: vi.fn(),
      },
    };

    expect(failServerSideGridRead(params)).toBe(false);
    expect(params.fail).not.toHaveBeenCalled();
    expect(params.api.showNoRowsOverlay).not.toHaveBeenCalled();
  });
});
