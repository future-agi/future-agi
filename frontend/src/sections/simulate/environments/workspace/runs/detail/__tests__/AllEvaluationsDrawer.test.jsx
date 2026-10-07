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
import AllEvaluationsDrawer from "../AllEvaluationsDrawer";
import {
  HARNESS_ONLY_TOOLTIP,
  NOT_EDITABLE_TOOLTIP,
} from "../allEvaluationsDrawer.constants";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/api/simulate-environments/environments", () => ({
  useEnvironmentRunTest: vi.fn(),
  useRemoveAppliedEvaluation: vi.fn(),
}));
// The edit form, the confirm and the re-run itself belong to the run page
// (`useRunEvalActions`, tested on its own); this drawer only asks for them.
vi.mock("src/api/simulate-environments/runEvals", () => ({
  runResultsKey: (id) => ["simulation-run-results-v3", id],
  runAnalyticsKey: (id) => ["simulation-run-analytics-v3", id],
  callDetailKeyPrefix: ["simulation-call-detail-v3"],
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

let removeMutate;

beforeEach(() => {
  useEnvironmentRunTest.mockReset();
  useRemoveAppliedEvaluation.mockReset();

  useEnvironmentRunTest.mockReturnValue({
    data: CONFIGS,
    isPending: false,
    isError: false,
  });
  removeMutate = vi.fn();
  useRemoveAppliedEvaluation.mockReturnValue({
    mutate: removeMutate,
    isPending: false,
  });
});

const setup = (props = {}) => {
  const onClose = vi.fn();
  const onAddEvaluations = vi.fn();
  const onRerun = vi.fn();
  const onEdit = vi.fn();
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
        onRerun={onRerun}
        onEdit={onEdit}
        {...props}
        {...overrides}
      />
    </QueryClientProvider>
  );
  const utils = rtlRender(tree());
  // Re-render the same, still-mounted drawer with some props changed, as
  // the run page does when it opens and closes it.
  const rerenderWith = (overrides) => utils.rerender(tree(overrides));
  return {
    ...utils,
    client,
    rerenderWith,
    onClose,
    onAddEvaluations,
    onRerun,
    onEdit,
  };
};

// The props the run page derives from its execution status.
const forStatus = (status) => ({
  canRun: status === "completed",
  grading: status === "evaluating",
});

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

  it("asks the run page to run one eval from its row", () => {
    const { onRerun } = setup();

    fireEvent.click(screen.getByRole("button", { name: "Run no_misselling" }));
    expect(onRerun).toHaveBeenCalledWith([CONFIGS[0]], {
      onSuccess: expect.any(Function),
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Run customer_agent_task_completion",
      }),
    );
    expect(onRerun).toHaveBeenLastCalledWith([CONFIGS[1]], {
      onSuccess: expect.any(Function),
    });
  });

  it("stays open until the run page says the grading it asked for is queued", () => {
    const { onClose, onRerun } = setup();

    fireEvent.click(screen.getByRole("button", { name: "Run no_misselling" }));
    expect(onClose).not.toHaveBeenCalled();

    act(() => onRerun.mock.calls[0][1].onSuccess());
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("holds every run while the run page's re-run is on its way", () => {
    setup({ rerunPending: true });

    expect(
      screen.getByRole("button", { name: "Run no_misselling" }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Select no_misselling" }),
    );
    expect(screen.getByRole("button", { name: "Run (1)" })).toBeDisabled();
  });

  it("runs the ticked evals from the footer and says Run All when every runnable one is ticked", () => {
    const { onRerun, onClose } = setup();

    expect(screen.getByRole("button", { name: "Run (0)" })).toBeDisabled();

    fireEvent.click(
      screen.getByRole("checkbox", { name: "Select no_misselling" }),
    );
    expect(screen.getByRole("button", { name: "Run (1)" })).toBeEnabled();

    fireEvent.click(screen.getByRole("checkbox", { name: "Evals (3)" }));
    fireEvent.click(screen.getByRole("button", { name: "Run All (2)" }));

    expect(onRerun).toHaveBeenCalledWith([CONFIGS[0], CONFIGS[1]], {
      onSuccess: expect.any(Function),
    });
    act(() => onRerun.mock.calls[0][1].onSuccess());
    expect(onClose).toHaveBeenCalledTimes(1);
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

  it.each(["failed", "cancelled", "running"])(
    "says only a completed run can be graded when the run is %s",
    async (status) => {
      setup(forStatus(status));

      fireEvent.mouseOver(
        screen.getByRole("button", { name: "Run no_misselling" }).parentElement,
      );
      expect(
        await screen.findByText("Only a completed run can be graded again."),
      ).toBeInTheDocument();
      expect(screen.queryByText("Available once grading finishes.")).toBeNull();
    },
  );

  it("says the footer waits for grading while the run is evaluating", async () => {
    setup(forStatus("evaluating"));

    fireEvent.click(
      screen.getByRole("checkbox", { name: "Select no_misselling" }),
    );
    const footer = screen.getByRole("button", { name: "Run (1)" });
    expect(footer).toBeDisabled();
    fireEvent.mouseOver(footer.parentElement);
    expect(
      await screen.findByText("Available once grading finishes."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Only a completed run can be graded again."),
    ).toBeNull();
  });

  it("says a row waits for grading while the run is evaluating", async () => {
    setup(forStatus("evaluating"));

    fireEvent.mouseOver(
      screen.getByRole("button", { name: "Run no_misselling" }).parentElement,
    );
    expect(
      await screen.findByText("Available once grading finishes."),
    ).toBeInTheDocument();
  });

  it("says only a completed run can be graded on the footer of a failed run", async () => {
    setup(forStatus("failed"));

    fireEvent.mouseOver(
      screen.getByRole("button", { name: "Run (0)" }).parentElement,
    );
    expect(
      await screen.findByText("Only a completed run can be graded again."),
    ).toBeInTheDocument();
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

  it("removes any eval, including one the harness reports, and refreshes the run's table, analytics and call details", () => {
    removeMutate.mockImplementation((_v, o) => o.onSuccess());
    const { client } = setup();
    const invalidateSpy = vi.spyOn(client, "invalidateQueries");

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
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ["simulation-run-results-v3", "ex1"],
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ["simulation-run-analytics-v3", "ex1"],
    });
    // A call drawer reopened within its stale window must not list it.
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ["simulation-call-detail-v3"],
    });

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

  it("opens the add flow", () => {
    const { onAddEvaluations } = setup();

    fireEvent.click(screen.getByRole("button", { name: "Add Evaluations" }));
    expect(onAddEvaluations).toHaveBeenCalledTimes(1);
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
      await screen.findByText("Only a completed run can be graded again."),
    ).toBeInTheDocument();
  });

  it("asks the run page to edit that eval, and stays open until its re-run is queued", () => {
    const { onEdit, onClose } = setup();

    fireEvent.click(screen.getByRole("button", { name: "Edit no_misselling" }));
    expect(onEdit).toHaveBeenCalledWith(CONFIGS[0], {
      onSuccess: expect.any(Function),
    });
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByText("All Evaluations")).toBeInTheDocument();

    act(() => onEdit.mock.calls[0][1].onSuccess());
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("holds every edit while the run page's edit form is open", () => {
    setup({ editOpen: true });

    expect(
      screen.getByRole("button", { name: "Edit no_misselling" }),
    ).toBeDisabled();
  });
});
