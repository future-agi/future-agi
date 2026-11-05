import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { toRun } from "src/api/agents/agentDetail";
import { ENVIRONMENT_RUN } from "src/api/agents/_fixtures/agentDetail";
import AgentEnvironmentsTab from "../AgentEnvironmentsTab";

const envState = { current: {} };
const envCalls = [];
const agentState = { current: { environmentCount: 2 } };

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

beforeEach(() => {
  envCalls.length = 0;
  agentState.current = { environmentCount: 2 };
  envState.current = {
    isLoading: false,
    isError: false,
    data: [
      {
        id: "e5391b8a",
        name: "ride-voice-agent",
        status: "ready",
        lastRun: toRun({
          ...ENVIRONMENT_RUN,
          outcome_passed: 8,
          outcome_failed: 2,
          completed_at: twoHoursAgo,
        }),
      },
      {
        id: "e9999999",
        name: "Refund edge cases",
        status: "failed",
        lastRun: null,
      },
    ],
  };
});

describe("AgentEnvironmentsTab", () => {
  it("has the contract's columns plus Status, and no actions", () => {
    renderTab();
    ["Environment", "Status", "Testing", "Last result"].forEach((h) =>
      expect(screen.getByText(h)).toBeInTheDocument(),
    );
    expect(screen.queryByText("Open")).toBeNull();
  });

  it("shows each environment's status like My Environments does", () => {
    renderTab();
    expect(screen.getByTestId("env-status-e5391b8a")).toHaveTextContent(
      "Ready",
    );
    expect(screen.getByTestId("env-status-e9999999")).toHaveTextContent(
      "Failed",
    );
  });

  it("shows the version tested and the last result", () => {
    renderTab();
    expect(screen.getByText("v3")).toBeInTheDocument();
    expect(screen.getByTestId("last-result-e5391b8a")).toHaveTextContent(
      "80% · agent v3 · 2h ago",
    );
  });

  it("shows no pass rate while the last run is still going", () => {
    envState.current = {
      isLoading: false,
      isError: false,
      data: [
        {
          id: "e5391b8a",
          name: "ride-voice-agent",
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
      screen.getByText("No environments test this agent yet"),
    ).toBeInTheDocument();
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
