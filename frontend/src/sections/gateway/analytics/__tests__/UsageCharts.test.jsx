import React from "react";
import { render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import UsageCharts from "../UsageCharts";

const charts = vi.hoisted(() => ({ props: [] }));
vi.mock("react-apexcharts", () => ({
  default: (props) => {
    charts.props.push(props);
    return <div data-testid="chart" />;
  },
}));
const usage = vi.hoisted(() => ({ grouped: null }));
vi.mock("../hooks/useAnalyticsUsage", () => ({
  useAnalyticsUsage: ({ groupBy }) => ({
    isLoading: false,
    data:
      groupBy && usage.grouped
        ? usage.grouped
        : {
            series: [
              {
                bucket: "2026-10-21T10:00:00Z",
                request_count: 4,
                input_tokens: 120,
                output_tokens: 80,
              },
            ],
          },
  }),
}));

const LIGHT = createTheme({ palette: palette("light"), typography });
const DARK = createTheme({ palette: palette("dark"), typography });

function tokenChart() {
  charts.props = [];
  render(
    <ThemeProvider theme={LIGHT}>
      <UsageCharts
        start="2026-10-20T10:00:00Z"
        end="2026-10-21T10:00:00Z"
        granularity="hour"
      />
    </ThemeProvider>,
  );
  return charts.props.find((p) =>
    p.series?.some((s) => /tokens/i.test(s.name)),
  );
}

// The output-tokens series was warning.main
// (#F5E65F, under 3:1 on white), and the legend was in Title Case.
describe("Token usage chart", () => {
  it("token series colours exclude warning.main", () => {
    const chart = tokenChart();
    expect(chart.options.colors).not.toContain(LIGHT.palette.warning.main);
    expect(chart.options.colors).toEqual([
      LIGHT.palette.info.main,
      LIGHT.palette.primary.main,
    ]);
  });

  it("names the series in sentence case", () => {
    const chart = tokenChart();
    expect(chart.series.map((s) => s.name)).toEqual([
      "Input tokens",
      "Output tokens",
    ]);
  });
});

// Group by Model over 7 days drew 20 series in 6
// colours that repeat, two near-identical purples and the sub-3:1 yellow
// and light green, under a six-line legend. A grouped chart draws the top 8
// by requests plus "Other", in 8 distinct FA hues and a neutral grey.
const BUCKETS = ["2026-10-20T10:00:00Z", "2026-10-20T11:00:00Z"];
const twentyModels = () => ({
  group_by: "model",
  groups: Object.fromEntries(
    Array.from({ length: 20 }, (_, i) => [
      `model-${String(i + 1).padStart(2, "0")}`,
      BUCKETS.map((bucket) => ({
        bucket,
        // model-01 is the busiest, model-20 the quietest.
        request_count: 21 - (i + 1),
        total_tokens: (21 - (i + 1)) * 10,
      })),
    ]),
  ),
});

async function groupedCharts(theme = LIGHT) {
  charts.props = [];
  usage.grouped = twentyModels();
  const view = render(
    <ThemeProvider theme={theme}>
      <UsageCharts
        start="2026-10-14T10:00:00Z"
        end="2026-10-21T10:00:00Z"
        granularity="hour"
      />
    </ThemeProvider>,
  );
  await userEvent.click(screen.getByRole("button", { name: "Model" }));
  const last = charts.props.slice(-2);
  usage.grouped = null;
  view.unmount();
  return last;
}

describe("Grouped usage charts", () => {
  it("draw the top 8 groups by requests, then Other with the rest summed", async () => {
    const [requests, tokens] = await groupedCharts();
    const names = requests.series.map((s) => s.name);
    expect(names).toEqual([
      "model-01",
      "model-02",
      "model-03",
      "model-04",
      "model-05",
      "model-06",
      "model-07",
      "model-08",
      "Other",
    ]);
    // Models 9-20 each sent 12 down to 1 requests per bucket: 78 in all.
    expect(requests.series[8].data.map((p) => p.y)).toEqual([78, 78]);
    // The tokens chart shows the same groups, so a model keeps its colour.
    expect(tokens.series.map((s) => s.name)).toEqual(names);
    expect(tokens.options.colors).toEqual(requests.options.colors);
  });

  it("colour each group with a distinct FA hue and Other in neutral grey", async () => {
    for (const theme of [LIGHT, DARK]) {
      const [requests] = await groupedCharts(theme);
      const { colors } = requests.options;
      expect(colors).toHaveLength(9);
      // Nine real colours (a missing token would be one undefined).
      expect(colors.every((c) => /^#[0-9a-f]{6}$/i.test(c))).toBe(true);
      expect(new Set(colors).size).toBe(9);
      const p = theme.palette;
      for (const faint of [p.warning.main, p.success.main]) {
        expect(colors).not.toContain(faint);
      }
      // DS foundation adae445253 makes light secondary.main equal to the
      // first series hue, purple[500]; dark secondary.main is a lighter tint.
      expect(colors[0]).toBe(p.purple[500]);
      // Other is grey: no hue.
      const other = colors[8].replace("#", "");
      const [r, g, b] = [0, 2, 4].map((i) =>
        parseInt(other.slice(i, i + 2), 16),
      );
      expect(r === g && g === b).toBe(true);
    }
  });
});

// With a few requests a bucket the chart puts its y ticks between whole
// numbers (0.4, 0.8, 1.2 ...). Rounding each tick labelled the axis
// "0 0 1 1 1 2", which reads as broken; a count axis labels whole counts only.
describe("Requests and Tokens over time: the count axis", () => {
  it("labels only whole counts on both charts", () => {
    const tokens = tokenChart();
    const requests = charts.props.find((p) => p !== tokens);
    for (const chart of [requests, tokens]) {
      const label = chart.options.yaxis.labels.formatter;
      expect(
        [0, 0.4, 0.8, 1, 1.2, 2, 3.0000000000000004].map((v) => label(v)),
      ).toEqual(["0", "", "", "1", "", "2", "3"]);
      expect(label(1500)).toBe("1.5K");
      expect(label(2_500_000)).toBe("2.5M");
    }
  });
});
