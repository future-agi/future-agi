import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";

import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import { generatedPool } from "src/api/simulate-environments/_fixtures/scenarioPool";
import { getScenarioSelection } from "../../../buildEnvironment/console/scenarioSelectionBus";
import ScenariosStep from "../ScenariosStep";

// notistack ships a default context, but the bulk-delete Undo test needs to
// reach into the enqueued snackbar's action, so capture every call.
const { mockSnack } = vi.hoisted(() => ({
  mockSnack: { calls: [], close: () => {} },
}));
vi.mock("notistack", () => ({
  useSnackbar: () => ({
    enqueueSnackbar: (message, options) => {
      mockSnack.calls.push({ message, options });
      return "snack-key";
    },
    closeSnackbar: (...args) => mockSnack.close(...args),
  }),
}));

const env = MOCK_WORLD;
// The first twelve pooled rows are rule probes — four rules x three variants —
// so the fixture carries exactly four use cases with three scenarios each,
// which is what the search and filter assertions below lean on.
const rows = generatedPool(env).slice(0, 12);

const renderStep = (scenarios = rows) => {
  const patch = vi.fn();
  render(<ScenariosStep env={env} envState={{ scenarios }} patch={patch} />);
  return { patch };
};

beforeEach(() => {
  mockSnack.calls = [];
});

describe("ScenariosStep", () => {
  it("renders the scenario count", () => {
    renderStep();
    expect(screen.getByText("(12)")).toBeInTheDocument();
  });

  it("narrows the list on search, shows N of M, and clears", () => {
    renderStep();
    const search = screen.getByPlaceholderText(/Search scenarios/i);
    fireEvent.change(search, { target: { value: rows[0].name } });

    expect(screen.getByText("1 of 12")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.queryByText("1 of 12")).not.toBeInTheDocument();
    expect(search).toHaveValue("");
  });

  it("filters the list to a use case through the platform FilterPanel", async () => {
    renderStep();
    fireEvent.click(screen.getByRole("button", { name: /^Filter/ }));

    // The shared FilterPanel is open — its Ask-AI box is unique to it.
    expect(screen.getByPlaceholderText(/Ask AI/i)).toBeInTheDocument();

    // The default Basic row targets Use case; open its value picker and pick the
    // use case rows[0] belongs to. The picker option renders into a portal
    // appended after the table, so the label appears twice — the option is last.
    fireEvent.click(screen.getByText("Select values..."));
    const searchValues = screen.getByPlaceholderText("Search values...");
    fireEvent.change(searchValues, { target: { value: rows[0].useCase } });
    const options = screen.getAllByText(rows[0].useCase);
    fireEvent.click(options[options.length - 1]);

    // The selected rule use case carries three of the twelve scenarios; it
    // applies on the panel's debounce.
    expect(await screen.findByText("3 of 12")).toBeInTheDocument();
  });

  it("excludes a use case with the Is-not operator", async () => {
    renderStep();
    fireEvent.click(screen.getByRole("button", { name: /^Filter/ }));

    // Flip the default Use case row from Is to Is not. MUI Select opens on
    // mousedown; the operator select is the second combobox in the row.
    const combos = screen.getAllByRole("combobox");
    fireEvent.mouseDown(combos[1]);
    fireEvent.click(screen.getByRole("option", { name: "Is not" }));

    fireEvent.click(screen.getByText("Select values..."));
    const searchValues = screen.getByPlaceholderText("Search values...");
    fireEvent.change(searchValues, { target: { value: rows[0].useCase } });
    const options = screen.getAllByText(rows[0].useCase);
    fireEvent.click(options[options.length - 1]);

    // Excluding that three-scenario use case leaves nine of twelve.
    expect(await screen.findByText("9 of 12")).toBeInTheDocument();
  });

  it("switches between the table and list views", () => {
    renderStep();
    expect(screen.getByRole("table")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "List" }));
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText(rows[0].name)).toBeInTheDocument();
  });

  it("removes a scenario through patch without that id", () => {
    const { patch } = renderStep();
    const removes = screen.getAllByRole("button", {
      name: "Remove from this environment",
    });
    fireEvent.click(removes[0]);

    expect(patch).toHaveBeenCalledTimes(1);
    const next = patch.mock.calls[0][0].scenarios;
    expect(next).toHaveLength(11);
    expect(next.map((s) => s.id)).not.toContain(rows[0].id);
  });

  it("shows the empty placeholder with a disabled (coming-soon) Add when there are no scenarios", () => {
    renderStep([]);
    expect(screen.getByText("No scenarios yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add scenarios" })).toBeDisabled();
  });

  it("enables the edit pencil", () => {
    renderStep();
    const pencils = screen.getAllByRole("button", { name: "Edit scenario" });
    expect(pencils[0]).toBeEnabled();
  });
});

describe("ScenariosStep — bulk selection", () => {
  // getAllByRole("checkbox")[0] is the table's select-all header; [1] is the
  // first data row.
  const selectFirstRow = () => fireEvent.click(screen.getAllByRole("checkbox")[1]);

  it("surfaces the SelectionBar and publishes the selection to the bus", () => {
    renderStep();
    selectFirstRow();

    expect(screen.getByText("scenario selected")).toBeInTheDocument();
    expect(getScenarioSelection().ids).toEqual([rows[0].id]);
  });

  it("bulk-deletes the selected rows through patch and offers an Undo that restores them", () => {
    const { patch } = renderStep();
    selectFirstRow();

    // MUI applies the button's Tooltip title as its accessible name.
    fireEvent.click(screen.getByRole("button", { name: "Delete selected scenarios" }));

    expect(patch).toHaveBeenCalledTimes(1);
    const afterDelete = patch.mock.calls[0][0].scenarios;
    expect(afterDelete).toHaveLength(11);
    expect(afterDelete.map((s) => s.id)).not.toContain(rows[0].id);

    // The delete cleared the selection off the bus.
    expect(getScenarioSelection().ids).toEqual([]);

    // One info snackbar was enqueued; render its Undo action and click it.
    expect(mockSnack.calls).toHaveLength(1);
    expect(mockSnack.calls[0].options.variant).toBe("info");
    render(mockSnack.calls[0].options.action("snack-key"));
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));

    const restored = patch.mock.calls[1][0].scenarios;
    expect(restored).toHaveLength(12);
    expect(restored.map((s) => s.id)).toContain(rows[0].id);
  });

  it("clears the selection and empties the bus", () => {
    renderStep();
    selectFirstRow();
    expect(getScenarioSelection().ids).toEqual([rows[0].id]);

    // The SelectionBar's clear affordance is the count-pill ✕, labelled
    // "Clear selection".
    fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(screen.queryByText("scenario selected")).not.toBeInTheDocument();
    expect(getScenarioSelection().ids).toEqual([]);
  });
});

describe("ScenariosStep — locked template", () => {
  const LOCK_TOOLTIP = "Fork this environment to edit.";

  const renderLocked = (locked) => {
    const patch = vi.fn();
    render(<ScenariosStep env={env} envState={{ scenarios: rows }} patch={patch} locked={locked} />);
    return { patch };
  };

  it("locks the Add scenarios CTA behind the fork tooltip", () => {
    renderLocked(true);
    expect(screen.getByRole("button", { name: "Add scenarios" })).toBeDisabled();
    // MUI applies the Tooltip title as an aria-label on the wrapping span.
    expect(screen.getAllByLabelText(LOCK_TOOLTIP).length).toBeGreaterThan(0);
  });

  it("disables the per-row edit and delete with the fork tooltip", () => {
    renderLocked(true);
    expect(screen.getAllByRole("button", { name: "Edit scenario" })[0]).toBeDisabled();
    expect(
      screen.getAllByRole("button", { name: "Remove from this environment" })[0],
    ).toBeDisabled();
    // Every disabled row control carries the fork tooltip.
    expect(screen.getAllByLabelText(LOCK_TOOLTIP).length).toBeGreaterThan(0);
  });

  it("hides bulk-select — no checkboxes while locked", () => {
    renderLocked(true);
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
  });

  it("disables the List-view per-row edit and delete while locked", () => {
    renderLocked(true);
    fireEvent.click(screen.getByRole("tab", { name: "List" }));
    expect(screen.getAllByRole("button", { name: "Edit scenario" })[0]).toBeDisabled();
    expect(
      screen.getAllByRole("button", { name: "Remove from this environment" })[0],
    ).toBeDisabled();
  });

  it("keeps group-by and hide-group available while locked — they're view state, not mutations", () => {
    renderLocked(true);
    expect(screen.getByRole("button", { name: /^Group by/ })).toBeEnabled();
    expect(screen.getAllByRole("button", { name: "Hide this group" })[0]).toBeEnabled();
  });

  it("leaves the row controls enabled and checkboxes present when not locked", () => {
    renderLocked(false);
    expect(screen.getAllByRole("button", { name: "Edit scenario" })[0]).toBeEnabled();
    expect(screen.getAllByRole("button", { name: "Remove from this environment" })[0]).toBeEnabled();
    expect(screen.queryAllByRole("checkbox").length).toBeGreaterThan(0);
    expect(screen.queryByLabelText(LOCK_TOOLTIP)).toBeNull();
  });
});

describe("groupScenarios", () => {
  it("buckets rows by their use case, largest group first", async () => {
    const { groupScenarios } = await import("../scenarios.constants");
    const groups = groupScenarios(rows);
    expect(groups).toHaveLength(4);
    groups.forEach((g) => expect(g.rows).toHaveLength(3));
  });

  it("buckets by persona with a lowercased, prefixed id", async () => {
    const { groupScenarios } = await import("../scenarios.constants");
    const groups = groupScenarios(rows, "persona", env);
    groups.forEach((g) => {
      expect(g.id).toMatch(/^persona:/);
      expect(g.id).toBe(g.id.toLowerCase());
    });
    expect(groups.reduce((n, g) => n + g.rows.length, 0)).toBe(rows.length);
  });

  it("buckets by sub-goal with a slugged, prefixed id", async () => {
    const { groupScenarios } = await import("../scenarios.constants");
    const groups = groupScenarios(rows, "subgoal", env);
    groups.forEach((g) => expect(g.id).toMatch(/^subgoal:[a-z0-9-]+$/));
    expect(groups.reduce((n, g) => n + g.rows.length, 0)).toBe(rows.length);
  });
});

describe("groupKeyOf fallbacks", () => {
  it("labels a persona-less row 'No persona'", async () => {
    const { groupKeyOf } = await import("../scenarios.constants");
    expect(groupKeyOf({ id: "x" }, "persona")).toEqual({ id: "persona:none", label: "No persona" });
  });

  it("labels a sub-goal-less row 'No sub-goals'", async () => {
    const { groupKeyOf } = await import("../scenarios.constants");
    expect(groupKeyOf(null, "subgoal")).toEqual({ id: "subgoal:none", label: "No sub-goals" });
  });
});

describe("ScenariosStep — group-by axis", () => {
  it("regroups on a new axis and clears any hidden groups", async () => {
    const { groupScenarios } = await import("../scenarios.constants");
    renderStep();
    // Default axis is Goal: four use-case groups, each with a hide control.
    expect(groupScenarios(rows)).toHaveLength(4);
    // Hide one group, so there's a hidden set to clear.
    fireEvent.click(screen.getAllByRole("button", { name: "Hide this group" })[0]);
    expect(screen.getByText("12 of 12 · 1 group hidden")).toBeInTheDocument();

    // Switch the axis to Persona through the popover.
    fireEvent.click(screen.getByRole("button", { name: /^Group by/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Persona" }));

    expect(screen.getByRole("button", { name: /Group by · Persona/ })).toBeInTheDocument();
    // Switching the axis dropped the hidden set — the counter is gone.
    expect(screen.queryByText(/group hidden/)).not.toBeInTheDocument();
  });
});

describe("ScenariosStep — hide group", () => {
  it("hides a group from the table and restores it with Show all", () => {
    renderStep();
    expect(screen.getAllByRole("button", { name: "Hide this group" })).toHaveLength(4);

    fireEvent.click(screen.getAllByRole("button", { name: "Hide this group" })[0]);
    expect(screen.getAllByRole("button", { name: "Hide this group" })).toHaveLength(3);
    expect(screen.getByText("12 of 12 · 1 group hidden")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(screen.getAllByRole("button", { name: "Hide this group" })).toHaveLength(4);
  });

  it("shows the all-hidden empty state when every group is hidden", () => {
    renderStep();
    // Hiding always removes the first remaining group; four clicks empties it.
    [0, 1, 2, 3].forEach(() => {
      fireEvent.click(screen.getAllByRole("button", { name: "Hide this group" })[0]);
    });
    expect(
      screen.getByText("Every group is hidden — click Show all to bring them back."),
    ).toBeInTheDocument();
    expect(screen.getByText("12 of 12 · 4 groups hidden")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(screen.getAllByRole("button", { name: "Hide this group" })).toHaveLength(4);
  });

  it("hides a group from the list view", () => {
    renderStep();
    fireEvent.click(screen.getByRole("tab", { name: "List" }));
    expect(screen.getAllByRole("button", { name: "Hide this group" })).toHaveLength(4);

    fireEvent.click(screen.getAllByRole("button", { name: "Hide this group" })[0]);
    expect(screen.getAllByRole("button", { name: "Hide this group" })).toHaveLength(3);
    expect(screen.getByText("12 of 12 · 1 group hidden")).toBeInTheDocument();
  });

  it("keyboard activation on the list hide button does not collapse the group", async () => {
    const { groupScenarios } = await import("../scenarios.constants");
    const memberName = groupScenarios(rows)[0].rows[0].name;
    renderStep();
    fireEvent.click(screen.getByRole("tab", { name: "List" }));
    expect(screen.getByText(memberName)).toBeInTheDocument();

    // Enter on the eye button must not bubble to the header's toggle handler.
    fireEvent.keyDown(screen.getAllByRole("button", { name: "Hide this group" })[0], { key: "Enter" });
    expect(screen.getByText(memberName)).toBeInTheDocument();
  });
});
