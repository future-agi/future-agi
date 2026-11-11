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

const state = vi.hoisted(() => ({
  task: null,
  audit: null,
  confirm: null,
  panel: null,
}));
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
  default: (props) => {
    state.panel = props;
    return null;
  },
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
    within(screen.getByText("Picked conversations").parentElement);
  expect(sampledUnits().getByText("6")).toBeVisible();
  expect(client.getQueryData(["samplingAudit", "task-1"]).cohort_id).toBe(
    "cohort-6",
  );
  fireEvent.click(screen.getByRole("button", { name: /^Re-run$/ }));
  await screen.findByText("Review confirm rerun");
  // Each option says what happens to the frozen picks, not "Select one".
  expect(state.confirm.content).toBe(
    "Choose what happens to the current sample.",
  );
  expect(state.confirm.options).toEqual({
    fresh_run: {
      description:
        "Removes the current results and evaluates the same picked conversations again.",
    },
    edit_rerun: {
      description:
        "Keeps the conversations already picked and their results, and evaluates only what is missing.",
    },
  });
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

const sampledTask = (overrides = {}) => ({
  id: "task-1",
  name: "Review task",
  project_id: "project-1",
  project_name: "Review project",
  status: "completed",
  // A saved attribute filter: an untouched form must still compare equal.
  filters_applied: {
    project_id: "project-1",
    filters: [
      {
        column_id: "region",
        filter_config: {
          filter_type: "text",
          filter_op: "equals",
          filter_value: "Europe",
          col_type: "SPAN_ATTRIBUTE",
        },
      },
    ],
  },
  evals_applied: [{ id: "eval-1" }],
  sampling_rate: 100,
  spans_limit: 100,
  run_type: "historical",
  row_type: "spans",
  sampling_policy: {
    ...defaultSamplingPolicy(),
    default: { target_n: 10 },
  },
  ...overrides,
});

const renderPage = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  get.mockImplementation(async () => ({ data: { result: audit(6) } }));
  state.panel = null;
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/dashboard/tasks/task-1"]}>
        <Routes>
          <Route path="/dashboard/tasks/:taskId" element={<TaskDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return client;
};
const openSave = async () => {
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Review confirm rerun");
};

it("offers Edit & Re-run for an untouched sampled task with saved filters", async () => {
  state.task = sampledTask();
  const client = renderPage();
  await openSave();
  expect(state.confirm.options.edit_rerun.disabled).toBeUndefined();
  expect(state.confirm.content).toBe(
    "Choose what happens to the current sample.",
  );
  client.clear();
});

it("disables Edit & Re-run when only the filters changed, as the API refuses it", async () => {
  state.task = sampledTask();
  const client = renderPage();
  await waitFor(() => expect(state.panel).not.toBeNull());
  act(() =>
    state.panel.setValue(
      "filters",
      [
        ...state.panel.getValues("filters"),
        { property: "session_id", filterConfig: { filterValue: "s-42" } },
      ],
      { shouldDirty: true },
    ),
  );
  await openSave();
  expect(state.confirm.content).toBe(
    "You changed the filters, so the current sample no longer applies.",
  );
  expect(state.confirm.options).toEqual({
    fresh_run: {
      description:
        "Removes the current results and picks a new sample with your changes.",
    },
    edit_rerun: {
      disabled: true,
      description: "Not available: you changed the filters.",
    },
  });
  await act(async () => state.confirm.onConfirm("fresh_run"));
  expect(patch).toHaveBeenLastCalledWith(
    "/tracer/eval-task/update_eval_task/",
    expect.objectContaining({
      edit_type: "fresh_run",
      filters: expect.objectContaining({ session_id: ["s-42"] }),
    }),
  );
  client.clear();
});

it("disables Edit & Re-run when the sampling changed", async () => {
  state.task = sampledTask();
  const client = renderPage();
  await waitFor(() => expect(state.panel).not.toBeNull());
  act(() =>
    state.panel.setValue(
      "samplingPolicy",
      { ...state.task.sampling_policy, default: { target_n: 20 } },
      { shouldDirty: true },
    ),
  );
  await openSave();
  expect(state.confirm.options.edit_rerun).toEqual({
    disabled: true,
    description: "Not available: you changed the sampling.",
  });
  client.clear();
});
