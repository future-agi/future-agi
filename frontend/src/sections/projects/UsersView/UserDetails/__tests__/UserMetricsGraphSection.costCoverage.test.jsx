import { describe, expect, it } from "vitest";
import { transformGraphDataToChartData } from "../../common";

const timestamp = "2026-10-20T12:00:00+00:00";
const response = (coverage) => ({
  result: {
    cost: [{ timestamp, cost: 1.25 }],
    trace: [{ timestamp, trace: 2 }],
    ...(coverage ? { cost_coverage: [{ timestamp, ...coverage }] } : {}),
  },
});

describe("user detail cost series and summary", () => {
  it("suppresses a partial sum and names the known subtotal", () => {
    const charts = transformGraphDataToChartData(
      response({
        status: "partial",
        known_subtotal: 1.25,
        known_fact_count: 1,
        unknown_fact_count: 1,
      }),
    );
    expect(charts.map((chart) => chart.id)).toEqual(["cost", "trace"]);
    expect(charts[0].series[0].data[0].y).toBeNull();
    expect(charts[0].totalLabel).toBe("Partial · Known subtotal $1.25");
    expect(charts[1].total).toBe(2);
  });

  // W10-TRACING-OTHER-U02: summing per-bucket float subtotals must not
  // print binary noise such as $0.30000000000000004.
  it("formats a summed partial known subtotal as money", () => {
    const later = "2026-10-20T13:00:00+00:00";
    const charts = transformGraphDataToChartData({
      result: {
        cost: [
          { timestamp, cost: 0.1 },
          { timestamp: later, cost: 0.2 },
        ],
        cost_coverage: [
          {
            timestamp,
            status: "partial",
            known_subtotal: 0.1,
            known_fact_count: 1,
            unknown_fact_count: 1,
          },
          {
            timestamp: later,
            status: "complete",
            known_subtotal: 0.2,
            known_fact_count: 1,
            unknown_fact_count: 0,
          },
        ],
      },
    });
    expect(charts[0].totalLabel).toBe("Partial · Known subtotal $0.30");
  });

  it("all-unknown shows Unknown, never a complete total zero", () => {
    const input = response({
      status: "unknown",
      known_subtotal: 0,
      known_fact_count: 0,
      unknown_fact_count: 1,
    });
    input.result.cost[0].cost = 0;
    const chart = transformGraphDataToChartData(input)[0];
    expect(chart.totalLabel).toBe("Unknown");
    expect(chart.series[0].data[0].y).toBeNull();
  });

  it.each([
    undefined,
    {
      status: "complete",
      known_subtotal: 0,
      known_fact_count: 1,
      unknown_fact_count: 0,
    },
  ])("keeps complete and legacy zeros unchanged (%s)", (coverage) => {
    const input = response(coverage);
    input.result.cost[0].cost = 0;
    const chart = transformGraphDataToChartData(input)[0];
    expect(chart.totalLabel).toBeUndefined();
    expect(chart.total).toBe(0);
    expect(chart.series[0].data[0].y).toBe(0);
  });
});
