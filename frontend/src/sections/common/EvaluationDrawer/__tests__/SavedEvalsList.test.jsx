import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";

import SavedEvalsList from "../SavedEvalsList";
import usePendingEvalSelections from "../usePendingEvalSelections";

const evalRow = (id, name) => ({
  id,
  name,
  eval_type: "llm",
  template_name: name,
  mapping: { output: "model_output" },
  eval_required_keys: ["output"],
});
const A = evalRow("e1", "toxicity");
const B = evalRow("e2", "groundedness");

// Exercise the same queue and acknowledgement path as EvaluationDrawer. These
// buttons stand in for successful saves; changing evals simulates a GET refresh.
function SavedListSession({ evals }) {
  const { autoSelectRequests, requestAutoSelect, acknowledgeAutoSelect } =
    usePendingEvalSelections();
  return (
    <>
      <button onClick={() => requestAutoSelect(A.name)}>Save A</button>
      <button onClick={() => requestAutoSelect(B.name)}>Save B</button>
      <button
        onClick={() => {
          requestAutoSelect(A.name);
          requestAutoSelect(B.name);
        }}
      >
        Save both
      </button>
      <output aria-label="Pending saves">{autoSelectRequests.length}</output>
      <SavedEvalsList
        evals={evals}
        autoSelectRequests={autoSelectRequests}
        onAutoSelectApplied={acknowledgeAutoSelect}
        allColumns={[]}
      />
    </>
  );
}

SavedListSession.propTypes = { evals: PropTypes.array.isRequired };

// The first checkbox is select-all; row checkboxes follow in evals order.
const rowCheckboxes = () => screen.getAllByRole("checkbox").slice(1);
const checkboxFor = (evalItem) =>
  screen.getByRole("checkbox", { name: `Select ${evalItem.name}` });
const save = (user, name) => user.click(screen.getByRole("button", { name }));

describe("SavedEvalsList — auto-select of saved evals", () => {
  it("waits for a newly saved row before selecting it and acknowledging the save", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<SavedListSession evals={[]} />);
    await save(user, "Save A");
    expect(screen.getByLabelText("Pending saves")).toHaveTextContent("1");

    rerender(<SavedListSession evals={[A]} />);
    expect(rowCheckboxes()[0]).toBeChecked();
    expect(screen.getByLabelText("Pending saves")).toHaveTextContent("0");
  });

  it("selects only the eval that was saved", async () => {
    const user = userEvent.setup();
    render(<SavedListSession evals={[A, B]} />);
    await save(user, "Save A");
    expect(rowCheckboxes()[0]).toBeChecked();
    expect(rowCheckboxes()[1]).not.toBeChecked();
  });

  it("keeps a manual uncheck when another eval is added", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<SavedListSession evals={[A]} />);
    await save(user, "Save A");
    await user.click(rowCheckboxes()[0]);
    await save(user, "Save B");
    rerender(<SavedListSession evals={[A, B]} />);
    expect(rowCheckboxes()[0]).not.toBeChecked();
    expect(rowCheckboxes()[1]).toBeChecked();
  });

  it("re-selects an eval explicitly edited after a manual uncheck", async () => {
    const user = userEvent.setup();
    render(<SavedListSession evals={[A, B]} />);
    await save(user, "Save both");
    await user.click(rowCheckboxes()[0]);
    expect(rowCheckboxes()[0]).not.toBeChecked();
    await save(user, "Save A");
    expect(rowCheckboxes()[0]).toBeChecked();
    expect(rowCheckboxes()[1]).toBeChecked();
  });

  it("does not re-select on a status refresh", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<SavedListSession evals={[A]} />);
    await save(user, "Save A");
    await user.click(rowCheckboxes()[0]);
    rerender(<SavedListSession evals={[{ ...A, status: "running" }]} />);
    expect(rowCheckboxes()[0]).not.toBeChecked();
    expect(screen.getByText("Evals (1)")).toBeInTheDocument();
  });

  it("selects nothing when no eval has been saved in this session", () => {
    render(<SavedListSession evals={[A]} />);
    expect(rowCheckboxes()[0]).not.toBeChecked();
  });

  it("retains two saves before a delayed refresh brings both rows", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<SavedListSession evals={[]} />);
    // Both updates happen before React renders, catching stale queue writes.
    await save(user, "Save both");
    expect(screen.getByLabelText("Pending saves")).toHaveTextContent("2");
    rerender(<SavedListSession evals={[A, B]} />);
    expect(rowCheckboxes()[0]).toBeChecked();
    expect(rowCheckboxes()[1]).toBeChecked();
    expect(screen.getByLabelText("Pending saves")).toHaveTextContent("0");
  });

  it("handles rows arriving out of save order without undoing a manual uncheck", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<SavedListSession evals={[]} />);
    await save(user, "Save A");
    await save(user, "Save B");
    rerender(<SavedListSession evals={[B]} />);
    expect(rowCheckboxes()[0]).toBeChecked();
    expect(screen.getByLabelText("Pending saves")).toHaveTextContent("1");
    await user.click(rowCheckboxes()[0]);
    rerender(<SavedListSession evals={[A, B]} />);
    expect(rowCheckboxes()[0]).toBeChecked();
    expect(rowCheckboxes()[1]).not.toBeChecked();
    expect(screen.getByLabelText("Pending saves")).toHaveTextContent("0");
  });
});

// Simulation has its own Run Evaluation button, so it asks for checkboxes
// without the list's run actions and reads the selection back.
describe("SavedEvalsList — selectable without run actions", () => {
  it("shows checkboxes but no run buttons, and reports the checked ids", async () => {
    const user = userEvent.setup();
    const onSelectionChange = vi.fn();
    render(
      <SavedEvalsList
        evals={[A, B]}
        showRun={false}
        selectable
        onSelectionChange={onSelectionChange}
        allColumns={[]}
      />,
    );

    expect(checkboxFor(A)).not.toBeChecked();
    expect(screen.queryByRole("button", { name: /Run All/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();

    await user.click(checkboxFor(B));
    expect(checkboxFor(B)).toBeChecked();
    expect(onSelectionChange).toHaveBeenLastCalledWith([B.id]);
    expect(screen.queryByRole("button", { name: /Run Selected/ })).toBeNull();

    await user.click(checkboxFor(B));
    expect(onSelectionChange).toHaveBeenLastCalledWith([]);
  });

  it("names each checkbox after what it selects", async () => {
    const user = userEvent.setup();
    render(
      <SavedEvalsList
        evals={[A, B]}
        showRun={false}
        selectable
        allColumns={[]}
      />,
    );

    expect(checkboxFor(A)).not.toBeChecked();
    expect(checkboxFor(B)).not.toBeChecked();
    await user.click(
      screen.getByRole("checkbox", { name: "Select all evaluations" }),
    );
    expect(checkboxFor(A)).toBeChecked();
    expect(checkboxFor(B)).toBeChecked();
  });

  it("keeps checkboxes hidden when run actions are off and selectable is not set", () => {
    render(<SavedEvalsList evals={[A, B]} showRun={false} allColumns={[]} />);
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
  });
});
