import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ChartsGenerator from "../ChartsGenerator";
import { isIncompleteCost, monetaryCostLabel } from "../../LLMTracing/GraphSection/common";

const chart = vi.hoisted(() => ({ props: null }));
vi.mock("../ChartsTheme", () => ({ default: (props) => {
  chart.props = props;
  return <div data-testid="cost-chart" />;
} }));

const partial = { status: "partial", known_subtotal: 1.25, known_fact_count: 1, unknown_fact_count: 1 };

describe("cost series coverage presentation", () => {
  it("labels a partial subtotal rather than an average and omits complete-looking totals", () => {
    render(<ChartsGenerator id="cost" unit="$" label="Cost" series={[{ name: "Cost", data: [
      { x: 1792497600000, y: null, cost_coverage: partial },
    ] }]} />);
    expect(screen.getByText(/Unknown or partial prices are not plotted/)).toBeInTheDocument();
    expect(screen.getByTestId("cost-chart").parentElement).not.toHaveAttribute("data-chart-total");
    const tooltip = chart.props.options.tooltip.custom({
      series: [[null]], seriesIndex: 0, dataPointIndex: 0,
      w: { globals: { seriesX: [[1792497600000]], series: [[null]], collapsedSeriesIndices: [] },
           config: { series: chart.props.series } },
    });
    expect(tooltip).toContain("Partial · Known subtotal $1.25");
    expect(tooltip).not.toContain("$0.625");
    expect(tooltip).not.toContain("$null");
  });

  it("preserves genuine zero and legacy numbers while suppressing unknown points", () => {
    expect(isIncompleteCost(partial)).toBe(true);
    expect(isIncompleteCost({ ...partial, status: "unknown", known_fact_count: 0, known_subtotal: 0 })).toBe(true);
    expect(isIncompleteCost({ ...partial, status: "complete", unknown_fact_count: 0 })).toBe(false);
    expect(isIncompleteCost(undefined)).toBe(false);
    expect(monetaryCostLabel(0, { ...partial, status: "complete", unknown_fact_count: 0 })).toBe("$0");
    expect(monetaryCostLabel(0, { ...partial, status: "unknown", known_subtotal: 0 })).toBe("Unknown");
    expect(monetaryCostLabel(1.25)).toBe("$1.25");
  });
});
