/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { useEnvironmentRunTest } from "src/api/simulate-environments/environments";
import { useRunNewEvals } from "src/api/simulate-environments/runEvals";
import EvalColumnActions, { EVAL_GONE_TOOLTIP } from "../EvalColumnActions";
import {
  GRADING_TOOLTIP,
  HARNESS_NOTE,
  HARNESS_ONLY_TOOLTIP,
  NOT_EDITABLE_TOOLTIP,
  NOT_FINISHED_TOOLTIP,
} from "../AllEvaluationsDrawer";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/api/simulate-environments/environments", () => ({
  useEnvironmentRunTest: vi.fn(),
  useRemoveAppliedEvaluation: vi.fn(() => ({ mutate: vi.fn() })),
}));
vi.mock("src/api/simulate-environments/runEvals", () => ({
  useRunNewEvals: vi.fn(),
  runResultsKey: (id) => ["simulation-run-results-v3", id],
}));
// The edit form is covered by the add drawer's own tests; here it only has
// to open for the right eval and report a save back.
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

let mutate;
beforeEach(() => {
  editor.props = null;
  editor.updated = null;
  useEnvironmentRunTest.mockReset();
  useEnvironmentRunTest.mockReturnValue({ data: CONFIGS, isPending: false });
  mutate = vi.fn();
  useRunNewEvals.mockReset();
  useRunNewEvals.mockReturnValue({ mutate, isPending: false });
});

const setup = ({ evalId = "c1", ...props } = {}) => {
  const onClose = vi.fn();
  const ui = (menuFor) => (
    <EvalColumnActions
      env={{ id: "env-1" }}
      runTestId="rt1"
      executionId="ex1"
      canRun
      grading={false}
      menuFor={menuFor}
      onClose={onClose}
      {...props}
    />
  );
  const utils = render(
    ui(evalId ? { evalId, name: evalId, anchorEl: document.body } : null),
  );
  return {
    ...utils,
    onClose,
    closeMenu: () => utils.rerender(ui(null)),
  };
};
const item = (label) =>
  screen.getByRole("menuitem", { name: new RegExp(`^${label}`) });

describe("EvalColumnActions", () => {
  it("shows no menu until a column's menu is opened", () => {
    setup({ evalId: null });
    expect(screen.queryByRole("menuitem")).toBeNull();
  });

  it("offers Re-run and Edit on an eval a person added to a finished run", () => {
    setup();
    expect(item("Re-run")).not.toHaveAttribute("aria-disabled", "true");
    expect(item("Edit")).not.toHaveAttribute("aria-disabled", "true");
  });

  it("re-runs just that eval on this run once confirmed", () => {
    const { onClose, closeMenu } = setup();
    fireEvent.click(item("Re-run"));
    expect(onClose).toHaveBeenCalled();
    closeMenu();

    expect(
      screen.getByText("This will overwrite previous evaluation results."),
    ).toBeInTheDocument();
    expect(screen.queryByText(HARNESS_NOTE)).toBeNull();
    fireEvent.click(screen.getByText("Run Evaluations"));
    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        runTestId: "rt1",
        executionId: "ex1",
        evalConfigIds: ["c1"],
      }),
      expect.any(Object),
    );
  });

  it("closes the confirm once grading is dispatched", async () => {
    mutate.mockImplementation((_v, o) => o.onSuccess({ message: "ok" }));
    const { closeMenu } = setup();
    fireEvent.click(item("Re-run"));
    closeMenu();
    fireEvent.click(screen.getByText("Run Evaluations"));
    // The dialog leaves through its exit transition.
    await waitFor(() =>
      expect(screen.queryByText("Run Evaluations")).toBeNull(),
    );
  });

  it("warns that harness scores are replaced for a suite eval, which can't be edited", () => {
    const { closeMenu } = setup({ evalId: "c2" });
    expect(item("Edit")).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText(NOT_EDITABLE_TOOLTIP)).toBeInTheDocument();

    fireEvent.click(item("Re-run"));
    closeMenu();
    expect(screen.getByText(HARNESS_NOTE)).toBeInTheDocument();
  });

  it("can't re-run an eval only the harness scores, and says why", () => {
    setup({ evalId: "c3" });
    expect(item("Re-run")).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText(HARNESS_ONLY_TOOLTIP)).toBeInTheDocument();
  });

  it("holds both until the run has finished", () => {
    setup({ canRun: false });
    expect(item("Re-run")).toHaveAttribute("aria-disabled", "true");
    expect(item("Edit")).toHaveAttribute("aria-disabled", "true");
    expect(screen.getAllByText(NOT_FINISHED_TOOLTIP)).toHaveLength(2);
  });

  it("holds edits while the run is being graded", () => {
    setup({ canRun: false, grading: true });
    expect(item("Edit")).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText(GRADING_TOOLTIP)).toBeInTheDocument();
  });

  it("holds re-runs while one is being sent", () => {
    useRunNewEvals.mockReturnValue({ mutate, isPending: true });
    setup();
    expect(item("Re-run")).toHaveAttribute("aria-disabled", "true");
  });

  it("says so when the column's eval is no longer on the environment", () => {
    setup({ evalId: "gone" });
    expect(item("Re-run")).toHaveAttribute("aria-disabled", "true");
    expect(item("Edit")).toHaveAttribute("aria-disabled", "true");
    expect(screen.getAllByText(EVAL_GONE_TOOLTIP)).toHaveLength(2);
  });

  it("holds both, without a reason, while the evals load", () => {
    useEnvironmentRunTest.mockReturnValue({ data: undefined, isPending: true });
    setup();
    expect(item("Re-run")).toHaveAttribute("aria-disabled", "true");
    expect(item("Edit")).toHaveAttribute("aria-disabled", "true");
    expect(screen.queryByText(EVAL_GONE_TOOLTIP)).toBeNull();
  });

  it("opens the edit form on that eval, without grading the run by name", () => {
    const { onClose } = setup();
    fireEvent.click(item("Edit"));
    expect(onClose).toHaveBeenCalled();
    expect(screen.getByText("edit-drawer:c1")).toBeInTheDocument();
    expect(editor.props.env).toEqual({ id: "env-1" });
    expect(editor.props.executionId).toBeUndefined();
  });

  it("offers to re-run the eval once its edit is saved", () => {
    editor.updated = {
      ...CONFIGS[0],
      mapping: { conversation: "call.transcript" },
    };
    const { closeMenu } = setup();
    fireEvent.click(item("Edit"));
    closeMenu();
    fireEvent.click(screen.getByText("save edit"));

    expect(screen.queryByTestId("edit-drawer")).toBeNull();
    fireEvent.click(screen.getByText("Run Evaluations"));
    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({ evalConfigIds: ["c1"] }),
      expect.any(Object),
    );
  });

  it("grades nothing when the save hands back no eval", () => {
    const { closeMenu } = setup();
    fireEvent.click(item("Edit"));
    closeMenu();
    fireEvent.click(screen.getByText("save edit"));

    expect(screen.queryByTestId("edit-drawer")).toBeNull();
    expect(screen.queryByText("Run Evaluations")).toBeNull();
    expect(mutate).not.toHaveBeenCalled();
  });

  it("grades nothing when the edit form is closed without saving", () => {
    const { closeMenu } = setup();
    fireEvent.click(item("Edit"));
    closeMenu();
    fireEvent.click(screen.getByText("close edit"));

    expect(screen.queryByTestId("edit-drawer")).toBeNull();
    expect(screen.queryByText("Run Evaluations")).toBeNull();
    expect(mutate).not.toHaveBeenCalled();
  });
});
