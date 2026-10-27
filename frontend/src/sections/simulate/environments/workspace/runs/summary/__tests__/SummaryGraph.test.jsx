import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";

// Capture what the graph hands ApexCharts — jsdom can't lay the chart out.
const chartProps = vi.fn();
vi.mock("react-apexcharts", () => ({
  default: (props) => {
    chartProps(props);
    return null;
  },
}));

const { default: SummaryGraph } = await import("../SummaryGraph");

const SERIES = [
  { id: "task", name: "Task success", color: "#16A34A", data: [40, 80] },
  {
    id: "policy",
    name: "Policy adherence",
    color: "#7857FC",
    data: [60, null],
  },
];
const lastProps = () => chartProps.mock.calls.at(-1)[0];

describe("SummaryGraph", () => {
  beforeEach(() => chartProps.mockClear());

  it("draws the eval trend as lines when there are several runs", () => {
    render(<SummaryGraph categories={["Run 1", "Run 2"]} series={SERIES} />);
    expect(lastProps().type).toBe("line");
    expect(lastProps().series).toEqual([
      { name: "Task success", data: [40, 80] },
      { name: "Policy adherence", data: [60, null] },
    ]);
  });

  it("draws a single run as one tight group of columns, one per eval", () => {
    const one = SERIES.map((s) => ({ ...s, data: s.data.slice(-1) }));
    render(<SummaryGraph categories={["Run 2 · latest"]} series={one} />);
    const props = lastProps();
    expect(props.type).toBe("bar");
    expect(props.series).toEqual([
      { name: "Task success", data: [80] },
      { name: "Policy adherence", data: [null] },
    ]);
    expect(props.options.xaxis.categories).toEqual(["Run 2 · latest"]);
    expect(props.options.plotOptions.bar.distributed).toBeFalsy();
    expect(props.options.plotOptions.bar.columnWidth).toMatch(/px$/);
    expect(props.options.colors).toEqual(["#16A34A", "#7857FC"]);
  });

  it("ticks the score axis every 25", () => {
    render(<SummaryGraph categories={["Run 1", "Run 2"]} series={SERIES} />);
    expect(lastProps().options.yaxis.tickAmount).toBe(4);
  });

  it("dashes lines that sit exactly on top of each other so each colour shows", () => {
    const same = [
      { id: "a", name: "A", color: "#16A34A", data: [100, 100] },
      { id: "b", name: "B", color: "#7857FC", data: [60, 70] },
      { id: "c", name: "C", color: "#2563EB", data: [100, 100] },
    ];
    render(<SummaryGraph categories={["Run 1", "Run 2"]} series={same} />);

    const svg = document.createElement("div");
    svg.innerHTML = same
      .map(
        (_, i) =>
          `<g class="apexcharts-series" data:realIndex="${i}"><path class="apexcharts-line"></path></g>`,
      )
      .join("");
    lastProps().options.chart.events.mounted({ el: svg });

    const dash = (i) =>
      svg
        .querySelector(`[data\\:realIndex="${i}"] path`)
        .getAttribute("stroke-dasharray");
    expect(dash(0)).toBe("0 0 8 8");
    expect(dash(2)).toBe("0 8 8 0");
    expect(dash(1)).toBeNull();
  });

  it("dims every eval but the hovered one", () => {
    render(
      <SummaryGraph
        categories={["Run 1", "Run 2"]}
        series={SERIES}
        highlightedId="policy"
      />,
    );
    const [task, policy] = lastProps().options.colors;
    expect(policy).toBe("#7857FC");
    expect(task).not.toBe("#16A34A");
    expect(task).toMatch(/^rgba\(/);
  });

  it("dims nothing when the hovered eval is hidden and has no line to stand out", () => {
    render(
      <SummaryGraph
        categories={["Run 1", "Run 2"]}
        series={SERIES}
        highlightedId="gone"
      />,
    );
    expect(lastProps().options.colors).toEqual(["#16A34A", "#7857FC"]);
  });

  // Apex keeps the tooltip and its guide on the previous run when the hovered
  // run has no points to anchor to, while swapping in the new run's text.
  it("hides the tooltip over a run with no scores instead of leaving it on the last run", () => {
    const gappy = [
      { id: "a", name: "A", color: "#16A34A", data: [40, null, 80] },
      { id: "b", name: "B", color: "#7857FC", data: [60, null, null] },
    ];
    render(
      <SummaryGraph categories={["Run 1", "Run 2", "Run 3"]} series={gappy} />,
    );
    const { mouseMove } = lastProps().options.chart.events;
    const el = document.createElement("div");
    el.innerHTML = "<svg></svg>";
    // Three runs over a 200px plot starting 20px in: runs sit at 20, 120, 220.
    const ctx = { el, w: { globals: { gridWidth: 200, translateX: 20 } } };

    mouseMove({ clientX: 115 }, ctx);
    expect(el.classList.contains("summary-run-unscored")).toBe(true);

    mouseMove({ clientX: 215 }, ctx);
    expect(el.classList.contains("summary-run-unscored")).toBe(false);
  });

  it("reads the run from a touch drag too, rather than hiding the tooltip for it", () => {
    render(
      <SummaryGraph
        categories={["Run 1", "Run 2", "Run 3"]}
        series={SERIES.map((s) => ({ ...s, data: [...s.data, 50] }))}
      />,
    );
    const { mouseMove } = lastProps().options.chart.events;
    const el = document.createElement("div");
    el.innerHTML = "<svg></svg>";
    const ctx = { el, w: { globals: { gridWidth: 200, translateX: 20 } } };

    mouseMove({ touches: [{ clientX: 115 }] }, ctx);

    expect(el.classList.contains("summary-run-unscored")).toBe(false);
  });
});
