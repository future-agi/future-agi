import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "src/utils/test-utils";
import MetricStatusLine from "../MetricStatusLine";
import { forwardCoverageCopy } from "../copy";
import { metricReadMessage } from "../metricPageValues";
import { pointValueState } from "../unitValueState";
import {
  coveragePending,
  coverageWindow,
  firstValuesInstant,
  formatMetricTime,
} from "../metricTime";
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
    const first = firstValuesInstant(forward.coverage_start).toISOString();
    expect(coverageWindow(window, forward)).toEqual({
      window_start: first,
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
    const first = firstValuesInstant(forward.coverage_start).toISOString();
    const copy = `Values from ${formatMetricTime(first)}. History isn't computed for projects this large yet.`;
    expect(forwardCoverageCopy(forward.coverage_start)).toBe(copy);
    expect(screen.getByTestId("metric-coverage")).toHaveTextContent(copy);
    expect(
      screen.getByText(
        "Values from 05 Nov 2026, 09:15:00 UTC. History isn't computed for projects this large yet.",
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

it("treats a forward window inside the coverage-start bucket as pending", () => {
  const currentBucket = {
    window_start: "2026-11-05T09:00:00.000Z",
    window_end: "2026-11-05T09:12:10.000Z",
  };
  expect(
    coveragePending(currentBucket, forward, "2026-11-05T09:12:10.000Z"),
  ).toBe(true);
  expect(coverageWindow(currentBucket, forward)).toEqual({
    ...currentBucket,
    window_start: "2026-11-05T09:15:00.000Z",
  });
});

describe("a forward metric whose latest values are not final yet", () => {
  const through = "2026-11-07T22:15:00.000Z";

  it("says where final values stop when the read is partial", () => {
    render(
      <MetricStatusLine
        provenance={{ ...provenance, ...forward }}
        definition={{ backfill_status: "complete" }}
        completeThrough={through}
        partial
      />,
    );
    expect(screen.getByTestId("metric-calculated-through")).toHaveTextContent(
      `Final through ${formatMetricTime(through)}. Later values are not calculated yet or still settling.`,
    );
  });

  it("says nothing more when the whole window is final", () => {
    render(
      <MetricStatusLine
        provenance={{ ...provenance, ...forward }}
        definition={{ backfill_status: "complete" }}
        completeThrough={through}
        partial={false}
      />,
    );
    expect(
      screen.queryByTestId("metric-calculated-through"),
    ).not.toBeInTheDocument();
  });

  it("never shows a window without final values as no data", () => {
    expect(
      metricReadMessage(
        {
          code: "window_not_calculated_yet",
          calculated_through: through,
          reason: "not_calculated",
        },
        "KPI",
        forward,
      ),
    ).toBe(
      `Not calculated yet. Values are final through ${formatMetricTime(through)}. Choose an earlier window or check back after the next calculation.`,
    );
    expect(
      metricReadMessage(
        {
          code: "window_not_calculated_yet",
          calculated_through: through,
          reason: "settling",
        },
        "KPI",
        forward,
      ),
    ).toBe(
      `Not final yet: conversations in this window are still open. Values are final through ${formatMetricTime(through)}. Choose an earlier window or check back after the next calculation.`,
    );
    for (const [status, label] of [
      ["not_calculated", "Not calculated yet"],
      ["settling", "Not final yet: conversations still open"],
    ]) {
      const state = pointValueState({
        point_status: status,
        value: null,
        value_mode: "materialized",
      });
      expect(state.state).toBe("pending");
      expect(state.label).toBe(label);
    }
  });
});
