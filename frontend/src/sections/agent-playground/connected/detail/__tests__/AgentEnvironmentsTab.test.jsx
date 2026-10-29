import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { toRun } from "src/api/agents/agentDetail";
import { ENVIRONMENT_RUN } from "src/api/agents/_fixtures/agentDetail";
import AgentEnvironmentsTab, { productionAhead } from "../AgentEnvironmentsTab";

const envState = { current: {} };
const envCalls = [];
const agentState = {
  current: { environmentCount: 2, activeVersionNumber: 3 },
};

vi.mock("src/api/agents/agentDetail", async () => {
  const actual = await vi.importActual("src/api/agents/agentDetail");
  return {
    ...actual,
    useAgentEnvironments: (...args) => {
      envCalls.push(args);
      return envState.current;
    },
  };
});

vi.mock("../useAgentRoute", () => ({
  default: () => ({ agentId: "a1", agent: agentState.current }),
}));

let currentLocation;
const LocationProbe = () => {
  currentLocation = useLocation();
  return null;
};

const renderTab = () =>
  render(
    <MemoryRouter
      initialEntries={["/dashboard/agents/connected/a1/environments"]}
    >
      <Routes>
        <Route
          path="/dashboard/agents/connected/:agentId/environments"
          element={<AgentEnvironmentsTab />}
        />
        <Route path="*" element={null} />
      </Routes>
      <LocationProbe />
    </MemoryRouter>,
  );

const twoHoursAgo = new Date(Date.now() - 2 * 3600 * 1000).toISOString();

const TESTED = {
  id: "e5391b8a",
  name: "ride-voice-agent",
  status: "ready",
  agentVersion: { id: "93956ae6", number: 3 },
  lastRun: toRun({
    ...ENVIRONMENT_RUN,
    outcome_passed: 8,
    outcome_failed: 2,
    completed_at: twoHoursAgo,
  }),
};
const UNTESTED = {
  id: "e9999999",
  name: "Refund edge cases",
  status: "failed",
  agentVersion: null,
  lastRun: null,
};

beforeEach(() => {
  envCalls.length = 0;
  agentState.current = { environmentCount: 2, activeVersionNumber: 3 };
  envState.current = {
    isLoading: false,
    isError: false,
    data: [TESTED, UNTESTED],
  };
});

describe("productionAhead", () => {
  it("is nothing while the environment tests the version in production", () => {
    expect(productionAhead(TESTED, { activeVersionNumber: 3 })).toBeNull();
  });

  it("names the version in production once it has moved past the pin", () => {
    expect(productionAhead(TESTED, { activeVersionNumber: 5 })).toBe(5);
  });

  it("has nothing to say for an environment with no pin", () => {
    expect(productionAhead(UNTESTED, { activeVersionNumber: 5 })).toBeNull();
  });
});

describe("AgentEnvironmentsTab", () => {
  it("has the designed columns and an Open action per row", () => {
    renderTab();
    ["Environment", "Testing", "Last result", "Status"].forEach((h) =>
      expect(screen.getByText(h)).toBeInTheDocument(),
    );
    expect(screen.getAllByRole("button", { name: "Open" })).toHaveLength(2);
  });

  it("shows each environment's build state in the Status column", () => {
    renderTab();
    expect(screen.getByTestId("env-status-e5391b8a")).toHaveTextContent(
      "Ready",
    );
    expect(screen.getByTestId("env-status-e9999999")).toHaveTextContent(
      "Failed",
    );
  });

  it("shows the version each environment is pinned to, not its last run's", () => {
    envState.current = {
      isLoading: false,
      isError: false,
      data: [{ ...TESTED, agentVersion: { id: "v2", number: 2 } }, UNTESTED],
    };
    renderTab();
    expect(screen.getByTestId("testing-e5391b8a")).toHaveTextContent("v2");
    expect(screen.getByTestId("testing-e9999999")).toHaveTextContent("—");
  });

  it("names the last result as agent × environment", () => {
    renderTab();
    expect(screen.getByTestId("last-result-e5391b8a")).toHaveTextContent(
      "80% · agent v3 × environment v7 · 2h ago",
    );
  });

  it("says when production has moved past an environment's pin", () => {
    agentState.current = { environmentCount: 2, activeVersionNumber: 5 };
    renderTab();
    expect(screen.getByTestId("production-ahead-e5391b8a")).toHaveTextContent(
      "v5 in production",
    );
    expect(screen.queryByTestId("production-ahead-e9999999")).toBeNull();
  });

  it("says nothing extra while the pin is the version in production", () => {
    renderTab();
    expect(screen.queryByTestId("production-ahead-e5391b8a")).toBeNull();
  });

  it("shows no pass rate while the last run is still going", () => {
    envState.current = {
      isLoading: false,
      isError: false,
      data: [
        {
          ...TESTED,
          status: "running",
          lastRun: toRun({
            ...ENVIRONMENT_RUN,
            status: "running",
            outcome_passed: 0,
            outcome_failed: 1,
            completed_at: null,
            started_at: twoHoursAgo,
          }),
        },
      ],
    };
    renderTab();
    const cell = screen.getByTestId("last-result-e5391b8a");
    expect(cell).not.toHaveTextContent("%");
    expect(cell).toHaveTextContent("started 2h ago");
  });

  it("says when an environment has no runs", () => {
    renderTab();
    expect(screen.getByTestId("last-result-e9999999")).toHaveTextContent(
      "No runs yet",
    );
  });

  it("opens the environment from its row", () => {
    renderTab();
    fireEvent.click(screen.getByText("ride-voice-agent"));
    expect(currentLocation.pathname).toBe(
      "/dashboard/simulate/environments/e5391b8a",
    );
  });

  it("opens the environment from its Open button", () => {
    renderTab();
    fireEvent.click(screen.getAllByRole("button", { name: "Open" })[1]);
    expect(currentLocation.pathname).toBe(
      "/dashboard/simulate/environments/e9999999",
    );
  });

  it("offers Build environment as a disabled Coming soon action", () => {
    renderTab();
    const button = screen.getByRole("button", { name: "Build environment" });
    expect(button).toBeDisabled();
    expect(screen.getAllByLabelText("Coming soon").length).toBeGreaterThan(0);
  });

  it("holds the table's shape with skeleton rows while it loads", () => {
    envState.current = { isLoading: true, isError: false, data: undefined };
    renderTab();
    expect(screen.getByText("Environment")).toBeInTheDocument();
    expect(screen.getByText("Last result")).toBeInTheDocument();
    expect(screen.getAllByTestId("environment-row-skeleton")).toHaveLength(3);
    expect(screen.queryByText("Loading…")).toBeNull();
  });

  it("says when no environment tests the agent", () => {
    envState.current = { isLoading: false, isError: false, data: [] };
    renderTab();
    expect(
      screen.getByText("Not used in any environment yet"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Build environment" }),
    ).toBeDisabled();
  });

  it("shows a load error as an error", () => {
    envState.current = {
      isLoading: false,
      isError: true,
      error: { message: "agent_definition_id: Unknown field." },
    };
    renderTab();
    expect(
      screen.getByText("agent_definition_id: Unknown field."),
    ).toBeInTheDocument();
  });
});
