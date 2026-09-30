import { describe, expect, it } from "vitest";
import { latencyChartLabels } from "../common";

describe("latencyChartLabels", () => {
  it("labels latency as the mean the server declares", () => {
    expect(
      latencyChartLabels({
        latency: "mean",
        tokens: "sum",
        cost: "mean",
        traffic: "count",
      }),
    ).toEqual({
      label: "Latency (avg, ms)",
      seriesName: "Latency (avg, ms)",
      yAxisLabel: "Avg latency (ms)",
    });
  });

  it.each([undefined, null, {}, { latency: "median" }, { latency: "p95" }])(
    "keeps the plain label when latency is not a declared mean (%j)",
    (statistics) => {
      expect(latencyChartLabels(statistics)).toEqual({
        label: "Latency",
        seriesName: "Latency",
        yAxisLabel: "Latency in (ms)",
      });
    },
  );
});
