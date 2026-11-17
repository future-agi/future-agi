import { describe, expect, it } from "vitest";
import {
  getDashboardMetricSeriesState,
  getSeriesScalar,
  groupPieSeries,
} from "../widgetUtils";
import chartSource from "../WidgetChart.jsx?raw";
import editorSource from "../WidgetEditorView.jsx?raw";

const metric = (aggregation, aggregateValue) => ({
  id: "latency",
  name: "Latency",
  aggregation,
  unit: "ms",
  query_complete: true,
  query_status: "complete",
  query_sampled: false,
  series: [
    {
      name: "voice",
      aggregate_value: aggregateValue,
      data: [
        { timestamp: "2026-11-01T00:00:00Z", value: 10 },
        { timestamp: "2026-11-02T00:00:00Z", value: 100 },
      ],
    },
  ],
});

describe("OBS-SW-06 exact period scalars", () => {
  it.each([
    ["avg", 19],
    ["count_distinct", 3],
    ["p95", 100],
    ["sum", 0],
  ])(
    "uses the server's %s scalar in cards and pie slices",
    (aggregation, value) => {
      const { series } = getDashboardMetricSeriesState([
        metric(aggregation, value),
      ]);
      const s = series[0];
      expect(s.aggregateValue).toBe(value);
      expect(getSeriesScalar(s.data, s.aggregation, s.aggregateValue)).toBe(
        value,
      );
      const pie = groupPieSeries(series)[0];
      expect(pie.hasValues).toBe(true);
      expect(pie.slices).toEqual(value > 0 ? [{ name: "voice", value }] : []);
      expect(s.data.map((point) => point.y)).toEqual([10, 100]);
    },
  );

  it("does not substitute a bucket mean for an explicitly missing full-period value", () => {
    const { series } = getDashboardMetricSeriesState([metric("avg", null)]);
    const s = series[0];
    expect(getSeriesScalar(s.data, s.aggregation, s.aggregateValue)).toBeNull();
    expect(groupPieSeries(series)[0].hasValues).toBe(false);
  });

  it.each([NaN, Infinity, -Infinity, "not-a-number"])(
    "does not render invalid period scalar %s as an exact value",
    (value) => expect(getSeriesScalar([{ y: 10 }], "avg", value)).toBeNull(),
  );

  it.each([
    ["chart", chartSource, 2],
    ["editor", editorSource, 7],
  ])(
    "wires every %s card, aggregate cell and sort to the period scalar",
    (_name, source, count) => {
      const calls = [
        ...source.matchAll(
          /getSeriesScalar\(\s*([^,]+)\.data,\s*\1\.aggregation,\s*\1\.aggregateValue\s*,?\s*\)/g,
        ),
      ];
      expect(calls).toHaveLength(count);
      expect([...source.matchAll(/getSeriesScalar\(/g)]).toHaveLength(count);
    },
  );
});
