import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "src/utils/test-utils";
import { MetricPausedNotice } from "../MetricStatusLine";
import { calculationPausedCopy, windowNotCalculatedYetCopy } from "../copy";

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
