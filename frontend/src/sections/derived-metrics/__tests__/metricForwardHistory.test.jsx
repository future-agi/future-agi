import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "src/utils/test-utils";
import MetricStatusLine from "../MetricStatusLine";
import { forwardCoverageCopy } from "../copy";
import { coverageWindow, formatMetricTime } from "../metricTime";
import { provenance } from "./metricPageFixtures";

afterEach(cleanup);

const window = {
  window_start: "2026-11-01T00:00:00.000Z",
  window_end: "2026-11-08T00:00:00.000Z",
};
const forward = {
  history_mode: "forward",
  coverage_start: "2026-11-05T09:12:00.000Z",
};

describe("a metric that started with no history", () => {
  it("reads from its coverage start instead of the chosen window start", () => {
    expect(coverageWindow(window, forward)).toEqual({
      window_start: forward.coverage_start,
      window_end: window.window_end,
    });
  });

  it("keeps every other window unchanged", () => {
    // History (or legacy) metrics, and windows that already start later.
    expect(coverageWindow(window, { history_mode: "history" })).toBe(window);
    expect(coverageWindow(window, { history_mode: null })).toBe(window);
    expect(coverageWindow(window, undefined)).toBe(window);
    const later = { ...window, window_start: "2026-11-06T00:00:00.000Z" };
    expect(coverageWindow(later, forward)).toBe(later);
    // A window wholly before the coverage start stays as chosen, so the read
    // says it was not calculated instead of inventing an empty window.
    const earlier = { ...window, window_end: "2026-11-04T00:00:00.000Z" };
    expect(coverageWindow(earlier, forward)).toBe(earlier);
    expect(
      coverageWindow(window, { history_mode: "forward", coverage_start: null }),
    ).toBe(window);
  });

  it("says where values begin and why, from the read's provenance", () => {
    render(
      <MetricStatusLine
        provenance={{ ...provenance, ...forward }}
        definition={{ backfill_status: "complete" }}
      />,
    );
    const copy = `Values from ${formatMetricTime(forward.coverage_start)}. History isn't computed for projects this large yet.`;
    expect(forwardCoverageCopy(forward.coverage_start)).toBe(copy);
    expect(screen.getByTestId("metric-coverage")).toHaveTextContent(copy);
    expect(
      screen.getByText(
        "Values from 05 Nov 2026, 09:12:00 UTC. History isn't computed for projects this large yet.",
      ),
    ).toBeVisible();
    // No history recalculation is pending, so no progress bar is shown.
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("shows no coverage line for a metric with history", () => {
    render(
      <MetricStatusLine
        provenance={{ ...provenance, history_mode: "history" }}
        definition={{ backfill_status: "complete" }}
      />,
    );
    expect(screen.queryByTestId("metric-coverage")).not.toBeInTheDocument();
  });
});
