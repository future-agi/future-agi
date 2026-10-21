import React from "react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "src/utils/test-utils";
import axios from "src/utils/axios";
import TaskCreatePage from "../TaskCreatePage";

const harness = vi.hoisted(() => ({
  navigate: vi.fn(),
  saveDraft: vi.fn(),
  clearDraft: vi.fn(),
  initialValues: null,
}));

const mixedTaskFilters = [
  {
    id: "system",
    property: "status",
    propertyId: "status",
    fieldCategory: "system",
    apiColType: "SYSTEM_METRIC",
    filterConfig: {
      filterType: "text",
      filterOp: "equals",
      filterValue: "OK",
    },
  },
  {
    id: "eval",
    property: "quality_eval",
    propertyId: "quality_eval",
    registryId: "eval:quality-eval",
    fieldCategory: "eval",
    apiColType: "EVAL_METRIC",
    filterConfig: {
      filterType: "number",
      filterOp: "greater_than",
      filterValue: 0.8,
    },
  },
  {
    id: "annotation",
    property: "quality_label",
    propertyId: "quality_label",
    registryId: "annotation:quality-label",
    fieldCategory: "annotation",
    apiColType: "ANNOTATION",
    filterConfig: {
      filterType: "categorical",
      filterOp: "in",
      filterValue: ["approved"],
    },
  },
  {
    id: "attribute",
    property: "attributes",
    propertyId: "customer.tier",
    registryId: "custom_attribute:customer.tier",
    fieldCategory: "attribute",
    apiColType: "SPAN_ATTRIBUTE",
    filterConfig: {
      filterType: "text",
      filterOp: "is_not_null",
    },
  },
];

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin" }),
}));

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => harness.navigate,
}));

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useSearchParams: () => [new URLSearchParams()],
}));

vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn() },
  endpoints: {
    project: { createEvalTask: () => "/tracer/eval-task/" },
  },
}));

vi.mock("../hooks/useTaskDraft", () => ({
  useTaskDraft: () => ({
    initialValues: harness.initialValues,
    save: harness.saveDraft,
    clear: harness.clearDraft,
  }),
}));

vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: ({ leftPanel, rightPanel }) => (
    <div>
      {leftPanel}
      {rightPanel}
    </div>
  ),
}));

vi.mock("../components/TaskHeader", () => ({ default: () => null }));
vi.mock("../components/TaskConfigPanel", () => ({ default: () => null }));
vi.mock("../components/TaskLivePreview", async () => {
  const ReactModule = await import("react");
  return { default: ReactModule.forwardRef(() => null) };
});
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));

describe("Task Create mixed catalog filter submission", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    harness.initialValues = {
      name: "Mixed catalog task",
      project: "project-1",
      rowType: "traces",
      filters: mixedTaskFilters,
      spansLimit: 100,
      samplingRate: 50,
      evalsDetails: [{ id: "eval-config-1" }],
      startDate: "2026-08-01T00:00:00.000Z",
      endDate: "2026-08-25T00:00:00.000Z",
      runType: "historical",
    };
    axios.post.mockResolvedValue({ data: { result: { id: "task-1" } } });
  });

  it("submits system, eval, annotation, and valueless attribute filters unchanged", async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <TaskCreatePage />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Create Task" }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/tracer/eval-task/",
        expect.objectContaining({
          name: "Mixed catalog task",
          project: "project-1",
          evals: ["eval-config-1"],
          run_type: "historical",
          row_type: "traces",
          spans_limit: 100,
          sampling_rate: 50,
          filters: {
            project_id: "project-1",
            date_preset: "custom",
            date_range: [
              "2026-08-01T00:00:00.000Z",
              "2026-08-25T00:00:00.000Z",
            ],
            filters: [
              {
                column_id: "status",
                filter_config: {
                  filter_type: "text",
                  filter_op: "equals",
                  filter_value: "OK",
                  col_type: "SYSTEM_METRIC",
                },
              },
              {
                column_id: "quality_eval",
                property_id: "eval:quality-eval",
                filter_config: {
                  filter_type: "number",
                  filter_op: "greater_than",
                  filter_value: 0.8,
                  col_type: "EVAL_METRIC",
                },
              },
              {
                column_id: "quality_label",
                property_id: "annotation:quality-label",
                filter_config: {
                  filter_type: "categorical",
                  filter_op: "in",
                  filter_value: ["approved"],
                  col_type: "ANNOTATION",
                },
              },
              {
                column_id: "customer.tier",
                property_id: "custom_attribute:customer.tier",
                filter_config: {
                  filter_type: "text",
                  filter_op: "is_not_null",
                  filter_value: null,
                  col_type: "SPAN_ATTRIBUTE",
                },
              },
            ],
          },
        }),
      );
    });
  });
});
