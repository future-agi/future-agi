import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { clearScenarioSelection } from "../../buildEnvironment/console/scenarioSelectionBus";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/api/harness/harness", () => ({
  getHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: vi.fn() } };
});

const { getHarnessJob } = await import("src/api/harness/harness");
const axiosMod = await import("src/utils/axios");
const axios = axiosMod.default;
const { default: EnvironmentWorkspace } = await import("../EnvironmentWorkspace");
const { resetEnvironmentsStore } = await import("../../store/useEnvironmentsStore");

const NOW = "2026-09-15T09:00:00Z";
const PREFIX = "Observability was not turned on: ";

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
  scenarios: [
    {
      scenario_key: "refund-happy-path",
      name: "refund_happy_path",
      instruction: "Ask for a refund on a delivered order.",
    },
  ],
};

const BUILDING_JOB = {
  job: {
    job_id: "job-build",
    metadata: { name: "Building Environment" },
    scenario_count: 3,
    agent: { connector: "livekit" },
  },
  status: { stage: "generating_environment", created_at: NOW },
  credentials: { detected_connectors: ["livekit"] },
  stage_outputs: [],
};

const withRefusal = (base, refusal) => ({
  ...base,
  job: {
    ...base.job,
    metadata: { ...base.job.metadata, observability_refusal: refusal },
  },
});

function renderWorkspace(jobId) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, retryDelay: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/dashboard/simulate/environments/${jobId}`]}>
        <Routes>
          <Route
            path="/dashboard/simulate/environments/:envId"
            element={<EnvironmentWorkspace />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const STATES = [
  ["while building", BUILDING_JOB, "job-build", /Building Environment/],
  ["on a ready Environment", COMPLETED_JOB, "job-done", /Done Environment/],
];

const REFUSALS = [
  [
    "a refusal with details",
    {
      code: "instrumentation_missing",
      details: {
        instrumentation: [
          "The agent's code carries no instrumentation",
          "A second instrumentation message",
        ],
        api_key: ["An API key is required", "A second key message"],
      },
    },
    "The agent's code carries no instrumentation; An API key is required",
  ],
  [
    "agent_not_stored",
    { code: "agent_not_stored", details: {} },
    "the agent's code could not be read",
  ],
  [
    "bound",
    { code: "bound", details: {} },
    "the run uses an existing agent; turn it on from the agent page",
  ],
  [
    "a project error with no details",
    { code: "ObservabilityProjectError", details: {} },
    "the project could not be created",
  ],
  [
    "a details field with an empty list",
    { code: "instrumentation_missing", details: { instrumentation: [] } },
    "the project could not be created",
  ],
];

// A stored value that is not the refusal object must not render a line.
const MALFORMED_REFUSALS = [
  ["a string", "x"],
  ["an array", []],
];

describe("EnvironmentWorkspace observability refusal line", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    resetEnvironmentsStore();
    clearScenarioSelection();
    getHarnessJob.mockReset();
    axios.get.mockReset();
    axios.get.mockImplementation((url) => {
      if (/\/harness-environments\/[^/]+\/$/.test(url)) {
        return Promise.reject(Object.assign(new Error("Not found"), { statusCode: 404 }));
      }
      return Promise.resolve({ data: { results: [], count: 0 } });
    });
  });

  describe.each(STATES)("%s", (_state, base, jobId, nameText) => {
    it.each(REFUSALS)("shows the line for %s", async (_name, refusal, reason) => {
      getHarnessJob.mockResolvedValue(withRefusal(base, refusal));
      renderWorkspace(jobId);

      const line = await screen.findByText(`${PREFIX}${reason}`);
      expect(line.closest('[role="status"]')).not.toBeNull();
      expect(screen.getAllByText(nameText).length).toBeGreaterThan(0);
    });

    it.each(MALFORMED_REFUSALS)("shows nothing when the refusal is %s", async (_name, refusal) => {
      getHarnessJob.mockResolvedValue(withRefusal(base, refusal));
      renderWorkspace(jobId);

      expect((await screen.findAllByText(nameText)).length).toBeGreaterThan(0);
      expect(screen.queryByText(new RegExp(PREFIX))).toBeNull();
    });

    it("shows nothing when the job carries no refusal", async () => {
      getHarnessJob.mockResolvedValue(base);
      renderWorkspace(jobId);

      expect((await screen.findAllByText(nameText)).length).toBeGreaterThan(0);
      expect(screen.queryByText(new RegExp(PREFIX))).toBeNull();
    });
  });
});
