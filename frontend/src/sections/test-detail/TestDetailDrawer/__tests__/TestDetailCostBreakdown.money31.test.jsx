import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import TestDetailCostBreakdown from "../TestDetailCostBreakdown";
import CostCard from "../CostCard";

const chart = vi.hoisted(() => ({ current: null }));
vi.mock("react-apexcharts", () => ({
  default: (props) => {
    chart.current = props;
    return <div data-testid="cost-chart" />;
  },
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));

const costs = (llm, tts, stt) => ({
  llm: { cost: llm },
  tts: { cost: tts },
  stt: { cost: stt },
});
const cardValue = (title) =>
  screen.getByText(title).nextElementSibling.textContent;

describe("MONEY31 r2 cost card", () => {
  it.each([null, undefined, "", NaN, "unpriced"])(
    "renders unknown %s without a dollar prefix",
    (value) => {
      render(<CostCard title="Cost" value={value} />);
      expect(cardValue("Cost")).toBe("—");
    },
  );

  it.each([
    [0, "$0"],
    ["0", "$0"],
    ["0.1", "$0.1"],
    ["1e-9", "$0.000000001"],
    ["12345678901234567.89", "$12345678901234567.89"],
  ])("renders genuine money %s exactly", (value, expected) => {
    render(<CostCard title="Cost" value={value} />);
    expect(cardValue("Cost")).toBe(expected);
  });
});

describe("MONEY31 r2 breakdown population", () => {
  it.each([null, undefined, "", NaN, "unpriced"])(
    "retains unknown component %s and makes the total and shares unknown",
    (unknown) => {
      render(
        <TestDetailCostBreakdown
          costBreakdown={costs("0.1", "0.2", unknown)}
        />,
      );
      expect(cardValue("Speech-to-text cost")).toBe("—");
      expect(cardValue("LLM cost")).toBe("$0.1");
      expect(cardValue("Text-to-speech cost")).toBe("$0.2");
      expect(
        chart.current.options.plotOptions.pie.donut.labels.total.formatter(),
      ).toBe("—");
      expect(chart.current.series).toEqual([0.1, 0.2]);
      expect(
        chart.current.options.dataLabels.formatter(33.3, { seriesIndex: 0 }),
      ).toEqual(["$0.10", "—"]);
      expect(screen.queryByText(/All costs are \$0/)).toBeNull();
      expect(document.body.textContent).not.toContain("%");
    },
  );

  it.each([null, undefined, {}])(
    "does not describe missing breakdown %s as a free call",
    (costBreakdown) => {
      render(<TestDetailCostBreakdown costBreakdown={costBreakdown} />);
      expect(cardValue("Speech-to-text cost")).toBe("—");
      expect(cardValue("LLM cost")).toBe("—");
      expect(cardValue("Text-to-speech cost")).toBe("—");
      expect(screen.queryByText(/All costs are \$0/)).toBeNull();
      expect(document.body.textContent).not.toContain("$0");
    },
  );

  it.each([0, "0"])("preserves a genuinely free call %s", (zero) => {
    render(<TestDetailCostBreakdown costBreakdown={costs(zero, zero, zero)} />);
    expect(cardValue("Speech-to-text cost")).toBe("$0");
    expect(cardValue("LLM cost")).toBe("$0");
    expect(cardValue("Text-to-speech cost")).toBe("$0");
    expect(
      screen.getByText("All costs are $0 for this call"),
    ).toBeInTheDocument();
  });

  it("sums decimal components exactly, with zero kept in the population", () => {
    render(
      <TestDetailCostBreakdown costBreakdown={costs("0.1", "0.2", "0")} />,
    );
    expect(
      chart.current.options.plotOptions.pie.donut.labels.total.formatter(),
    ).toBe("$0.30");
    expect(
      chart.current.options.dataLabels.formatter(33.3333, {
        seriesIndex: 0,
      })[0],
    ).toBe("$0.10");
    expect(cardValue("Speech-to-text cost")).toBe("$0");
  });

  it("preserves large and sub-cent amounts in cards, labels, tooltips and totals", () => {
    render(
      <TestDetailCostBreakdown
        costBreakdown={costs("12345678901234567.89", "1e-9", "0")}
      />,
    );
    const options = chart.current.options;
    expect(cardValue("Text-to-speech cost")).toBe("$0.000000001");
    expect(cardValue("LLM cost")).toBe("$12345678901234567.89");
    expect(options.plotOptions.pie.donut.labels.total.formatter()).toBe(
      "$12345678901234567.890000001",
    );
    expect(
      options.plotOptions.pie.donut.labels.value.formatter(0, {
        seriesIndex: 1,
      }),
    ).toBe("$0.000000001");
    expect(
      options.tooltip.custom({
        seriesIndex: 1,
        w: {
          globals: { labels: options.labels },
          config: { colors: options.colors },
        },
      }),
    ).toContain("$0.000000001");
  });

  it("does not hide a tiny nonzero cost behind the free-call empty state", () => {
    render(
      <TestDetailCostBreakdown costBreakdown={costs("1e-400", "0", "0")} />,
    );
    expect(cardValue("LLM cost")).toBe(`$0.${"0".repeat(399)}1`);
    expect(screen.queryByText(/All costs are \$0/)).toBeNull();
    expect(document.body.textContent).not.toMatch(/NaN|Infinity/);
  });

  it("retains exact costs when initially missing data arrives", () => {
    const { rerender } = render(<TestDetailCostBreakdown />);
    rerender(
      <TestDetailCostBreakdown costBreakdown={costs("0.1", "0.2", "0")} />,
    );
    expect(screen.getByTestId("cost-chart")).toBeInTheDocument();
    expect(
      chart.current.options.plotOptions.pie.donut.labels.total.formatter(),
    ).toBe("$0.30");
  });

  it("never makes a partial total known by hiding the unknown component", () => {
    render(
      <TestDetailCostBreakdown costBreakdown={costs("0.1", "0.2", null)} />,
    );
    expect(screen.getAllByRole("checkbox")).toHaveLength(3);
    fireEvent.click(screen.getAllByRole("checkbox")[2]);
    expect(
      chart.current.options.plotOptions.pie.donut.labels.total.formatter(),
    ).toBe("—");
    expect(document.body.textContent).not.toContain("%");
  });
});
