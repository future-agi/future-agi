import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import {
  fireEvent,
  renderWithRouter,
  waitFor,
  screen,
} from "src/utils/test-utils";
import EvalCreatePage from "./EvalCreatePage";

const EVAL_QUICK_START_QUERY =
  "quick_start_goal=evaluate_quality&quick_start_id=evals&quick_start_primary_path=evals";

const mocks = vi.hoisted(() => ({
  axiosGet: vi.fn(),
  axiosPost: vi.fn(),
  invalidateQueries: vi.fn(),
  recordActivationEvent: vi.fn(),
  testResult: null,
  testPlaygroundProps: null,
  runTest: vi.fn(),
  updateDraftMutate: vi.fn(),
  updateDraftMutateAsync: vi.fn(),
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

vi.mock("react-router", async () => {
  const actual = await vi.importActual("react-router");
  return {
    ...actual,
    useParams: () => {
      const match = window.location.pathname.match(
        /^\/dashboard\/evaluations\/create\/([^/]+)/,
      );
      return match ? { draftId: decodeURIComponent(match[1]) } : {};
    },
  };
});

vi.mock("src/utils/axios", () => ({
  default: {
    get: mocks.axiosGet,
    post: mocks.axiosPost,
  },
  endpoints: {
    develop: {
      eval: {
        createEvalTemplateV2: "/api/evals/templates/",
        evalPlayground: "/api/evals/playground/",
        getEvalDetail: (id) => `/api/evals/templates/${id}/`,
      },
    },
  },
}));

vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: false }),
}));

vi.mock("src/sections/onboarding-home/hooks/useRecordActivationEvent", () => ({
  useRecordActivationEvent: () => ({
    mutate: mocks.recordActivationEvent,
  }),
}));

vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: ({ rightPanel }) => (
    <div data-testid="resizable-panels">{rightPanel}</div>
  ),
}));

vi.mock("./TestPlayground", async () => {
  const React = await vi.importActual("react");
  const TestPlaygroundMock = React.forwardRef((props, ref) => {
    const { initialTraceId, onReadyChange, onTestResult } = props;
    mocks.testPlaygroundProps = props;
    React.useImperativeHandle(ref, () => ({
      runTest: (templateId) => {
        mocks.runTest(templateId);
        if (mocks.testResult) {
          onTestResult?.(true, mocks.testResult);
        }
      },
    }));
    React.useEffect(() => {
      onReadyChange?.(true);
      return () => onReadyChange?.(false);
    }, [onReadyChange]);
    return (
      <div
        data-testid="test-playground"
        data-initial-trace-id={initialTraceId || ""}
      />
    );
  });
  TestPlaygroundMock.displayName = "TestPlaygroundMock";
  TestPlaygroundMock.propTypes = {
    initialTraceId: () => null,
    onReadyChange: () => null,
    onTestResult: () => null,
  };

  return {
    default: TestPlaygroundMock,
  };
});

vi.mock("../hooks/useCompositeChildrenKeys", () => ({
  useCompositeChildrenUnionKeys: () => [],
}));

vi.mock("../hooks/useCreateEval", () => ({
  useCreateEval: () => ({ isLoading: false }),
}));

vi.mock("../hooks/useEvalDetail", () => ({
  useUpdateEval: () => ({
    isLoading: false,
    mutate: mocks.updateDraftMutate,
    mutateAsync: mocks.updateDraftMutateAsync,
  }),
}));

vi.mock("../hooks/useCompositeEval", () => ({
  useCreateCompositeEval: () => ({ isLoading: false }),
}));

describe("EvalCreatePage onboarding source handoff", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.testResult = null;
    mocks.testPlaygroundProps = null;
    mocks.axiosPost.mockImplementation((url) => {
      if (url === "/api/evals/playground/") {
        return Promise.resolve({
          data: {
            status: true,
            result: {
              log_id: "log-1",
              run_id: "provider-run-1",
              status: "completed",
            },
          },
        });
      }
      return Promise.resolve({
        data: { result: { id: "eval-draft-1" } },
      });
    });
    mocks.axiosGet.mockResolvedValue({
      data: { result: {} },
    });
    mocks.updateDraftMutateAsync.mockResolvedValue({});
  });

  it("auto-advances known trace-project sources to the scorer step", async () => {
    renderWithRouter(<EvalCreatePage />, {
      route:
        "/dashboard/evaluations/create?source=onboarding&step=data&source_type=trace_project&source_id=project-1&trace_id=trace-1&provider=anthropic&language=typescript",
    });

    expect(
      screen.queryByRole("button", { name: "Use trace project" }),
    ).not.toBeInTheDocument();

    await waitFor(() =>
      expect(window.location.pathname).toBe(
        "/dashboard/evaluations/create/eval-draft-1",
      ),
    );
    await waitFor(() =>
      expect(new URLSearchParams(window.location.search).get("step")).toBe(
        "scorer",
      ),
    );

    expect(new URLSearchParams(window.location.search).get("source_id")).toBe(
      "project-1",
    );
    expect(new URLSearchParams(window.location.search).get("trace_id")).toBe(
      "trace-1",
    );
    expect(new URLSearchParams(window.location.search).get("provider")).toBe(
      "anthropic",
    );
    expect(new URLSearchParams(window.location.search).get("language")).toBe(
      "typescript",
    );
    expect(mocks.recordActivationEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        eventName: "onboarding_eval_source_selected",
        artifactId: "project-1",
        artifactType: "observe_project",
        metadata: expect.objectContaining({
          source_id: "project-1",
          source_type: "trace_project",
          setup_language: "typescript",
          setup_provider: "anthropic",
          step: "data",
          surface: "tracing",
          trace_id: "trace-1",
        }),
      }),
    );
  });

  it("auto-saves the untouched trace starter scorer and opens the run step", async () => {
    renderWithRouter(<EvalCreatePage />, {
      route: `/dashboard/evaluations/create/eval-draft-1?source=onboarding&step=scorer&source_type=trace_project&source_id=project-1&trace_id=trace-1&provider=anthropic&language=typescript&${EVAL_QUICK_START_QUERY}`,
    });

    await waitFor(() =>
      expect(mocks.updateDraftMutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({
          code: expect.stringContaining("def evaluate("),
          code_language: "python",
          description: "Starter scorer for trace project.",
          eval_type: "code",
          name: "output-quality-project-1",
          output_type: "percentage",
          pass_threshold: 0.7,
          publish: true,
        }),
      ),
    );

    await waitFor(() =>
      expect(new URLSearchParams(window.location.search).get("step")).toBe(
        "run",
      ),
    );
    expect(window.location.pathname).toBe(
      "/dashboard/evaluations/create/eval-draft-1",
    );
    expect(new URLSearchParams(window.location.search).get("source_id")).toBe(
      "project-1",
    );
    expect(new URLSearchParams(window.location.search).get("trace_id")).toBe(
      "trace-1",
    );
    expect(
      new URLSearchParams(window.location.search).get("quick_start_id"),
    ).toBe("evals");
    expect(new URLSearchParams(window.location.search).get("provider")).toBe(
      "anthropic",
    );
    expect(new URLSearchParams(window.location.search).get("language")).toBe(
      "typescript",
    );
    expect(mocks.recordActivationEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        eventName: "eval_scorer_created",
        artifactId: "eval-draft-1",
        artifactType: "eval_scorer",
        quick_start_goal: "evaluate_quality",
        quick_start_id: "evals",
        quick_start_primary_path: "evals",
        metadata: expect.objectContaining({
          eval_id: "eval-draft-1",
          eval_type: "code",
          source_id: "project-1",
          source_type: "trace_project",
          setup_language: "typescript",
          setup_provider: "anthropic",
          step: "scorer",
          trace_id: "trace-1",
        }),
      }),
    );
  });

  it("records when an onboarding user starts the first eval run", async () => {
    renderWithRouter(<EvalCreatePage />, {
      route:
        "/dashboard/evaluations/create/eval-draft-1?source=onboarding&step=run&source_type=trace_project&source_id=project-1&trace_id=trace-1&provider=anthropic&language=typescript",
    });

    expect(
      await screen.findByText("Run Anthropic TypeScript quality check"),
    ).toBeVisible();
    expect(
      screen.queryByRole("tab", { name: "Composite" }),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("test-playground")).toHaveAttribute(
      "data-initial-trace-id",
      "trace-1",
    );
    expect(mocks.testPlaygroundProps).toMatchObject({
      initialTraceId: "trace-1",
      initialTraceProjectId: "project-1",
      initialTraceRowType: "Trace",
    });

    const runButton = await screen.findByRole("button", {
      name: "Run quality check",
    });
    await waitFor(() => expect(runButton).toBeEnabled());

    fireEvent.click(runButton);

    await waitFor(() =>
      expect(mocks.recordActivationEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          eventName: "onboarding_eval_run_clicked",
          artifactId: "eval-draft-1",
          artifactType: "eval",
          metadata: expect.objectContaining({
            eval_id: "eval-draft-1",
            eval_type: "agent",
            mode: "single",
            source_id: "project-1",
            source_type: "trace_project",
            setup_language: "typescript",
            setup_provider: "anthropic",
            step: "run",
            trace_id: "trace-1",
          }),
        }),
      ),
    );
    await waitFor(() =>
      expect(mocks.updateDraftMutateAsync).toHaveBeenCalled(),
    );
    expect(mocks.axiosPost).toHaveBeenCalledWith(
      "/api/evals/playground/",
      expect.objectContaining({
        template_id: "eval-draft-1",
        trace_id: "trace-1",
        config: {
          mapping: {
            output: "output",
          },
        },
      }),
    );
    expect(mocks.runTest).not.toHaveBeenCalled();
  });

  it("opens the first run review route with the returned usage log id", async () => {
    mocks.testResult = {
      log_id: "log-1",
      run_id: "provider-run-1",
      status: "completed",
    };

    renderWithRouter(<EvalCreatePage />, {
      route:
        "/dashboard/evaluations/create/eval-draft-1?source=onboarding&step=run&source_type=trace_project&source_id=project-1&trace_id=trace-1&provider=anthropic&language=typescript",
    });

    const runButton = await screen.findByRole("button", {
      name: "Run quality check",
    });
    await waitFor(() => expect(runButton).toBeEnabled());

    fireEvent.click(runButton);

    await waitFor(() => {
      expect(window.location.pathname).toBe(
        "/dashboard/evaluations/eval-draft-1",
      );
      const params = new URLSearchParams(window.location.search);
      expect(params.get("tab")).toBe("usage");
      expect(params.get("source")).toBe("onboarding");
      expect(params.get("step")).toBe("review");
      expect(params.get("run_id")).toBe("log-1");
      expect(params.get("source_type")).toBe("trace_project");
      expect(params.get("source_id")).toBe("project-1");
      expect(params.get("trace_id")).toBe("trace-1");
      expect(params.get("provider")).toBe("anthropic");
      expect(params.get("language")).toBe("typescript");
    });
    expect(mocks.recordActivationEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        eventName: "eval_run_completed",
        artifactId: "log-1",
        metadata: expect.objectContaining({
          eval_id: "eval-draft-1",
          log_id: "log-1",
          run_id: "log-1",
          source_id: "project-1",
          source_type: "trace_project",
          setup_language: "typescript",
          setup_provider: "anthropic",
          step: "run",
          trace_id: "trace-1",
        }),
      }),
    );
  });

  it("does not record the first-run click event for repair reruns", async () => {
    renderWithRouter(<EvalCreatePage />, {
      route:
        "/dashboard/evaluations/create/eval-draft-1?source=onboarding&step=run&source_type=trace_project&source_id=project-1&rerun_from=source_fix&previous_run_id=run-1",
    });

    const rerunButton = await screen.findByRole("button", {
      name: "Rerun quality check",
    });
    await waitFor(() => expect(rerunButton).toBeEnabled());

    fireEvent.click(rerunButton);

    await waitFor(() =>
      expect(mocks.updateDraftMutateAsync).toHaveBeenCalled(),
    );
    expect(mocks.recordActivationEvent).not.toHaveBeenCalledWith(
      expect.objectContaining({
        eventName: "onboarding_eval_run_clicked",
      }),
    );
    expect(mocks.runTest).toHaveBeenCalledWith("eval-draft-1");
  });
});
