import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  MemoryRouter,
  Routes,
  Route,
  Navigate,
  Outlet,
  useLocation,
} from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// The heavy product run detail is mocked to a marker that still renders its
// Outlet, so the nested index → call-details redirect mounts and the URL
// settles on /call-details the way the real tree does.
vi.mock("src/sections/test-detail/TestRunDetailView", () => ({
  default: () => (
    <div>
      test-run-detail
      <Outlet />
    </div>
  ),
}));

// getHarnessJob resolves the harness-backed environment; listHarnessJobs is
// exported for parity with the other consumers of this module.
vi.mock("src/api/harness/harness", () => ({
  getHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
}));

// Keep the real `endpoints` so the executions URL assertion is genuine; only
// the axios instance is stubbed.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: vi.fn() } };
});

const { getHarnessJob } = await import("src/api/harness/harness");
const axios = (await import("src/utils/axios")).default;
const { default: EnvironmentWorkspace } = await import("../EnvironmentWorkspace");
const { default: WorkspaceExecutionDetail } = await import(
  "../runs/WorkspaceExecutionDetail"
);
const { useEnvironmentsStore, resetEnvironmentsStore } = await import(
  "../../store/useEnvironmentsStore"
);
const { emptyEnvState } = await import("../../store/envState");
const { seedFromTemplate } = await import("../helpers/seedEnvState");

const NOW = "2026-09-15T09:00:00Z";

const TEMPLATE = {
  id: "env-1",
  name: "Refund Copilot",
  surface: "voice",
  tagline: "Inbound phone support for an online storefront",
  tools: [{ name: "issue_refund", desc: "Issues a refund." }],
  rules: ["Never refund twice"],
  seed: { tables: [{ name: "orders", rows: 500 }] },
  evalPreset: ["task_success", "tone"],
};

// A completed harness job with the platform ids at the TOP LEVEL (sibling of
// job/status), the run-test bridge the workspace runs against.
const COMPLETED_JOB = {
  job: {
    job_id: "job-done",
    metadata: { name: "Done Environment" },
    scenario_count: 3,
    agent: { connector: "livekit" },
  },
  status: { stage: "completed", created_at: NOW },
  credentials: { detected_connectors: ["livekit"] },
  platform: { run_test_id: "rt1", test_execution_id: "ex1" },
  stage_outputs: [],
};

// A still-building job: no platform bridge yet, a non-terminal stage. The
// workspace's building banner counts its progress, so buildProgress must be an
// object the banner can read.
const BUILDING_JOB = {
  job: {
    job_id: "job-build",
    metadata: { name: "Building Environment" },
    scenario_count: 3,
    agent: { connector: "livekit" },
  },
  status: { stage: "running", created_at: NOW },
  credentials: { detected_connectors: ["livekit"] },
  stage_outputs: [],
};

const EXECUTIONS = {
  results: [
    {
      id: "ex1",
      status: "Completed",
      start_time: "2026-01-14T09:12:00.000Z",
      agent_version: "v1",
      total_chats: 12,
      success_rate: 100,
    },
  ],
  count: 1,
};

function LocationProbe() {
  const { pathname, search } = useLocation();
  return <div data-testid="location">{`${pathname}${search}`}</div>;
}

function renderWorkspace(entry) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <LocationProbe />
        <Routes>
          <Route
            path="/dashboard/simulate/environments/:envId"
            element={<EnvironmentWorkspace />}
          >
            <Route
              path="runs/:testId/:executionId"
              element={<WorkspaceExecutionDetail />}
            >
              <Route index element={<Navigate to="call-details" replace />} />
              <Route path="call-details" element={<div>call-details-body</div>} />
            </Route>
          </Route>
          <Route
            path="/dashboard/simulate/environments"
            element={<div>environments-list</div>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const seedClientEnv = (env, envState) =>
  useEnvironmentsStore.setState({
    workspaceEnvs: { [env.id]: env },
    byEnv: { [env.id]: envState },
  });

describe("EnvironmentWorkspace route shell", () => {
  beforeEach(() => {
    // jsdom has no layout, so the console's scroll-to-bottom is a no-op here.
    Element.prototype.scrollIntoView = vi.fn();
    resetEnvironmentsStore();
    getHarnessJob.mockReset();
    axios.get.mockReset();
    axios.get.mockResolvedValue({ data: EXECUTIONS });
  });

  it("renders a seeded client env: name, Live pill and the five tabs", async () => {
    seedClientEnv(TEMPLATE, {
      ...emptyEnvState(),
      agent: { name: "Support agent" },
      scenarios: [{ id: "s1" }],
    });

    renderWorkspace("/dashboard/simulate/environments/env-1");

    // The name also appears in the Overview capability graph (an SVG <text>),
    // so scope the header match to its <p>.
    expect(await screen.findByText("Refund Copilot", { selector: "p" }))
      .toBeInTheDocument();
    expect(screen.getByText("Live")).toBeInTheDocument();
    ["Summary", "Contract", "Scenarios", "Evaluations", "Runs"].forEach((label) =>
      expect(screen.getByRole("tab", { name: new RegExp(label) })).toBeInTheDocument(),
    );
  });

  it("shows no fixture world on a real harness env whose outputs are empty", async () => {
    getHarnessJob.mockResolvedValue(COMPLETED_JOB);

    renderWorkspace("/dashboard/simulate/environments/job-done?tab=contract");

    expect(await screen.findByText("Done Environment", { selector: "p" }))
      .toBeInTheDocument();
    // The "Customer Support Line" fixture world (MOCK_WORLD) used to fill every
    // missing field on a real environment, so its tools and rules rendered as
    // this environment's own.
    expect(screen.queryByText(/verify_identity/)).toBeNull();
    expect(screen.queryByText(/Goodwill credit is capped/)).toBeNull();
    expect(
      screen.queryByText(/A returns-and-orders phone line for a mid-size retailer/),
    ).toBeNull();
  });

  it("does not seed a still-building harness env with fixture scenarios", async () => {
    getHarnessJob.mockResolvedValue(BUILDING_JOB);

    renderWorkspace("/dashboard/simulate/environments/job-build?tab=scenarios");

    expect(await screen.findByText("Building Environment", { selector: "p" }))
      .toBeInTheDocument();
    // The fixture pool used to seed the scenario list while the job was still
    // building, and useEnvState froze that bootstrap in the store, so the real
    // scenarios never replaced it.
    await waitFor(() => {
      const scenariosTab = screen
        .getAllByRole("tab")
        .find((t) => t.textContent.startsWith("Scenarios"));
      expect(scenariosTab.textContent).toBe("Scenarios");
    });
  });

  it("opens the Runs tab from ?tab=runs", async () => {
    seedClientEnv(TEMPLATE, {
      ...emptyEnvState(),
      agent: { name: "Support agent" },
      scenarios: [{ id: "s1" }],
    });

    renderWorkspace("/dashboard/simulate/environments/env-1?tab=runs");

    expect(await screen.findByText("Pre-flight")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Runs/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("shows the not-found state for an unknown id", async () => {
    const err = new Error("Not found");
    err.response = { status: 404 };
    getHarnessJob.mockRejectedValue(err);

    renderWorkspace("/dashboard/simulate/environments/nope");

    expect(await screen.findByText("Environment not found")).toBeInTheDocument();
  });

  it("resolves a harness job: Run enabled, runs listed, row opens the detail", async () => {
    getHarnessJob.mockResolvedValue(COMPLETED_JOB);
    const user = userEvent.setup();

    renderWorkspace("/dashboard/simulate/environments/job-done?tab=runs");

    // Header Run simulation is enabled for a built harness job.
    const runButton = await screen.findByRole("button", { name: /Run simulation/ });
    await waitFor(() => expect(runButton).toBeEnabled());

    // The run history comes from the mocked executions API.
    const row = await screen.findByRole("button", { name: /Run 1/ });
    await user.click(row);

    expect(await screen.findByText("test-run-detail")).toBeInTheDocument();
    expect(screen.getByText("call-details-body")).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/dashboard/simulate/environments/job-done/runs/rt1/ex1/call-details",
    );
  });

  it("shows the building banner with step progress for a still-building job", async () => {
    getHarnessJob.mockResolvedValue(BUILDING_JOB);

    renderWorkspace("/dashboard/simulate/environments/job-build");

    expect(
      await screen.findByText("Environment is still being built"),
    ).toBeInTheDocument();
    expect(screen.getByText(/steps done\./)).toBeInTheDocument();
  });

  it("locks a template-seeded env: no overflow, Fork to edit on Overview", async () => {
    seedClientEnv(TEMPLATE, seedFromTemplate(TEMPLATE, NOW));

    renderWorkspace("/dashboard/simulate/environments/env-1");

    expect(await screen.findByText("Refund Copilot", { selector: "p" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "More actions" })).toBeNull();
    expect(screen.getByRole("button", { name: /Fork to edit/ })).toBeInTheDocument();
  });

  it("forks an unlocked env into a new id and navigates to it", async () => {
    seedClientEnv(TEMPLATE, {
      ...emptyEnvState(),
      agent: { name: "Support agent" },
      scenarios: [{ id: "s1" }],
    });
    const user = userEvent.setup();

    renderWorkspace("/dashboard/simulate/environments/env-1");

    await screen.findByText("Refund Copilot", { selector: "p" });
    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(screen.getByText("Fork environment"));

    const ids = Object.keys(useEnvironmentsStore.getState().workspaceEnvs);
    const forkId = ids.find((id) => id.startsWith("env-1-fork-"));
    expect(forkId).toBeTruthy();
    expect(screen.getByTestId("location")).toHaveTextContent(
      `/dashboard/simulate/environments/${forkId}`,
    );
  });
});
