import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import QueryEstimate from "./QueryEstimate";
import { ESTIMATE_DEBOUNCE_MS, describeEstimate } from "./estimateText";
import { sqlApi } from "./api";

vi.mock("./api", () => ({ sqlApi: { estimate: vi.fn() } }));

const within = {
  status: "ok",
  reason: "",
  sources: [
    {
      dataset: "eval_results",
      estimated: false,
      parts: null,
      marks: null,
      max_rows: null,
      reason: "Not estimated: shared index.",
    },
    {
      dataset: "spans",
      estimated: true,
      parts: 2,
      marks: 3,
      max_rows: 24576,
      reason: "",
    },
  ],
  max_rows_to_read: 10000000,
  exceeds_scan_bound: false,
  elapsed_ms: 4,
};

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  return { promise, resolve, reject };
}

async function settle(ms = ESTIMATE_DEBOUNCE_MS) {
  await act(async () => {
    vi.advanceTimersByTime(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("QueryEstimate", () => {
  it("waits for a typing pause and estimates only the latest SQL", async () => {
    sqlApi.estimate.mockResolvedValue(within);
    const { rerender } = render(
      <QueryEstimate projectId="p1" sql="SELECT 1 FROM spans" hours={24} />,
    );
    await settle(ESTIMATE_DEBOUNCE_MS - 100);
    rerender(
      <QueryEstimate projectId="p1" sql="SELECT 2 FROM spans" hours={24} />,
    );
    await settle(ESTIMATE_DEBOUNCE_MS - 100);
    expect(sqlApi.estimate).not.toHaveBeenCalled();
    await settle(100);
    expect(sqlApi.estimate).toHaveBeenCalledTimes(1);
    const [body, signal] = sqlApi.estimate.mock.calls[0];
    expect(body.project_id).toBe("p1");
    expect(body.sql).toBe("SELECT 2 FROM spans");
    expect(new Date(body.end).getTime() - new Date(body.start).getTime()).toBe(
      24 * 3600000,
    );
    expect(signal).toBeInstanceOf(AbortSignal);
    const line = screen.getByTestId("sql-estimate");
    expect(line).toHaveTextContent(
      "Estimate: eval_results not estimated (shared index) · spans ≤ 24,576 rows (3 index granules) · within the 10,000,000-row scan limit",
    );
    expect(line).toHaveAttribute("data-tone", "info");
    expect(line).toHaveAttribute("aria-live", "polite");
  });

  it("aborts a superseded request and ignores its late answer", async () => {
    const first = deferred();
    const second = deferred();
    sqlApi.estimate
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const { rerender } = render(
      <QueryEstimate projectId="p1" sql="SELECT 1 FROM spans" hours={1} />,
    );
    await settle();
    const firstSignal = sqlApi.estimate.mock.calls[0][1];
    rerender(
      <QueryEstimate projectId="p1" sql="SELECT 2 FROM spans" hours={1} />,
    );
    expect(firstSignal.aborted).toBe(true);
    await settle();
    await act(async () => {
      second.resolve(within);
    });
    await act(async () => {
      first.resolve({ ...within, exceeds_scan_bound: true });
    });
    expect(screen.getByTestId("sql-estimate")).toHaveAttribute(
      "data-tone",
      "info",
    );
  });

  it("warns when the scan limit is likely exceeded", async () => {
    sqlApi.estimate.mockResolvedValue({
      ...within,
      exceeds_scan_bound: true,
    });
    render(
      <QueryEstimate projectId="p1" sql="SELECT 1 FROM spans" hours={744} />,
    );
    await settle();
    const line = screen.getByTestId("sql-estimate");
    expect(line).toHaveAttribute("data-tone", "warning");
    expect(line).toHaveTextContent(
      "likely over the 10,000,000-row scan limit. Narrow the time window or filter the query.",
    );
  });

  it.each([
    [
      "an unavailable estimate",
      () =>
        Promise.resolve({
          status: "unavailable",
          reason: "Estimate unavailable. Run is not affected.",
          sources: [],
          max_rows_to_read: 10000000,
          exceeds_scan_bound: null,
          elapsed_ms: 5000,
        }),
      "Estimate unavailable. Run is not affected.",
      "info",
    ],
    [
      "a rate limit",
      () => Promise.reject({ response: { status: 429, data: {} } }),
      "Estimate paused: too many estimates. Run is not affected.",
      "info",
    ],
    [
      "a rejected query",
      () =>
        Promise.reject({
          response: { status: 400, data: { detail: "Unknown dataset: x." } },
        }),
      "This query will be rejected: Unknown dataset: x.",
      "warning",
    ],
    [
      "a network failure",
      () => Promise.reject(new Error("Network Error")),
      "Estimate unavailable. Run is not affected.",
      "info",
    ],
  ])("reports %s on one advisory line", async (_, answer, text, tone) => {
    sqlApi.estimate.mockImplementation(answer);
    render(
      <QueryEstimate projectId="p1" sql="SELECT 1 FROM spans" hours={24} />,
    );
    await settle();
    const line = screen.getByTestId("sql-estimate");
    expect(line).toHaveTextContent(text);
    expect(line).toHaveAttribute("data-tone", tone);
  });

  it.each([
    ["disabled", { enabled: false, sql: "SELECT 1 FROM spans" }],
    ["blank SQL", { enabled: true, sql: "   " }],
  ])("requests nothing while %s", async (_, props) => {
    render(<QueryEstimate projectId="p1" hours={24} {...props} />);
    await settle(5 * ESTIMATE_DEBOUNCE_MS);
    expect(sqlApi.estimate).not.toHaveBeenCalled();
    expect(screen.queryByTestId("sql-estimate")).not.toBeInTheDocument();
  });

  it("describes an unchecked scan bound without claiming it is within", () => {
    expect(
      describeEstimate({
        status: "done",
        value: { ...within, exceeds_scan_bound: null },
      }).text,
    ).toBe(
      "Estimate: eval_results not estimated (shared index) · spans ≤ 24,576 rows (3 index granules) · scan limit not checked",
    );
    expect(describeEstimate({ status: "loading" }).text).toBe(
      "Estimating scan…",
    );
  });
});
