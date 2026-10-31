import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  renderWithRouter,
  screen,
  fireEvent,
  within,
} from "src/utils/test-utils";

vi.mock("src/api/simulate-environments/runs", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useEnvironmentRuns: vi.fn() };
});
vi.mock("src/api/simulate-environments/runCalls", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useRunCalls: vi.fn() };
});
vi.mock("notistack", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, enqueueSnackbar: vi.fn() };
});

const { useEnvironmentRuns } = await import(
  "src/api/simulate-environments/runs"
);
const { useRunCalls } = await import("src/api/simulate-environments/runCalls");
const { enqueueSnackbar } = await import("notistack");
const { default: FailingCasesRoute } = await import("../FailingCasesRoute");

const ENV = { id: "env-1", platform: { runTestId: "rt-1" } };

const LATEST = {
  id: "ex-3",
  executionId: "ex-3",
  ordinal: 3,
  label: "Run 3",
  status: "failed",
  runState: "completed",
  trials: 1,
  agentVersion: "v4",
  finishedAt: null,
};
const OLDER = {
  ...LATEST,
  id: "ex-2",
  executionId: "ex-2",
  ordinal: 2,
  label: "Run 2",
};

const call = (id, name, status, extra = {}) => ({
  id,
  scenario: name,
  sourceScenario: name,
  sourceScenarioKey: `key-${name}`,
  status,
  persona: "Impatient caller",
  goal: `Goal of ${name}`,
  trialIndex: null,
  evalResults: [],
  ...extra,
});

const runsReturn = (runs) => ({
  runs,
  count: runs.length,
  coveredScenarioCount: null,
  isLoading: false,
});
const callsReturn = (tasks) => ({
  tasks,
  count: tasks.length,
  isLoading: false,
  error: null,
});

const renderRoute = (onClose = vi.fn()) =>
  renderWithRouter(
    <FailingCasesRoute env={ENV} envState={{}} onClose={onClose} />,
  );

describe("FailingCasesRoute", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useEnvironmentRuns.mockReturnValue(runsReturn([LATEST, OLDER]));
    useRunCalls.mockReturnValue(
      callsReturn([
        call("c1", "Refund after cutoff", "failed", {
          evalResults: [
            {
              id: "ev1",
              name: "Policy adherence",
              passed: false,
              reason: "Agent promised a refund past the window.",
            },
          ],
        }),
        call("c2", "Change delivery address", "passed"),
        call("c3", "Cancel a booked ride", "failed"),
      ]),
    );
  });

  it("lists the latest run's failed scenarios and not the passed ones", () => {
    renderRoute();
    expect(useRunCalls).toHaveBeenCalledWith("ex-3", expect.any(Object));
    expect(screen.getByText("Refund after cutoff")).toBeInTheDocument();
    expect(screen.getByText("Cancel a booked ride")).toBeInTheDocument();
    expect(
      screen.queryByText("Change delivery address"),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Policy adherence")).toBeInTheDocument();
  });

  it("shows a no-runs empty state when the environment has never run", () => {
    useEnvironmentRuns.mockReturnValue(runsReturn([]));
    useRunCalls.mockReturnValue(callsReturn([]));
    renderRoute();
    expect(screen.getByText("No runs yet")).toBeInTheDocument();
  });

  it("says nothing failed when the latest run has no failures", () => {
    useRunCalls.mockReturnValue(
      callsReturn([call("c2", "Change delivery address", "passed")]),
    );
    renderRoute();
    expect(
      screen.getByText("Nothing failed in the latest run"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Change delivery address"),
    ).not.toBeInTheDocument();
  });

  it("marks a scenario that passed some trials and failed others as flaky", () => {
    useEnvironmentRuns.mockReturnValue(runsReturn([{ ...LATEST, trials: 3 }]));
    useRunCalls.mockReturnValue(
      callsReturn([
        call("t1", "Refund after cutoff", "failed", { trialIndex: 1 }),
        call("t2", "Refund after cutoff", "passed", { trialIndex: 2 }),
        call("t3", "Refund after cutoff", "failed", { trialIndex: 3 }),
        call("t4", "Cancel a booked ride", "failed", { trialIndex: 1 }),
        call("t5", "Cancel a booked ride", "failed", { trialIndex: 2 }),
      ]),
    );
    renderRoute();
    expect(screen.getAllByText("Refund after cutoff")).toHaveLength(1);
    expect(screen.getByText("Flaky, passed 1 of 3")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });

  it("enables Add N variations once rows are picked, and only says UI only", () => {
    const onClose = vi.fn();
    renderRoute(onClose);
    const add = screen.getByRole("button", { name: /add variations/i });
    expect(add).toBeDisabled();

    const boxes = screen.getAllByRole("checkbox", { name: /select/i });
    fireEvent.click(boxes[0]);
    fireEvent.click(boxes[1]);

    const addTwo = screen.getByRole("button", { name: "Add 2 variations" });
    expect(addTwo).toBeEnabled();
    fireEvent.click(addTwo);
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "UI only. Nothing was saved.",
      {
        variant: "info",
      },
    );
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("shows a UI only flag", () => {
    renderRoute();
    expect(screen.getAllByLabelText("UI only").length).toBeGreaterThan(0);
  });

  it("never renders an em dash", () => {
    const { container } = renderRoute();
    expect(container.textContent).not.toContain("—");
    const row = screen.getByText("Cancel a booked ride").closest("tr");
    expect(within(row).queryByText("—")).not.toBeInTheDocument();
  });
});
