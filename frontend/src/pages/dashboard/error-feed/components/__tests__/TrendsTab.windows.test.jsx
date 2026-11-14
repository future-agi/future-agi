import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import TrendsTab from "../TrendsTab";

// The windows and the no-data states are under test; the charts are stubs.
const state = vi.hoisted(() => ({ data: {}, calls: [] }));

vi.mock("src/api/errorFeed/error-feed", () => ({
  useErrorFeedTrends: (clusterId, params) => {
    state.calls.push([clusterId, params]);
    return { data: state.data, isLoading: false };
  },
}));
vi.mock("react-apexcharts", () => ({ default: () => null }));

const error = { cluster_id: "E-1" };
const payload = (overrides) => ({
  metrics: [],
  events_over_time: [],
  score_trends: [],
  activity_heatmap: [],
  ...overrides,
});

describe("TrendsTab windows", () => {
  beforeEach(() => {
    state.calls = [];
  });

  it("asks for the 14 day window the page is labelled with", () => {
    state.data = payload();

    render(<TrendsTab error={error} />);

    expect(state.calls[0]).toEqual(["E-1", { days: 14 }]);
    expect(screen.getByText("Last 14 days")).toBeInTheDocument();
  });

  it("hides the delta of a metric with nothing to measure", () => {
    state.data = payload({
      metrics: [
        { label: "Avg eval score", value: "—", delta: 0, unit: "" },
        { label: "Error rate", value: "12%", delta: 3, unit: "%" },
      ],
    });

    render(<TrendsTab error={error} />);

    // Only the measured card keeps its "vs prev period" delta.
    expect(screen.getAllByText("vs prev period")).toHaveLength(1);
  });

  it("says the score chart has no scores in the window", () => {
    state.data = payload();

    render(<TrendsTab error={error} />);

    expect(
      screen.getByText("No eval scores in the last 14 days."),
    ).toBeInTheDocument();
  });

  it("labels the heatmap with its own 30 day window", () => {
    state.data = payload({
      activity_heatmap: [
        Array.from({ length: 24 }, (_, hour) => ({
          day: 0,
          hour,
          value: hour === 0 ? 2 : 0,
        })),
      ],
    });

    render(<TrendsTab error={error} />);

    expect(
      screen.getByText("Activity Heatmap (errors by hour, last 30 days)"),
    ).toBeInTheDocument();
  });
});
