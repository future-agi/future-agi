import { describe, expect, it } from "vitest";

import { alertChartSeriesName } from "../common";

// An alert's chart names its line. For service provider error rate the name
// says which share is drawn, so the line and its threshold bands read the
// same way as the conditions beside them.
describe("the name of an alert chart's line", () => {
  it("is the share of providers with errors on the current rule", () => {
    expect(
      alertChartSeriesName({ metricType: "service_provider_error_rates" }),
    ).toBe("Share of providers with errors");
  });

  it("is the share of providers with no errors on the earlier rule", () => {
    expect(
      alertChartSeriesName({
        metricType: "service_provider_error_rates",
        earlier: true,
      }),
    ).toBe("Share of providers with no errors");
  });

  it("is the metric's own name for every other metric", () => {
    expect(alertChartSeriesName({ metricType: "count_of_errors" })).toBe(
      "Count Of Errors",
    );
    expect(
      alertChartSeriesName({ metricType: "count_of_errors", earlier: true }),
    ).toBe("Count Of Errors");
  });
});
