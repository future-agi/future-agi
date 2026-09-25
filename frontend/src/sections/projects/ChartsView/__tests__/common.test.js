import { describe, expect, it } from "vitest";
import { latencyChartLabels } from "../common";

describe("latencyChartLabels", () => {
  it("labels latency as the median the server declares", () => {
    expect(
      latencyChartLabels({
        latency: "median",
        tokens: "sum",
        cost: "mean",
        traffic: "count",
      }),
    ).toEqual({
      label: "Latency (median)",
      seriesName: "Latency (median)",
      yAxisLabel: "Median latency (ms)",
    });
  });

  it.each([undefined, null, {}, { latency: "mean" }])(
    "keeps the plain label when the statistic is not a declared median (%j)",
    (statistics) => {
      expect(latencyChartLabels(statistics)).toEqual({
        label: "Latency",
        seriesName: "Latency",
        yAxisLabel: "Latency in (ms)",
      });
    },
  );
});
