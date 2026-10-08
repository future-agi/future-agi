/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  act,
  render,
  renderHook,
  screen,
  fireEvent,
} from "@testing-library/react";
import { useRunNewEvals } from "src/api/simulate-environments/runEvals";
import { enqueueSnackbar } from "notistack";
import RunEvalDialogs from "../RunEvalDialogs";
import { useRunEvalActions } from "../useRunEvalActions";
import { RUN_FALLBACK } from "../useRegradeEvals";
import { HARNESS_NOTE } from "../allEvaluationsDrawer.constants";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/api/simulate-environments/runEvals", () => ({
  useRunNewEvals: vi.fn(),
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

const MAPPED = {
  id: "c1",
  name: "no_misselling",
  mapping: { conversation: "voice_recording" },
  regradable: true,
  editable: true,
};
const SUITE = {
  id: "c2",
  name: "customer_agent_task_completion",
  mapping: {},
  regradable: true,
  editable: false,
};

// Stands in for the run page: two buttons that ask, as the drawer and the
// column menus do, and the one set of dialogs that answers.
function Host({ onSuccess }) {
  const actions = useRunEvalActions({ envId: "env-1", executionId: "ex1" });
  return (
    <>
      <span>{`pending:${actions.isPending}:edit-open:${actions.editOpen}`}</span>
      <button
        type="button"
        onClick={() => actions.requestRerun([MAPPED], { onSuccess })}
      >
        rerun mapped
      </button>
      <button type="button" onClick={() => actions.requestRerun([SUITE])}>
        rerun suite
      </button>
      <button type="button" onClick={() => actions.requestEdit(MAPPED)}>
        edit mapped
      </button>
      <RunEvalDialogs env={{ id: "env-1" }} {...actions.dialogProps} />
    </>
  );
}

const CONFIRM_BODY = "This will overwrite previous evaluation results.";

let mutate;
beforeEach(() => {
  editor.props = null;
  editor.updated = null;
  enqueueSnackbar.mockReset();
  mutate = vi.fn();
  useRunNewEvals.mockReset();
  useRunNewEvals.mockReturnValue({ mutate, isPending: false });
});

const setup = () => {
  const onSuccess = vi.fn();
  return { ...render(<Host onSuccess={onSuccess} />), onSuccess };
};

describe("useRunEvalActions — re-run", () => {
  it("re-runs the asked-for evals on this run once confirmed", () => {
    setup();
    expect(screen.queryByText(CONFIRM_BODY)).toBeNull();

    fireEvent.click(screen.getByText("rerun mapped"));
    expect(screen.getByText(CONFIRM_BODY)).toBeInTheDocument();
    expect(screen.queryByText(HARNESS_NOTE)).toBeNull();

    fireEvent.click(screen.getByText("Run Evaluations"));
    expect(mutate).toHaveBeenCalledWith(
      { id: "env-1", executionId: "ex1", evalConfigIds: ["c1"] },
      expect.any(Object),
    );
  });

  it("warns that harness scores are replaced when a suite eval is chosen", () => {
    setup();
    fireEvent.click(screen.getByText("rerun suite"));
    expect(screen.getByText(HARNESS_NOTE)).toBeInTheDocument();
  });

  it("closes the confirm and tells whoever asked once grading is queued", async () => {
    mutate.mockImplementation((_v, o) =>
      o.onSuccess({ call_execution_count: 4 }),
    );
    const { onSuccess } = setup();

    fireEvent.click(screen.getByText("rerun mapped"));
    fireEvent.click(screen.getByText("Run Evaluations"));

    expect(onSuccess).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Grading 1 evaluation. This run updates when grading finishes.",
      { variant: "success" },
    );
    // Outlast the dialog's exit transition.
    await act(() => new Promise((resolve) => setTimeout(resolve, 500)));
    expect(screen.queryByText(CONFIRM_BODY)).toBeNull();
  });

  it("shows the server's reason when a run is refused and keeps the dialog open", async () => {
    const refusal =
      "refund_issued_claim is scored by the harness during the call. Only rerunning the call refreshes it.";
    mutate.mockImplementation((_v, o) =>
      o.onError({ statusCode: 400, message: refusal }),
    );
    const { onSuccess } = setup();

    fireEvent.click(screen.getByText("rerun mapped"));
    fireEvent.click(screen.getByText("Run Evaluations"));

    expect(enqueueSnackbar).toHaveBeenCalledWith(refusal, { variant: "error" });
    // Outlast the dialog's exit transition, so a dialog that had closed would
    // be gone by now rather than still fading out.
    await act(() => new Promise((resolve) => setTimeout(resolve, 500)));
    expect(screen.getByText(CONFIRM_BODY)).toBeInTheDocument();
    expect(onSuccess).not.toHaveBeenCalled();
  });

  const QUEUE_REFUSAL = "Grading couldn't be started. Try again.";
  it.each([
    ["the server's sentence", QUEUE_REFUSAL, QUEUE_REFUSAL],
    ["its own sentence", undefined, RUN_FALLBACK],
  ])(
    "shows %s and stays open when grading couldn't be queued",
    async (_label, detail, sentence) => {
      mutate.mockImplementation((_v, o) =>
        o.onError({ statusCode: 503, detail }),
      );
      const { onSuccess } = setup();

      fireEvent.click(screen.getByText("rerun mapped"));
      fireEvent.click(screen.getByText("Run Evaluations"));

      expect(enqueueSnackbar).toHaveBeenCalledWith(sentence, {
        variant: "error",
      });
      expect(enqueueSnackbar).not.toHaveBeenCalledWith(expect.any(String), {
        variant: "success",
      });
      await act(() => new Promise((resolve) => setTimeout(resolve, 500)));
      expect(screen.getByText(CONFIRM_BODY)).toBeInTheDocument();
      expect(onSuccess).not.toHaveBeenCalled();
    },
  );

  it("holds the confirm while the re-run is on its way", () => {
    useRunNewEvals.mockReturnValue({ mutate, isPending: true });
    setup();
    expect(
      screen.getByText("pending:true:edit-open:false"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByText("rerun mapped"));
    expect(
      screen.getByRole("button", { name: /Run Evaluations/ }),
    ).toBeDisabled();
  });
});

describe("useRunEvalActions — editing an eval", () => {
  it("opens the edit form on that eval, without grading the run by name", () => {
    setup();
    expect(screen.queryByTestId("edit-drawer")).toBeNull();

    fireEvent.click(screen.getByText("edit mapped"));
    expect(screen.getByText("edit-drawer:c1")).toBeInTheDocument();
    expect(
      screen.getByText("pending:false:edit-open:true"),
    ).toBeInTheDocument();
    expect(editor.props.env).toEqual({ id: "env-1" });
    expect(editor.props.executionId).toBeUndefined();
  });

  it("grades nothing when the edit form is closed without saving", () => {
    setup();
    fireEvent.click(screen.getByText("edit mapped"));
    fireEvent.click(screen.getByText("close edit"));

    expect(screen.queryByTestId("edit-drawer")).toBeNull();
    expect(
      screen.getByText("pending:false:edit-open:false"),
    ).toBeInTheDocument();
    expect(screen.queryByText(CONFIRM_BODY)).toBeNull();
    expect(mutate).not.toHaveBeenCalled();
  });

  it("only saves: closes the edit form and grades nothing", () => {
    editor.updated = {
      ...MAPPED,
      mapping: { conversation: "call.transcript" },
    };
    setup();

    fireEvent.click(screen.getByText("edit mapped"));
    fireEvent.click(screen.getByText("save edit"));

    expect(screen.queryByTestId("edit-drawer")).toBeNull();
    expect(
      screen.getByText("pending:false:edit-open:false"),
    ).toBeInTheDocument();
    expect(screen.queryByText(CONFIRM_BODY)).toBeNull();
    expect(mutate).not.toHaveBeenCalled();
  });

  it("leaves the confirm closed after a save", () => {
    const { result } = renderHook(() =>
      useRunEvalActions({ envId: "env-1", executionId: "ex1" }),
    );
    act(() => result.current.requestEdit(MAPPED));
    act(() =>
      result.current.dialogProps.onEdited({
        ...MAPPED,
        mapping: { conversation: "call.transcript" },
      }),
    );

    expect(result.current.editOpen).toBe(false);
    expect(result.current.dialogProps.editing).toBeNull();
    expect(result.current.dialogProps.confirming).toBeNull();
    expect(mutate).not.toHaveBeenCalled();
  });
});

describe("useRunEvalActions — moving to another run", () => {
  const renderActions = () =>
    renderHook(
      ({ executionId }) => useRunEvalActions({ envId: "env-1", executionId }),
      { initialProps: { executionId: "ex1" } },
    );
  const openBoth = (result) => {
    act(() => result.current.requestEdit(MAPPED));
    act(() => result.current.requestRerun([SUITE]));
    expect(result.current.dialogProps.editing).toBe(MAPPED);
    expect(result.current.dialogProps.confirming).toEqual([SUITE]);
  };

  it("closes both dialogs once the page shows another run", () => {
    const { result, rerender } = renderActions();
    openBoth(result);

    rerender({ executionId: "ex2" });
    expect(result.current.editOpen).toBe(false);
    expect(result.current.dialogProps.editing).toBeNull();
    expect(result.current.dialogProps.confirming).toBeNull();
  });

  it("keeps both open while the page stays on the same run", () => {
    const { result, rerender } = renderActions();
    openBoth(result);

    rerender({ executionId: "ex1" });
    expect(result.current.editOpen).toBe(true);
    expect(result.current.dialogProps.editing).toBe(MAPPED);
    expect(result.current.dialogProps.confirming).toEqual([SUITE]);
  });
});
