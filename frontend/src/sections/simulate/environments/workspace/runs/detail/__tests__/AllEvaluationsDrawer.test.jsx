/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  act,
  render as rtlRender,
  screen,
  fireEvent,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  useEnvironmentRunTest,
  useRemoveAppliedEvaluation,
} from "src/api/simulate-environments/environments";
import { useRunNewEvals } from "src/api/simulate-environments/runEvals";
import { enqueueSnackbar } from "notistack";
import AllEvaluationsDrawer, {
  HARNESS_ONLY_TOOLTIP,
  HARNESS_NOTE,
  NOT_EDITABLE_TOOLTIP,
} from "../AllEvaluationsDrawer";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/api/simulate-environments/environments", () => ({
  useEnvironmentRunTest: vi.fn(),
  useRemoveAppliedEvaluation: vi.fn(),
}));
// The edit form itself is covered by the add drawer's own tests; here it only
// has to open for the right row and report a save back.
const editor = vi.hoisted(() => ({ props: null, updated: null }));
vi.mock("../../../evals/AddEvaluationDrawer", () => ({
  default: (props) => {
    editor.props = props;
    if (!props.open) return null;
    return (
      <div data-testid="edit-drawer">
        {`edit-drawer:${props.editingEval?.id}`}
        <button type="button" onClick={() => props.onEdited(editor.updated)}>
          save edit
        </button>
        <button type="button" onClick={props.onClose}>
          close edit
        </button>
      </div>
    );
  },
}));
vi.mock("src/api/simulate-environments/runEvals", () => ({
  useRunNewEvals: vi.fn(),
  runResultsKey: (id) => ["simulation-run-results-v3", id],
}));

const CONFIGS = [
  {
    id: "c1",
    name: "no_misselling",
    mapping: { conversation: "voice_recording" },
    eval_type: "llm",
    regradable: true,
    editable: true,
  },
  {
    id: "c2",
    name: "customer_agent_task_completion",
    mapping: {},
    eval_type: "agent",
    regradable: true,
    editable: false,
  },
  {
    id: "c3",
    name: "refund_issued_claim",
    mapping: {},
    eval_type: "llm",
    regradable: false,
    editable: false,
  },
];

let runMutate;
let removeMutate;

beforeEach(() => {
  editor.props = null;
  editor.updated = null;
  enqueueSnackbar.mockReset();
  useEnvironmentRunTest.mockReset();
  useRemoveAppliedEvaluation.mockReset();
  useRunNewEvals.mockReset();

  useEnvironmentRunTest.mockReturnValue({
    data: CONFIGS,
    isPending: false,
    isError: false,
  });
  runMutate = vi.fn();
  useRunNewEvals.mockReturnValue({ mutate: runMutate, isPending: false });
  removeMutate = vi.fn();
  useRemoveAppliedEvaluation.mockReturnValue({
    mutate: removeMutate,
    isPending: false,
  });
});

const setup = (props = {}) => {
  const onClose = vi.fn();
  const onAddEvaluations = vi.fn();
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  const tree = (overrides = {}) => (
    <QueryClientProvider client={client}>
      <AllEvaluationsDrawer
        open
        env={{ id: "env-1" }}
        runTestId="rt1"
        executionId="ex1"
        canRun
        onClose={onClose}
        onAddEvaluations={onAddEvaluations}
        {...props}
        {...overrides}
      />
    </QueryClientProvider>
  );
  const utils = rtlRender(tree());
  // Re-render the same, still-mounted drawer with some props changed, as
  // the run page does when it opens and closes it.
  const rerenderWith = (overrides) => utils.rerender(tree(overrides));
  return { ...utils, rerenderWith, onClose, onAddEvaluations };
};

describe("AllEvaluationsDrawer", () => {
  it("lists every eval with its type and a select-all count", () => {
    setup();

    expect(screen.getByText("All Evaluations")).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "Evals (3)" }),
    ).toBeInTheDocument();
    expect(screen.getByText("no_misselling")).toBeInTheDocument();
    expect(
      screen.getByText("customer_agent_task_completion"),
    ).toBeInTheDocument();
    expect(screen.getByText("refund_issued_claim")).toBeInTheDocument();
    expect(screen.getByText("Agent")).toBeInTheDocument();
    expect(useEnvironmentRunTest).toHaveBeenCalledWith("rt1", {
      enabled: true,
    });
  });

  it("runs one eval from its row once confirmed", () => {
    setup();

    fireEvent.click(screen.getByRole("button", { name: "Run no_misselling" }));
    expect(
      screen.getByText("This will overwrite previous evaluation results."),
    ).toBeInTheDocument();
    expect(screen.queryByText(HARNESS_NOTE)).toBeNull();

    fireEvent.click(screen.getByText("Run Evaluations"));
    expect(runMutate).toHaveBeenCalledWith(
      { runTestId: "rt1", executionId: "ex1", evalConfigIds: ["c1"] },
      expect.any(Object),
    );
  });

  it("warns that harness scores are replaced when a suite eval is chosen", () => {
    setup();

    fireEvent.click(
      screen.getByRole("button", {
        name: "Run customer_agent_task_completion",
      }),
    );
    expect(screen.getByText(HARNESS_NOTE)).toBeInTheDocument();
  });

  it("runs the ticked evals from the footer and says Run All when every runnable one is ticked", () => {
    setup();

    expect(screen.getByRole("button", { name: "Run (0)" })).toBeDisabled();

    fireEvent.click(
      screen.getByRole("checkbox", { name: "Select no_misselling" }),
    );
    expect(screen.getByRole("button", { name: "Run (1)" })).toBeEnabled();

    fireEvent.click(screen.getByRole("checkbox", { name: "Evals (3)" }));
    fireEvent.click(screen.getByRole("button", { name: "Run All (2)" }));
    fireEvent.click(screen.getByText("Run Evaluations"));

    expect(runMutate).toHaveBeenCalledWith(
      expect.objectContaining({ evalConfigIds: ["c1", "c2"] }),
      expect.any(Object),
    );
  });

  it("keeps a harness-only eval listed but unrunnable, and says why", async () => {
    setup();

    expect(
      screen.getByRole("checkbox", { name: "Select refund_issued_claim" }),
    ).toBeDisabled();
    const runButton = screen.getByRole("button", {
      name: "Run refund_issued_claim",
    });
    expect(runButton).toBeDisabled();

    fireEvent.mouseOver(runButton.parentElement);
    expect(await screen.findByText(HARNESS_ONLY_TOOLTIP)).toBeInTheDocument();
  });

  it("runs nothing until the run has finished", () => {
    setup({ canRun: false });

    expect(
      screen.getByRole("button", { name: "Run no_misselling" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", {
        name: "Run customer_agent_task_completion",
      }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Run refund_issued_claim" }),
    ).toBeDisabled();

    fireEvent.click(
      screen.getByRole("checkbox", { name: "Select no_misselling" }),
    );
    expect(screen.getByRole("button", { name: "Run (1)" })).toBeDisabled();
  });

  it("removes any eval, including one the harness reports", () => {
    setup();

    expect(
      screen.getByRole("button", { name: "Remove no_misselling" }),
    ).toBeEnabled();
    expect(
      screen.getByRole("button", {
        name: "Remove customer_agent_task_completion",
      }),
    ).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Remove refund_issued_claim" }),
    ).toBeEnabled();

    fireEvent.click(
      screen.getByRole("button", { name: "Remove no_misselling" }),
    );
    expect(removeMutate).toHaveBeenCalledWith(
      { id: "env-1", evalConfigId: "c1" },
      expect.any(Object),
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Remove refund_issued_claim" }),
    );
    expect(removeMutate).toHaveBeenCalledWith(
      { id: "env-1", evalConfigId: "c3" },
      expect.any(Object),
    );
  });

  it("keeps every eval until grading on the run finishes", async () => {
    setup({ canRun: false, grading: true });

    for (const name of [
      "no_misselling",
      "customer_agent_task_completion",
      "refund_issued_claim",
    ]) {
      expect(
        screen.getByRole("button", { name: `Remove ${name}` }),
      ).toBeDisabled();
    }
    fireEvent.mouseOver(
      screen.getByRole("button", { name: "Remove no_misselling" })
        .parentElement,
    );
    expect(
      await screen.findByText("Available once grading finishes."),
    ).toBeInTheDocument();
  });

  it("still removes an eval from a run that can't be graded but isn't grading", () => {
    setup({ canRun: false, grading: false });

    expect(
      screen.getByRole("button", { name: "Remove no_misselling" }),
    ).toBeEnabled();
  });

  it("forgets its ticks once it is closed", async () => {
    const { rerenderWith } = setup();

    fireEvent.click(
      screen.getByRole("checkbox", { name: "Select no_misselling" }),
    );
    expect(screen.getByRole("button", { name: "Run (1)" })).toBeEnabled();

    rerenderWith({ open: false });
    rerenderWith({ open: true });

    expect(
      await screen.findByRole("button", { name: "Run (0)" }),
    ).toBeDisabled();
  });

  it("shows the server's reason when a run is refused and keeps the dialog open", async () => {
    const refusal =
      "refund_issued_claim is scored by the harness during the call. Only rerunning the call refreshes it.";
    runMutate.mockImplementation((_v, o) =>
      o.onError({ statusCode: 400, message: refusal }),
    );
    const { onClose } = setup();

    fireEvent.click(screen.getByRole("button", { name: "Run no_misselling" }));
    fireEvent.click(screen.getByText("Run Evaluations"));

    expect(enqueueSnackbar).toHaveBeenCalledWith(refusal, { variant: "error" });
    // Outlast the dialog's exit transition, so a dialog that had closed would
    // be gone by now rather than still fading out.
    await act(() => new Promise((resolve) => setTimeout(resolve, 500)));
    expect(
      screen.getByText("This will overwrite previous evaluation results."),
    ).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("opens the add flow", () => {
    const { onAddEvaluations } = setup();

    fireEvent.click(screen.getByRole("button", { name: "Add Evaluations" }));
    expect(onAddEvaluations).toHaveBeenCalledTimes(1);
  });

  it("closes and reports once grading is dispatched, but not when dispatch failed", async () => {
    runMutate.mockImplementation((_v, o) =>
      o.onSuccess({ message: "New evaluations dispatched successfully." }),
    );
    const { onClose } = setup();

    fireEvent.click(screen.getByRole("button", { name: "Run no_misselling" }));
    fireEvent.click(screen.getByText("Run Evaluations"));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar).toHaveBeenCalledWith(expect.any(String), {
      variant: "success",
    });

    runMutate.mockImplementation((_v, o) =>
      o.onSuccess({
        message:
          "New evaluations may not have started; async dispatch failed and can be retried.",
      }),
    );
    // The confirm dialog hides the drawer from assistive tech until its exit
    // transition ends, so wait for the row's button to be reachable again.
    fireEvent.click(
      await screen.findByRole("button", { name: "Run no_misselling" }),
    );
    fireEvent.click(screen.getByText("Run Evaluations"));

    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Grading may not have started. Try again.",
      { variant: "warning" },
    );
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("AllEvaluationsDrawer — editing an eval", () => {
  it("offers edit on every row but only lets a person-added eval be edited", () => {
    setup();

    expect(
      screen.getByRole("button", { name: "Edit no_misselling" }),
    ).toBeEnabled();
    expect(
      screen.getByRole("button", {
        name: "Edit customer_agent_task_completion",
      }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Edit refund_issued_claim" }),
    ).toBeDisabled();
  });

  it("says why a harness-set eval can't be edited", async () => {
    setup();

    fireEvent.mouseOver(
      screen.getByRole("button", { name: "Edit refund_issued_claim" })
        .parentElement,
    );
    expect(await screen.findByText(NOT_EDITABLE_TOOLTIP)).toBeInTheDocument();
  });

  it("treats an eval the server didn't mark editable as not editable", () => {
    const { editable: _editable, ...unmarked } = CONFIGS[0];
    useEnvironmentRunTest.mockReturnValue({
      data: [unmarked],
      isPending: false,
      isError: false,
    });
    setup();

    expect(
      screen.getByRole("button", { name: "Edit no_misselling" }),
    ).toBeDisabled();
  });

  it("holds edits while the run is being graded", async () => {
    setup({ canRun: false, grading: true });

    const edit = screen.getByRole("button", { name: "Edit no_misselling" });
    expect(edit).toBeDisabled();
    fireEvent.mouseOver(edit.parentElement);
    expect(
      await screen.findByText("Available once grading finishes."),
    ).toBeInTheDocument();
  });

  it("holds edits until the run has finished", async () => {
    setup({ canRun: false });

    const edit = screen.getByRole("button", { name: "Edit no_misselling" });
    expect(edit).toBeDisabled();
    fireEvent.mouseOver(edit.parentElement);
    expect(
      await screen.findByText("Available once this run finishes."),
    ).toBeInTheDocument();
  });

  it("opens the edit form for that eval and keeps this drawer open", () => {
    setup();

    expect(screen.queryByTestId("edit-drawer")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Edit no_misselling" }));

    expect(screen.getByText("edit-drawer:c1")).toBeInTheDocument();
    expect(editor.props.env).toEqual({ id: "env-1" });
    expect(screen.getByText("All Evaluations")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Edit no_misselling" }),
    ).toBeDisabled();
  });

  it("forgets an open edit once the drawer itself is closed", () => {
    const { rerenderWith } = setup();

    fireEvent.click(screen.getByRole("button", { name: "Edit no_misselling" }));
    expect(screen.getByTestId("edit-drawer")).toBeInTheDocument();
    act(() => rerenderWith({ open: false }));
    act(() => rerenderWith({ open: true }));

    expect(screen.queryByTestId("edit-drawer")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Edit no_misselling" }),
    ).toBeEnabled();
  });

  it("opens no confirm when the save hands back no eval", () => {
    editor.updated = undefined;
    setup();

    fireEvent.click(screen.getByRole("button", { name: "Edit no_misselling" }));
    fireEvent.click(screen.getByText("save edit"));

    expect(screen.queryByTestId("edit-drawer")).toBeNull();
    expect(
      screen.queryByText("This will overwrite previous evaluation results."),
    ).toBeNull();
  });

  it("grades nothing when the edit form is closed without saving", () => {
    setup();

    fireEvent.click(screen.getByRole("button", { name: "Edit no_misselling" }));
    fireEvent.click(screen.getByText("close edit"));

    expect(screen.queryByTestId("edit-drawer")).toBeNull();
    expect(
      screen.queryByText("This will overwrite previous evaluation results."),
    ).toBeNull();
    expect(runMutate).not.toHaveBeenCalled();
  });

  it("offers to grade the edited eval again once it is saved", () => {
    editor.updated = {
      ...CONFIGS[0],
      mapping: { conversation: "call.transcript" },
    };
    setup();

    fireEvent.click(screen.getByRole("button", { name: "Edit no_misselling" }));
    fireEvent.click(screen.getByText("save edit"));

    expect(screen.queryByTestId("edit-drawer")).toBeNull();
    expect(
      screen.getByText("This will overwrite previous evaluation results."),
    ).toBeInTheDocument();
    expect(screen.queryByText(HARNESS_NOTE)).toBeNull();

    fireEvent.click(screen.getByText("Run Evaluations"));
    expect(runMutate).toHaveBeenCalledWith(
      expect.objectContaining({ evalConfigIds: ["c1"] }),
      expect.any(Object),
    );
  });

  it("warns that harness scores are replaced when the saved eval has no mapping of its own", () => {
    editor.updated = { ...CONFIGS[0], mapping: {} };
    setup();

    fireEvent.click(screen.getByRole("button", { name: "Edit no_misselling" }));
    fireEvent.click(screen.getByText("save edit"));

    expect(screen.getByText(HARNESS_NOTE)).toBeInTheDocument();
  });
});
