import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { toRun } from "src/api/agents/agentDetail";
import {
  ENVIRONMENT_RUN,
  SIMULATE_RUN,
} from "src/api/agents/_fixtures/agentDetail";
import AgentOverviewTab from "../AgentOverviewTab";
import { pairLabel } from "../runCells";

const runsState = { current: {} };
const authState = { user: {} };

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => authState,
}));

vi.mock("src/api/agents/agentDetail", async () => {
  const actual = await vi.importActual("src/api/agents/agentDetail");
  return {
    ...actual,
    useAgentRuns: (...args) => {
      runsState.args = args;
      return runsState.current;
    },
  };
});

const agentState = { current: { id: "a1", agentKind: null } };
vi.mock("../useAgentRoute", () => ({
  default: () => ({ agentId: "a1", agent: agentState.current }),
}));

vi.mock("../AgentNextSteps", () => ({
  default: ({ agent }) => <div data-testid="next-steps">{agent.id}</div>,
}));

let currentLocation;
const LocationProbe = () => {
  currentLocation = useLocation();
  return null;
};

const renderTab = () =>
  render(
    <MemoryRouter initialEntries={["/dashboard/agents/connected/a1/overview"]}>
      <Routes>
        <Route
          path="/dashboard/agents/connected/:agentId/overview"
          element={<AgentOverviewTab />}
        />
        <Route path="*" element={null} />
      </Routes>
      <LocationProbe />
    </MemoryRouter>,
  );

const loaded = (raw) => ({
  isLoading: false,
  isError: false,
  data: raw.map(toRun),
});

const ENV_RUN_CELL = `run-cell-${ENVIRONMENT_RUN.test_execution_id}`;
const COLUMNS = [
  "Run",
  "Status",
  "Environment",
  "Agent × environment",
  "Pass rate",
  "When",
];

beforeEach(() => {
  runsState.current = loaded([ENVIRONMENT_RUN, SIMULATE_RUN]);
  authState.user = {};
  agentState.current = { id: "a1", agentKind: null };
});

describe("pairLabel", () => {
  it("names what a run held fixed", () => {
    expect(pairLabel(toRun(ENVIRONMENT_RUN))).toBe("agent v3 × environment v7");
  });

  it("gives only the side a run recorded", () => {
    expect(
      pairLabel(toRun({ ...ENVIRONMENT_RUN, environment_version: null })),
    ).toBe("agent v3");
    expect(pairLabel(toRun(SIMULATE_RUN))).toBeNull();
  });
});

describe("AgentOverviewTab", () => {
  it("asks for the 5 newest runs", () => {
    renderTab();
    expect(runsState.args).toEqual(["a1", { limit: 5 }]);
  });

  it("puts Next steps above the runs", () => {
    agentState.current = { id: "a1", agentKind: "source" };
    renderTab();
    const card = screen.getByTestId("next-steps");
    expect(card).toHaveTextContent("a1");
    expect(
      card.compareDocumentPosition(screen.getByText("Recent runs")) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("holds Next steps' place while the agent loads", () => {
    agentState.current = null;
    renderTab();
    expect(screen.queryByTestId("next-steps")).toBeNull();
    expect(screen.getByTestId("next-steps-skeleton")).toBeInTheDocument();
    expect(screen.getByText("Recent runs")).toBeInTheDocument();
  });

  it("shows more runs on request, when there may be more", () => {
    runsState.current = loaded(
      Array.from({ length: 5 }, (_, i) => ({
        ...ENVIRONMENT_RUN,
        test_execution_id: `run-${i}`,
        run_number: i + 1,
      })),
    );
    renderTab();
    fireEvent.click(screen.getByRole("button", { name: "Show more runs" }));
    expect(runsState.args).toEqual(["a1", { limit: 20 }]);
  });

  it("offers no more runs when fewer than five came back", () => {
    renderTab();
    expect(screen.queryByRole("button", { name: "Show more runs" })).toBeNull();
  });

  it("shows only Recent runs with the designed columns", () => {
    renderTab();
    expect(screen.getByText("Recent runs")).toBeInTheDocument();
    expect(
      screen.getAllByRole("columnheader").map((h) => h.textContent.trim()),
    ).toEqual(COLUMNS);
  });

  it("shows an environment run's number, environment, pairing and pass rate", () => {
    renderTab();
    expect(screen.getByText("Run 2")).toBeInTheDocument();
    expect(screen.getByText("ride-voice-agent")).toBeInTheDocument();
    expect(screen.getByText("agent v3 × environment v7")).toBeInTheDocument();
    expect(
      screen.getByTestId("pass-rate-68dedb99-42b6-46e7-8e94-b511041fa2cb"),
    ).toHaveTextContent("30% 3/10");
  });

  it("marks a running Simulate run and gives it no pass rate", () => {
    renderTab();
    expect(screen.getByText("Old refill check")).toBeInTheDocument();
    expect(screen.getByText("Simulate")).toBeInTheDocument();
    expect(
      screen.getByTestId("run-status-c766900d-00e6-41c7-b922-c4e8eed1e7ec"),
    ).toHaveTextContent("Running");
    expect(
      screen.getByTestId("pass-rate-c766900d-00e6-41c7-b922-c4e8eed1e7ec"),
    ).toHaveTextContent("—");
    expect(screen.getByText(/^started /)).toBeInTheDocument();
  });

  it.each([
    ["pending", "Queued"],
    ["running", "Running"],
    ["evaluating", "Grading"],
    ["cancelling", "Cancelling"],
    ["completed", "Completed"],
    ["failed", "Failed"],
    ["cancelled", "Cancelled"],
  ])(
    "shows a %s run as %s, like the environment runs table",
    (status, label) => {
      runsState.current = loaded([{ ...ENVIRONMENT_RUN, status }]);
      renderTab();
      expect(
        screen.getByTestId(`run-status-${ENVIRONMENT_RUN.test_execution_id}`),
      ).toHaveTextContent(label);
    },
  );

  it.each(["pending", "evaluating", "cancelling"])(
    "gives a %s run no final rate",
    (status) => {
      runsState.current = loaded([{ ...ENVIRONMENT_RUN, status }]);
      renderTab();
      expect(
        screen.getByTestId("pass-rate-68dedb99-42b6-46e7-8e94-b511041fa2cb"),
      ).toHaveTextContent("—");
    },
  );

  it("tags a run of only some scenarios as Partial", () => {
    runsState.current = loaded([{ ...ENVIRONMENT_RUN, partial: true }]);
    renderTab();
    expect(
      within(screen.getByTestId(ENV_RUN_CELL)).getByText("Partial"),
    ).toBeInTheDocument();
  });

  it("doesn't tag a full run, or one still going, as Partial", () => {
    runsState.current = loaded([
      ENVIRONMENT_RUN,
      { ...SIMULATE_RUN, partial: true },
    ]);
    renderTab();
    expect(screen.queryByText("Partial")).toBeNull();
  });

  it("opens an environment run on its environment's run page", () => {
    renderTab();
    fireEvent.click(screen.getByText("Run 2"));
    expect(currentLocation.pathname).toBe(
      "/dashboard/simulate/environments/e5391b8a-156e-46dd-b5a4-4761971f2ccd/runs/b741b14e-91c0-403d-81e5-5dd7e40d09c7/68dedb99-42b6-46e7-8e94-b511041fa2cb",
    );
  });

  it("opens a Simulate run on the Simulate run page", () => {
    renderTab();
    fireEvent.click(screen.getByText("Run 1"));
    expect(currentLocation.pathname).toBe(
      "/dashboard/simulate/test/c8cc6400-c677-4efd-9ad0-d177c912dad4/c766900d-00e6-41c7-b922-c4e8eed1e7ec",
    );
  });

  // The Simulate run page is one the router does not register for an org
  // restricted to Environments, so its row must not lead to the 404.
  it("does not open a Simulate run for an org restricted to Environments", () => {
    authState.user = { restrict_simulate_to_environments: true };
    renderTab();
    fireEvent.click(screen.getByText("Run 1"));
    expect(currentLocation.pathname).toBe(
      "/dashboard/agents/connected/a1/overview",
    );
  });

  it("still opens an environment run for an org restricted to Environments", () => {
    authState.user = { restrict_simulate_to_environments: true };
    renderTab();
    fireEvent.click(screen.getByText("Run 2"));
    expect(currentLocation.pathname).toBe(
      "/dashboard/simulate/environments/e5391b8a-156e-46dd-b5a4-4761971f2ccd/runs/b741b14e-91c0-403d-81e5-5dd7e40d09c7/68dedb99-42b6-46e7-8e94-b511041fa2cb",
    );
  });

  it("holds the table's shape with skeleton rows while runs load", () => {
    runsState.current = { isLoading: true, isError: false, data: undefined };
    renderTab();
    expect(
      screen.getAllByRole("columnheader").map((h) => h.textContent.trim()),
    ).toEqual(COLUMNS);
    expect(screen.getAllByTestId("run-row-skeleton")).toHaveLength(5);
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("invites a first run when nothing has run yet", () => {
    runsState.current = loaded([]);
    renderTab();
    expect(screen.getByText("No runs yet")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Runs of this agent from any environment will show up here.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryAllByRole("columnheader")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "View environments" }));
    expect(currentLocation.pathname).toBe(
      "/dashboard/agents/connected/a1/environments",
    );
  });

  it("shows the server's reason and a retry when the runs can't load", () => {
    const refetch = vi.fn();
    runsState.current = {
      isLoading: false,
      isError: true,
      error: { code: "not_found", message: "Agent definition not found." },
      refetch,
    };
    renderTab();
    expect(screen.getByText("Couldn't load recent runs")).toBeInTheDocument();
    expect(screen.getByText("Agent definition not found.")).toBeInTheDocument();
    expect(screen.queryByText("No runs yet")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
  });
});
