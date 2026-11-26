import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "src/utils/test-utils";
import { MetricPausedNotice } from "../MetricStatusLine";
import { calculationPausedCopy, windowNotCalculatedYetCopy } from "../copy";
import { metricReadMessage } from "../metricPageValues";
import { formatMetricTime } from "../metricTime";

afterEach(cleanup);

const paused = {
  reason: "too_large",
  since: "2026-11-13T09:00:00Z",
  rows: 12500000,
  limit: 10000000,
};

describe("a project whose metrics paused as too large", () => {
  it("says why, with the measured rows and the limit", () => {
    expect(calculationPausedCopy(paused)).toBe(
      "Calculation paused: this project stores 12,500,000 metric rows, above the limit of 10,000,000. Values calculated so far stay available; newer data is not calculated until the limit is raised.",
    );
  });

  it("says nothing for a project that is calculating", () => {
    expect(calculationPausedCopy(null)).toBe("");
    expect(calculationPausedCopy(undefined)).toBe("");
    expect(calculationPausedCopy({ ...paused, reason: "other" })).toBe("");
  });

  it("shows one warning on the metric page while paused", () => {
    render(<MetricPausedNotice paused={paused} />);
    const notice = screen.getByTestId("metric-calculation-paused");
    expect(notice).toHaveTextContent(calculationPausedCopy(paused));
    expect(screen.getByRole("alert")).toBe(notice);
  });

  it("says a window past the pause is not calculated because it is too large", () => {
    expect(windowNotCalculatedYetCopy(null, "too_large")).toBe(
      "Not calculated: this project's metrics are paused because it is too large. Choose an earlier window.",
    );
  });

  it("renders nothing when not paused", () => {
    render(<MetricPausedNotice paused={null} />);
    expect(screen.queryByTestId("metric-calculation-paused")).toBeNull();
  });
});

// A mixed project whose busiest 15-minute period cannot finish in one pass
// pauses instead of looping. The contract has no row count for it, and its
// limit is the row limit, which has nothing to do with why it paused.
const busy = {
  reason: "too_busy",
  since: "2026-11-17T00:00:00Z",
  rows: null,
  limit: 10000000,
  work: { owners: 3200, captures: 100, head_generation: 4 },
};
const busyCopy =
  "Calculation paused: one 15-minute period in this project has too much activity to calculate in a single pass. Values calculated so far stay available; newer data is not calculated until Run now retries it or processing capacity is raised.";
const through = "2026-11-16T23:45:00.000Z";

describe("a project whose metrics paused as too busy", () => {
  it("says why and what resumes it, without row counts or the row limit", () => {
    const message = calculationPausedCopy(busy);
    expect(message).toBe(busyCopy);
    expect(message).not.toContain("null");
    expect(message).not.toContain("10,000,000");
    expect(message).not.toContain("3,200");
  });

  it("does not depend on the work counts being present", () => {
    const withoutWork = { ...busy };
    delete withoutWork.work;
    expect(calculationPausedCopy(withoutWork)).toBe(busyCopy);
  });

  it("shows one warning on the metric page while paused", () => {
    render(<MetricPausedNotice paused={busy} />);
    const notice = screen.getByTestId("metric-calculation-paused");
    expect(notice).toHaveTextContent(busyCopy);
    expect(screen.getByRole("alert")).toBe(notice);
  });

  it("says a window past the pause is not calculated because it is too busy", () => {
    expect(windowNotCalculatedYetCopy(null, "too_busy")).toBe(
      "Not calculated: this project's metrics are paused because one 15-minute period has too much activity. Choose an earlier window.",
    );
    expect(windowNotCalculatedYetCopy(through, "too_busy")).toBe(
      `Not calculated: this project's metrics are paused because one 15-minute period has too much activity. Values are final through ${formatMetricTime(through)}. Choose an earlier window.`,
    );
  });

  it("explains a chart read past the pause the same way as a too-large pause", () => {
    for (const reason of ["too_large", "too_busy"]) {
      const error = {
        code: "window_not_calculated_yet",
        calculated_through: through,
        reason,
      };
      const message = metricReadMessage(error, "Trend");
      expect(message).toBe(windowNotCalculatedYetCopy(through, reason));
      expect(message).toMatch(
        /^Not calculated: this project's metrics are paused/,
      );
      expect(message).toMatch(/Choose an earlier window\.$/);
      expect(message).not.toContain("check back after the next calculation");
    }
  });
});
