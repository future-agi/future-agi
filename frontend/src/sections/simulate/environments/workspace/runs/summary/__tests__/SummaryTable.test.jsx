import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import { format } from "../../detail/analytics/DashboardCharts";

vi.mock("../../StopRunControl", () => ({ default: () => null }));

const { default: SummaryTable } = await import("../SummaryTable");
const { STATUS_META } = await import("../../runs.constants");

const passCell = (label) => {
  const row = screen.getByText(label).closest("tr");
  // Run, Status, Scenarios, Trials, Simulations, then Pass.
  return within(row).getAllByRole("cell")[5];
};

describe("SummaryTable Pass column", () => {
  it("shows a rate, No score, or a dash, and never a null percentage", () => {
    render(
      <SummaryTable
        evals={[]}
        rows={[
          { id: "a", label: "Run 3", status: "failed", passRate: 60 },
          { id: "b", label: "Run 2", status: "no_score", passRate: null },
          { id: "c", label: "Run 1", status: "cancelled", passRate: null },
        ]}
      />,
    );
    expect(passCell("Run 3")).toHaveTextContent("60%");
    expect(passCell("Run 2")).toHaveTextContent(STATUS_META.no_score.label);
    expect(passCell("Run 1")).toHaveTextContent("-");
  });

  it("rounds a rate as the run's analytics does, so both pages read the same", () => {
    render(
      <SummaryTable
        evals={[]}
        rows={[{ id: "a", label: "Run 3", status: "failed", passRate: 66.67 }]}
      />,
    );
    expect(passCell("Run 3")).toHaveTextContent(format(66.67, "percent"));
    expect(passCell("Run 3")).not.toHaveTextContent("66.67");
  });
});

describe("SummaryTable release split", () => {
  it("tags a run with its release and split", () => {
    render(
      <SummaryTable
        evals={[]}
        rows={[
          {
            id: "a",
            label: "Run 3",
            status: "passed",
            passRate: 80,
            split: "test",
            release: { label: "2026.11.1", locked: true },
          },
          {
            id: "b",
            label: "Run 2",
            status: "passed",
            passRate: 80,
            release: { label: "2026.11.1", locked: true },
          },
          { id: "c", label: "Run 1", status: "passed", passRate: 80 },
        ]}
      />,
    );
    const row = (label) => screen.getByText(label).closest("tr");
    expect(
      within(row("Run 3")).getByText("release 2026.11.1 · test"),
    ).toBeInTheDocument();
    expect(
      within(row("Run 2")).getByText("release 2026.11.1"),
    ).toBeInTheDocument();
    expect(within(row("Run 1")).queryByText(/^release /)).toBeNull();
  });
});
