import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { toRun } from "src/api/agents/agentDetail";
import {
  ENVIRONMENT_RUN,
  SIMULATE_RUN,
} from "src/api/agents/_fixtures/agentDetail";
import AgentOverviewTab from "../AgentOverviewTab";

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

beforeEach(() => {
  runsState.current = loaded([ENVIRONMENT_RUN, SIMULATE_RUN]);
  authState.user = {};
});

describe("AgentOverviewTab", () => {
  it("asks for the 15 newest runs", () => {
    renderTab();
    expect(runsState.args).toEqual(["a1", { limit: 15 }]);
  });

  it("shows only Recent runs with the ticket's columns", () => {
    renderTab();
    expect(screen.getByText("Recent runs")).toBeInTheDocument();
    const headers = screen
      .getAllByRole("columnheader")
      .map((h) => h.textContent.trim());
    expect(headers).toEqual([
      "Run",
      "Status",
      "Environment",
      "Agent version",
      "Pass rate",
      "When",
    ]);
  });

  it("shows an environment run's number, environment, version and pass rate", () => {
    renderTab();
    expect(screen.getByText("Run 2")).toBeInTheDocument();
    expect(screen.getByText("ride-voice-agent")).toBeInTheDocument();
    expect(screen.getByText("v3")).toBeInTheDocument();
    expect(
      screen.getByTestId("pass-rate-68dedb99-42b6-46e7-8e94-b511041fa2cb"),
    ).toHaveTextContent("30% 3/10");
  });

  it("marks a running Simulate run and gives it no pass rate", () => {
    renderTab();
    expect(screen.getByText("Old refill check")).toBeInTheDocument();
    expect(screen.getByText("Simulate")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(
      screen.getByTestId("pass-rate-c766900d-00e6-41c7-b922-c4e8eed1e7ec"),
    ).toHaveTextContent("—");
    expect(screen.getByText(/^started /)).toBeInTheDocument();
  });

  it("shows an evaluating run as grading, with no final rate", () => {
    runsState.current = loaded([{ ...ENVIRONMENT_RUN, status: "evaluating" }]);
    renderTab();
    expect(screen.getByText("Grading")).toBeInTheDocument();
    expect(
      screen.getByTestId("pass-rate-68dedb99-42b6-46e7-8e94-b511041fa2cb"),
    ).toHaveTextContent("—");
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
        screen.getByTestId("run-status-68dedb99-42b6-46e7-8e94-b511041fa2cb"),
      ).toHaveTextContent(label);
    },
  );

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
  // restricted to Environments (TH-8602), so its row must not lead to the 404.
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
    ).toEqual([
      "Run",
      "Status",
      "Environment",
      "Agent version",
      "Pass rate",
      "When",
    ]);
    expect(screen.getAllByTestId("run-row-skeleton")).toHaveLength(8);
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
