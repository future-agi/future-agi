import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  fireEvent,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import {
  CALLS,
  ENV,
  callRow,
  callsPage,
  environmentResult,
  fetchCallsFrom,
  runsResult,
} from "./compareFixtures";
import { renderComparePage } from "./renderComparePage";
import { useComparison } from "../useComparison";

const useEnvironment = vi.fn();
vi.mock(
  "src/api/simulate-environments/environment",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useEnvironment: (...args) => useEnvironment(...args),
  }),
);

const useEnvironmentRuns = vi.fn();
vi.mock("src/api/simulate-environments/runs", async (importOriginal) => ({
  ...(await importOriginal()),
  useEnvironmentRuns: (...args) => useEnvironmentRuns(...args),
}));

const fetchRunCalls = vi.fn();
vi.mock("src/api/simulate-environments/runCalls", async (importOriginal) => ({
  ...(await importOriginal()),
  fetchRunCalls: (...args) => fetchRunCalls(...args),
}));

const enqueueSnackbar = vi.fn();
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...args) => enqueueSnackbar(...args),
}));

let setItem;
beforeEach(() => {
  setItem = vi.spyOn(Storage.prototype, "setItem");
  useEnvironment.mockReturnValue(environmentResult());
  useEnvironmentRuns.mockReturnValue(runsResult());
  fetchRunCalls.mockImplementation(fetchCallsFrom());
});

afterEach(() => {
  // Nothing about the comparison is written to the browser (Iconify caches
  // its icons there on its own).
  expect(
    setItem.mock.calls.filter(([key]) => !String(key).startsWith("iconify")),
  ).toEqual([]);
  setItem.mockRestore();
  vi.clearAllMocks();
});

function renderComparison(runIds) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderHook(() => useComparison(ENV.id, runIds), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
}

// The scenario rows of the table, by their title cell.
const rowTitles = () =>
  screen
    .getAllByTestId("compare-row")
    .map((row) => within(row).getByTestId("compare-row-title").textContent);

async function openPage(search) {
  const page = renderComparePage(search);
  await screen.findAllByTestId("compare-row");
  return page;
}

describe("useComparison", () => {
  it("rows match calls across runs by source_scenario_key and trial", async () => {
    fetchRunCalls.mockImplementation(
      fetchCallsFrom({
        ...CALLS,
        // An untagged call still pairs, on its scenario name.
        r1: [
          ...CALLS.r1,
          callRow("c1-loose", null, "passed", { scenario: "Loose call" }),
        ],
        r3: [
          ...CALLS.r3,
          callRow("c3-loose", null, "failed", { scenario: "Loose call" }),
        ],
      }),
    );
    const { result } = renderComparison(["r1", "r3"]);
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    const { rows } = result.current;
    expect(rows.map((r) => r.scenarioKey).sort()).toEqual([
      "Loose call",
      "address",
      "cancel",
      "greet",
      "refund",
    ]);

    const refund = rows.find((r) => r.scenarioKey === "refund");
    expect(refund.cells.r1.map((c) => c.id)).toEqual(["c1-refund"]);
    // Trials line up by their index, whatever order the API sent them in.
    expect(refund.cells.r3.map((c) => [c.id, c.trialIndex])).toEqual([
      ["c3-refund-1", 1],
      ["c3-refund-2", 2],
    ]);
    expect(refund.cells.r3[0]).toMatchObject({
      status: "passed",
      durationMs: 20000,
      tokens: 1000,
      csat: 4,
      turns: 8,
      latencyMs: 900,
    });
    expect(refund.cells.r3[0].evalResults[0]).toMatchObject({
      id: "ev-task",
      score: 0.9,
      passed: true,
    });

    const cancel = rows.find((r) => r.scenarioKey === "cancel");
    expect(cancel.cells.r1).toEqual([]);
    expect(cancel.cells.r3).toHaveLength(1);

    expect(result.current.flips).toEqual({ fixed: 1, regressed: 2 });
    expect(result.current.capped).toBe(false);
  });
});

describe("Compare table", () => {
  it('fixed and regressed chips; "N of M ended differently"', async () => {
    await openPage("?runs=r1,r3");
    expect(screen.getByText("1 fixed")).toBeInTheDocument();
    expect(screen.getByText("1 regressed")).toBeInTheDocument();
    expect(
      screen.getByText(/2 of 4 ended differently · 1 passed → failed/),
    ).toBeInTheDocument();
    // Regressions lead the table.
    expect(rowTitles()[0]).toBe("address");
  });

  it("quick filters passed→failed, failed→passed, unchanged", async () => {
    await openPage("?runs=r1,r3");
    fireEvent.click(screen.getByRole("button", { name: "Display" }));

    const toggle = (name) =>
      fireEvent.click(screen.getByRole("checkbox", { name }));

    toggle("Passed → failed");
    expect(rowTitles()).toEqual(["address"]);
    toggle("Passed → failed");

    toggle("Failed → passed");
    expect(rowTitles()).toEqual(["refund"]);
    toggle("Failed → passed");

    toggle("Unchanged");
    expect(rowTitles()).toEqual(["greet"]);
  });

  it("deciding text and Show diff are soon chips", async () => {
    await openPage("?runs=r1,r3");
    expect(screen.getByText("Deciding moment soon")).toBeInTheDocument();
    fireEvent.mouseOver(screen.getByText("Show diff soon"));
    expect(
      await screen.findByText(
        "A diff of what each run did differently isn't in the API yet",
      ),
    ).toBeInTheDocument();
  });

  it("capped runs say so", async () => {
    // A few calls stand in for each full page of 500.
    const pageOf = (n) =>
      Array.from({ length: 2 }, (_, i) =>
        callRow(`c-${n}-${i}`, `s-${n}-${i}`, "passed"),
      );
    fetchRunCalls.mockImplementation((executionId, { page }) =>
      Promise.resolve(
        executionId === "r3"
          ? callsPage(pageOf(page), { count: 2500, totalPages: 5 })
          : callsPage(CALLS.r1),
      ),
    );
    renderComparePage("?runs=r1,r3");
    expect(
      await screen.findByText(
        "Run 3 has 2,500 calls. Comparing the first 2,000.",
        {},
        { timeout: 5000 },
      ),
    ).toBeInTheDocument();
    const r3Pages = fetchRunCalls.mock.calls
      .filter(([id]) => id === "r3")
      .map(([, params]) => params.page);
    expect(r3Pages).toEqual([1, 2, 3, 4]);
    expect(
      fetchRunCalls.mock.calls.every(([, params]) => params.page_size === 500),
    ).toBe(true);
  });

  it("views can't be saved yet: Save as new view and Set as default view are disabled", async () => {
    await openPage("?runs=r1,r3");

    fireEvent.click(screen.getByRole("button", { name: /All scenarios/ }));
    const saveNew = screen.getByRole("menuitem", {
      name: "Save current as new view",
    });
    expect(saveNew).toHaveAttribute("aria-disabled", "true");
    fireEvent.mouseOver(saveNew.parentElement);
    expect(
      await screen.findByText("Saving views isn't available yet."),
    ).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });

    fireEvent.click(screen.getByRole("button", { name: "Display" }));
    expect(
      screen.getByRole("menuitem", { name: "Set as default view" }),
    ).toHaveAttribute("aria-disabled", "true");
  });

  it("the reading lasts for the visit only", async () => {
    const { unmount } = await openPage("?runs=r1,r3");
    fireEvent.click(screen.getByRole("button", { name: "Display" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Passed → failed" }));
    fireEvent.keyDown(screen.getByRole("presentation"), { key: "Escape" });
    expect(rowTitles()).toEqual(["address"]);
    unmount();

    // A view named in the link that isn't there opens the default reading.
    await openPage("?runs=r1,r3&view=regressions");
    expect(
      screen.getByRole("button", { name: /All scenarios/ }),
    ).toBeInTheDocument();
    expect(rowTitles()).not.toEqual(["address"]);
  });
});
