/* eslint-disable react/prop-types */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { createTheme } from "@mui/material/styles";
import EvaluationDrawer from "./EvaluationDrawer";
import { useRunEvaluationStore } from "src/sections/develop-detail/states";
import { palette } from "src/theme/palette";
import { customShadows } from "src/theme/custom-shadows";

// EvaluationDrawer.jsx reads theme.customShadows.drawer directly (MUI's
// Drawer PaperProps sx) — the default test theme from test-utils.jsx doesn't
// define customShadows, so it needs to be supplied explicitly here.
const themeWithCustomShadows = createTheme({
  palette: palette("light"),
  customShadows: customShadows("light"),
});

// Mutable state read by the mocked axios/getEvalsList modules below. Declared
// via vi.hoisted so it's initialized before the hoisted vi.mock() factories run.
const mocks = vi.hoisted(() => ({
  mockPost: vi.fn(),
  mockDelete: vi.fn(),
  enqueueSnackbar: vi.fn(),
  addEvalSpy: vi.fn((id) => `add-eval/${id}`),
  editEvalSpy: vi.fn((id, evalId) => `edit-eval/${id}/${evalId}`),
  deleteEvalSpy: vi.fn((id, evalId) => `delete-eval/${id}/${evalId}`),
  runEvalsSpy: vi.fn((id) => `run-evals/${id}`),
  stopEvalSpy: vi.fn((id, evalId) => `stop-eval/${id}/${evalId}`),
  createEvalTaskConfigSpy: vi.fn(() => "task-add-eval"),
  createOrUpdateEvalConfigSpy: vi.fn((id) => `workbench-upsert/${id}`),
  deleteEvalConfigSpy: vi.fn((id, evalId) => `workbench-delete/${id}/${evalId}`),
  runEvalsOnMultipleVersionsSpy: vi.fn((id) => `workbench-run/${id}`),
  experimentAddEvalSpy: vi.fn((experimentId) => `experiment-add-eval/${experimentId}`),
  evalsListState: { data: { evals: [] }, isLoading: false },
  evalPickerPayload: null,
}));

vi.mock("src/utils/axios", () => ({
  default: {
    post: (...args) => mocks.mockPost(...args),
    delete: (...args) => mocks.mockDelete(...args),
  },
  endpoints: {
    project: {
      createEvalTaskConfig: (...args) => mocks.createEvalTaskConfigSpy(...args),
    },
    develop: {
      eval: {
        addEval: (...args) => mocks.addEvalSpy(...args),
        editEval: (...args) => mocks.editEvalSpy(...args),
        deleteEval: (...args) => mocks.deleteEvalSpy(...args),
        runEvals: (...args) => mocks.runEvalsSpy(...args),
        stopEval: (...args) => mocks.stopEvalSpy(...args),
        applyEvalGroup: "apply-eval-group",
      },
      runPrompt: {
        createOrUpdateEvalConfig: (...args) => mocks.createOrUpdateEvalConfigSpy(...args),
        deleteEvalConfig: (...args) => mocks.deleteEvalConfigSpy(...args),
        runEvalsOnMultipleVersions: (...args) => mocks.runEvalsOnMultipleVersionsSpy(...args),
      },
      experiment: {
        addEval: (...args) => mocks.experimentAddEvalSpy(...args),
      },
    },
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: (...args) => mocks.enqueueSnackbar(...args),
}));

vi.mock("./getEvalsList", () => ({
  useEvalsList: () => mocks.evalsListState,
  getUserEvalListKey: (module, id) => ["mock-user-eval-list", module, id],
}));

vi.mock("./SavedEvalsList", () => ({
  default: (props) => (
    <div data-testid="saved-evals-list">
      <div data-testid="saved-evals-count">{props.evals?.length ?? 0}</div>
      <div data-testid="disable-delete">{String(!!props.disableDelete)}</div>
      <button onClick={() => props.onDeleteEvalClick(props.evals[0])}>
        delete-eval
      </button>
      <button onClick={() => props.onRunEvalClick(props.evals[0])}>
        run-eval
      </button>
      <button onClick={() => props.onStopEvalClick(props.evals[0])}>
        stop-eval
      </button>
      <button onClick={() => props.onEditEvalClick(props.evals[0])}>
        edit-eval
      </button>
    </div>
  ),
}));

vi.mock("./SavedEvalsSkeleton", () => ({
  default: () => <div data-testid="skeleton" />,
}));
vi.mock("./DeleteEval", () => ({ default: () => null }));
vi.mock("./RunEvals", () => ({ default: () => null }));
vi.mock("./CustomEvalsForm", () => ({ default: () => null }));
vi.mock("./EvaluationMappingForm", () => ({ default: () => null }));
vi.mock("./CreateEvaluationGroupDrawer", () => ({ default: () => null }));
vi.mock("./EvaluationsSelectionGrid", () => ({
  default: () => <div data-testid="evaluation-selection-grid" />,
}));

vi.mock("src/sections/common/EvalPicker", () => ({
  EvalPickerDrawer: (props) => (
    <div data-testid="eval-picker-drawer" data-open={String(!!props.open)}>
      <button onClick={() => props.onEvalAdded(mocks.evalPickerPayload)}>
        trigger-eval-added
      </button>
    </div>
  ),
}));

function renderComponent(props = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const defaultProps = {
    open: true,
    onClose: vi.fn(),
    allColumns: [],
    refreshGrid: vi.fn(),
    id: "dataset-1",
  };
  return render(
    <QueryClientProvider client={queryClient}>
      <EvaluationDrawer {...defaultProps} {...props} />
    </QueryClientProvider>,
    { theme: themeWithCustomShadows },
  );
}

describe("EvaluationDrawer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.evalsListState = { data: { evals: [] }, isLoading: false };
    mocks.evalPickerPayload = null;
    useRunEvaluationStore.setState({
      openRunEvaluation: false,
      pendingEditEvalId: null,
    });
  });

  it("shows the loading skeleton while evals are loading", () => {
    mocks.evalsListState = { data: undefined, isLoading: true };
    renderComponent();
    expect(screen.getByTestId("skeleton")).toBeInTheDocument();
  });

  it("shows the empty state and opens the eval picker instead of the old config view when Add Evaluations is clicked", async () => {
    const user = userEvent.setup();
    renderComponent();

    expect(screen.getByText("No evaluations added")).toBeInTheDocument();
    expect(screen.getByTestId("eval-picker-drawer")).toHaveAttribute(
      "data-open",
      "false",
    );

    await user.click(screen.getByRole("button", { name: /add evaluations/i }));

    // "config" is intercepted so the new EvalPickerDrawer opens instead of
    // rendering the old EvaluationsSelectionGrid inline.
    expect(screen.getByTestId("eval-picker-drawer")).toHaveAttribute(
      "data-open",
      "true",
    );
  });

  it("disables delete for an experiment with only one saved eval", () => {
    mocks.evalsListState = {
      data: { evals: [{ id: "e1", name: "Eval One" }] },
      isLoading: false,
    };
    renderComponent({ module: "experiment" });
    expect(screen.getByTestId("saved-evals-count")).toHaveTextContent("1");
    expect(screen.getByTestId("disable-delete")).toHaveTextContent("true");
  });

  it("does not disable delete for an experiment with more than one saved eval", () => {
    mocks.evalsListState = {
      data: {
        evals: [
          { id: "e1", name: "Eval One" },
          { id: "e2", name: "Eval Two" },
        ],
      },
      isLoading: false,
    };
    renderComponent({ module: "experiment" });
    expect(screen.getByTestId("disable-delete")).toHaveTextContent("false");
  });

  it("adds a new dataset eval as save-only (run:false) via add_user_eval", async () => {
    mocks.mockPost.mockResolvedValueOnce({ data: { status: true } });
    mocks.evalPickerPayload = {
      name: "my_eval",
      templateId: "tpl-1",
      model: "turing_large",
      mapping: { input: "col1" },
    };
    const user = userEvent.setup();
    // `run` is only skipped for the literal "dataset" module (the
    // EvaluationDrawerChild payload check is `module !== "dataset"`, not a
    // falsy/undefined check), so it must be passed explicitly here.
    renderComponent({ id: "dataset-1", module: "dataset" });

    await user.click(screen.getByRole("button", { name: "trigger-eval-added" }));

    await vi.waitFor(() => expect(mocks.mockPost).toHaveBeenCalled());
    expect(mocks.addEvalSpy).toHaveBeenCalledWith("dataset-1");
    const [url, payload] = mocks.mockPost.mock.calls[0];
    expect(url).toBe("add-eval/dataset-1");
    expect(payload.run).toBe(false);
    expect(payload.template_id).toBe("tpl-1");
  });

  it("adds a task eval with run:true via the task eval-config endpoint", async () => {
    mocks.mockPost.mockResolvedValueOnce({ data: { status: true } });
    mocks.evalPickerPayload = {
      name: "task_eval",
      templateId: "tpl-2",
      mapping: {},
    };
    const user = userEvent.setup();
    renderComponent({ module: "task", id: "task-1" });

    await user.click(screen.getByRole("button", { name: "trigger-eval-added" }));

    await vi.waitFor(() => expect(mocks.mockPost).toHaveBeenCalled());
    expect(mocks.createEvalTaskConfigSpy).toHaveBeenCalled();
    const [url, payload] = mocks.mockPost.mock.calls[0];
    expect(url).toBe("task-add-eval");
    expect(payload.run).toBe(true);
  });

  it("routes an edit (existing userEvalId) to edit_and_run_user_eval instead of add_user_eval", async () => {
    mocks.mockPost.mockResolvedValueOnce({ data: { status: true } });
    mocks.evalPickerPayload = {
      name: "renamed_eval",
      templateId: "tpl-3",
      userEvalId: "ue-9",
      mapping: {},
    };
    const user = userEvent.setup();
    renderComponent({ id: "dataset-1" });

    await user.click(screen.getByRole("button", { name: "trigger-eval-added" }));

    await vi.waitFor(() => expect(mocks.mockPost).toHaveBeenCalled());
    expect(mocks.editEvalSpy).toHaveBeenCalledWith("dataset-1", "ue-9");
    // The top-level `endpoint` useMemo always computes the default add-eval
    // URL as a side effect of its switch statement, even when unused — so
    // asserting on the outgoing request URL (not whether addEvalSpy was ever
    // invoked) is what actually proves the edit route was taken.
    const [url] = mocks.mockPost.mock.calls[0];
    expect(url).toBe("edit-eval/dataset-1/ue-9");
  });

  it("deletes an eval through delete_user_eval and shows a success toast", async () => {
    mocks.mockDelete.mockResolvedValueOnce({ data: { status: true } });
    mocks.evalsListState = {
      data: { evals: [{ id: "e1", name: "My Eval" }] },
      isLoading: false,
    };
    const user = userEvent.setup();
    renderComponent({ id: "dataset-1" });

    await user.click(screen.getByRole("button", { name: "delete-eval" }));

    await vi.waitFor(() => expect(mocks.mockDelete).toHaveBeenCalled());
    expect(mocks.deleteEvalSpy).toHaveBeenCalledWith("dataset-1", "e1");
    expect(mocks.mockDelete).toHaveBeenCalledWith("delete-eval/dataset-1/e1", {
      data: { delete_column: true },
    });
    expect(mocks.enqueueSnackbar).toHaveBeenCalledWith("My Eval deleted", {
      variant: "success",
    });
  });

  it("runs an eval through start_evals_process and shows a success toast", async () => {
    mocks.mockPost.mockResolvedValueOnce({ data: { status: true } });
    mocks.evalsListState = {
      data: { evals: [{ id: "e1", name: "My Eval" }] },
      isLoading: false,
    };
    const user = userEvent.setup();
    renderComponent({ id: "dataset-1" });

    await user.click(screen.getByRole("button", { name: "run-eval" }));

    await vi.waitFor(() => expect(mocks.mockPost).toHaveBeenCalled());
    expect(mocks.runEvalsSpy).toHaveBeenCalledWith("dataset-1");
    expect(mocks.mockPost).toHaveBeenCalledWith("run-evals/dataset-1", {
      user_eval_ids: ["e1"],
    });
    expect(mocks.enqueueSnackbar).toHaveBeenCalledWith("My Eval started", {
      variant: "success",
    });
  });
});
