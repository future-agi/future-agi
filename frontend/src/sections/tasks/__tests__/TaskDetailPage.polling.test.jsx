/* eslint-disable react/prop-types -- Lightweight UI doubles exercise the real page and form. */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { useFieldArray, useWatch } from "react-hook-form";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TaskDetailPage from "../TaskDetailPage";
import { useGetTaskData } from "src/sections/common/EvalsTasks/common";

const axiosPatchMock = vi.hoisted(() => vi.fn());
const confirmDialogMock = vi.hoisted(() => vi.fn());
const route = vi.hoisted(() => ({ taskId: "task-1" }));

vi.mock("react-router", async () => ({
  ...(await vi.importActual("react-router")),
  useParams: () => route,
  useNavigate: () => vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { patch: axiosPatchMock },
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

vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/custom-popover", () => ({
  default: () => null,
  usePopover: () => ({ open: null, onOpen: vi.fn(), onClose: vi.fn() }),
}));
vi.mock("src/components/tooltip/CustomTooltip", () => ({
  default: ({ children }) => children,
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
  default: ({ name, status }) => (
    <div>
      <span>{name}</span>
      <span data-testid="task-status">{status}</span>
    </div>
  ),
}));
vi.mock("../components/TaskConfigPanel", () => {
  const MockTaskConfigPanel = ({ control, setValue }) => {
    const values = useWatch({ control });
    const { replace } = useFieldArray({
      control,
      name: "evalsDetails",
      keyName: "_fieldId",
    });
    return (
      <div>
        <input
          aria-label="Sampling rate"
          type="number"
          {...control.register("samplingRate", { valueAsNumber: true })}
        />
        <button
          type="button"
          onClick={() =>
            setValue(
              "filters",
              [
                {
                  property: "trace_id",
                  filterConfig: {
                    filterOp: "equals",
                    filterValue: "trace-edited",
                  },
                },
              ],
              { shouldDirty: true },
            )
          }
        >
          Edit filters
        </button>
        <button type="button" onClick={() => replace([{ id: "eval-edited" }])}>
          Replace evaluations
        </button>
        <button type="button" onClick={() => replace([])}>
          Clear evaluations
        </button>
        <button
          type="button"
          onClick={() => replace([{ id: "eval-1" }, { id: "eval-2" }])}
        >
          Restore evaluations
        </button>
        <output data-testid="form-values">{JSON.stringify(values)}</output>
      </div>
    );
  };
  return { default: MockTaskConfigPanel };
});
vi.mock("../components/TaskLivePreview", () => {
  const MockTaskLivePreview = React.forwardRef(() => null);
  MockTaskLivePreview.displayName = "MockTaskLivePreview";
  return { default: MockTaskLivePreview };
});
vi.mock("../components/TaskUsageTab", () => ({ default: () => null }));
vi.mock("../components/DuplicateTaskDialog", () => ({ default: () => null }));
vi.mock("src/sections/common/EvalsTasks/EditTaskDrawer/TaskConfirmBox", () => ({
  default: (props) => {
    confirmDialogMock(props);
    return props.open ? <div>{props.title}</div> : null;
  },
}));

const loadedTask = (overrides = {}) => ({
  id: "task-1",
  name: "Running task",
  project_id: "project-1",
  status: "running",
  last_run: "2026-01-01T00:00:00Z",
  filters_applied: { trace_id: ["trace-saved"] },
  evals_applied: [{ id: "eval-1" }, { id: "eval-2" }],
  sampling_rate: 50,
  spans_limit: 100,
  run_type: "continuous",
  row_type: "spans",
  ...overrides,
});

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
    refresh: (data) => {
      useGetTaskData.mockReturnValue({
        data,
        isLoading: false,
        isError: false,
      });
      view.rerender(tree());
    },
  };
};

const readForm = () =>
  JSON.parse(screen.getByTestId("form-values").textContent);
const editConfiguration = () => {
  fireEvent.change(screen.getByRole("spinbutton", { name: "Sampling rate" }), {
    target: { value: "10" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Edit filters" }));
  fireEvent.click(screen.getByRole("button", { name: "Replace evaluations" }));
};
const confirmSave = async () => {
  await act(async () => {
    confirmDialogMock.mock.calls.at(-1)[0].onConfirm("edit");
  });
};
// Save stays disabled while a task runs (the server refuses that update), so
// a test that saves starts from a status Save accepts.
const startAs = (status) =>
  useGetTaskData.mockReturnValue({
    data: loadedTask({ status }),
    isLoading: false,
    isError: false,
  });

// W10-SETTINGS-TASKS-L03: keep the same mounted form through each poll.
describe("TaskDetailPage polling preserves edits", () => {
  beforeEach(() => {
    route.taskId = "task-1";
    axiosPatchMock.mockReset();
    axiosPatchMock.mockResolvedValue({ data: { result: {} } });
    confirmDialogMock.mockReset();
    useGetTaskData.mockReset();
    useGetTaskData.mockReturnValue({
      data: loadedTask(),
      isLoading: false,
      isError: false,
    });
  });

  it("preserves sampling, filter and evaluation edits on a last_run-only poll", () => {
    const { refresh } = renderTaskDetail();
    expect(readForm().samplingRate).toBe(50);
    editConfiguration();
    const { samplingRate, filters, evalsDetails } = readForm();
    const edited = { samplingRate, filters, evalsDetails };
    expect(edited.samplingRate).toBe(10);
    expect(edited.evalsDetails).toEqual([{ id: "eval-edited" }]);
    expect(edited.filters[0].filterConfig.filterValue).toBe("trace-edited");

    refresh(loadedTask({ last_run: "2026-01-01T00:00:04Z" }));

    expect(readForm()).toMatchObject(edited);
    expect(
      screen.getByRole("spinbutton", { name: "Sampling rate" }),
    ).toHaveValue(10);

    refresh(
      loadedTask({ status: "pending", last_run: "2026-01-01T00:00:08Z" }),
    );
    expect(screen.getByTestId("task-status")).toHaveTextContent("pending");
    expect(readForm()).toMatchObject(edited);
  });

  it("keeps whole dirty arrays across repeated configuration refreshes", () => {
    const { refresh } = renderTaskDetail();
    editConfiguration();
    const { samplingRate, filters, evalsDetails } = readForm();
    const edited = { samplingRate, filters, evalsDetails };

    refresh(loadedTask({ name: "Renamed task", sampling_rate: 75 }));
    expect(readForm()).toMatchObject({ name: "Renamed task", ...edited });
    expect(readForm().evalsDetails).toEqual([{ id: "eval-edited" }]);

    refresh(loadedTask({ name: "Renamed again", sampling_rate: 90 }));
    expect(readForm()).toMatchObject({ name: "Renamed again", ...edited });
    expect(readForm().evalsDetails).toEqual([{ id: "eval-edited" }]);
  });

  it("does not restore removed evaluation rows during polling", () => {
    const { refresh } = renderTaskDetail();
    fireEvent.click(screen.getByRole("button", { name: "Clear evaluations" }));
    expect(readForm().evalsDetails).toEqual([]);

    refresh(loadedTask({ last_run: "2026-01-01T00:00:04Z" }));
    expect(readForm().evalsDetails).toEqual([]);
    refresh(loadedTask({ last_run: "2026-01-01T00:00:08Z" }));
    expect(readForm().evalsDetails).toEqual([]);
  });

  it("refreshes an evaluation array after its edits are reverted", () => {
    const { refresh } = renderTaskDetail();
    editConfiguration();
    fireEvent.click(
      screen.getByRole("button", { name: "Restore evaluations" }),
    );
    expect(readForm().evalsDetails).toEqual([
      { id: "eval-1" },
      { id: "eval-2" },
    ]);

    refresh(loadedTask({ evals_applied: [{ id: "eval-other" }] }));
    expect(readForm().evalsDetails).toEqual([{ id: "eval-other" }]);
    expect(readForm().samplingRate).toBe(10);
    expect(readForm().filters[0].filterConfig.filterValue).toBe("trace-edited");
  });

  it("submits the edited configuration after polling with confirmation open", async () => {
    startAs("pending");
    const { refresh } = renderTaskDetail();
    editConfiguration();
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await screen.findByText("Update Task");

    refresh(
      loadedTask({ status: "pending", last_run: "2026-01-01T00:00:04Z" }),
    );
    await confirmSave();

    await waitFor(() =>
      expect(axiosPatchMock).toHaveBeenCalledWith(
        "/tracer/eval-task/update_eval_task/",
        expect.objectContaining({
          eval_task_id: "task-1",
          sampling_rate: 10,
          evals: ["eval-edited"],
          filters: expect.objectContaining({ trace_id: ["trace-edited"] }),
        }),
      ),
    );
  });

  it("does not replace pristine confirmation values during a background refresh", async () => {
    startAs("paused");
    const { refresh } = renderTaskDetail();
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await screen.findByText("Update Task");

    refresh(
      loadedTask({
        status: "pending",
        last_run: "2026-01-01T00:00:04Z",
        sampling_rate: 75,
        evals_applied: [{ id: "eval-other" }],
      }),
    );
    expect(screen.getByTestId("task-status")).toHaveTextContent("pending");
    await confirmSave();

    await waitFor(() =>
      expect(axiosPatchMock).toHaveBeenCalledWith(
        "/tracer/eval-task/update_eval_task/",
        expect.objectContaining({
          sampling_rate: 50,
          evals: ["eval-1", "eval-2"],
        }),
      ),
    );
  });

  it("refreshes pristine fields without discarding dirty fields", () => {
    const { refresh } = renderTaskDetail();
    fireEvent.change(
      screen.getByRole("spinbutton", { name: "Sampling rate" }),
      {
        target: { value: "10" },
      },
    );

    refresh(
      loadedTask({
        name: "Renamed task",
        evals_applied: [{ id: "eval-other" }],
      }),
    );

    expect(readForm()).toMatchObject({
      name: "Renamed task",
      samplingRate: 10,
      evalsDetails: [{ id: "eval-other" }],
    });
  });

  it("hydrates a different task and closes the previous task confirmation", async () => {
    startAs("pending");
    const { refresh } = renderTaskDetail();
    editConfiguration();
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await screen.findByText("Update Task");

    route.taskId = "task-2";
    refresh(
      loadedTask({ id: "task-2", name: "Second task", sampling_rate: 80 }),
    );

    expect(screen.queryByText("Update Task")).not.toBeInTheDocument();
    expect(readForm()).toMatchObject({
      name: "Second task",
      samplingRate: 80,
      evalsDetails: [{ id: "eval-1" }, { id: "eval-2" }],
    });
    expect(readForm().filters[0].filterConfig.filterValue).toBe("trace-saved");
    expect(axiosPatchMock).not.toHaveBeenCalled();
  });
});
