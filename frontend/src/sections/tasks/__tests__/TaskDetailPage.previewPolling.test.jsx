/* eslint-disable react/prop-types -- Lightweight UI doubles around the real page and preview. */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TaskDetailPage from "../TaskDetailPage";
import { useGetTaskData } from "src/sections/common/EvalsTasks/common";

const mocks = vi.hoisted(() => {
  const rows = [
    { trace_id: "trace-a", input: "input A", output: "output A" },
    { trace_id: "trace-b", input: "input B", output: "output B" },
  ];
  return {
    rows,
    list: { rows, total: 2, columns: [] },
    execute: vi.fn(),
    patch: vi.fn(),
  };
});
const route = vi.hoisted(() => ({ taskId: "task-1" }));

vi.mock("react-router", async () => ({
  ...(await vi.importActual("react-router")),
  useParams: () => route,
  useNavigate: () => vi.fn(),
}));

// Preview list and row detail stay ready and stable, so only the task poll
// changes between renders. Mutations and the query client stay real.
vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual("@tanstack/react-query");
  return {
    ...actual,
    useQuery: ({ queryKey }) => ({
      data:
        queryKey[0] === "task-preview-list"
          ? mocks.list
          : queryKey[0] === "task-preview-detail"
            ? mocks.rows.find((row) => row.trace_id === queryKey[3])
            : undefined,
      isLoading: false,
      isFetching: false,
      isError: false,
    }),
  };
});

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), patch: mocks.patch },
  readQuery: vi.fn(),
  endpoints: {
    project: { patchEvalTask: () => "/tracer/eval-task/update_eval_task/" },
  },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin" }),
}));
vi.mock("src/sections/common/EvalsTasks/common", async () => ({
  ...(await vi.importActual("src/sections/common/EvalsTasks/common")),
  useGetTaskData: vi.fn(),
}));
vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: () => ({ isSuccess: true }),
}));
vi.mock("src/sections/evals/utils/evalExecution", () => ({
  executeEvalForRow: mocks.execute,
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/custom-popover", () => ({
  default: () => null,
  usePopover: () => ({ open: null, onOpen: vi.fn(), onClose: vi.fn() }),
}));
vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: ({ leftPanel, rightPanel }) => (
    <div>
      {leftPanel}
      {rightPanel}
    </div>
  ),
}));
vi.mock("src/sections/common/EvalsTasks/TaskLogsView", () => ({
  default: () => null,
}));
vi.mock("../components/TaskHeader", () => ({
  default: ({ status }) => <span data-testid="task-status">{status}</span>,
}));
vi.mock("../components/TaskConfigPanel", () => ({ default: () => null }));
vi.mock("../components/TaskUsageTab", () => ({ default: () => null }));
vi.mock("../components/DuplicateTaskDialog", () => ({ default: () => null }));
vi.mock("src/sections/common/EvalsTasks/EditTaskDrawer/TaskConfirmBox", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/DatasetTestMode", () => ({
  JsonValueTree: () => null,
}));
vi.mock("src/sections/evals/components/SpanRowList", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/EvalResultDisplay", () => ({
  default: ({ result }) => (
    <pre data-testid="eval-result">{JSON.stringify(result)}</pre>
  ),
}));

const EVAL = {
  id: "eval-1",
  name: "Preview evaluation",
  template_id: "template-1",
  mapping: { answer: "output" },
};
const SUCCESS = {
  ok: true,
  isComposite: false,
  raw: { score: 0.8, output: "slow answer", reason: "slow explanation" },
};
const NOW = new Date("2026-03-10T12:00:00Z");

// A continuous task keeps polling while it runs. Without saved dates, its
// form window is computed from the current time.
const loadedTask = (overrides = {}) => ({
  id: "task-1",
  name: "Running task",
  project_id: "project-1",
  status: "running",
  last_run: "2026-03-10T11:59:56Z",
  filters_applied: {},
  evals_applied: [EVAL],
  sampling_rate: 100,
  spans_limit: 100,
  run_type: "continuous",
  row_type: "traces",
  ...overrides,
});

function deferred() {
  let resolve;
  const promise = new Promise((finish) => {
    resolve = finish;
  });
  return { promise, resolve };
}

const renderTaskDetail = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const tree = () => (
    <QueryClientProvider client={queryClient}>
      <TaskDetailPage />
    </QueryClientProvider>
  );
  const view = render(tree());
  return {
    poll: (data) => {
      useGetTaskData.mockReturnValue({
        data,
        isLoading: false,
        isError: false,
      });
      view.rerender(tree());
    },
  };
};

// W11-TASKS-POLL-PREVIEW: a running task's operational poll (status, last run)
// must leave the live preview's row and pending test result alone.
describe("TaskDetailPage polling with the live preview", () => {
  beforeEach(() => {
    // Freeze only Date so the poll's own clock is explicit; timers stay real.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
    route.taskId = "task-1";
    mocks.execute.mockReset();
    mocks.patch.mockReset();
    useGetTaskData.mockReset();
    useGetTaskData.mockReturnValue({
      data: loadedTask(),
      isLoading: false,
      isError: false,
    });
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("keeps a slow preview result through a last_run-only poll", async () => {
    const pending = deferred();
    mocks.execute.mockReturnValueOnce(pending.promise);
    const { poll } = renderTaskDetail();

    const testButton = screen.getByRole("button", { name: /^test$/i });
    await waitFor(() => expect(testButton).toBeEnabled());
    fireEvent.click(testButton);
    expect(mocks.execute).toHaveBeenCalledWith(
      expect.objectContaining({ currentRow: mocks.rows[0] }),
    );
    expect(screen.getByText("Running eval…")).toBeInTheDocument();

    poll(loadedTask({ last_run: "2026-03-10T12:00:00Z" }));
    expect(screen.getByText("Running eval…")).toBeInTheDocument();

    await act(async () => {
      pending.resolve(SUCCESS);
    });
    const result = await screen.findByTestId("eval-result");
    expect(JSON.parse(result.textContent)).toEqual(SUCCESS.raw);
  });

  it("keeps the selected preview row when a running task polls later", () => {
    const { poll } = renderTaskDetail();
    fireEvent.click(screen.getByRole("button", { name: "Next row" }));
    // String values in the real row-detail table include JSON-style quotes.
    expect(screen.getByText('"input B"')).toBeInTheDocument();

    vi.setSystemTime(new Date(NOW.getTime() + 4000));
    poll(loadedTask({ status: "running", last_run: "2026-03-10T12:00:04Z" }));

    expect(screen.getByTestId("task-status")).toHaveTextContent("running");
    expect(screen.getByText('"input B"')).toBeInTheDocument();
    expect(screen.queryByText('"input A"')).not.toBeInTheDocument();
  });
});
