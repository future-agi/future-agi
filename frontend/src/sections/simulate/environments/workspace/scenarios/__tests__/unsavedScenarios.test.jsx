import { describe, it, expect, vi, beforeEach, afterAll } from "vitest";
import { configure } from "@testing-library/react";
import { screen, fireEvent, waitFor, within } from "src/utils/test-utils";

// Heavy ScenariosStep render — give async utils headroom under full-suite load.
configure({ asyncUtilTimeout: 5000 });
afterAll(() => configure({ asyncUtilTimeout: 1000 }));

import ScenariosStep from "../ScenariosStep";
import { DRAFTS_COPY } from "../../workspace.constants";
import {
  TEST_ENV,
  makeServerRows,
  envStateFor,
  renderWithClient,
  pickView,
} from "./scenariosTestUtils";

const { mockSnack } = vi.hoisted(() => ({ mockSnack: { calls: [] } }));
vi.mock("notistack", () => ({
  useSnackbar: () => ({
    enqueueSnackbar: (message, options) => {
      mockSnack.calls.push({ message, options });
      return "snack-key";
    },
    closeSnackbar: () => {},
  }),
}));

vi.mock("src/api/simulate-environments/scenarios", async () => {
  const actual = await vi.importActual(
    "src/api/simulate-environments/scenarios",
  );
  return {
    ...actual,
    listScenarios: vi.fn(),
    amendScenarios: vi.fn(),
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

// The list while scenarios have unsaved changes: one row added, one changed,
// the rest as saved.
const CHANGES = { 0: "added", 1: "changed" };
const draftRows = (n) =>
  makeServerRows(n).map((row, i) => ({
    ...row,
    draft_change: CHANGES[i] || "unchanged",
  }));
const ADDED_NAME = makeServerRows(1)[0].name;
const CHANGED_NAME = makeServerRows(2)[1].name;

const renderStep = (rows) => {
  listScenarios.mockImplementation((jobId, params) =>
    queryScenarioFixture(params, rows),
  );
  const onStartRun = vi.fn();
  renderWithClient(
    <ScenariosStep
      env={TEST_ENV}
      envState={envStateFor(rows)}
      patch={vi.fn()}
      canRun
      onStartRun={onStartRun}
    />,
  );
  return { onStartRun };
};

const rowOf = (name) => screen.getByText(name, { selector: "p" }).closest("tr");

beforeEach(() => {
  mockSnack.calls = [];
  resetScenarioFixture();
  listScenarios.mockReset();
  amendScenarios.mockReset();
  amendScenarios.mockResolvedValue({ receipts: [] });
});

describe("ScenariosStep — unsaved changes", () => {
  it("marks the added and changed rows Unsaved, and only those", async () => {
    renderStep(draftRows(4));
    await screen.findByRole("table");
    expect(
      within(rowOf(ADDED_NAME)).getByText(DRAFTS_COPY.unsaved),
    ).toBeInTheDocument();
    expect(
      within(rowOf(CHANGED_NAME)).getByText(DRAFTS_COPY.unsaved),
    ).toBeInTheDocument();
    expect(screen.getAllByText(DRAFTS_COPY.unsaved)).toHaveLength(2);
  });

  it("keeps an added row out of a selection, saying why in view", async () => {
    renderStep(draftRows(4));
    await screen.findByRole("table");
    const added = within(rowOf(ADDED_NAME)).getByRole("checkbox");
    expect(added).toBeDisabled();
    expect(added).toHaveAccessibleDescription(DRAFTS_COPY.saveToRun);
    expect(
      within(rowOf(ADDED_NAME)).getByText(DRAFTS_COPY.saveToRun),
    ).toBeVisible();
    expect(within(rowOf(CHANGED_NAME)).getByRole("checkbox")).toBeEnabled();

    // The page's header checkbox picks every row but the added one.
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    expect(added).not.toBeChecked();
    expect(within(rowOf(CHANGED_NAME)).getByRole("checkbox")).toBeChecked();
    expect(
      screen.getByText("scenarios selected").previousElementSibling,
    ).toHaveTextContent(/^3$/);
  });

  it("keeps an added row out of the list view's selection too", async () => {
    renderStep(draftRows(4));
    await screen.findByRole("table");
    pickView("List");
    await waitFor(() => expect(screen.queryByRole("table")).toBeNull());
    const disabled = screen
      .getAllByRole("checkbox")
      .filter((box) => box.disabled);
    expect(disabled).toHaveLength(1);
    expect(disabled[0]).toHaveAccessibleDescription(DRAFTS_COPY.saveToRun);
    expect(screen.getByText(DRAFTS_COPY.saveToRun)).toBeVisible();
    expect(screen.getAllByText(DRAFTS_COPY.unsaved)).toHaveLength(2);
  });

  it("runs every match but the added rows when all matching are selected", async () => {
    const rows = draftRows(30);
    const { onStartRun } = renderStep(rows);
    await screen.findByText(/Showing 1–25 of 30/);
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    fireEvent.click(
      screen.getByRole("button", { name: /Select all 30 matching/ }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: /scenarios? × \d+ repeat/ }),
    );

    await waitFor(() => expect(onStartRun).toHaveBeenCalledTimes(1));
    const [keys] = onStartRun.mock.calls[0];
    expect(keys).toHaveLength(29);
    expect(keys).not.toContain("key-0");
  });

  it("says to save first when the selection holds only added rows", async () => {
    const rows = makeServerRows(30).map((row, i) => ({
      ...row,
      draft_change: i === 0 ? "unchanged" : "added",
    }));
    const { onStartRun } = renderStep(rows);
    await screen.findByText(/Showing 1–25 of 30/);
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    fireEvent.click(
      screen.getByRole("button", { name: /Select all 30 matching/ }),
    );
    // Only the added rows stay selected.
    const saved = screen
      .getAllByRole("checkbox")
      .slice(1)
      .find((box) => !box.disabled);
    fireEvent.click(saved);
    fireEvent.click(
      screen.getByRole("button", { name: /scenarios? × \d+ repeat/ }),
    );

    await waitFor(() => expect(mockSnack.calls).toHaveLength(1));
    expect(mockSnack.calls[0].message).toBe(DRAFTS_COPY.saveToRunSelected);
    expect(onStartRun).not.toHaveBeenCalled();
  });

  it("says an edit was added to the unsaved changes", async () => {
    amendScenarios.mockResolvedValue({
      receipts: [],
      draft: { id: "sd", revision: 2 },
    });
    renderStep(draftRows(4));
    await screen.findByRole("table");
    // The row's remove sits in its More actions menu.
    fireEvent.click(
      within(rowOf(CHANGED_NAME)).getByRole("button", {
        name: /^More actions for /,
      }),
    );
    fireEvent.click(screen.getByRole("menuitem", { name: "Remove" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(mockSnack.calls).toHaveLength(1));
    expect(mockSnack.calls[0].message).toBe(DRAFTS_COPY.addedToDraft);
  });

  it("keeps its own words when the edit left nothing unsaved", async () => {
    amendScenarios.mockResolvedValue({ receipts: [], draft: null });
    renderStep(draftRows(4));
    await screen.findByRole("table");
    // The row's remove sits in its More actions menu.
    fireEvent.click(
      within(rowOf(CHANGED_NAME)).getByRole("button", {
        name: /^More actions for /,
      }),
    );
    fireEvent.click(screen.getByRole("menuitem", { name: "Remove" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(mockSnack.calls).toHaveLength(1));
    expect(mockSnack.calls[0].message).toBe("Deleted 1 scenario");
  });
});
