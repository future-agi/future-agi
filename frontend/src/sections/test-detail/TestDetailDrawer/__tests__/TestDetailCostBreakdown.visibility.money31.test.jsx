import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import TestDetailCostBreakdown from "../TestDetailCostBreakdown";

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
const total = () =>
  chart.current.options.plotOptions.pie.donut.labels.total.formatter();
const cardValue = (title) =>
  screen.getByText(title).nextElementSibling.textContent;
const expectRestored = (series) => {
  expect(screen.queryAllByRole("checkbox")).toHaveLength(3);
  expect(screen.queryByTestId("cost-chart")).toBeInTheDocument();
  screen.getAllByRole("checkbox").forEach((checkbox) => {
    expect(checkbox).toBeChecked();
  });
  expect(chart.current.series).toEqual(series);
  expect(screen.queryByText(/Total cost: \$0\.00/)).toBeNull();
};

// Literal amounts are independent oracles. The chart mock exposes callbacks,
// while real checkbox/row/button interactions exercise component state.
describe("MONEY31 r3 last-plottable visibility", () => {
  it("keeps the sole paid slice and controls when zero companions are checked", () => {
    render(<TestDetailCostBreakdown costBreakdown={costs("0.1", "0", "0")} />);
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    expectRestored([0.1]);
    expect(total()).toBe("$0.10");
    expect(cardValue("Text-to-speech cost")).toBe("$0");
  });

  it("restores both paid slices when the last is unchecked beside a zero", () => {
    render(
      <TestDetailCostBreakdown costBreakdown={costs("0.1", "0.2", "0")} />,
    );
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    expect(chart.current.series).toEqual([0.2]);
    expect(total()).toBe("$0.20");
    fireEvent.click(screen.getAllByRole("checkbox")[1]);
    expectRestored([0.1, 0.2]);
    expect(total()).toBe("$0.30");
  });

  it.each([null, undefined, "", "unpriced"])(
    "guards the last paid slice beside unknown %s without inventing shares",
    (unknown) => {
      render(
        <TestDetailCostBreakdown costBreakdown={costs("0.1", unknown, "0")} />,
      );
      fireEvent.click(screen.getAllByRole("checkbox")[0]);
      expectRestored([0.1]);
      expect(total()).toBe("—");
      expect(cardValue("Text-to-speech cost")).toBe("—");
      expect(
        chart.current.options.dataLabels.formatter(100, { seriesIndex: 0 }),
      ).toEqual(["$0.10", "—"]);
      expect(document.body.textContent).not.toContain("%");
    },
  );

  it.each([
    ["1e-400", `$0.${"0".repeat(399)}1`, `$0.1${"0".repeat(398)}1`],
    ["1e400", `$1${"0".repeat(400)}`, `$1${"0".repeat(400)}.10`],
  ])(
    "excludes unplottable companion %s from the last-slice count",
    (cost, card, sum) => {
      render(
        <TestDetailCostBreakdown costBreakdown={costs("0.1", cost, "0")} />,
      );
      fireEvent.click(screen.getAllByRole("checkbox")[0]);
      expectRestored([0.1]);
      expect(cardValue("Text-to-speech cost")).toBe(card);
      expect(total()).toBe(sum);
    },
  );

  it("uses the same last-plottable guard for a legend row click", () => {
    render(<TestDetailCostBreakdown costBreakdown={costs("0.1", "0", "0")} />);
    fireEvent.click(screen.getByText("LLM:"));
    expectRestored([0.1]);
  });

  it("does not reset hidden slices when toggling a nonplottable companion", () => {
    render(
      <TestDetailCostBreakdown costBreakdown={costs("0.1", "0.2", "0")} />,
    );
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    fireEvent.click(screen.getAllByRole("checkbox")[2]);
    expect(screen.getAllByRole("checkbox")[0]).not.toBeChecked();
    expect(screen.getAllByRole("checkbox")[2]).not.toBeChecked();
    expect(chart.current.series).toEqual([0.2]);
    fireEvent.click(
      screen.getByRole("button", { name: "Restore all components" }),
    );
    expectRestored([0.1, 0.2]);
  });

  it("restores a hidden unknown companion without making a partial total known", () => {
    render(
      <TestDetailCostBreakdown costBreakdown={costs("0.1", "0.2", null)} />,
    );
    fireEvent.click(screen.getAllByRole("checkbox")[2]);
    expect(total()).toBe("—");
    fireEvent.click(
      screen.getByRole("button", { name: "Restore all components" }),
    );
    expectRestored([0.1, 0.2]);
    expect(total()).toBe("—");
    expect(document.body.textContent).not.toContain("%");
  });

  it.each([
    ["0", "Total cost: $0.10 (chart unavailable)", "$0.10"],
    [null, "Cost data is unavailable for this call", "—"],
    [
      "1e-400",
      `Total cost: $0.1${"0".repeat(398)}1 (chart unavailable)`,
      `$0.1${"0".repeat(398)}1`,
    ],
  ])(
    "keeps restore reachable when a data refresh makes the remaining slice %s",
    (tts, fallback, sum) => {
      const { rerender } = render(
        <TestDetailCostBreakdown costBreakdown={costs("0.1", "0.2", "0")} />,
      );
      fireEvent.click(screen.getAllByRole("checkbox")[0]);
      rerender(
        <TestDetailCostBreakdown costBreakdown={costs("0.1", tts, "0")} />,
      );
      expect(screen.queryAllByRole("checkbox")).toHaveLength(3);
      expect(screen.queryByTestId("cost-chart")).toBeNull();
      expect(screen.getByText(fallback)).toBeInTheDocument();
      expect(screen.queryByText(/Total cost: \$0\.00/)).toBeNull();
      fireEvent.click(
        screen.getByRole("button", { name: "Restore all components" }),
      );
      expectRestored([0.1]);
      expect(total()).toBe(sum);
    },
  );

  it("can restore a hidden slice directly after a refresh removes the chart", () => {
    const { rerender } = render(
      <TestDetailCostBreakdown costBreakdown={costs("0.1", "0.2", "0")} />,
    );
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    rerender(
      <TestDetailCostBreakdown costBreakdown={costs("0.1", "0", "0")} />,
    );
    expect(screen.queryAllByRole("checkbox")).toHaveLength(3);
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    expectRestored([0.1]);
    expect(total()).toBe("$0.10");
  });

  it("preserves a true all-zero call without chart or hidden controls", () => {
    render(<TestDetailCostBreakdown costBreakdown={costs("0", 0, "0")} />);
    expect(
      screen.getByText("All costs are $0 for this call"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("cost-chart")).toBeNull();
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(cardValue("LLM cost")).toBe("$0");
    expect(cardValue("Speech-to-text cost")).toBe("$0");
    expect(cardValue("Text-to-speech cost")).toBe("$0");
  });

  it("keeps restoring available if all amounts become zero while one is hidden", () => {
    const { rerender } = render(
      <TestDetailCostBreakdown costBreakdown={costs("0.1", "0.2", "0")} />,
    );
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    rerender(<TestDetailCostBreakdown costBreakdown={costs("0", "0", "0")} />);
    expect(screen.queryAllByRole("checkbox")).toHaveLength(3);
    expect(
      screen.getByText("All costs are $0 for this call"),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Restore all components" }),
    );
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    rerender(
      <TestDetailCostBreakdown costBreakdown={costs("0.1", "0.2", "0")} />,
    );
    expectRestored([0.1, 0.2]);
  });
});
