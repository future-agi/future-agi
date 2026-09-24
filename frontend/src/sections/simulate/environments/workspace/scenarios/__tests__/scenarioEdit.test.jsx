import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";

import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import { generatedPool } from "src/api/simulate-environments/_fixtures/scenarioPool";
import ScenariosStep from "../ScenariosStep";
import ScenarioEditor from "../ScenarioEditor";
import AddScenariosDrawer from "../AddScenariosDrawer";

const env = MOCK_WORLD;
// The first twelve pooled rows are the rule probes; the rest of the pool
// (traps, adversarial, edge, core) are the candidates the Add drawer offers,
// since it only surfaces scenarios not already on the environment.
const rows = generatedPool(env).slice(0, 12);

const renderStep = (scenarios = rows) => {
  const patch = vi.fn();
  render(<ScenariosStep env={env} envState={{ scenarios }} patch={patch} />);
  return { patch };
};

describe("ScenariosStep — add", () => {
  it("disables the Add scenarios button (coming soon) — the drawer stays built", () => {
    renderStep();
    expect(screen.getByRole("button", { name: "Add scenarios" })).toBeDisabled();
  });

  // The drawer is kept fully wired even though the Add button is disabled for
  // now, so it is exercised directly here rather than through the trigger.
  it("the add drawer appends the chosen scenarios through onAdd", () => {
    const onAdd = vi.fn();
    render(
      <AddScenariosDrawer open env={env} selected={rows} onAdd={onAdd} onClose={() => {}} />,
    );

    // The drawer's subtitle is unique to it.
    expect(screen.getByText(/could not know to write/i)).toBeInTheDocument();

    // Select every candidate, then confirm.
    fireEvent.click(screen.getByRole("checkbox", { name: /select all/i }));
    fireEvent.click(screen.getByRole("button", { name: /^Add \d+ scenarios?$/ }));

    expect(onAdd).toHaveBeenCalledTimes(1);
    const added = onAdd.mock.calls[0][0];
    expect(added.length).toBeGreaterThan(0);
  });
});

describe("ScenariosStep — edit", () => {
  it("enables the per-row edit pencil", () => {
    renderStep();
    const pencils = screen.getAllByRole("button", { name: "Edit scenario" });
    expect(pencils[0]).toBeEnabled();
  });

  it("opens the editor prefilled and patches the edited row on save", () => {
    const { patch } = renderStep();
    fireEvent.click(screen.getAllByRole("button", { name: "Edit scenario" })[0]);

    // The scenario Name field is the first "Name" input; the persona also has one.
    const nameField = screen.getAllByLabelText("Name", { selector: "input" })[0];
    expect(nameField).toHaveValue(rows[0].name);
    // Sub-goals prefill from the same derivation the table renders.
    expect(screen.getByLabelText("Sub-goals")).not.toHaveValue("");

    // Nothing edited yet, so Save is held; it unlocks once a field changes.
    const saveBtn = screen.getByRole("button", { name: "Save scenario" });
    expect(saveBtn).toBeDisabled();

    fireEvent.change(nameField, { target: { value: "renamed-scenario" } });
    expect(saveBtn).toBeEnabled();
    fireEvent.click(saveBtn);

    expect(patch).toHaveBeenCalledTimes(1);
    const next = patch.mock.calls[0][0].scenarios;
    expect(next).toHaveLength(12);
    const edited = next.find((s) => s.id === rows[0].id);
    expect(edited.name).toBe("renamed-scenario");
  });
});

describe("ScenarioEditor draft", () => {
  const row = {
    id: "s1",
    name: "original-name",
    useCase: "Refunds",
    situation: "A caller wants a refund.",
    expected: "Agent refuses.",
  };

  const renderEditor = (r) =>
    render(
      <ScenarioEditor open row={r} env={env} onClose={vi.fn()} onSave={vi.fn()} />,
    );

  it("keeps an in-progress edit when the same row arrives as a new object", () => {
    const { rerender } = renderEditor(row);
    const nameField = screen.getAllByLabelText("Name", { selector: "input" })[0];

    fireEvent.change(nameField, { target: { value: "half-typed" } });
    expect(screen.getAllByLabelText("Name", { selector: "input" })[0]).toHaveValue(
      "half-typed",
    );

    // A poll re-renders the parent, handing down an equal-but-new row object.
    rerender(
      <ScenarioEditor
        open
        row={{ ...row }}
        env={env}
        onClose={vi.fn()}
        onSave={vi.fn()}
      />,
    );

    expect(screen.getAllByLabelText("Name", { selector: "input" })[0]).toHaveValue(
      "half-typed",
    );
  });

  it("re-opens on a different row with that row's values", () => {
    const { rerender } = renderEditor(row);
    fireEvent.change(
      screen.getAllByLabelText("Name", { selector: "input" })[0],
      { target: { value: "half-typed" } },
    );

    rerender(
      <ScenarioEditor
        open
        row={{ ...row, id: "s2", name: "second-name" }}
        env={env}
        onClose={vi.fn()}
        onSave={vi.fn()}
      />,
    );

    expect(screen.getAllByLabelText("Name", { selector: "input" })[0]).toHaveValue(
      "second-name",
    );
  });
});
