import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";

const widgetProps = [];

const mockOverviewData = {
  volume: {
    change: 0,
    total_count: 9,
    volume: [
      { x: "2026-06-01T10:00:00Z", y: 4 },
      { x: "2026-06-01T11:00:00Z", y: 5 },
    ],
  },
  issues: {
    change: 100,
    total_count: 3,
    last_day: [
      { x: "2026-06-01T10:00:00Z", y: 1 },
      { x: "2026-06-01T11:00:00Z", y: 2 },
    ],
  },
};

vi.mock("src/api/misc/overview", () => ({
  useOverviewData: () => ({ data: mockOverviewData }),
}));

vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({ themeStretch: false }),
}));

vi.mock("src/assets/illustrations", () => ({
  SeoIllustration: () => <div data-testid="seo-illustration" />,
}));

vi.mock("src/sections/model/view", () => ({
  ModelListView: () => <div>Model list</div>,
}));

vi.mock("../../app-widget-summary", () => ({
  default: (props) => {
    widgetProps.push(props);
    return <div>{props.title}</div>;
  },
}));

describe("OverviewView", () => {
  beforeEach(() => {
    widgetProps.length = 0;
  });

  it("passes issue buckets to the Total issues chart", async () => {
    const { default: OverviewView } = await import("../view");

    render(<OverviewView />);

    expect(screen.getByText("Last 24 Hr Volume")).toBeInTheDocument();
    expect(screen.getByText("Total issues")).toBeInTheDocument();
    expect(screen.getByText("Model list")).toBeInTheDocument();

    const issueWidget = widgetProps.find(
      (props) => props.title === "Total issues",
    );
    expect(issueWidget.chart.series).toEqual([1, 2]);
  });
});
