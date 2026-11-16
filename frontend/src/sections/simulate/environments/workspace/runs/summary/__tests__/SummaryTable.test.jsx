import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";

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
});
