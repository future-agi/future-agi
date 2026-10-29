import React, { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createTheme, getContrastRatio } from "@mui/material/styles";
import { fireEvent, render, screen } from "src/utils/test-utils";
import { palette } from "src/theme/palette";
import WidgetChart from "../WidgetChart";

const h = vi.hoisted(() => ({
  query: { data: null, isPending: false, isError: false, mutate: vi.fn() },
}));
vi.mock("src/hooks/useDashboards", () => ({
  useDashboardQuery: () => h.query,
}));
const saved = {
  custom_start: "2026-10-25T23:00:00Z",
  custom_end: "2026-10-26T00:00:00Z",
};
const widget = {
  id: "widget",
  query_config: {
    project_ids: ["project"],
    time_range: saved,
    metrics: [
      {
        name: "Resolution",
        aggregation: "avg",
        type: "derived_metric",
        derived_run_id: "run",
      },
    ],
    filters: [{ field: "model", value: "synthetic" }],
  },
  chart_config: { chart_type: "metric" },
};
const outside = { start: "2026-10-23T00:00:00Z", end: "2026-10-24T00:00:00Z" };
const response = (points) => ({
  data: {
    result: {
      query_complete: true,
      query_status: "complete",
      query_sampled: false,
      metrics: [
        {
          name: "Resolution",
          aggregation: "avg",
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
          series: [{ name: "total", data: points }],
        },
      ],
    },
  },
});
beforeEach(() => {
  vi.clearAllMocks();
  h.query.data = response([]);
  h.query.isError = false;
  h.query.isPending = false;
});

describe("pinned metric recovery", () => {
  it("explains a disjoint stored window and restores only the date override", () => {
    function Harness() {
      const [range, setRange] = useState(outside);
      return (
        <WidgetChart
          widget={widget}
          globalDateRange={range}
          onRestoreSavedWindow={() => setRange(null)}
        />
      );
    }
    render(<Harness />);
    expect(
      screen.getByText(/saved observation.*is outside/),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Restore saved window" }),
    );
    expect(
      screen.queryByText(/saved observation.*is outside/),
    ).not.toBeInTheDocument();
    expect(h.query.mutate.mock.calls.at(-1)[0].queryConfig).toMatchObject({
      time_range: saved,
      filters: widget.query_config.filters,
      project_ids: ["project"],
    });
  });
  it("does not blame an overlapping filter for missing backend data", () => {
    render(
      <WidgetChart
        widget={widget}
        globalDateRange={{ start: saved.custom_start, end: saved.custom_end }}
      />,
    );
    expect(screen.getByText(/No data available/)).toBeInTheDocument();
    expect(screen.queryByText(/outside/)).not.toBeInTheDocument();
  });
  it("does not blame a failed read on the date filter", () => {
    h.query.data = null;
    render(<WidgetChart widget={widget} globalDateRange={outside} />);
    expect(screen.queryByText(/outside/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No data available/)).not.toBeInTheDocument();
  });
  it.each(["light", "dark"])(
    "uses feature-local scalar text without recoloring shared empty states in %s (not browser acceptance)",
    (mode) => {
      const theme = createTheme({ palette: palette(mode) });
      h.query.data = response([
        {
          timestamp: saved.custom_end,
          value: 0.5,
          ci_low: null,
          ci_high: null,
          ci_reason: "unstable_variance",
        },
      ]);
      const view = render(<WidgetChart widget={widget} />, { theme });
      const value = screen.getByText("0.50");
      expect(getComputedStyle(value).color).toBe(
        getComputedStyle(document.body).color,
      );
      expect(
        getContrastRatio(
          theme.palette.text.primary,
          theme.palette.background.paper,
        ),
      ).toBeGreaterThanOrEqual(4.5);
      expect(screen.getByText(/Point estimate only/)).toBeInTheDocument();
      expect(
        screen.getByText(/No 95% interval: unstable variance/),
      ).toBeInTheDocument();
      view.unmount();
      h.query.data = response([]);
      render(<WidgetChart widget={widget} />, { theme });
      const empty = screen.getByText(/No data available/);
      expect(empty).toBeInTheDocument();
    },
  );
  it("keeps a valid zero distinct from a missing scalar", () => {
    h.query.data = response([{ timestamp: saved.custom_end, value: 0 }]);
    const view = render(<WidgetChart widget={widget} />);
    expect(screen.getByText("0.00")).toBeInTheDocument();
    view.unmount();
    h.query.data = response([{ timestamp: saved.custom_end, value: null }]);
    render(<WidgetChart widget={widget} />);
    expect(screen.queryByText("0.00")).not.toBeInTheDocument();
  });
});
