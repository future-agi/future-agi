import { describe, it, expect, vi, beforeEach, afterAll } from "vitest";
import { configure } from "@testing-library/react";
import { screen, fireEvent, waitFor, within } from "src/utils/test-utils";

// ScenariosStep is a heavy render (toolbar + coverage + table + filter panel);
// under full-suite parallel load its filtered-count `findByText` can exceed the
// 1000ms default. Give the async utils more headroom (well under the 30s test
// timeout) so these assertions are load-stable, not flaky.
configure({ asyncUtilTimeout: 5000 });
afterAll(() => configure({ asyncUtilTimeout: 1000 }));

import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import { generatedPool } from "src/api/simulate-environments/_fixtures/scenarioPool";
import { getScenarioSelection } from "../../../buildEnvironment/console/scenarioSelectionBus";
import ScenariosStep from "../ScenariosStep";
// Static (tests F3): a per-test `await import()` is a cold module load on a
// loaded host; the helpers are imported once with the file.
import { groupKeyOf, groupScenarios } from "../scenarios.constants";
import {
  TEST_ENV,
  SERVER_ROWS,
  envStateFor,
  renderWithClient,
  openDisplay,
  closeDisplay,
  pickView,
  pickGroupBy,
} from "./scenariosTestUtils";

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

// The list reads the server (fixtures) source; serve the 20-row sample suite.
vi.mock("src/api/simulate-environments/scenarios", async () => {
  const actual = await vi.importActual(
    "src/api/simulate-environments/scenarios",
  );
  return {
    ...actual,
    listScenarios: vi.fn(),
    amendScenarios: vi.fn(),
    // Mock coverage too so CoverageMatrix does not fire a real (failing) request
    // that spams the axios auth-redirect interceptor on every render.
    scenarioCoverage: vi.fn(async () => ({
      axes: [],
      per_axis: [],
      rows: [],
      columns: [],
      cells: [],
    })),
  };
});
const { listScenarios, amendScenarios } = await import(
  "src/api/simulate-environments/scenarios"
);
const { queryScenarioFixture, resetScenarioFixture } = await import(
  "src/api/simulate-environments/_fixtures/scenariosFixtures"
);

// The default (goal-grouped) view of the whole suite — what the table shows and
// the order the row controls follow.
const defaultView = () => queryScenarioFixture({ limit: 25 });
const firstRowId = () => defaultView().results[0].id;

const renderStep = (rows = SERVER_ROWS) => {
  const patch = vi.fn();
  renderWithClient(
    <ScenariosStep env={TEST_ENV} envState={envStateFor(rows)} patch={patch} />,
  );
  return { patch };
};

beforeEach(() => {
  mockSnack.calls = [];
  resetScenarioFixture();
  listScenarios.mockReset();
  listScenarios.mockImplementation((jobId, params) =>
    queryScenarioFixture(params, SERVER_ROWS),
  );
  amendScenarios.mockReset();
  amendScenarios.mockResolvedValue({ receipts: [] });
});

describe("ScenariosStep", () => {
  it("opens on its content, with no heading repeating the tab or its count", () => {
    renderStep();
    expect(screen.queryByText(/Each scenario is one task/)).toBeNull();
    expect(screen.queryByText(`(${SERVER_ROWS.length})`)).toBeNull();
  });

  it("puts a disabled Add scenarios in the list toolbar", () => {
    renderStep();
    const toolbar = screen.getByRole("toolbar", { name: "Scenarios" });
    const add = within(toolbar).getByRole("button", { name: "Add scenarios" });
    expect(add).toBeDisabled();
    expect(add).toHaveAccessibleDescription("Not available in this release");
  });

  it("puts Coverage in the list toolbar instead of a card above it", async () => {
    renderStep();
    const toolbar = screen.getByRole("toolbar", { name: "Scenarios" });
    expect(
      await within(toolbar).findByRole("button", { name: /^Coverage/ }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/click to expand/)).toBeNull();
  });

  it("folds view, grouping and row height into one Display panel", async () => {
    renderStep();
    await screen.findByRole("table");
    expect(screen.queryByRole("tab", { name: "List" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Group by/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Row height" })).toBeNull();

    const panel = openDisplay();
    expect(within(panel).getByRole("tab", { name: "Table" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(within(panel).getByRole("tab", { name: "Short" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    // Grouping is one dropdown, so more server axes never lengthen the panel.
    expect(
      within(panel).getByRole("combobox", { name: "Group by" }),
    ).toBeInTheDocument();
    expect(within(panel).queryByRole("option")).toBeNull();
    fireEvent.click(within(panel).getByRole("tab", { name: "Large" }));
    expect(within(panel).getByRole("tab", { name: "Large" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    closeDisplay();
  });

  it("narrows the list on server search, shows N of M, and clears", async () => {
    renderStep();
    await screen.findByRole("table");
    const search = screen.getByPlaceholderText(/Search scenarios/i);
    fireEvent.change(search, { target: { value: "injection" } });

    const expected = queryScenarioFixture({
      limit: 25,
      search: "injection",
    }).count;
    expect(expected).toBeGreaterThan(0);
    const readout = await screen.findByText(
      `${expected} of ${SERVER_ROWS.length}`,
    );
    // The count sits with the results, not in the toolbar row.
    expect(
      screen.getByRole("toolbar", { name: "Scenarios" }),
    ).not.toContainElement(readout);

    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(
      screen.queryByText(`${expected} of ${SERVER_ROWS.length}`),
    ).not.toBeInTheDocument();
    expect(search).toHaveValue("");
  });

  it("filters to a use case through the platform FilterPanel", async () => {
    renderStep();
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("button", { name: /^Filter/ }));
    // The AI filter box is hidden for scenarios (no grounded backend source);
    // the Basic filter rows are still there.
    expect(screen.queryByPlaceholderText(/Ask AI/i)).not.toBeInTheDocument();

    // The default Basic row lands on the first field (Scenario, a string). Switch
    // it to the Use case enum, then pick a value. The field select is the first
    // combobox in the row.
    const combos = screen.getAllByRole("combobox");
    fireEvent.mouseDown(combos[0]);
    fireEvent.click(screen.getByRole("option", { name: "Use case" }));

    const UC = SERVER_ROWS.find((r) => r.number === 1).use_case;
    fireEvent.click(screen.getByText("Select values..."));
    const searchValues = screen.getByPlaceholderText("Search values...");
    fireEvent.change(searchValues, { target: { value: UC.slice(0, 24) } });
    const options = screen.getAllByText(UC);
    fireEvent.click(options[options.length - 1]);

    const expected = queryScenarioFixture({ limit: 25, use_case: [UC] }).count;
    expect(
      await screen.findByText(`${expected} of ${SERVER_ROWS.length}`),
    ).toBeInTheDocument();
    // applyFilters emits the SERVER param name (use_case), not a client alias.
    expect(listScenarios).toHaveBeenLastCalledWith(
      "job-test",
      expect.objectContaining({ use_case: [UC] }),
    );
  });

  it("excludes a use case with the Is-not operator", async () => {
    renderStep();
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("button", { name: /^Filter/ }));

    const combos = screen.getAllByRole("combobox");
    fireEvent.mouseDown(combos[0]);
    fireEvent.click(screen.getByRole("option", { name: "Use case" }));

    // Flip the row from Is to Is not (the operator is the second combobox).
    const afterField = screen.getAllByRole("combobox");
    fireEvent.mouseDown(afterField[1]);
    fireEvent.click(screen.getByRole("option", { name: "Is not" }));

    const UC = SERVER_ROWS.find((r) => r.number === 1).use_case;
    fireEvent.click(screen.getByText("Select values..."));
    const searchValues = screen.getByPlaceholderText("Search values...");
    fireEvent.change(searchValues, { target: { value: UC.slice(0, 24) } });
    const options = screen.getAllByText(UC);
    fireEvent.click(options[options.length - 1]);

    const expected = queryScenarioFixture({
      limit: 25,
      use_case_not: [UC],
    }).count;
    expect(
      await screen.findByText(`${expected} of ${SERVER_ROWS.length}`),
    ).toBeInTheDocument();
    // Negation emits the `<field>_not` server key.
    expect(listScenarios).toHaveBeenLastCalledWith(
      "job-test",
      expect.objectContaining({ use_case_not: [UC] }),
    );
  });

  it("switches between the table and list views", async () => {
    renderStep();
    expect(await screen.findByRole("table")).toBeInTheDocument();
    const aName = SERVER_ROWS[0].name;

    pickView("List");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getAllByText(aName).length).toBeGreaterThan(0);
  });

  it("removes a scenario through a confirmed amend drop by name", async () => {
    renderStep();
    await screen.findByRole("table");
    const firstName = defaultView().results[0].name;
    const removes = screen.getAllByRole("button", {
      name: "Remove from this environment",
    });
    fireEvent.click(removes[0]);

    // The trash asks first — a server delete has no undo.
    expect(screen.getByText("Delete scenarios?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(amendScenarios).toHaveBeenCalledTimes(1));
    expect(amendScenarios).toHaveBeenCalledWith("job-test", {
      rework: true,
      changes: [{ op: "drop", scenarios: [firstName] }],
    });
  });

  it("shows the empty placeholder with a disabled Add that says Not available in this release", async () => {
    // The empty-state gate reads the server's unfiltered suite total now, so the
    // list source must report an empty suite (not just an empty envState).
    listScenarios.mockImplementation((jobId, params) =>
      queryScenarioFixture(params, []),
    );
    renderStep([]);
    expect(await screen.findByText("No scenarios yet")).toBeInTheDocument();
    const add = screen.getByRole("button", { name: "Add scenarios" });
    expect(add).toBeDisabled();
    expect(add).toHaveAccessibleDescription("Not available in this release");
    expect(screen.queryByText(/coming soon/i)).toBeNull();
  });

  it("names the builder chat as the way to add scenarios when there are none", async () => {
    // The tab has no working Add, so the empty state names what works: asking
    // the builder in the chat. Same empty-suite setup as the test above.
    listScenarios.mockImplementation((jobId, params) =>
      queryScenarioFixture(params, []),
    );
    renderStep([]);
    expect(await screen.findByText("No scenarios yet")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Ask the builder in the chat to add scenarios, for example “Add 10 scenarios for refund requests”.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/normally derived/)).toBeNull();
  });

  it("enables the edit pencil", async () => {
    renderStep();
    await screen.findByRole("table");
    const pencils = screen.getAllByRole("button", { name: "Edit scenario" });
    expect(pencils[0]).toBeEnabled();
  });
});

// W10-FILTERS-U01: the server matches a Scenario or Situation filter against
// the whole text, so only Equals and Not equals work. A "Contains" row used to
// send the typed text as an exact name (the list went empty) and "Does not
// contain" kept the row instead of hiding it. Nothing is removed: Contains,
// Starts with and Does not contain are still listed, turned off, each with
// "Not available in this release" under it.
describe("ScenariosStep — text filters match the whole text", () => {
  const openFilter = async () => {
    renderStep();
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("button", { name: /^Filter/ }));
  };
  const optionLabels = () =>
    screen.getAllByRole("option").map((o) => o.textContent.trim());
  // An option's text is its label, then its reason when it has one.
  const TEXT_OPERATORS = [
    "Contains",
    "Equals",
    "Not equals",
    "Starts with",
    "Does not contain",
  ];
  const labelOf = (o) =>
    TEXT_OPERATORS.find((label) => o.textContent.startsWith(label));
  const option = (label) =>
    screen.getAllByRole("option").find((o) => labelOf(o) === label);

  const expectTextOperators = () => {
    expect(screen.getAllByRole("option").map(labelOf)).toEqual(TEXT_OPERATORS);
    for (const label of ["Contains", "Starts with", "Does not contain"]) {
      expect(option(label)).toHaveAttribute("aria-disabled", "true");
      expect(
        within(option(label)).getByText("Not available in this release"),
      ).toBeInTheDocument();
    }
    for (const label of ["Equals", "Not equals"]) {
      expect(option(label)).not.toHaveAttribute("aria-disabled", "true");
      expect(option(label).textContent).toBe(label);
    }
  };

  it("lists Contains, Starts with and Does not contain for Scenario, turned off with the reason", async () => {
    await openFilter();

    // The default row lands on Scenario, a text field; the operator is the
    // second combobox in the row. A new row still starts on Equals.
    const combos = screen.getAllByRole("combobox");
    expect(combos[0].textContent).toBe("Scenario");
    expect(combos[1].textContent).toBe("Equals");

    fireEvent.mouseDown(combos[1]);
    expectTextOperators();
  });

  it("lists the same turned-off operators for Situation", async () => {
    await openFilter();

    fireEvent.mouseDown(screen.getAllByRole("combobox")[0]);
    fireEvent.click(screen.getByRole("option", { name: "Situation" }));

    const combos = screen.getAllByRole("combobox");
    expect(combos[0].textContent).toBe("Situation");
    expect(combos[1].textContent).toBe("Equals");

    fireEvent.mouseDown(combos[1]);
    expectTextOperators();
  });

  it("hides the named scenario with Not equals", async () => {
    await openFilter();
    fireEvent.mouseDown(screen.getAllByRole("combobox")[1]);
    fireEvent.click(screen.getByRole("option", { name: "Not equals" }));

    const NAME = SERVER_ROWS.find((r) => r.number === 1).name;
    fireEvent.change(screen.getByPlaceholderText("Enter value"), {
      target: { value: NAME },
    });

    const expected = queryScenarioFixture({
      limit: 25,
      name_not: [NAME],
    }).count;
    expect(expected).toBe(SERVER_ROWS.length - 1);
    expect(
      await screen.findByText(`${expected} of ${SERVER_ROWS.length}`),
    ).toBeInTheDocument();
    expect(listScenarios).toHaveBeenLastCalledWith(
      "job-test",
      expect.objectContaining({ name_not: [NAME] }),
    );
  });

  it("offers the same two operators in the Query tab", async () => {
    await openFilter();
    fireEvent.click(screen.getByRole("tab", { name: "Query" }));

    const input = screen.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "Scenario" } });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(screen.getByRole("combobox")).toHaveAttribute(
        "placeholder",
        "pick operator...",
      ),
    );

    // Every operator label has an "s" in it, so this lists all on offer.
    fireEvent.focus(screen.getByRole("combobox"));
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "s" } });
    await waitFor(() =>
      expect(optionLabels()).toEqual(["Equals", "Not equals"]),
    );
  });
});

describe("ScenariosStep — bulk selection", () => {
  const selectFirstRow = () =>
    fireEvent.click(screen.getAllByRole("checkbox")[1]);

  it("surfaces the SelectionBar and publishes the selection to the bus", async () => {
    renderStep();
    await screen.findByRole("table");
    selectFirstRow();

    expect(screen.getByText("scenario selected")).toBeInTheDocument();
    expect(getScenarioSelection().ids).toEqual([firstRowId()]);
  });

  it("bulk-deletes the selected rows through a confirmed amend drop (no undo)", async () => {
    renderStep();
    await screen.findByRole("table");
    const firstName = defaultView().results[0].name;
    selectFirstRow();

    fireEvent.click(
      screen.getByRole("button", { name: "Delete selected scenarios" }),
    );
    // Confirm-then-drop: the dialog gates the delete, and there is no Undo.
    expect(screen.getByText("Delete scenarios?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(amendScenarios).toHaveBeenCalledTimes(1));
    // include-mode: the drop names the picked row (resolved server-side by name).
    expect(amendScenarios).toHaveBeenCalledWith("job-test", {
      rework: true,
      changes: [{ op: "drop", scenarios: [firstName] }],
    });
    // The selection clears on success and the bus empties.
    await waitFor(() => expect(getScenarioSelection().ids).toEqual([]));
  });

  it("clears the selection and empties the bus", async () => {
    renderStep();
    await screen.findByRole("table");
    selectFirstRow();
    expect(getScenarioSelection().ids).toEqual([firstRowId()]);

    fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(screen.queryByText("scenario selected")).not.toBeInTheDocument();
    expect(getScenarioSelection().ids).toEqual([]);
  });
});

describe("ScenariosStep — locked template", () => {
  const LOCK_TOOLTIP = "Fork this environment to edit.";

  const renderLocked = (locked) => {
    const patch = vi.fn();
    renderWithClient(
      <ScenariosStep
        env={TEST_ENV}
        envState={envStateFor()}
        patch={patch}
        locked={locked}
      />,
    );
    return { patch };
  };

  it("keeps Add scenarios disabled with the fork reason on a locked environment", () => {
    renderLocked(true);
    const add = screen.getByRole("button", { name: "Add scenarios" });
    expect(add).toBeDisabled();
    expect(add).toHaveAccessibleDescription(LOCK_TOOLTIP);
    expect(screen.queryByText(/coming soon/i)).toBeNull();
  });

  it("keeps Add scenarios disabled with the shared line when not locked", () => {
    renderLocked(false);
    const add = screen.getByRole("button", { name: "Add scenarios" });
    expect(add).toBeDisabled();
    expect(add).toHaveAccessibleDescription("Not available in this release");
  });

  it("disables the per-row edit and delete with the fork tooltip", async () => {
    renderLocked(true);
    await screen.findByRole("table");
    expect(
      screen.getAllByRole("button", { name: "Edit scenario" })[0],
    ).toBeDisabled();
    expect(
      screen.getAllByRole("button", {
        name: "Remove from this environment",
      })[0],
    ).toBeDisabled();
    expect(screen.getAllByLabelText(LOCK_TOOLTIP).length).toBeGreaterThan(0);
  });

  it("hides bulk-select — no checkboxes while locked", async () => {
    renderLocked(true);
    await screen.findByRole("table");
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
  });

  it("disables the List-view per-row edit and delete while locked", async () => {
    renderLocked(true);
    await screen.findByRole("table");
    pickView("List");
    expect(
      screen.getAllByRole("button", { name: "Edit scenario" })[0],
    ).toBeDisabled();
    expect(
      screen.getAllByRole("button", { name: "Edit scenario" })[0],
    ).toBeDisabled();
    expect(
      screen.getAllByRole("button", {
        name: "Remove from this environment",
      })[0],
    ).toBeDisabled();
  });

  it("keeps group-by and hide-group available while locked — they're view state, not mutations", async () => {
    renderLocked(true);
    await screen.findByRole("table");
    expect(
      screen.getAllByRole("button", { name: "Hide this group" })[0],
    ).toBeEnabled();
    const panel = openDisplay();
    expect(
      within(panel).getByRole("combobox", { name: "Group by" }),
    ).not.toHaveAttribute("aria-disabled");
  });

  it("leaves the row controls enabled and checkboxes present when not locked", async () => {
    renderLocked(false);
    await screen.findByRole("table");
    expect(
      screen.getAllByRole("button", { name: "Edit scenario" })[0],
    ).toBeEnabled();
    expect(
      screen.getAllByRole("button", {
        name: "Remove from this environment",
      })[0],
    ).toBeEnabled();
    expect(screen.queryAllByRole("checkbox").length).toBeGreaterThan(0);
    expect(screen.queryByLabelText(LOCK_TOOLTIP)).toBeNull();
  });
});

describe("ScenariosStep — group-by axis", () => {
  it("names the No grouping choice in the Group by control once it's picked", async () => {
    renderStep();
    await screen.findByRole("table");
    pickGroupBy("No grouping");

    const panel = openDisplay();
    await waitFor(() =>
      expect(
        within(panel).getByRole("combobox", { name: "Group by" }),
      ).toHaveTextContent("No grouping"),
    );
    closeDisplay();
  });

  it("regroups on a new axis (from the server groupings) and clears any hidden groups", async () => {
    renderStep();
    await screen.findByRole("table");
    // Hide one group so there's a hidden set to clear.
    fireEvent.click(
      screen.getAllByRole("button", { name: "Hide this group" })[0],
    );
    expect(screen.getByText(/· 1 group hidden/)).toBeInTheDocument();

    // Switch the axis to Accent through the Display panel.
    pickGroupBy("Accent");

    const panel = openDisplay();
    await waitFor(() =>
      expect(
        within(panel).getByRole("combobox", { name: "Group by" }),
      ).toHaveTextContent("Accent"),
    );
    closeDisplay();
    // Switching the axis dropped the hidden set — the counter is gone.
    expect(screen.queryByText(/group hidden/)).not.toBeInTheDocument();
  });
});

describe("ScenariosStep — hide group", () => {
  const groupCount = () => defaultView().groups.length;

  it("hides a group from the table and restores it with Show all", async () => {
    renderStep();
    await screen.findByRole("table");
    const n = groupCount();
    expect(
      screen.getAllByRole("button", { name: "Hide this group" }),
    ).toHaveLength(n);

    fireEvent.click(
      screen.getAllByRole("button", { name: "Hide this group" })[0],
    );
    expect(
      screen.getAllByRole("button", { name: "Hide this group" }),
    ).toHaveLength(n - 1);
    expect(screen.getByText(/· 1 group hidden/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(
      screen.getAllByRole("button", { name: "Hide this group" }),
    ).toHaveLength(n);
  });

  it("shows the all-hidden empty state when every group is hidden", async () => {
    renderStep();
    await screen.findByRole("table");
    const n = groupCount();
    // Hiding always removes the first remaining group; n clicks empties it.
    for (let i = 0; i < n; i += 1) {
      fireEvent.click(
        screen.getAllByRole("button", { name: "Hide this group" })[0],
      );
    }
    expect(
      screen.getByText(
        "Every group is hidden. Click Show all to bring them back.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(new RegExp(`· ${n} groups hidden`)),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(
      screen.getAllByRole("button", { name: "Hide this group" }),
    ).toHaveLength(n);
  });

  it("hides a group from the list view", async () => {
    renderStep();
    await screen.findByRole("table");
    const n = groupCount();
    pickView("List");
    expect(
      screen.getAllByRole("button", { name: "Hide this group" }),
    ).toHaveLength(n);

    fireEvent.click(
      screen.getAllByRole("button", { name: "Hide this group" })[0],
    );
    expect(
      screen.getAllByRole("button", { name: "Hide this group" }),
    ).toHaveLength(n - 1);
    expect(screen.getByText(/· 1 group hidden/)).toBeInTheDocument();
  });
});

// The client grouping helpers are unchanged and still unit-tested directly.
const env = MOCK_WORLD;
const pooled = generatedPool(env).slice(0, 12);

describe("groupScenarios", () => {
  it("buckets rows by their use case, largest group first", () => {
    const groups = groupScenarios(pooled);
    expect(groups).toHaveLength(4);
    groups.forEach((g) => expect(g.rows).toHaveLength(3));
  });

  it("buckets by persona with a lowercased, prefixed id", () => {
    const groups = groupScenarios(pooled, "persona", env);
    groups.forEach((g) => {
      expect(g.id).toMatch(/^persona:/);
      expect(g.id).toBe(g.id.toLowerCase());
    });
    expect(groups.reduce((n, g) => n + g.rows.length, 0)).toBe(pooled.length);
  });

  it("buckets by sub-goal with a slugged, prefixed id", () => {
    const groups = groupScenarios(pooled, "subgoal", env);
    groups.forEach((g) => expect(g.id).toMatch(/^subgoal:[a-z0-9-]+$/));
    expect(groups.reduce((n, g) => n + g.rows.length, 0)).toBe(pooled.length);
  });
});

describe("groupKeyOf fallbacks", () => {
  it("labels a persona-less row 'No persona'", () => {
    expect(groupKeyOf({ id: "x" }, "persona")).toEqual({
      id: "persona:none",
      label: "No persona",
    });
  });

  it("labels a sub-goal-less row 'No sub-goals'", () => {
    expect(groupKeyOf(null, "subgoal")).toEqual({
      id: "subgoal:none",
      label: "No sub-goals",
    });
  });
});
