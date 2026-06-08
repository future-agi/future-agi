import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
} from "src/utils/test-utils";
import EvalUsageTab from "./EvalUsageTab";
import {
  EVAL_REVIEW_RUN_POLL_INTERVAL_MS,
  shouldPollEvalOnboardingReviewRun,
} from "./evalUsageOnboarding";

const mocks = vi.hoisted(() => ({
  invalidateQueries: vi.fn(),
  logsData: null,
  recordActivationEvent: vi.fn(),
  useEvalUsageChart: vi.fn(),
  useEvalUsageLogs: vi.fn(),
}));

vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual("@tanstack/react-query");
  return {
    ...actual,
    useQueryClient: () => ({
      invalidateQueries: mocks.invalidateQueries,
    }),
  };
});

vi.mock("@monaco-editor/react", () => ({
  default: () => null,
}));

vi.mock("src/components/data-table", async () => {
  const ReactActual = await vi.importActual("react");
  return {
    DataTable: ({ data = [], emptyMessage, onRowClick }) =>
      ReactActual.createElement(
        "div",
        { "data-testid": "eval-usage-table" },
        data.length
          ? data.map((row) =>
              ReactActual.createElement(
                "button",
                {
                  key: row.id,
                  type: "button",
                  onClick: () => onRowClick?.(row),
                },
                row.id,
              ),
            )
          : ReactActual.createElement("span", null, emptyMessage),
      ),
    DataTablePagination: () => null,
  };
});

vi.mock("src/components/FormSearchField/FormSearchField", async () => {
  const ReactActual = await vi.importActual("react");
  return {
    default: ({ onChange, placeholder, value }) =>
      ReactActual.createElement("input", {
        "aria-label": placeholder,
        onChange,
        placeholder,
        value,
      }),
  };
});

vi.mock("src/components/iconify", async () => {
  const ReactActual = await vi.importActual("react");
  return {
    default: ({ icon }) =>
      ReactActual.createElement("span", { "data-icon": icon }),
  };
});

vi.mock("src/sections/common/EvalsTasks/PartialInputWarningDetails", () => ({
  default: () => null,
  PARTIAL_INPUT_WARNING_TYPE: "partial_input",
}));

vi.mock(
  "src/sections/evals/EvalDetails/EvalsFeedback/AddEvalsFeedbackDrawer",
  () => ({
    default: () => null,
  }),
);

vi.mock("src/sections/projects/DateTimeRangePicker", () => ({
  default: () => null,
}));

vi.mock("./UsageChart", () => ({
  default: () => null,
}));

vi.mock("../hooks/useEvalUsage", () => ({
  useEvalUsageChart: mocks.useEvalUsageChart,
  useEvalUsageLogs: mocks.useEvalUsageLogs,
}));

vi.mock("src/sections/onboarding-home/hooks/useRecordActivationEvent", () => ({
  useRecordActivationEvent: () => ({
    mutate: mocks.recordActivationEvent,
  }),
}));

const reviewRoute =
  "/dashboard/evaluations/eval-1?tab=usage&source=onboarding&step=review&run_id=run-1&source_type=trace_project&source_id=project-1&trace_id=trace-1&provider=anthropic&language=python";

const matchingUsageLog = {
  id: "usage-log-1",
  run_id: "run-1",
  score: 0.2,
  result: "Failed",
  reason: "Missing output from first eval run",
  status: "success",
  source: "eval_playground",
  input: "Review the first run",
  created_at: "2026-05-30T10:00:00.000Z",
  detail: {
    run_id: "run-1",
  },
};

const passedUsageLog = {
  ...matchingUsageLog,
  id: "usage-log-pass-1",
  reason: "Output exists and is ready for review.",
  result: "Passed",
  score: 0.95,
};

const pendingUsageLog = {
  ...matchingUsageLog,
  id: "usage-log-pending-1",
  result: "",
  score: null,
  status: "running",
};

const mismatchedUsageLog = {
  ...passedUsageLog,
  id: "usage-log-pass-2",
  run_id: "run-2",
  detail: {
    run_id: "run-2",
  },
};

const renderUsageTab = ({ onReviewComplete } = {}) =>
  renderWithRouter(
    <EvalUsageTab
      templateId="eval-1"
      evalType="code"
      outputType="pass_fail"
      onReviewComplete={onReviewComplete}
    />,
    { route: reviewRoute },
  );

beforeEach(() => {
  vi.clearAllMocks();
  mocks.logsData = { items: [], total: 0 };
  mocks.useEvalUsageChart.mockReturnValue({
    data: { chart: [], stats: {} },
    isLoading: false,
  });
  mocks.useEvalUsageLogs.mockImplementation(() => ({
    data: mocks.logsData,
    isFetching: false,
    isLoading: false,
  }));
});

describe("shouldPollEvalOnboardingReviewRun", () => {
  it("polls only while an onboarding review run has not opened", () => {
    expect(
      shouldPollEvalOnboardingReviewRun({
        isOnboarding: true,
        runId: "run-1",
        step: "review",
      }),
    ).toBe(true);

    expect(
      shouldPollEvalOnboardingReviewRun({
        autoOpenedRunId: "run-1",
        isOnboarding: true,
        runId: "run-1",
        step: "review",
      }),
    ).toBe(false);
  });

  it("does not poll outside review onboarding", () => {
    expect(
      shouldPollEvalOnboardingReviewRun({
        isOnboarding: false,
        runId: "run-1",
        step: "review",
      }),
    ).toBe(false);
    expect(
      shouldPollEvalOnboardingReviewRun({
        isOnboarding: true,
        runId: "run-1",
        step: "run",
      }),
    ).toBe(false);
    expect(
      shouldPollEvalOnboardingReviewRun({
        isOnboarding: true,
        step: "review",
      }),
    ).toBe(false);
  });

  it("stops polling after the target run enters recovery", () => {
    expect(
      shouldPollEvalOnboardingReviewRun({
        isOnboarding: true,
        recoveryRunId: "run-1",
        runId: "run-1",
        step: "review",
      }),
    ).toBe(false);

    expect(
      shouldPollEvalOnboardingReviewRun({
        isOnboarding: true,
        recoveryRunId: "run-2",
        runId: "run-1",
        step: "review",
      }),
    ).toBe(true);
  });
});

describe("EvalUsageTab onboarding review run recovery", () => {
  it("waits on an empty first response, then opens the matching usage row", async () => {
    const { rerender } = renderUsageTab();

    expect(screen.getByTestId("eval-review-run-waiting")).toHaveTextContent(
      "Opening first eval result",
    );
    expect(mocks.useEvalUsageLogs).toHaveBeenCalledWith(
      "eval-1",
      expect.objectContaining({
        refetchInterval: EVAL_REVIEW_RUN_POLL_INTERVAL_MS,
      }),
    );

    mocks.logsData = { items: [matchingUsageLog], total: 1 };
    rerender(
      <EvalUsageTab
        templateId="eval-1"
        evalType="code"
        outputType="pass_fail"
      />,
    );

    await waitFor(() =>
      expect(
        screen.queryByTestId("eval-review-run-waiting"),
      ).not.toBeInTheDocument(),
    );
    expect(
      await screen.findByText("Missing output from first eval run"),
    ).toBeInTheDocument();
    expect(mocks.useEvalUsageLogs).toHaveBeenLastCalledWith(
      "eval-1",
      expect.objectContaining({ refetchInterval: false }),
    );
    await waitFor(() =>
      expect(mocks.recordActivationEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          artifactId: "run-1",
          eventName: "eval_failures_reviewed",
          metadata: expect.objectContaining({
            eval_id: "eval-1",
            eval_log_id: "usage-log-1",
            review_outcome: "failure_reviewed",
            run_id: "run-1",
            setup_language: "python",
            setup_provider: "anthropic",
            source_id: "project-1",
            source_type: "trace_project",
            trace_id: "trace-1",
          }),
        }),
      ),
    );
    expect(await screen.findByText("Next action")).toBeInTheDocument();
    expect(screen.getByText("Fix trace source")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Review the traces that produced this result, adjust the source behavior, then rerun the quality check.",
      ),
    ).toBeInTheDocument();
  });

  it("continues to Home after a healthy first trace evaluator result", async () => {
    const onReviewComplete = vi.fn();
    mocks.logsData = { items: [passedUsageLog], total: 1 };

    renderUsageTab({ onReviewComplete });

    expect(
      await screen.findByText("Output exists and is ready for review."),
    ).toBeInTheDocument();
    expect(await screen.findByText("Next action")).toBeInTheDocument();
    const continueButton = screen.getByRole("button", {
      name: "Continue to Home",
    });
    expect(continueButton).toBeInTheDocument();
    expect(screen.queryByText("Tune scorer")).not.toBeInTheDocument();

    fireEvent.click(continueButton);

    expect(onReviewComplete).toHaveBeenCalledWith({
      row: expect.objectContaining({ id: "usage-log-pass-1" }),
    });
    await waitFor(() =>
      expect(mocks.recordActivationEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          eventName: "eval_failures_reviewed",
          metadata: expect.objectContaining({
            review_outcome: "result_summary_reviewed",
            setup_language: "python",
            setup_provider: "anthropic",
            trace_id: "trace-1",
          }),
        }),
      ),
    );
  });

  it("does not show onboarding review actions for a mismatched usage row", async () => {
    const onReviewComplete = vi.fn();
    mocks.logsData = { items: [mismatchedUsageLog], total: 1 };

    renderUsageTab({ onReviewComplete });

    fireEvent.click(
      await screen.findByRole("button", { name: "usage-log-pass-2" }),
    );

    expect(
      await screen.findByText("Output exists and is ready for review."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Next action")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Continue to Home" }),
    ).not.toBeInTheDocument();
    expect(onReviewComplete).not.toHaveBeenCalled();
    expect(mocks.recordActivationEvent).not.toHaveBeenCalledWith(
      expect.objectContaining({
        eventName: "eval_failures_reviewed",
      }),
    );
  });

  it("does not offer completion while the target run is still pending", async () => {
    mocks.logsData = { items: [pendingUsageLog], total: 1 };

    renderUsageTab({ onReviewComplete: vi.fn() });

    expect(await screen.findByText("Status")).toBeInTheDocument();
    expect(screen.queryByText("Next action")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Continue to Home" }),
    ).not.toBeInTheDocument();
  });
});
