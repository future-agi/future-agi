import { describe, it, expect, vi, beforeEach, afterAll } from "vitest";
import { configure } from "@testing-library/react";
import { screen, fireEvent, waitFor } from "src/utils/test-utils";

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
import { filterParamKey } from "../scenarioEditor.constants";
import {
  TEST_ENV,
  SERVER_ROWS,
  envStateFor,
  renderWithClient,
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
  const actual = await vi.importActual("src/api/simulate-environments/scenarios");
  return {
    ...actual,
    listScenarios: vi.fn(),
    amendScenarios: vi.fn(),
    // Mock coverage too so CoverageMatrix does not fire a real (failing) request
    // that spams the axios auth-redirect interceptor on every render.
    scenarioCoverage: vi.fn(async () => ({ axes: [], per_axis: [], rows: [], columns: [], cells: [] })),
  };
});
const { listScenarios, amendScenarios } = await import("src/api/simulate-environments/scenarios");
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
  it("renders the scenario count from envState", () => {
    renderStep();
    expect(screen.getByText(`(${SERVER_ROWS.length})`)).toBeInTheDocument();
  });

  it("narrows the list on server search, shows N of M, and clears", async () => {
    renderStep();
    await screen.findByRole("table");
    const search = screen.getByPlaceholderText(/Search scenarios/i);
    fireEvent.change(search, { target: { value: "injection" } });

    const expected = queryScenarioFixture({ limit: 25, search: "injection" }).count;
    expect(expected).toBeGreaterThan(0);
    expect(await screen.findByText(`${expected} of ${SERVER_ROWS.length}`)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.queryByText(`${expected} of ${SERVER_ROWS.length}`)).not.toBeInTheDocument();
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
    expect(await screen.findByText(`${expected} of ${SERVER_ROWS.length}`)).toBeInTheDocument();
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

    const expected = queryScenarioFixture({ limit: 25, use_case_not: [UC] }).count;
    expect(await screen.findByText(`${expected} of ${SERVER_ROWS.length}`)).toBeInTheDocument();
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

    fireEvent.click(screen.getByRole("tab", { name: "List" }));
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

  it("shows the empty placeholder with a disabled (coming-soon) Add when there are no scenarios", async () => {
    // The empty-state gate reads the server's unfiltered suite total now, so the
    // list source must report an empty suite (not just an empty envState).
    listScenarios.mockImplementation((jobId, params) => queryScenarioFixture(params, []));
    renderStep([]);
    expect(await screen.findByText("No scenarios yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add scenarios" })).toBeDisabled();
  });

  it("enables the edit pencil", async () => {
    renderStep();
    await screen.findByRole("table");
    const pencils = screen.getAllByRole("button", { name: "Edit scenario" });
    expect(pencils[0]).toBeEnabled();
  });
});

describe("ScenariosStep — bulk selection", () => {
  const selectFirstRow = () => fireEvent.click(screen.getAllByRole("checkbox")[1]);

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

    fireEvent.click(screen.getByRole("button", { name: "Delete selected scenarios" }));
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
      <ScenariosStep env={TEST_ENV} envState={envStateFor()} patch={patch} locked={locked} />,
    );
    return { patch };
  };

  it("locks the Add scenarios CTA behind the fork tooltip", () => {
    renderLocked(true);
    expect(screen.getByRole("button", { name: "Add scenarios" })).toBeDisabled();
    expect(screen.getAllByLabelText(LOCK_TOOLTIP).length).toBeGreaterThan(0);
  });

  it("disables the per-row edit and delete with the fork tooltip", async () => {
    renderLocked(true);
    await screen.findByRole("table");
    expect(screen.getAllByRole("button", { name: "Edit scenario" })[0]).toBeDisabled();
    expect(
      screen.getAllByRole("button", { name: "Remove from this environment" })[0],
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
    fireEvent.click(screen.getByRole("tab", { name: "List" }));
    expect(screen.getAllByRole("button", { name: "Edit scenario" })[0]).toBeDisabled();
    expect(
      screen.getAllByRole("button", { name: "Remove from this environment" })[0],
    ).toBeDisabled();
  });

  it("keeps group-by and hide-group available while locked — they're view state, not mutations", async () => {
    renderLocked(true);
    await screen.findByRole("table");
    expect(screen.getByRole("button", { name: /^Group by/ })).toBeEnabled();
    expect(screen.getAllByRole("button", { name: "Hide this group" })[0]).toBeEnabled();
  });

  it("leaves the row controls enabled and checkboxes present when not locked", async () => {
    renderLocked(false);
    await screen.findByRole("table");
    expect(screen.getAllByRole("button", { name: "Edit scenario" })[0]).toBeEnabled();
    expect(screen.getAllByRole("button", { name: "Remove from this environment" })[0]).toBeEnabled();
    expect(screen.queryAllByRole("checkbox").length).toBeGreaterThan(0);
    expect(screen.queryByLabelText(LOCK_TOOLTIP)).toBeNull();
  });
});

describe("ScenariosStep — group-by axis", () => {
  it("regroups on a new axis (from the server groupings) and clears any hidden groups", async () => {
    renderStep();
    await screen.findByRole("table");
    // Hide one group so there's a hidden set to clear.
    fireEvent.click(screen.getAllByRole("button", { name: "Hide this group" })[0]);
    expect(screen.getByText(/· 1 group hidden/)).toBeInTheDocument();

    // Switch the axis to Accent through the popover.
    fireEvent.click(screen.getByRole("button", { name: /^Group by/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Accent" }));

    expect(await screen.findByRole("button", { name: /Group by · Accent/ })).toBeInTheDocument();
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
    expect(screen.getAllByRole("button", { name: "Hide this group" })).toHaveLength(n);

    fireEvent.click(screen.getAllByRole("button", { name: "Hide this group" })[0]);
    expect(screen.getAllByRole("button", { name: "Hide this group" })).toHaveLength(n - 1);
    expect(screen.getByText(/· 1 group hidden/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(screen.getAllByRole("button", { name: "Hide this group" })).toHaveLength(n);
  });

  it("shows the all-hidden empty state when every group is hidden", async () => {
    renderStep();
    await screen.findByRole("table");
    const n = groupCount();
    // Hiding always removes the first remaining group; n clicks empties it.
    for (let i = 0; i < n; i += 1) {
      fireEvent.click(screen.getAllByRole("button", { name: "Hide this group" })[0]);
    }
    expect(
      screen.getByText("Every group is hidden — click Show all to bring them back."),
    ).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`· ${n} groups hidden`))).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(screen.getAllByRole("button", { name: "Hide this group" })).toHaveLength(n);
  });

  it("hides a group from the list view", async () => {
    renderStep();
    await screen.findByRole("table");
    const n = groupCount();
    fireEvent.click(screen.getByRole("tab", { name: "List" }));
    expect(screen.getAllByRole("button", { name: "Hide this group" })).toHaveLength(n);

    fireEvent.click(screen.getAllByRole("button", { name: "Hide this group" })[0]);
    expect(screen.getAllByRole("button", { name: "Hide this group" })).toHaveLength(n - 1);
    expect(screen.getByText(/· 1 group hidden/)).toBeInTheDocument();
  });
});

// The client grouping helpers are unchanged and still unit-tested directly.
const env = MOCK_WORLD;
const pooled = generatedPool(env).slice(0, 12);

describe("groupScenarios", () => {
  it("buckets rows by their use case, largest group first", async () => {
    const { groupScenarios } = await import("../scenarios.constants");
    const groups = groupScenarios(pooled);
    expect(groups).toHaveLength(4);
    groups.forEach((g) => expect(g.rows).toHaveLength(3));
  });

  it("buckets by persona with a lowercased, prefixed id", async () => {
    const { groupScenarios } = await import("../scenarios.constants");
    const groups = groupScenarios(pooled, "persona", env);
    groups.forEach((g) => {
      expect(g.id).toMatch(/^persona:/);
      expect(g.id).toBe(g.id.toLowerCase());
    });
    expect(groups.reduce((n, g) => n + g.rows.length, 0)).toBe(pooled.length);
  });

  it("buckets by sub-goal with a slugged, prefixed id", async () => {
    const { groupScenarios } = await import("../scenarios.constants");
    const groups = groupScenarios(pooled, "subgoal", env);
    groups.forEach((g) => expect(g.id).toMatch(/^subgoal:[a-z0-9-]+$/));
    expect(groups.reduce((n, g) => n + g.rows.length, 0)).toBe(pooled.length);
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

describe("filterParamKey", () => {
  it("carries the operator as the suffix the server reads", () => {
    expect(filterParamKey("name", "contains")).toBe("name_contains");
    expect(filterParamKey("name", "not_contains")).toBe("name_not_contains");
    expect(filterParamKey("use_case", "is_not")).toBe("use_case_not");
    expect(filterParamKey("name", "not_equals")).toBe("name_not");
    expect(filterParamKey("use_case", "is")).toBe("use_case");
    expect(filterParamKey("name", "equals")).toBe("name");
  });
});
