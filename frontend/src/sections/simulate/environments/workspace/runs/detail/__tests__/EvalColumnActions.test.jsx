import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { useEnvironmentRunTest } from "src/api/simulate-environments/environments";
import EvalColumnActions from "../EvalColumnActions";
import {
  EVAL_GONE_TOOLTIP,
  GRADING_TOOLTIP,
  HARNESS_ONLY_TOOLTIP,
  NOT_COMPLETED_TOOLTIP,
  NOT_EDITABLE_TOOLTIP,
} from "../allEvaluationsDrawer.constants";

vi.mock("src/api/simulate-environments/environments", () => ({
  useEnvironmentRunTest: vi.fn(),
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

beforeEach(() => {
  useEnvironmentRunTest.mockReset();
  useEnvironmentRunTest.mockReturnValue({ data: CONFIGS, isPending: false });
});

// The edit form, the confirm and the re-run belong to the run page
// (`useRunEvalActions`, tested on its own); the menu only asks for them.
const setup = ({ evalId = "c1", ...props } = {}) => {
  const onClose = vi.fn();
  const onRerun = vi.fn();
  const onEdit = vi.fn();
  render(
    <EvalColumnActions
      runTestId="rt1"
      canRun
      grading={false}
      onRerun={onRerun}
      onEdit={onEdit}
      menuFor={
        evalId ? { evalId, name: evalId, anchorEl: document.body } : null
      }
      onClose={onClose}
      {...props}
    />,
  );
  return { onClose, onRerun, onEdit };
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

  it("closes and asks the run page to re-run just that eval", () => {
    const { onClose, onRerun, onEdit } = setup();
    fireEvent.click(item("Re-run"));
    expect(onClose).toHaveBeenCalled();
    expect(onRerun).toHaveBeenCalledWith([CONFIGS[0]]);
    expect(onEdit).not.toHaveBeenCalled();
  });

  it("re-runs a suite eval, which can't be edited", () => {
    const { onRerun } = setup({ evalId: "c2" });
    expect(item("Edit")).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText(NOT_EDITABLE_TOOLTIP)).toBeInTheDocument();

    fireEvent.click(item("Re-run"));
    expect(onRerun).toHaveBeenCalledWith([CONFIGS[1]]);
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
    expect(screen.getAllByText(NOT_COMPLETED_TOOLTIP)).toHaveLength(2);
  });

  it("holds both while the run is being graded, and says so", () => {
    setup({ canRun: false, grading: true });
    expect(item("Re-run")).toHaveAttribute("aria-disabled", "true");
    expect(item("Edit")).toHaveAttribute("aria-disabled", "true");
    expect(screen.getAllByText(GRADING_TOOLTIP)).toHaveLength(2);
    expect(screen.queryByText(NOT_COMPLETED_TOOLTIP)).toBeNull();
  });

  it("holds both while the run page's re-run is on its way, without a reason", () => {
    setup({ rerunPending: true });
    expect(item("Re-run")).toHaveAttribute("aria-disabled", "true");
    expect(item("Edit")).toHaveAttribute("aria-disabled", "true");
    expect(screen.queryByText(NOT_COMPLETED_TOOLTIP)).toBeNull();
    expect(screen.queryByText(GRADING_TOOLTIP)).toBeNull();
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

  it("closes and asks the run page to edit that eval", () => {
    const { onClose, onRerun, onEdit } = setup();
    fireEvent.click(item("Edit"));
    expect(onClose).toHaveBeenCalled();
    expect(onEdit).toHaveBeenCalledWith(CONFIGS[0]);
    expect(onRerun).not.toHaveBeenCalled();
  });
});
