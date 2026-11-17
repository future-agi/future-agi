import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";
import TaskLogsView from "../TaskLogsView";

const mocks = vi.hoisted(() => ({
  readEvalTaskLogs: vi.fn(),
}));

vi.mock("../task_log_read", async (importOriginal) => ({
  ...(await importOriginal()),
  readEvalTaskLogs: mocks.readEvalTaskLogs,
}));

vi.mock("src/components/iconify", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => <span data-testid="iconify" />,
}));

const NO_OUTPUT =
  "This trace has no output, which the evaluator needs as 'output'.";

const summary = (overrides = {}) => ({
  success_count: 2,
  errors_count: 0,
  skipped_count: 0,
  warnings_count: 0,
  total_count: 2,
  target_count: 2,
  error_groups: [],
  skipped_groups: [],
  warning_groups: [],
  error_groups_truncated: false,
  warning_groups_truncated: false,
  status: "completed",
  run_type: "historical",
  row_type: "traces",
  start_time: null,
  end_time: null,
  ...overrides,
});

function renderView() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <TaskLogsView evalTaskId="task-1" taskStatus="completed" />
    </QueryClientProvider>,
  );
}

// StatCard renders the value and its label as siblings in one box.
const statCardText = (label) =>
  screen.queryByText(label)?.parentElement.textContent;

describe("TaskLogsView skipped runs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("names the empty trace field instead of an unknown error", async () => {
    mocks.readEvalTaskLogs.mockResolvedValue(
      summary({
        skipped_count: 1,
        total_count: 3,
        target_count: 3,
        skipped_groups: [{ reason: NO_OUTPUT, count: 1 }],
      }),
    );

    renderView();

    await screen.findByText("Total Traces");
    expect(screen.queryByText(NO_OUTPUT)).toBeInTheDocument();
    expect(screen.queryByText("1 run")).toBeInTheDocument();
    expect(screen.queryByText("Unknown error")).not.toBeInTheDocument();
    expect(screen.queryByText(/auto-classify/)).not.toBeInTheDocument();
    expect(
      screen.queryByText("All evaluations completed successfully"),
    ).not.toBeInTheDocument();
  });

  it("counts skipped runs apart from errors", async () => {
    mocks.readEvalTaskLogs.mockResolvedValue(
      summary({
        skipped_count: 1,
        total_count: 3,
        target_count: 3,
        skipped_groups: [{ reason: NO_OUTPUT, count: 1 }],
      }),
    );

    renderView();

    await screen.findByText("Total Traces");
    expect(statCardText("Skipped")).toBe("1Skipped");
    expect(statCardText("Errors")).toBe("0Errors");
    expect(
      screen.getByText("2 / 3 evaluation runs completed without errors"),
    ).toBeInTheDocument();
  });

  it("says in a sentence which field a skipped span was missing", async () => {
    mocks.readEvalTaskLogs.mockResolvedValue(
      summary({
        row_type: "spans",
        skipped_count: 2,
        total_count: 4,
        target_count: 4,
        skipped_groups: [
          { reason: "missing_required_attribute: input", count: 2 },
        ],
      }),
    );

    renderView();

    await screen.findByText("Total Spans");
    expect(
      screen.queryByText("This span has no input, which the evaluator needs."),
    ).toBeInTheDocument();
    expect(screen.queryByText("2 runs")).toBeInTheDocument();
    expect(
      screen.queryByText("missing_required_attribute: input"),
    ).not.toBeInTheDocument();
  });

  it("keeps an evaluator failure under its classified title", async () => {
    mocks.readEvalTaskLogs.mockResolvedValue(
      summary({
        success_count: 1,
        errors_count: 1,
        error_groups: [
          {
            normalized: "Rate limit exceeded (429)",
            count: 1,
            sample: "Error during evaluation: Rate limit exceeded (429)",
          },
        ],
      }),
    );

    renderView();

    expect(
      await screen.findByText("Rate-limited by model provider"),
    ).toBeInTheDocument();
    expect(screen.getByText("Rate limit exceeded (429)")).toBeInTheDocument();
    expect(screen.queryByText("Skipped")).not.toBeInTheDocument();
  });

  it("says all completed when nothing was skipped or failed", async () => {
    mocks.readEvalTaskLogs.mockResolvedValue(summary());

    renderView();

    expect(
      await screen.findByText("All evaluations completed successfully"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Skipped")).not.toBeInTheDocument();
  });
});
