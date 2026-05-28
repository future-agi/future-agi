import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderWithRouter, waitFor, screen } from "src/utils/test-utils";
import EvalCreatePage from "./EvalCreatePage";

const mocks = vi.hoisted(() => ({
  axiosGet: vi.fn(),
  axiosPost: vi.fn(),
  invalidateQueries: vi.fn(),
  recordActivationEvent: vi.fn(),
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

vi.mock("src/utils/axios", () => ({
  default: {
    get: mocks.axiosGet,
    post: mocks.axiosPost,
  },
  endpoints: {
    develop: {
      eval: {
        createEvalTemplateV2: "/api/evals/templates/",
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
  default: () => <div data-testid="resizable-panels" />,
}));

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
    mocks.axiosPost.mockResolvedValue({
      data: { result: { id: "eval-draft-1" } },
    });
    mocks.axiosGet.mockResolvedValue({
      data: { result: {} },
    });
    mocks.updateDraftMutateAsync.mockResolvedValue({});
  });

  it("auto-advances known trace-project sources to the scorer step", async () => {
    renderWithRouter(<EvalCreatePage />, {
      route:
        "/dashboard/evaluations/create?source=onboarding&step=data&source_type=trace_project&source_id=project-1",
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
    expect(mocks.recordActivationEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        eventName: "onboarding_eval_source_selected",
        artifactId: "project-1",
        artifactType: "observe_project",
        metadata: expect.objectContaining({
          source_id: "project-1",
          source_type: "trace_project",
          step: "data",
          surface: "tracing",
        }),
      }),
    );
  });

  it("auto-saves the untouched trace starter scorer and opens the run step", async () => {
    renderWithRouter(<EvalCreatePage />, {
      route:
        "/dashboard/evaluations/create/eval-draft-1?source=onboarding&step=scorer&source_type=trace_project&source_id=project-1",
    });

    await waitFor(() =>
      expect(mocks.updateDraftMutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({
          code: expect.stringContaining("def evaluate("),
          code_language: "python",
          description: "Starter scorer for trace project onboarding.",
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
    expect(mocks.recordActivationEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        eventName: "eval_scorer_created",
        artifactId: "eval-draft-1",
        artifactType: "eval_scorer",
        metadata: expect.objectContaining({
          eval_id: "eval-draft-1",
          eval_type: "code",
          source_id: "project-1",
          source_type: "trace_project",
          step: "scorer",
        }),
      }),
    );
  });
});
