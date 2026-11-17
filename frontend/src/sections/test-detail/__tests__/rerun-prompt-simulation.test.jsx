/* eslint-disable react/prop-types */
import React from "react";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import { handleError } from "src/utils/queryErrorHandler";
import TestRunDetailHeader from "../TestRunDetailHeader";
import RerunModal from "../RerunModal";

// What the server answers (400) when a prompt simulation is re-run while it
// has no prompt version selected.
const NO_VERSION_REFUSAL =
  "Select a prompt version, then re-run this simulation.";

// That refusal as the app's request client hands it to the caller.
const noVersionRefusal = () => ({
  status: false,
  type: "validation_error",
  code: "invalid",
  detail: NO_VERSION_REFUSAL,
  message: NO_VERSION_REFUSAL,
  error: NO_VERSION_REFUSAL,
  result: NO_VERSION_REFUSAL,
  statusCode: 400,
});

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  snackbar: vi.fn(),
  rerunTest: vi.fn(),
  setSdkCodeOpen: vi.fn(),
  status: "completed",
  testData: null,
  kpis: null,
}));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ executionId: "exec-1", testId: "test-1" }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: mocks.post },
  endpoints: {
    runTests: {
      executionDetailsExport: (id) => `/simulate/test-executions/${id}/export/`,
    },
    testExecutions: {
      rerunExecution: (id) => `/simulate/test-executions/${id}/rerun-calls/`,
    },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: mocks.snackbar }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("../context/TestDetailContext", () => ({
  useTestDetail: () => ({ refreshGrid: vi.fn() }),
}));
vi.mock("src/sections/test/common", () => ({
  useCancelExecution: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../states", () => ({
  useTestExecutionStore: (selector) => selector({ status: mocks.status }),
}));
vi.mock("../common", () => ({
  RerunTestOptions: { EVAL_ONLY: "eval_only", CALL_AND_EVAL: "call_and_eval" },
  TestRunLoadingStatus: ["pending", "running", "evaluating", "cancelling"],
}));
// The real re-run request, so a refusal reaches the app's error toast; each
// payload is also recorded as it is sent.
vi.mock("src/api/tests/testDetails", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useRerunTest: (executionId, options) => {
      const rerun = actual.useRerunTest(executionId, options);
      return {
        ...rerun,
        mutate: (data) => {
          mocks.rerunTest(data);
          rerun.mutate(data);
        },
      };
    },
  };
});
vi.mock("src/hooks/useKpis", () => ({
  default: () => ({ data: mocks.kpis }),
}));
vi.mock("src/hooks/useTestRunDetails", () => ({
  default: () => ({ data: mocks.testData }),
}));
vi.mock("src/sections/test/TestRuns/state", () => ({
  useTestRunSdkStoreShallow: (selector) =>
    selector({ setSdkCodeOpen: mocks.setSdkCodeOpen }),
}));
vi.mock("src/sections/test/TestRuns/NewVoiceSimulationDrawer", () => ({
  default: () => null,
}));
vi.mock("../TestDetailBreadcrumb", () => ({ default: () => null }));
vi.mock("src/sections/test/CallLogs/CallStatus", () => ({
  default: () => null,
}));
vi.mock("../components/CallMetadataSection", () => ({
  default: () => null,
}));
vi.mock("src/components/CallLogsDrawer/CustomCallLogHeader", () => ({
  formatDurationSafe: () => "0s",
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/custom-dialog", () => ({
  ConfirmDialog: ({ open, title, content, action }) =>
    open ? (
      <div role="dialog" aria-label={title}>
        <p>{content}</p>
        {action}
      </div>
    ) : null,
}));

const PROMPT = { source_type: "prompt", prompt_template: "prompt-1" };
const AGENT = { source_type: "agent_definition", agent_definition: "agent-1" };

const clients = [];
const renderWithClient = (ui) => {
  // The app's client: a failed request is reported by the app's error toast.
  const client = new QueryClient({
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: { mutations: { retry: false } },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.status = "completed";
  mocks.testData = null;
  mocks.kpis = null;
  mocks.post.mockResolvedValue({ data: {} });
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe("Re-run simulation on a run's page", () => {
  // A prompt simulation's run reports its calls as chats, but the server makes
  // them again on a re-run: it confirms and re-runs every call, as a voice
  // simulation does, instead of opening the SDK steps of a chat agent.
  it.each([
    ["source_type", PROMPT],
    ["sourceType", { sourceType: "prompt", promptTemplate: "prompt-1" }],
  ])(
    "re-runs every call of a prompt simulation after the confirm (%s)",
    async (_field, testData) => {
      mocks.testData = testData;
      mocks.kpis = { total_calls: 4, agent_type: "text" };
      renderWithClient(<TestRunDetailHeader />);

      const rerun = screen.getByRole("button", { name: "Re-run simulation" });
      expect(rerun).not.toBeDisabled();
      expect(rerun).not.toHaveAttribute("aria-describedby");

      fireEvent.click(rerun);
      expect(mocks.setSdkCodeOpen).not.toHaveBeenCalled();
      const dialog = screen.getByRole("dialog", { name: "Confirm Rerun Test" });
      fireEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));

      expect(mocks.rerunTest).toHaveBeenCalledWith({
        select_all: true,
        rerun_type: "call_and_eval",
        call_execution_ids: [],
      });
      await waitFor(() =>
        expect(mocks.post).toHaveBeenCalledWith(
          "/simulate/test-executions/exec-1/rerun-calls/",
          {
            select_all: true,
            rerun_type: "call_and_eval",
            call_execution_ids: [],
          },
        ),
      );
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
    },
  );

  it("shows the server's sentence when a prompt simulation has no prompt version selected", async () => {
    mocks.testData = PROMPT;
    mocks.kpis = { total_calls: 4, agent_type: "text" };
    mocks.post.mockRejectedValueOnce(noVersionRefusal());
    renderWithClient(<TestRunDetailHeader />);

    fireEvent.click(screen.getByRole("button", { name: "Re-run simulation" }));
    const dialog = screen.getByRole("dialog", { name: "Confirm Rerun Test" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));

    await waitFor(() =>
      expect(mocks.snackbar).toHaveBeenCalledWith(NO_VERSION_REFUSAL, {
        variant: "error",
      }),
    );
    expect(mocks.snackbar).toHaveBeenCalledTimes(1);
    expect(mocks.snackbar).not.toHaveBeenCalledWith(
      "Something went wrong",
      expect.anything(),
    );
    // The confirm stays open, so the run can be re-run once a version is
    // selected.
    expect(
      screen.getByRole("dialog", { name: "Confirm Rerun Test" }),
    ).toBeInTheDocument();
  });

  it("gives way to Stop Running while a prompt simulation runs", () => {
    mocks.status = "running";
    mocks.testData = PROMPT;
    mocks.kpis = { total_calls: 4, agent_type: "text" };
    renderWithClient(<TestRunDetailHeader />);

    expect(
      screen.getByRole("button", { name: "Stop Running" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Re-run simulation" }),
    ).not.toBeInTheDocument();
  });

  it("re-runs every call of a voice simulation after the confirm, as before", () => {
    mocks.testData = AGENT;
    mocks.kpis = { total_calls: 4, agent_type: "voice" };
    renderWithClient(<TestRunDetailHeader />);

    const rerun = screen.getByRole("button", { name: "Re-run simulation" });
    expect(rerun).not.toBeDisabled();
    expect(rerun).not.toHaveAttribute("aria-describedby");

    fireEvent.click(rerun);
    const dialog = screen.getByRole("dialog", { name: "Confirm Rerun Test" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));

    expect(mocks.rerunTest).toHaveBeenCalledWith({
      select_all: true,
      rerun_type: "call_and_eval",
      call_execution_ids: [],
    });
  });

  it("opens the SDK steps for a chat agent simulation, as before", () => {
    mocks.testData = AGENT;
    mocks.kpis = { total_calls: 4, agent_type: "text" };
    renderWithClient(<TestRunDetailHeader />);

    const rerun = screen.getByRole("button", { name: "Re-run simulation" });
    expect(rerun).not.toBeDisabled();

    fireEvent.click(rerun);
    expect(mocks.setSdkCodeOpen).toHaveBeenCalledWith(true);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

// A prompt simulation's run reports its calls as chats, and the re-run for
// selected chats scores evals again on the calls already made. It starts no
// calls and says so.
describe("Rerun test for selected calls of a prompt simulation", () => {
  it("offers evals only and sends an eval-only re-run", () => {
    renderWithClient(
      <RerunModal
        open
        onClose={vi.fn()}
        selectedNodes={["call-1", "call-2"]}
        selectAll={false}
        selectedCount={2}
        executionId="exec-1"
        agentType="text"
      />,
    );

    expect(
      screen.getByText("Only evals can be re-run for selected chats"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("radio", { name: "Run test + Evals" }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(mocks.rerunTest).toHaveBeenCalledWith({
      select_all: false,
      rerun_type: "eval_only",
      call_execution_ids: ["call-1", "call-2"],
    });
  });
});
