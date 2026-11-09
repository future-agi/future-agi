import React from "react";
import { render } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import ErrorAnalytics from "../ErrorAnalytics";
import { VALUE_LABEL_ROOM } from "../barLabels";

const charts = vi.hoisted(() => ({ props: [], hook: null }));
vi.mock("react-apexcharts", () => ({
  default: (props) => {
    charts.props.push(props);
    return <div data-testid={`chart-${props.type}`} />;
  },
}));
vi.mock("ag-grid-react", () => ({ AgGridReact: () => null }));
vi.mock("../hooks/useAnalyticsErrors", () => ({
  useAnalyticsErrors: (args) => {
    charts.hook = args;
    return {
      isLoading: false,
      data: {
        breakdown: Array.from({ length: 8 }, (_, i) => ({
          name: String(500 + i),
          error_count: 10 - i,
        })),
        error_timeseries: [],
        top_errors: [],
      },
    };
  },
}));

const LIGHT = createTheme({ palette: palette("light"), typography });

// The error breakdown cycled warning.main (under 3:1 on
// white) and raw hex colours. It takes the gateway's series hues, top 8.
describe("Error breakdown chart", () => {
  it("colours its bars with the gateway's series hues", () => {
    charts.props = [];
    render(
      <ThemeProvider theme={LIGHT}>
        <ErrorAnalytics
          start="2026-10-20T00:00:00Z"
          end="2026-10-21T00:00:00Z"
          granularity="hour"
        />
      </ThemeProvider>,
    );
    expect(charts.hook.topN).toBe(8);
    const bar = charts.props.find((p) => p.type === "bar");
    const { colors } = bar.options;
    expect(colors).toHaveLength(8);
    expect(new Set(colors).size).toBe(8);
    expect(colors).not.toContain(LIGHT.palette.warning.main);
    expect(colors).not.toContain("#7C4DFF");
    expect(colors[0]).toBe("#7857FC");
  });
});

// The bars' counts were white, so a
// short bar's ran onto the white plot. Each is the bar's total label in the
// theme's text colour past the bar's end, in light and dark, with no label
// background (Firefox drew none: white on white, and cut).
describe("Error breakdown value labels", () => {
  for (const mode of ["light", "dark"]) {
    it(`writes each bar's count past its end in the text colour in ${mode} mode, with no label background`, () => {
      const theme = createTheme({ palette: palette(mode), typography });
      charts.props = [];
      render(
        <ThemeProvider theme={theme}>
          <ErrorAnalytics
            start="2026-10-20T00:00:00Z"
            end="2026-10-21T00:00:00Z"
            granularity="hour"
          />
        </ThemeProvider>,
      );
      const { chart, dataLabels, plotOptions, grid } = charts.props.find(
        (p) => p.type === "bar",
      ).options;
      // Firefox draws no ApexCharts label background (getBBox reads 0), so
      // a label that needs one read white on white there and was cut.
      expect(dataLabels.enabled).toBe(false);
      expect(dataLabels.background?.enabled).not.toBe(true);
      expect(chart.stacked).toBe(true);
      const { total } = plotOptions.bar.dataLabels;
      expect(total).toMatchObject({ enabled: true });
      expect(total.offsetX).toBeGreaterThan(0);
      expect(total.style.color).toBe(theme.palette.text.primary);
      expect(grid.padding.right).toBeGreaterThanOrEqual(VALUE_LABEL_ROOM);
      expect(total.formatter(1234)).toBe("1,234");
    });
  }
});
