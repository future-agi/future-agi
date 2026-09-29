import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";

import SavedEvalsList from "../SavedEvalsList";

const evalRow = (id, name) => ({
  id,
  name,
  eval_type: "llm",
  template_name: name,
  mapping: { output: "model_output" },
  eval_required_keys: ["output"],
});

// One save = one {name, token}. The drawer bumps the token on every successful
// save, so re-saving the same eval is a new event.
const save = (name, token) => ({ name, token });

const list = ({ evals, autoSelectRequest }) => (
  <SavedEvalsList
    evals={evals}
    autoSelectRequest={autoSelectRequest}
    onDeleteEvalClick={vi.fn()}
    onRunEvalClick={vi.fn()}
    onStopEvalClick={vi.fn()}
    onEditEvalClick={vi.fn()}
    allColumns={[]}
  />
);

const renderList = (props) => render(list(props));

// The first checkbox is the header select-all; row checkboxes follow in
// `evals` order.
const rowCheckboxes = () => screen.getAllByRole("checkbox").slice(1);

describe("SavedEvalsList — auto-select of saved evals", () => {
  it("checks a newly added eval once its row arrives in the list", async () => {
    // The drawer records the save immediately, but the row only appears after
    // the grid refresh, so the effect has to fire on the later `evals` change.
    const { rerender } = renderList({
      evals: [],
      autoSelectRequest: save("toxicity", 1),
    });

    rerender(
      list({
        evals: [evalRow("e1", "toxicity")],
        autoSelectRequest: save("toxicity", 1),
      }),
    );

    await waitFor(() => {
      expect(rowCheckboxes()[0]).toBeChecked();
    });
    expect(screen.getByText("1 of 1 selected")).toBeInTheDocument();
  });

  it("selects only the eval that was saved, not others in the list", () => {
    renderList({
      evals: [evalRow("e1", "toxicity"), evalRow("e2", "groundedness")],
      autoSelectRequest: save("toxicity", 1),
    });

    const [first, second] = rowCheckboxes();
    expect(first).toBeChecked();
    expect(second).not.toBeChecked();
  });

  it("keeps a manual uncheck when a different eval is added afterwards", async () => {
    const user = userEvent.setup();
    const { rerender } = renderList({
      evals: [evalRow("e1", "toxicity")],
      autoSelectRequest: save("toxicity", 1),
    });

    await waitFor(() => expect(rowCheckboxes()[0]).toBeChecked());

    // User deliberately deselects the auto-selected eval.
    await user.click(rowCheckboxes()[0]);
    expect(rowCheckboxes()[0]).not.toBeChecked();

    // A *different* eval is saved. Only that one should get checked — the
    // token names groundedness, so toxicity is never reconsidered.
    rerender(
      list({
        evals: [evalRow("e1", "toxicity"), evalRow("e2", "groundedness")],
        autoSelectRequest: save("groundedness", 2),
      }),
    );

    await waitFor(() => {
      expect(rowCheckboxes()[1]).toBeChecked();
    });
    expect(rowCheckboxes()[0]).not.toBeChecked();
    expect(screen.getByText("1 of 2 selected")).toBeInTheDocument();
  });

  it("re-selects an eval that is edited after being manually unchecked", async () => {
    // The counterpart to the test above, and the reason a set of names is not
    // enough: adding another eval must not revive toxicity, but explicitly
    // editing and saving toxicity must. Only the token distinguishes them.
    const user = userEvent.setup();
    const A = evalRow("e1", "toxicity");
    const B = evalRow("e2", "groundedness");

    const { rerender } = renderList({
      evals: [A],
      autoSelectRequest: save("toxicity", 1),
    });
    await waitFor(() => expect(rowCheckboxes()[0]).toBeChecked());

    rerender(
      list({ evals: [A, B], autoSelectRequest: save("groundedness", 2) }),
    );
    await waitFor(() => expect(rowCheckboxes()[1]).toBeChecked());

    await user.click(rowCheckboxes()[0]);
    expect(rowCheckboxes()[0]).not.toBeChecked();

    // User edits toxicity and saves — a fresh token for the same name.
    rerender(list({ evals: [A, B], autoSelectRequest: save("toxicity", 3) }));

    await waitFor(() => {
      expect(rowCheckboxes()[0]).toBeChecked();
    });
    expect(rowCheckboxes()[1]).toBeChecked();
    expect(screen.getByText("2 of 2 selected")).toBeInTheDocument();
  });

  it("does not re-select on an unrelated list refresh", async () => {
    // Status polling re-renders with a new `evals` array and the same request.
    // The token is already consumed, so a manual uncheck must survive it.
    const user = userEvent.setup();
    const { rerender } = renderList({
      evals: [evalRow("e1", "toxicity")],
      autoSelectRequest: save("toxicity", 1),
    });
    await waitFor(() => expect(rowCheckboxes()[0]).toBeChecked());

    await user.click(rowCheckboxes()[0]);
    expect(rowCheckboxes()[0]).not.toBeChecked();

    // Same token, fresh array objects — as a poll would produce.
    rerender(
      list({
        evals: [{ ...evalRow("e1", "toxicity"), status: "running" }],
        autoSelectRequest: save("toxicity", 1),
      }),
    );

    await waitFor(() => {
      expect(screen.getByText("Evals (1)")).toBeInTheDocument();
    });
    expect(rowCheckboxes()[0]).not.toBeChecked();
  });

  it("selects nothing when no eval has been saved in this session", () => {
    renderList({
      evals: [evalRow("e1", "toxicity")],
      autoSelectRequest: null,
    });

    expect(rowCheckboxes()[0]).not.toBeChecked();
    expect(screen.getByText("Evals (1)")).toBeInTheDocument();
  });
});
