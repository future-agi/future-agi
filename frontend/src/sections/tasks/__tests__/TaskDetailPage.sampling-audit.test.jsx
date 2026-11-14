import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { expect, it, vi } from "vitest";
import TaskDetailPage from "src/sections/tasks/TaskDetailPage";
import { defaultSamplingPolicy } from "src/sections/tasks/components/samplingPolicyForm";

const state = vi.hoisted(() => ({ task: null, audit: null, confirm: null }));
const get = vi.hoisted(() => vi.fn());
const patch = vi.hoisted(() => vi.fn());
const notify = vi.hoisted(() => vi.fn());
vi.mock("src/utils/axios", () => ({
  default: { get, patch, post: vi.fn() },
  endpoints: {
    project: {
      samplingAudit: () => "/tracer/eval-task/sampling_audit/",
      patchEvalTask: () => "/tracer/eval-task/update_eval_task/",
      updateEvalTask: (id) => `/tracer/eval-task/${id}/`,
    },
  },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin" }),
}));
vi.mock("src/sections/common/EvalsTasks/common", async () => {
  const actual = await vi.importActual("src/sections/common/EvalsTasks/common");
  return {
    ...actual,
    useGetTaskData: () => ({
      data: state.task,
      isLoading: false,
      isError: false,
    }),
  };
});
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: notify }));
vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: ({ leftPanel, rightPanel }) => (
    <>
      {leftPanel}
      {rightPanel}
    </>
  ),
}));
vi.mock("src/sections/common/EvalsTasks/TaskLogsView", () => ({
  default: () => null,
}));
vi.mock("src/sections/tasks/components/TaskHeader", () => ({
  default: ({ actions }) => <>{actions}</>,
}));
vi.mock("src/sections/tasks/components/TaskConfigPanel", () => ({
  default: () => null,
}));
vi.mock("src/sections/tasks/components/TaskLivePreview", () => ({
  default: React.forwardRef(function MockTaskLivePreview() {
    return null;
  }),
}));
vi.mock("src/sections/tasks/components/TaskUsageTab", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/EvalsTasks/EditTaskDrawer/TaskConfirmBox", () => ({
  default: (props) => {
    state.confirm = props;
    return props.open ? <div>Review confirm rerun</div> : null;
  },
}));

const audit = (units) => ({
  cohort_id: `cohort-${units}`,
  policy_version: "v1",
  mode: "closed",
  sampling_unit: "session",
  coordination: "independent",
  frozen_at: "2026-10-22T10:00:00Z",
  strata: [],
  balance: [],
  notes: [],
  summary: {
    integrity: "ok",
    population_size: 20,
    achieved: units,
    kish_effective_n: units,
    design_effect: 1,
    weight_cv: 0,
    unrepresented_population: 0,
    unrepresented_strata: 0,
    flagged_strata: 0,
  },
});

it("refreshes the mounted sampling audit after a fresh rerun and on demand", async () => {
  state.task = {
    id: "task-1",
    name: "Review task",
    project_id: "project-1",
    project_name: "Review project",
    status: "completed",
    filters_applied: {},
    evals_applied: [{ id: "eval-1" }],
    sampling_rate: 100,
    spans_limit: 100,
    run_type: "continuous",
    row_type: "spans",
    sampling_policy: defaultSamplingPolicy(),
  };
  state.audit = audit(6);
  get.mockImplementation(async () => ({ data: { result: state.audit } }));
  patch.mockImplementation(async () => {
    state.audit = audit(12);
    return { data: { result: {} } };
  });
  const client = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        staleTime: 5000,
        refetchOnWindowFocus: true,
        refetchOnMount: true,
      },
      mutations: { retry: false },
    },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/dashboard/tasks/task-1"]}>
        <Routes>
          <Route path="/dashboard/tasks/:taskId" element={<TaskDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("tab", { name: /Sampling audit/ }));
  await screen.findByLabelText("Sampling audit");
  const sampledUnits = () =>
    within(screen.getByText("Sampled units").parentElement);
  expect(sampledUnits().getByText("6")).toBeVisible();
  expect(client.getQueryData(["samplingAudit", "task-1"]).cohort_id).toBe(
    "cohort-6",
  );
  fireEvent.click(screen.getByRole("button", { name: /^Re-run$/ }));
  await screen.findByText("Review confirm rerun");
  await act(async () => state.confirm.onConfirm("fresh_run"));
  await waitFor(() =>
    expect(notify).toHaveBeenCalledWith("Re-run started", {
      variant: "success",
    }),
  );
  expect(patch).toHaveBeenCalledWith(
    "/tracer/eval-task/update_eval_task/",
    expect.objectContaining({ edit_type: "fresh_run" }),
  );
  await waitFor(() =>
    expect(client.getQueryData(["samplingAudit", "task-1"]).cohort_id).toBe(
      "cohort-12",
    ),
  );
  expect(sampledUnits().getByText("12")).toBeVisible();
  expect(sampledUnits().queryByText("6")).not.toBeInTheDocument();
  expect(get).toHaveBeenCalledTimes(2);
  state.audit = audit(18);
  fireEvent.click(
    screen.getByRole("button", { name: "Refresh sampling audit" }),
  );
  await waitFor(() =>
    expect(client.getQueryData(["samplingAudit", "task-1"]).cohort_id).toBe(
      "cohort-18",
    ),
  );
  expect(sampledUnits().getByText("18")).toBeVisible();
  expect(get).toHaveBeenCalledTimes(3);
  client.clear();
});
