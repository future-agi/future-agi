import React from "react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "src/utils/test-utils";
import axios from "src/utils/axios";
import TaskCreatePage from "../TaskCreatePage";
import TaskSamplingSection from "../components/task_sampling_section";
import TaskSchedulingSection from "../components/TaskSchedulingSection";
import { defaultSamplingPolicy } from "../components/samplingPolicyForm";

const harness = vi.hoisted(() => ({
  navigate: vi.fn(),
  saveDraft: vi.fn(),
  clearDraft: vi.fn(),
  initialValues: null,
  selection: false,
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
vi.mock("../components/TaskConfigPanel", () => ({
  default: ({ control, getValues }) =>
    harness.selection ? (
      <>
        <TaskSchedulingSection control={control} />
        <TaskSamplingSection control={control} getValues={getValues} />
      </>
    ) : null,
}));
vi.mock("../components/TaskLivePreview", async () => {
  const ReactModule = await import("react");
  return { default: ReactModule.forwardRef(() => null) };
});
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));

describe("Task Create mixed catalog filter submission", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    harness.selection = false;
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

  it("creates a valid continuous task after live → row percentage → Historical data → retained live restore", async () => {
    harness.selection = true;
    const live = {
      ...defaultSamplingPolicy(),
      mode: "live",
      sampling_unit: "trace",
      coordination: "project",
      panel: "retained-live",
      default: { rate: 37.25 },
      window: { start: "2026-10-20T00:00:00.000Z", end: null },
    };
    harness.initialValues = {
      ...harness.initialValues,
      runType: "continuous",
      samplingPolicy: live,
    };
    render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { mutations: { retry: false } } })
        }
      >
        <TaskCreatePage />
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    const closed = () =>
      waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
    await user.click(
      screen.getByRole("radio", { name: "A percentage of matching traces" }),
    );
    await user.click(
      screen.getByRole("button", { name: "Use row percentage" }),
    );
    await closed();
    await user.click(screen.getByRole("radio", { name: "Historical data" }));
    await user.click(
      screen.getByRole("radio", { name: "Pick whole groups of traces" }),
    );
    expect(screen.getByRole("dialog")).toHaveTextContent("New incoming data");
    await user.click(
      screen.getByRole("button", { name: "Restore group picking" }),
    );
    await closed();
    await user.click(screen.getByRole("button", { name: "Create task" }));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    const payload = axios.post.mock.calls[0][1];
    expect(payload.sampling_policy).toEqual(live);
    expect(payload.run_type).toBe("continuous");
    expect(
      payload.sampling_policy.mode === "live" &&
        payload.run_type !== "continuous",
    ).toBe(false);
    expect(payload.filters.filters).toHaveLength(4);
    expect(payload.filters.date_range).toBeUndefined();
  });

  it.each([
    [50, 100],
    [10, 100000],
    [100, 275],
  ])(
    "submits %i%% with legacy limit %i and every mixed filter unchanged",
    async (rate, limit) => {
      harness.initialValues.samplingRate = rate;
      harness.initialValues.spansLimit = limit;
      const user = userEvent.setup();
      const queryClient = new QueryClient({
        defaultOptions: { mutations: { retry: false } },
      });
      render(
        <QueryClientProvider client={queryClient}>
          <TaskCreatePage />
        </QueryClientProvider>,
      );

      await user.click(screen.getByRole("button", { name: "Create task" }));

      await waitFor(() => {
        expect(axios.post).toHaveBeenCalledWith(
          "/tracer/eval-task/",
          expect.objectContaining({
            name: "Mixed catalog task",
            project: "project-1",
            evals: ["eval-config-1"],
            run_type: "historical",
            row_type: "traces",
            spans_limit: limit,
            sampling_rate: rate,
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
    },
  );
});
