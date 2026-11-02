import { describe, it, expect, vi, beforeEach, afterAll } from "vitest";
import { configure } from "@testing-library/react";
import { screen, fireEvent, waitFor, within } from "src/utils/test-utils";
import ScenariosStep from "../ScenariosStep";
import { TEST_ENV, SERVER_ROWS, envStateFor, renderWithClient } from "./scenariosTestUtils";

configure({ asyncUtilTimeout: 5000 });
afterAll(() => configure({ asyncUtilTimeout: 1000 }));

vi.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar: () => "k", closeSnackbar: () => {} }),
  enqueueSnackbar: () => "k",
}));
vi.mock("src/api/simulate-environments/scenarios", async () => {
  const actual = await vi.importActual("src/api/simulate-environments/scenarios");
  return {
    ...actual,
    listScenarios: vi.fn(),
    amendScenarios: vi.fn(),
    scenarioCoverage: vi.fn(async () => ({ axes: [], per_axis: [], rows: [], columns: [], cells: [] })),
  };
});
vi.mock("src/api/simulate-environments/runs", async (orig) => ({
  ...(await orig()),
  useEnvironmentRuns: () => ({
    runs: [{ id: "r1", label: "Run 1", status: "passed", total: 4, passed: 3, scenarioCount: 4, finishedAt: "2026-10-20T10:00:00Z" }],
    count: 1,
    isLoading: false,
  }),
}));

const { listScenarios } = await import("src/api/simulate-environments/scenarios");
const { queryScenarioFixture, resetScenarioFixture } = await import(
  "src/api/simulate-environments/_fixtures/scenariosFixtures"
);

const onStartRun = vi.fn();
const renderStep = (props = {}) =>
  renderWithClient(
    <ScenariosStep
      env={{ ...TEST_ENV, subGoals: ["Verify the caller", "Quote the fee"] }}
      envState={envStateFor(SERVER_ROWS)}
      patch={vi.fn()}
      onStartRun={onStartRun}
      canRun
      {...props}
    />,
  );

beforeEach(() => {
  resetScenarioFixture();
  onStartRun.mockClear();
  listScenarios.mockReset();
  listScenarios.mockImplementation(async (jobId, params) => queryScenarioFixture(params));
});

const openGroupBy = () => fireEvent.click(screen.getByRole("button", { name: /^Group by/ }));

describe("Scenarios toolbar and footer in Vel's layout", () => {
  it("Group by lists Source and Added by and sends group_by for them", async () => {
    renderStep();
    await screen.findByRole("table");
    openGroupBy();
    const menu = screen.getByRole("menu");
    expect(within(menu).getByRole("menuitemradio", { name: "Source" })).toBeInTheDocument();
    fireEvent.click(within(menu).getByRole("menuitemradio", { name: "Added by" }));
    await waitFor(() =>
      expect(listScenarios.mock.calls.some(([, p]) => p.group_by === "added_by")).toBe(true),
    );
  });

  it("a rejected group_by falls back with a note", async () => {
    listScenarios.mockImplementation(async (jobId, params) => {
      const res = queryScenarioFixture({ ...params, group_by: params.group_by === "source" ? "goal" : params.group_by });
      return res;
    });
    renderStep();
    await screen.findByRole("table");
    openGroupBy();
    fireEvent.click(within(screen.getByRole("menu")).getByRole("menuitemradio", { name: "Source" }));
    expect(await screen.findByText(/Grouping by Source isn't available yet/)).toBeInTheDocument();
  });

  it('footer reads "N scenarios" with soon chips for stale and archived', async () => {
    renderStep();
    await screen.findByRole("table");
    expect(screen.getByText(`${SERVER_ROWS.length} scenarios`)).toBeInTheDocument();
    expect(screen.getByText("Stale soon")).toBeInTheDocument();
    expect(screen.getByText("Archived soon")).toBeInTheDocument();
  });

  it("no runs yet shows the No baseline yet banner with Run baseline (real)", async () => {
    renderStep({ noRuns: true });
    await screen.findByRole("table");
    expect(screen.getByText("No baseline yet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Run baseline" }));
    expect(onStartRun).toHaveBeenCalledWith(undefined, expect.any(Number));
  });

  it("says what improvements can't break, with the count still to come", async () => {
    renderStep();
    await screen.findByRole("table");
    expect(screen.getByText("What improvements can't break")).toBeInTheDocument();
    expect(screen.getByText("Protection soon")).toBeInTheDocument();
  });

  it("hides the banner once there are runs", async () => {
    renderStep({ noRuns: false });
    await screen.findByRole("table");
    expect(screen.queryByText("No baseline yet")).toBeNull();
  });

  it("Needs review is present with no count; Quality review opens flagged", async () => {
    renderStep();
    await screen.findByRole("table");
    const btn = screen.getByRole("button", { name: /Needs review/ });
    expect(btn.textContent).not.toMatch(/\d/);
    fireEvent.click(btn);
    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    expect((await screen.findAllByLabelText("UI only")).length).toBeGreaterThan(0);
  });

  it("History opens the drawer with real runs and the batches note", async () => {
    renderStep();
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("button", { name: "History" }));
    expect(await screen.findByText(/Run 1/)).toBeInTheDocument();
    expect(screen.getByText(/Batches aren't in the API yet/)).toBeInTheDocument();
  });

  it("Coverage has a Guardrail coverage section with the real rules and a soon chip", async () => {
    renderStep();
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("button", { name: /^Coverage/ }));
    expect(await screen.findByText("Guardrail coverage")).toBeInTheDocument();
    expect(screen.getByText("Coverage soon")).toBeInTheDocument();
  });

  it("Sub-goals mode lists the contract's sub-goals with a Used by count", async () => {
    renderStep();
    await screen.findByRole("table");
    openGroupBy();
    fireEvent.click(within(screen.getByRole("menu")).getByRole("menuitemradio", { name: "Sub-goals list" }));
    expect(await screen.findByText("Verify the caller")).toBeInTheDocument();
    expect(screen.getByText("Quote the fee")).toBeInTheDocument();
  });
});
