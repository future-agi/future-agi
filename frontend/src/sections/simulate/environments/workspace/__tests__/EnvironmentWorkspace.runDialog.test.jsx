import { describe, it, expect, beforeEach, vi } from "vitest";
import PropTypes from "prop-types";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

let keySeq = 0;
vi.mock("src/api/harness/harness", () => ({
  getHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
  harnessIdempotencyKey: vi.fn(() => `key-${(keySeq += 1)}`),
}));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: vi.fn(), post: vi.fn() } };
});

// The panels and the run page stand in for the three ways a run starts: the
// Scenarios selection bar and a re-run of a pinned run.
function PanelsStub({ onStartRun }) {
  return (
    <button type="button" onClick={() => onStartRun?.(["k1", "k2"], 2)}>
      run selection
    </button>
  );
}
PanelsStub.propTypes = { onStartRun: PropTypes.func };
vi.mock("../WorkspacePanels", () => ({ default: PanelsStub }));

function RunDetailStub({ onStartRun }) {
  return (
    <button
      type="button"
      onClick={() =>
        onStartRun?.(["k1"], 3, {
          initialPicks: { environment: "e1", suite: "s2" },
        })
      }
    >
      run again
    </button>
  );
}
RunDetailStub.propTypes = { onStartRun: PropTypes.func };
vi.mock("../runs/detail/RunDetail", () => ({ default: RunDetailStub }));

// The dialog itself is covered by its own tests; here it shows what it was
// opened with and submits fixed picks.
const dialogPicks = { current: { environment: "e1" } };
const dialogResolved = {
  current: { agent: "a1", environment: "e1", suite: "s1" },
};
function RunDialogStub({
  scenarioIds,
  trials,
  initialPicks,
  onSubmit,
  onClose,
  error,
  submitting,
}) {
  return (
    <div data-testid="run-dialog">
      {JSON.stringify({ scenarioIds, trials, initialPicks })}
      <span>{`error:${error?.error ?? "-"}`}</span>
      <span>{`submitting:${submitting}`}</span>
      <button
        type="button"
        onClick={() =>
          onSubmit(dialogPicks.current, scenarioIds, dialogResolved.current)
        }
      >
        submit picks
      </button>
      <button type="button" onClick={onClose}>
        close dialog
      </button>
    </div>
  );
}
RunDialogStub.propTypes = {
  scenarioIds: PropTypes.array,
  trials: PropTypes.number,
  initialPicks: PropTypes.object,
  onSubmit: PropTypes.func,
  onClose: PropTypes.func,
  error: PropTypes.object,
  submitting: PropTypes.bool,
};
vi.mock("../run/RunDialog", () => ({ default: RunDialogStub }));

const { getHarnessJob } = await import("src/api/harness/harness");
const { enqueueSnackbar } = await import("notistack");
const { default: axios } = await import("src/utils/axios");
const { default: EnvironmentWorkspace } = await import(
  "../EnvironmentWorkspace"
);
const { default: WorkspaceExecutionDetail } = await import(
  "../runs/WorkspaceExecutionDetail"
);
const { resetEnvironmentsStore } = await import(
  "../../store/useEnvironmentsStore"
);

const NOW = "2026-09-15T09:00:00Z";
const JOB = {
  job: {
    job_id: "job-done",
    metadata: { name: "Done Environment" },
    scenario_count: 1,
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
      instruction: "Ask for a refund.",
    },
  ],
};
const VERSIONED_JOB = { ...JOB, versioning: { enabled: true } };
const RUN_URL = "/simulate/api/harness-environments/job-done/run/";

function LocationProbe() {
  const { pathname } = useLocation();
  return <div data-testid="location">{pathname}</div>;
}

const newClient = () =>
  new QueryClient({
    defaultOptions: { queries: { retry: false, retryDelay: 0 } },
  });
const VERSIONS_KEY = { queryKey: ["harness-env-versions", "job-done"] };

const renderWorkspace = (
  entry = "/dashboard/simulate/environments/job-done",
  client = newClient(),
) =>
  render(
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
            />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

const headerRun = async () => {
  const button = await screen.findByRole("button", { name: /Run simulation/ });
  await waitFor(() => expect(button).toBeEnabled());
  return button;
};

describe("EnvironmentWorkspace run start", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    resetEnvironmentsStore();
    keySeq = 0;
    dialogPicks.current = { environment: "e1" };
    dialogResolved.current = { agent: "a1", environment: "e1", suite: "s1" };
    enqueueSnackbar.mockReset();
    getHarnessJob.mockReset();
    axios.get.mockReset();
    axios.post.mockReset();
    axios.get.mockImplementation((url) => {
      const detail = url.match(/\/harness-environments\/([^/]+)\/$/);
      if (detail) {
        // The detail loads before the page offers Run.
        const id = detail[1];
        return Promise.resolve({
          data: {
            id,
            overview: { id, name: "Done Environment" },
            contract: null,
          },
        });
      }
      if (url.endsWith("/scenarios/")) {
        return Promise.resolve({
          data: {
            results: [{ id: "r1", scenario_key: "refund-happy-path" }],
            total_pages: 1,
          },
        });
      }
      if (url.endsWith("/versions/")) {
        return Promise.resolve({
          data: { agent: [], environment: [], suite: [] },
        });
      }
      return Promise.resolve({ data: { results: [], count: 0 } });
    });
    axios.post.mockResolvedValue({
      data: {
        run_test_id: "rt1",
        test_execution_id: "ex2",
        versions: { agent: null, environment: null, suite: null },
        warnings: [],
      },
    });
  });

  it("starts the run directly while versioning is off", async () => {
    getHarnessJob.mockResolvedValue(JOB);
    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await headerRun());

    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(
        RUN_URL,
        { scenario_ids: ["refund-happy-path"], trials: 1 },
        { headers: { "Idempotency-Key": "key-1" } },
      ),
    );
    expect(screen.queryByTestId("run-dialog")).toBeNull();
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/dashboard/simulate/environments/job-done/runs/rt1/ex2",
      ),
    );
  });

  it("asks for versions from the header and sends only the picks", async () => {
    getHarnessJob.mockResolvedValue(VERSIONED_JOB);
    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await headerRun());
    expect(axios.post).not.toHaveBeenCalled();
    expect(screen.getByTestId("run-dialog")).toHaveTextContent(
      JSON.stringify({ trials: 1 }),
    );

    await user.click(screen.getByRole("button", { name: "submit picks" }));
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(
        RUN_URL,
        {
          scenario_ids: ["refund-happy-path"],
          trials: 1,
          environment_version_id: "e1",
        },
        { headers: { "Idempotency-Key": "key-1" } },
      ),
    );
    await waitFor(() => expect(screen.queryByTestId("run-dialog")).toBeNull());
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/dashboard/simulate/environments/job-done/runs/rt1/ex2",
    );
  });

  it("asks for versions from the selection bar", async () => {
    getHarnessJob.mockResolvedValue(VERSIONED_JOB);
    const user = userEvent.setup();
    renderWorkspace();

    await user.click(
      await screen.findByRole("button", { name: "run selection" }),
    );
    expect(screen.getByTestId("run-dialog")).toHaveTextContent(
      JSON.stringify({ scenarioIds: ["k1", "k2"], trials: 2 }),
    );

    dialogPicks.current = { suite: "s2" };
    await user.click(screen.getByRole("button", { name: "submit picks" }));
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(
        RUN_URL,
        { scenario_ids: ["k1", "k2"], trials: 2, suite_version_id: "s2" },
        { headers: { "Idempotency-Key": "key-1" } },
      ),
    );
  });

  it("starts the run on a changed agent version", async () => {
    getHarnessJob.mockResolvedValue(VERSIONED_JOB);
    const user = userEvent.setup();
    renderWorkspace();

    await user.click(
      await screen.findByRole("button", { name: "run selection" }),
    );
    dialogPicks.current = { agent: "a2" };
    await user.click(screen.getByRole("button", { name: "submit picks" }));
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(
        RUN_URL,
        { scenario_ids: ["k1", "k2"], trials: 2, agent_version_id: "a2" },
        { headers: { "Idempotency-Key": "key-1" } },
      ),
    );
  });

  it("pre-selects a re-run's versions in the dialog", async () => {
    getHarnessJob.mockResolvedValue(VERSIONED_JOB);
    const user = userEvent.setup();
    renderWorkspace("/dashboard/simulate/environments/job-done/runs/rt1/ex1");

    await user.click(await screen.findByRole("button", { name: "run again" }));
    expect(screen.getByTestId("run-dialog")).toHaveTextContent(
      JSON.stringify({
        scenarioIds: ["k1"],
        trials: 3,
        initialPicks: { environment: "e1", suite: "s2" },
      }),
    );

    await user.click(screen.getByRole("button", { name: "close dialog" }));
    expect(screen.queryByTestId("run-dialog")).toBeNull();
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("keeps a refusal in the dialog and asks with a fresh key after it", async () => {
    getHarnessJob.mockResolvedValue(VERSIONED_JOB);
    axios.post.mockRejectedValueOnce({
      statusCode: 422,
      error: "version_not_runnable",
      message: "These versions haven't been run together yet",
    });
    const client = newClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const user = userEvent.setup();
    renderWorkspace(undefined, client);

    await user.click(
      await screen.findByRole("button", { name: "run selection" }),
    );
    await user.click(screen.getByRole("button", { name: "submit picks" }));
    expect(
      await screen.findByText("error:version_not_runnable"),
    ).toBeInTheDocument();

    // The dialog explains it, so no snackbar repeats it.
    expect(enqueueSnackbar).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalledWith(VERSIONS_KEY);

    await user.click(screen.getByRole("button", { name: "submit picks" }));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
    expect(axios.post.mock.calls[1][2]).toEqual({
      headers: { "Idempotency-Key": "key-2" },
    });
  });

  it("shows a reused request key in the dialog and asks with a new key next", async () => {
    getHarnessJob.mockResolvedValue(VERSIONED_JOB);
    axios.post.mockRejectedValueOnce({
      statusCode: 409,
      error: "idempotency_key_reused",
      message: "This request was already sent with other versions",
    });
    const client = newClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const user = userEvent.setup();
    renderWorkspace(undefined, client);

    await user.click(
      await screen.findByRole("button", { name: "run selection" }),
    );
    await user.click(screen.getByRole("button", { name: "submit picks" }));
    expect(
      await screen.findByText("error:idempotency_key_reused"),
    ).toBeInTheDocument();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
    // The dialog re-reads the defaults, which moved since it opened.
    expect(invalidate).toHaveBeenCalledWith(VERSIONS_KEY);

    await user.click(screen.getByRole("button", { name: "submit picks" }));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
    expect(axios.post.mock.calls[1][2].headers["Idempotency-Key"]).toBe(
      "key-2",
    );
  });

  it("asks with a new key when the same picks resolve to moved defaults", async () => {
    getHarnessJob.mockResolvedValue(VERSIONED_JOB);
    axios.post
      .mockRejectedValueOnce({ statusCode: 503, message: "busy" })
      .mockRejectedValueOnce({ statusCode: 503, message: "busy" });
    const user = userEvent.setup();
    renderWorkspace();

    await user.click(
      await screen.findByRole("button", { name: "run selection" }),
    );
    await user.click(screen.getByRole("button", { name: "submit picks" }));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    // A server failure is not the dialog's to explain.
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith("busy", {
        variant: "error",
      }),
    );

    dialogResolved.current = { agent: "a2", environment: "e1", suite: "s1" };
    await screen.findByText("submitting:false");
    await user.click(screen.getByRole("button", { name: "submit picks" }));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
    expect(axios.post.mock.calls[1][1]).toEqual({
      scenario_ids: ["k1", "k2"],
      trials: 2,
      environment_version_id: "e1",
    });
    expect(axios.post.mock.calls[1][2].headers["Idempotency-Key"]).toBe(
      "key-2",
    );
  });

  it("reuses the key for the same picks after a server failure, not for new ones", async () => {
    getHarnessJob.mockResolvedValue(VERSIONED_JOB);
    axios.post
      .mockRejectedValueOnce({ statusCode: 503, message: "busy" })
      .mockRejectedValueOnce({ statusCode: 503, message: "busy" });
    const user = userEvent.setup();
    renderWorkspace();

    await user.click(
      await screen.findByRole("button", { name: "run selection" }),
    );
    await user.click(screen.getByRole("button", { name: "submit picks" }));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    await screen.findByText("submitting:false");
    await user.click(screen.getByRole("button", { name: "submit picks" }));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
    expect(axios.post.mock.calls[1][2].headers["Idempotency-Key"]).toBe(
      "key-1",
    );

    dialogPicks.current = { environment: "e9" };
    dialogResolved.current = { agent: "a1", environment: "e9", suite: "s1" };
    await screen.findByText("submitting:false");
    await user.click(screen.getByRole("button", { name: "submit picks" }));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(3));
    expect(axios.post.mock.calls[2][2].headers["Idempotency-Key"]).toBe(
      "key-2",
    );
  });
});
