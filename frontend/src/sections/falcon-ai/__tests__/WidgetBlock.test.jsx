import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// Stub the Imagine widget registry — WidgetBlock's contract is routing the
// widget config into the right component, not the chart rendering itself
// (that's covered by the Imagine widget components).
vi.mock("src/components/imagine/widgets", () => ({
  default: {
    bar_chart: ({ config }) => (
      <div data-testid="stub-bar-chart">
        {JSON.stringify(config.categories || [])}
      </div>
    ),
    metric_card: ({ config }) => (
      <div data-testid="stub-metric-card">{String(config.value)}</div>
    ),
  },
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));

import WidgetBlock from "../components/WidgetBlock";

describe("WidgetBlock (chat widget answers, Phase 4C)", () => {
  it("renders the title bar and the widget component with its static config", () => {
    render(
      <WidgetBlock
        widget={{
          id: "w-1",
          type: "bar_chart",
          title: "Token usage",
          config: { categories: ["Mon", "Tue"] },
        }}
      />,
    );
    expect(screen.getByText("Token usage")).toBeTruthy();
    expect(screen.getByTestId("stub-bar-chart").textContent).toContain("Mon");
  });

  it("renders without a title bar when the widget has no title", () => {
    render(
      <WidgetBlock
        widget={{ id: "w-2", type: "metric_card", config: { value: 42 } }}
      />,
    );
    expect(screen.getByTestId("stub-metric-card").textContent).toBe("42");
  });

  it("shows the Imagine hint for dataBinding-only widgets (no static data in chat)", () => {
    render(
      <WidgetBlock
        widget={{
          id: "w-3",
          type: "bar_chart",
          title: "Latency",
          config: {},
          dataBinding: { seriesFromSpans: { valuePath: "latency_ms" } },
        }}
      />,
    );
    expect(screen.queryByTestId("stub-bar-chart")).toBeNull();
    expect(
      screen.getByText(/open it on the Imagine canvas/i),
    ).toBeTruthy();
  });

  it("falls back gracefully on unknown widget types", () => {
    render(<WidgetBlock widget={{ id: "w-4", type: "hologram" }} />);
    expect(screen.getByText(/Unknown widget type: hologram/)).toBeTruthy();
  });
});
