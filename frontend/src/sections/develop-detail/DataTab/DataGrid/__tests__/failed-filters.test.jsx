import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { FailedOnlyChip } from "../ToolbarControls";
import { useDataGridViewStore } from "../useDataGridView";

const datasetId = "filter-test";
const evals = [
  { column_id: "quality", name: "Quality", count: 2 },
  { column_id: "safety", name: "Safety", count: 2 },
];

beforeEach(() => useDataGridViewStore.setState({ views: {} }));

describe("failed evaluation pill", () => {
  it("toggles the filter and clears a selected evaluator when disabled", () => {
    render(<FailedOnlyChip datasetId={datasetId} count={3} evals={evals} />);
    fireEvent.click(screen.getByRole("button", { name: /Failed only/ }));
    expect(useDataGridViewStore.getState().view(datasetId).failedOnly).toBe(
      true,
    );
    fireEvent.click(screen.getByRole("button", { name: "Failures by eval" }));
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Only rows failing Quality" }),
    );
    expect(
      useDataGridViewStore.getState().view(datasetId).failedEvalIds,
    ).toEqual(["quality"]);
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: /Failed only/ }));
    expect(useDataGridViewStore.getState().view(datasetId)).toMatchObject({
      failedOnly: false,
      failedEvalIds: [],
    });
  });

  it("keeps an active filter removable even when no rows or evals remain", () => {
    useDataGridViewStore.getState().patch(datasetId, { failedOnly: true });
    render(
      <FailedOnlyChip datasetId={datasetId} count={0} evals={[]} disabled />,
    );
    const toggle = screen.getByRole("button", { name: /Failed only/ });
    expect(toggle).not.toBeDisabled();
    fireEvent.click(toggle);
    expect(useDataGridViewStore.getState().view(datasetId).failedOnly).toBe(
      false,
    );
  });

  it("does not add overlapping per-evaluator counts", () => {
    useDataGridViewStore
      .getState()
      .patch(datasetId, {
        failedOnly: true,
        failedEvalIds: ["quality", "safety"],
      });
    render(<FailedOnlyChip datasetId={datasetId} count={3} evals={evals} />);
    expect(
      screen.getByRole("button", { name: /Failed only/ }),
    ).toHaveTextContent(/^Failed only$/);
  });
});
