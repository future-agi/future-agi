import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "src/utils/test-utils";

import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import { generatedPool } from "src/api/simulate-environments/_fixtures/scenarioPool";
import ScenariosStep from "../ScenariosStep";
import AddScenariosDrawer from "../AddScenariosDrawer";
import {
  TEST_ENV,
  SERVER_ROWS,
  envStateFor,
  renderWithClient,
} from "./scenariosTestUtils";

// The list reads the server (fixtures) source.
vi.mock("src/api/simulate-environments/scenarios", async () => {
  const actual = await vi.importActual("src/api/simulate-environments/scenarios");
  return { ...actual, listScenarios: vi.fn(), amendScenarios: vi.fn() };
});
const { listScenarios, amendScenarios } = await import("src/api/simulate-environments/scenarios");
const { queryScenarioFixture, resetScenarioFixture } = await import(
  "src/api/simulate-environments/_fixtures/scenariosFixtures"
);

const env = MOCK_WORLD;
// The Add drawer offers scenarios not already on the environment; it is rendered
// directly (the trigger is disabled), so it keeps its own pooled candidates.
const pooled = generatedPool(env).slice(0, 12);

// The first displayed row (default goal-grouped view) — the one the edit pencil
// opens.
const firstRow = () => queryScenarioFixture({ limit: 25 }).results[0];

const renderStep = () => {
  const patch = vi.fn();
  renderWithClient(
    <ScenariosStep env={TEST_ENV} envState={envStateFor()} patch={patch} />,
  );
  return { patch };
};

beforeEach(() => {
  resetScenarioFixture();
  listScenarios.mockReset();
  listScenarios.mockImplementation((jobId, params) =>
    queryScenarioFixture(params, SERVER_ROWS),
  );
  amendScenarios.mockReset();
  amendScenarios.mockResolvedValue({ receipts: [] });
});

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
      <AddScenariosDrawer open env={env} selected={pooled} onAdd={onAdd} onClose={() => {}} />,
    );

    expect(screen.getByText(/could not know to write/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox", { name: /select all/i }));
    fireEvent.click(screen.getByRole("button", { name: /^Add \d+ scenarios?$/ }));

    expect(onAdd).toHaveBeenCalledTimes(1);
    const added = onAdd.mock.calls[0][0];
    expect(added.length).toBeGreaterThan(0);
  });
});

describe("ScenariosStep — edit", () => {
  it("enables the per-row edit pencil", async () => {
    renderStep();
    await screen.findByRole("table");
    const pencils = screen.getAllByRole("button", { name: "Edit scenario" });
    expect(pencils[0]).toBeEnabled();
  });

  it("opens the editor prefilled, gates read-only fields, and amends the changed editable field", async () => {
    renderStep();
    await screen.findByRole("table");
    const row = firstRow();
    fireEvent.click(screen.getAllByRole("button", { name: "Edit scenario" })[0]);

    // Name is proved, not described — it prefills but is read-only (not in the
    // server's editable_fields).
    const nameField = screen.getAllByLabelText("Name", { selector: "input" })[0];
    expect(nameField).toHaveValue(row.name);
    expect(nameField).toBeDisabled();

    // "Passes when" (tests) IS editable; the save enables only after a change.
    const passesWhen = screen.getByLabelText("Passes when");
    expect(passesWhen).toBeEnabled();
    expect(passesWhen).toHaveValue(row.tests);

    const saveBtn = screen.getByRole("button", { name: "Save scenario" });
    expect(saveBtn).toBeDisabled();

    fireEvent.change(passesWhen, { target: { value: "Passes when it books the ride." } });
    expect(saveBtn).toBeEnabled();
    fireEvent.click(saveBtn);

    await waitFor(() => expect(amendScenarios).toHaveBeenCalledTimes(1));
    // tests is descriptive, so the amend does not force a re-proof.
    expect(amendScenarios).toHaveBeenCalledWith("job-test", {
      rework: false,
      changes: [
        { op: "set_field", scenario: row.name, field: "tests", value: "Passes when it books the ride." },
      ],
    });
  });
});
