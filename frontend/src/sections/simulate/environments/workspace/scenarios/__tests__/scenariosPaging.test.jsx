import { describe, it, expect, vi, beforeEach, afterAll } from "vitest";
import { configure } from "@testing-library/react";
import { screen, fireEvent, waitFor } from "src/utils/test-utils";

// Heavy ScenariosStep render — give async utils headroom so page-range/count
// findByText assertions stay load-stable under full-suite parallelism.
configure({ asyncUtilTimeout: 5000 });
afterAll(() => configure({ asyncUtilTimeout: 1000 }));

import ScenariosStep from "../ScenariosStep";
import { PAGE_SIZE } from "../useScenarioPage";
import {
  TEST_ENV,
  makeServerRows,
  envStateFor,
  renderWithClient,
} from "./scenariosTestUtils";

const { mockSnack } = vi.hoisted(() => ({ mockSnack: { calls: [], close: () => {} } }));
vi.mock("notistack", () => ({
  useSnackbar: () => ({
    enqueueSnackbar: (message, options) => { mockSnack.calls.push({ message, options }); return "snack-key"; },
    closeSnackbar: (...args) => mockSnack.close(...args),
  }),
}));

// The list reads the server (fixtures) source through listScenarios; serve a
// controlled 60-row suite so the pager engages at PAGE_SIZE 25.
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

// 60 rows (> two pages at PAGE_SIZE 25). envState is seeded from the SAME rows
// so the selection predicate and the bulk-delete patch line up by id.
const PAGING_ROWS = makeServerRows(60);

const renderStep = (scenarios = PAGING_ROWS) => {
  const patch = vi.fn();
  const onStartRun = vi.fn();
  renderWithClient(
    <ScenariosStep
      env={TEST_ENV}
      envState={envStateFor(scenarios)}
      patch={patch}
      canRun
      onStartRun={onStartRun}
    />,
  );
  return { patch, onStartRun };
};

const headerCheckbox = () => screen.getAllByRole("checkbox")[0];

beforeEach(() => {
  mockSnack.calls = [];
  resetScenarioFixture();
  listScenarios.mockReset();
  listScenarios.mockImplementation((jobId, params) =>
    queryScenarioFixture(params, PAGING_ROWS),
  );
  amendScenarios.mockReset();
  amendScenarios.mockResolvedValue({ receipts: [] });
});

describe("ScenariosStep — pagination", () => {
  it("shows one page of rows with a pager and range", async () => {
    renderStep();
    expect(await screen.findByText(/Showing 1–25 of 60/)).toBeInTheDocument();
    // header + PAGE_SIZE row checkboxes on the page
    expect(screen.getAllByRole("checkbox")).toHaveLength(PAGE_SIZE + 1);
    expect(screen.getByLabelText("Go to page 2")).toBeInTheDocument();
  });

  it("advances to the next page and shifts the range", async () => {
    renderStep();
    fireEvent.click(await screen.findByLabelText("Go to page 2"));
    expect(await screen.findByText(/Showing 26–50 of 60/)).toBeInTheDocument();
  });

  it("shows an error state, not the empty placeholder, when the list request fails", async () => {
    listScenarios.mockRejectedValue(new Error("boom"));
    renderStep();
    expect(await screen.findByText(/Couldn't load scenarios/i)).toBeInTheDocument();
    // Not the "no scenarios yet" empty placeholder.
    expect(screen.queryByText(/no scenarios/i)).toBeNull();
  });

  it("debounces the search and resets to the first page in the handler", async () => {
    renderStep();
    fireEvent.click(await screen.findByLabelText("Go to page 2"));
    expect(await screen.findByText(/Showing 26–50 of 60/)).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/Search scenarios/i), {
      target: { value: "ride" },
    });

    // After the debounce, the fetch is for page 1 (reset) with the typed term —
    // proving the reset happens in the change handler, not a tick-late effect.
    await waitFor(() => {
      const last = listScenarios.mock.calls.at(-1);
      expect(last[1]).toMatchObject({ page: 1, search: "ride" });
    });
  });

  it("clamps back onto the last real page after a delete empties the current one", async () => {
    // Serve 60 rows (3 pages) until a delete shrinks the suite to 40 (2 pages).
    let rowCount = 60;
    listScenarios.mockImplementation((jobId, params) =>
      queryScenarioFixture(params, makeServerRows(rowCount)),
    );
    amendScenarios.mockImplementation(async () => {
      rowCount = 40;
      return { receipts: [] };
    });
    renderStep(makeServerRows(60));

    fireEvent.click(await screen.findByLabelText("Go to page 3"));
    expect(await screen.findByText(/Showing 51–60 of 60/)).toBeInTheDocument();

    // Delete a row on the (now non-existent after shrink) last page.
    fireEvent.click(
      screen.getAllByRole("button", { name: "Remove from this environment" })[0],
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    // The suite is now 40 rows (2 pages); page 3 is gone, so it clamps to page 2
    // rather than requesting a 404'd page 3 forever.
    expect(await screen.findByText(/Showing 26–40 of 40/)).toBeInTheDocument();
  });
});

describe("ScenariosStep — select all matching", () => {
  it("header selects only the page, then the banner escalates to the whole match", async () => {
    renderStep();
    await screen.findByText(/Showing 1–25 of 60/);
    fireEvent.click(headerCheckbox());

    // Page selected → the banner offers the escalation to every match.
    expect(screen.getByRole("button", { name: /Select all 60 matching/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Select all 60 matching/ }));
    expect(screen.getByText(/All 60 matching scenarios selected/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Select all 60 matching/ })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Clear selection" })).toHaveLength(1);
  });

  it("keeps the whole-match selection across a page change without loading every row", async () => {
    renderStep();
    await screen.findByText(/Showing 1–25 of 60/);
    fireEvent.click(headerCheckbox());
    fireEvent.click(screen.getByRole("button", { name: /Select all 60 matching/ }));

    fireEvent.click(screen.getByLabelText("Go to page 2"));
    expect(await screen.findByText(/Showing 26–50 of 60/)).toBeInTheDocument();
    // Every checkbox on the new page is checked — the predicate covers rows the
    // client never loaded on page 1.
    screen.getAllByRole("checkbox").forEach((c) => expect(c).toBeChecked());
  });

  it("bulk-deletes the whole match by enumerating names, then one amend drop", async () => {
    renderStep();
    await screen.findByText(/Showing 1–25 of 60/);
    fireEvent.click(headerCheckbox());
    fireEvent.click(screen.getByRole("button", { name: /Select all 60 matching/ }));
    fireEvent.click(screen.getByRole("button", { name: "Delete selected scenarios" }));

    // Confirm, then all 60 matches are enumerated (paged server-side) and dropped
    // in a single amend naming every one.
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(amendScenarios).toHaveBeenCalledTimes(1));
    const body = amendScenarios.mock.calls[0][1];
    expect(body.changes).toHaveLength(1);
    expect(body.changes[0].op).toBe("drop");
    expect(body.changes[0].scenarios).toHaveLength(60);
  });

  it("un-checking a row in all-mode drops one from the count (an exclusion)", async () => {
    renderStep();
    await screen.findByText(/Showing 1–25 of 60/);
    fireEvent.click(headerCheckbox());
    fireEvent.click(screen.getByRole("button", { name: /Select all 60 matching/ }));
    // Uncheck the first row checkbox (index 1; 0 is the header).
    fireEvent.click(screen.getAllByRole("checkbox")[1]);
    expect(screen.getByText(/All 59 matching scenarios selected/)).toBeInTheDocument();
  });
});

describe("ScenariosStep — run a selection", () => {
  const runButton = () => screen.getByRole("button", { name: /scenarios? × \d+ repeat/ });

  it("runs the ticked rows by scenario key, not row id", async () => {
    const { onStartRun } = renderStep();
    await screen.findByText(/Showing 1–25 of 60/);
    fireEvent.click(screen.getAllByRole("checkbox")[1]);
    fireEvent.click(screen.getAllByRole("checkbox")[2]);
    fireEvent.click(runButton());

    await waitFor(() => expect(onStartRun).toHaveBeenCalledTimes(1));
    const [keys, trials] = onStartRun.mock.calls[0];
    // Two scenario keys (the table's default grouping decides which rows lead),
    // never the row ids (`srv-N`) the selection holds.
    expect(keys).toHaveLength(2);
    keys.forEach((k) => expect(k).toMatch(/^key-\d+$/));
    expect(trials).toBe(1);
  });

  it("runs every match minus the un-ticked rows, by key", async () => {
    const { onStartRun } = renderStep();
    await screen.findByText(/Showing 1–25 of 60/);
    fireEvent.click(headerCheckbox());
    fireEvent.click(screen.getByRole("button", { name: /Select all 60 matching/ }));
    fireEvent.click(screen.getAllByRole("checkbox")[1]);
    fireEvent.click(runButton());

    await waitFor(() => expect(onStartRun).toHaveBeenCalledTimes(1));
    const [keys] = onStartRun.mock.calls[0];
    expect(keys).toHaveLength(59);
    expect(new Set(keys).size).toBe(59);
    keys.forEach((k) => expect(k).toMatch(/^key-\d+$/));
    // Exactly the one un-ticked scenario is left out.
    const missing = PAGING_ROWS.map((r) => r.scenario_key).filter((k) => !keys.includes(k));
    expect(missing).toHaveLength(1);
  });

  it("runs only the filtered match in all-mode, not the whole suite", async () => {
    const term = PAGING_ROWS[0].name.slice(0, 10);
    const expected = queryScenarioFixture(
      { search: term, group_by: "", limit: 1000 },
      PAGING_ROWS,
    ).results.map((r) => r.scenario_key);
    expect(expected.length).toBeGreaterThan(0);
    expect(expected.length).toBeLessThan(60);

    const { onStartRun } = renderStep();
    await screen.findByText(/Showing 1–25 of 60/);
    fireEvent.change(screen.getByPlaceholderText(/Search scenarios/i), {
      target: { value: term },
    });
    await screen.findByText(new RegExp(`of ${expected.length}\\b`));
    fireEvent.click(headerCheckbox());
    const escalate = screen.queryByRole("button", { name: /Select all \d+ matching/ });
    if (escalate) fireEvent.click(escalate);
    fireEvent.click(runButton());

    await waitFor(() => expect(onStartRun).toHaveBeenCalledTimes(1));
    expect([...onStartRun.mock.calls[0][0]].sort()).toEqual([...expected].sort());
  });
});

describe("ScenariosStep — background refetch failure", () => {
  it("keeps the loaded rows and offers Retry when a later fetch fails", async () => {
    renderStep();
    await screen.findByText(/Showing 1–25 of 60/);
    listScenarios.mockRejectedValue(new Error("boom"));
    fireEvent.click(screen.getByLabelText("Go to page 2"));

    expect(await screen.findByText(/Couldn't refresh the scenarios/)).toBeInTheDocument();
    expect(screen.queryByText(/Couldn't load scenarios/)).toBeNull();

    listScenarios.mockImplementation((jobId, params) => queryScenarioFixture(params, PAGING_ROWS));
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.queryByText(/Couldn't refresh the scenarios/)).toBeNull());
  });
});

describe("ScenariosStep — list view has checkboxes", () => {
  it("renders per-row and per-group checkboxes in the list view", async () => {
    renderStep();
    await screen.findByText(/Showing 1–25 of 60/);
    fireEvent.click(screen.getByRole("tab", { name: "List" }));
    await waitFor(() =>
      expect(screen.getAllByLabelText(/^Select group/).length).toBeGreaterThan(0),
    );
    const rowBoxes = screen.getAllByLabelText(/^Select (?!group)/);
    expect(rowBoxes.length).toBeGreaterThan(0);
  });
});
