import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";

const getHarnessJob = vi.fn();
const listHarnessJobs = vi.fn();
const navigate = vi.fn();
const authState = { user: {} };

vi.mock("src/api/harness/harness", () => ({
  getHarnessJob: (...args) => getHarnessJob(...args),
  listHarnessJobs: (...args) => listHarnessJobs(...args),
  cancelHarnessJob: vi.fn(),
  adjustHarnessJob: vi.fn(),
  extendHarnessJob: vi.fn(),
  sendHarnessConversationMessage: vi.fn(),
}));

vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isCloud: true }),
}));

// user-info carries the Simulate cutoff flag for the organization.
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => authState,
}));

// One navigate for every render, so a test can read where the page went.
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useNavigate: () => navigate,
    useParams: () => ({ jobId: "job-1" }),
  };
});

const { default: HarnessDetail } = await import("./HarnessDetail");

// A finished run that reported into the platform, so it offers View simulation.
const FINISHED = {
  job: {
    job_id: "job-1",
    run_id: "harness-job-1",
    scenario_count: 1,
    metadata: { agent_name: "ride-voice-e2e-4" },
  },
  status: {
    state: "completed",
    stage: "completed",
    failure: null,
    cancel_requested_at: null,
    completed_scenarios: 1,
    total_scenarios: 1,
    attempt: 1,
    updated_at: "2026-08-25T11:29:41Z",
  },
  events: [],
  credentials: { detected_connectors: ["http", "livekit"] },
  conversation: null,
  platform: { run_test_id: "rt1", test_execution_id: "ex1" },
};

const renderDetail = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <HarnessDetail />
      </QueryClientProvider>
    </HelmetProvider>,
  );
};

const viewSimulation = () =>
  screen.findByRole("button", { name: /View simulation/ });

describe("HarnessDetail View simulation", () => {
  beforeEach(() => {
    navigate.mockReset();
    getHarnessJob.mockReset();
    getHarnessJob.mockResolvedValue(FINISHED);
    listHarnessJobs.mockReset();
    listHarnessJobs.mockResolvedValue([]);
    authState.user = {};
  });

  it("opens the run on its environment's run page for an organization that sees only Environments", async () => {
    authState.user = { restrict_simulate_to_environments: true };
    const user = userEvent.setup();
    renderDetail();

    await user.click(await viewSimulation());

    expect(navigate).toHaveBeenCalledTimes(1);
    expect(navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/environments/job-1/runs/rt1/ex1",
    );
  });

  it("opens the run's Run Simulation page for every other organization", async () => {
    const user = userEvent.setup();
    renderDetail();

    await user.click(await viewSimulation());

    expect(navigate).toHaveBeenCalledTimes(1);
    expect(navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/test/rt1/ex1/call-details",
    );
  });
});
