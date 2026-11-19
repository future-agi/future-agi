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

describe("SummaryTable run label", () => {
  it("names the environment version a run was pinned to after its agent", () => {
    render(
      <SummaryTable
        evals={[]}
        rows={[
          {
            id: "a",
            label: "Run 2",
            status: "completed",
            agentVersion: "v2",
            environmentVersion: { id: "e3", label: "v3" },
          },
          { id: "b", label: "Run 1", status: "completed", agentVersion: "v1" },
        ]}
      />,
    );
    expect(
      screen.getByText("Run 2 · agent v2 × environment v3"),
    ).toBeInTheDocument();
    // A run from before versioning has no environment version to name.
    expect(screen.getByText("Run 1 · agent v1")).toBeInTheDocument();
  });
});
