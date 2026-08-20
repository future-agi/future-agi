import { describe, expect, it } from "vitest";
import {
  buildChartSeriesColorMap,
  buildSeriesColorMap,
  getSeriesColorFromMap,
  shadeSeriesColor,
} from "../chartColors";

const brokenDownSeries = [
  {
    name: "Latency / alpha (avg)",
    breakdownName: "alpha",
    metricIndex: 0,
  },
  {
    name: "Tokens / alpha (sum)",
    breakdownName: "alpha",
    metricIndex: 1,
  },
  {
    name: "Latency / beta (avg)",
    breakdownName: "beta",
    metricIndex: 0,
  },
  {
    name: "Tokens / beta (sum)",
    breakdownName: "beta",
    metricIndex: 1,
  },
];

describe("dashboard chart colors", () => {
  it("uses one breakdown hue with a distinct, consistent shade per metric", () => {
    const colors = buildChartSeriesColorMap(brokenDownSeries, "light");
    const baseColors = buildSeriesColorMap(["alpha", "beta"]);

    expect(colors["Latency / alpha (avg)"]).toBe(
      shadeSeriesColor(baseColors.alpha, 0, "light"),
    );
    expect(colors["Tokens / alpha (sum)"]).toBe(
      shadeSeriesColor(baseColors.alpha, 1, "light"),
    );
    expect(colors["Latency / beta (avg)"]).toBe(
      shadeSeriesColor(baseColors.beta, 0, "light"),
    );
    expect(colors["Tokens / beta (sum)"]).toBe(
      shadeSeriesColor(baseColors.beta, 1, "light"),
    );
    expect(colors["Latency / alpha (avg)"]).not.toBe(
      colors["Tokens / alpha (sum)"],
    );
  });

  it("keeps breakdown colors stable when a re-query changes series order", () => {
    const original = buildChartSeriesColorMap(brokenDownSeries, "light");
    const reordered = buildChartSeriesColorMap(
      [...brokenDownSeries].reverse(),
      "light",
    );

    expect(reordered).toEqual(original);
  });

  it("provides five distinguishable shade levels in light and dark themes", () => {
    const fiveMetrics = Array.from({ length: 5 }, (_, metricIndex) => ({
      name: `Metric ${metricIndex} / alpha`,
      breakdownName: "alpha",
      metricIndex,
    }));

    const lightColors = Object.values(
      buildChartSeriesColorMap(fiveMetrics, "light"),
    );
    const darkColors = Object.values(
      buildChartSeriesColorMap(fiveMetrics, "dark"),
    );

    expect(new Set(lightColors).size).toBe(5);
    expect(new Set(darkColors).size).toBe(5);
    expect(darkColors).not.toEqual(lightColors);
    expect(lightColors).not.toContain("#FFFFFF");
    expect(darkColors).not.toContain("#000000");
  });

  it("preserves the legacy composite-label mapping without a breakdown", () => {
    const series = [
      { name: "Latency (avg)", breakdownName: "total", metricIndex: 0 },
      { name: "Tokens (sum)", breakdownName: "total", metricIndex: 1 },
    ];
    const legacyMap = buildSeriesColorMap(series.map((item) => item.name));
    const colors = buildChartSeriesColorMap(series, "dark");

    for (const item of series) {
      expect(colors[item.name]).toBe(
        getSeriesColorFromMap(legacyMap, item.name),
      );
    }
  });
});
