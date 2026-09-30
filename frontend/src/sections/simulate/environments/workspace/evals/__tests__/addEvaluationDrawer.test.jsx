/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render as rtlRender,
  screen,
  fireEvent,
  waitFor,
  act,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { serializeEvalConfig } from "src/sections/common/EvalPicker/serializeEvalConfig";
import {
  voiceEvalColumns,
  chatEvalColumns,
} from "src/components/run-tests/common";

const picker = vi.hoisted(() => ({ props: null }));

vi.mock("src/sections/common/EvalPicker", async () => ({
  serializeEvalConfig: (
    await vi.importActual("src/sections/common/EvalPicker/serializeEvalConfig")
  ).serializeEvalConfig,
  EvalPickerDrawer: (props) => {
    picker.props = props;
    if (!props.open) return null;
    return (
      <div data-testid="eval-picker">
        <button
          type="button"
          onClick={() => props.onEvalAdded(PICKED).catch(() => {})}
        >
          save picked
        </button>
        {props.addedEvalAction && (
          <button
            type="button"
            onClick={() => props.addedEvalAction.onClick(props.addedEvals[0])}
          >
            {props.addedEvalAction.label}
          </button>
        )}
        <button type="button" onClick={props.onClose}>
          close picker
        </button>
      </div>
    );
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: {
    runTests: {
      detail: (id) => `/simulate/run-tests/${id}/`,
      addEvals: (id) => `/simulate/run-tests/${id}/eval-configs/`,
    },
  },
}));
vi.mock("src/api/simulate-environments/harnessEnvironments", () => ({
  addRunEvaluation: vi.fn(),
  listHarnessEnvironments: vi.fn(),
  deleteHarnessEnvironment: vi.fn(),
  renameHarnessEnvironment: vi.fn(),
  getHarnessEnvironment: vi.fn(),
  deleteAppliedEvaluation: vi.fn(),
}));

const PICKED = {
  templateId: "tpl-tone",
  name: "tone_check",
  model: "turing_large",
  mapping: { output: "call.transcript", input: "" },
  config: {},
  error_localizer_enabled: false,
};
const CONFIGS = [
  {
    id: "c1",
    name: "no_misselling",
    template_id: "tpl-bound",
    mapping: { conversation: "voice_recording" },
  },
  {
    id: "c2",
    name: "call_quality_score",
    template_id: "tpl-result",
    mapping: {},
  },
];

const axios = (await import("src/utils/axios")).default;
const { addRunEvaluation, getHarnessEnvironment } = await import(
  "src/api/simulate-environments/harnessEnvironments"
);
const { enqueueSnackbar } = await import("notistack");
const { default: AddEvaluationDrawer } = await import("../AddEvaluationDrawer");

// harnessEnvironmentQuery sets its own `retry` (none on 404, up to 3 otherwise),
// which overrides `retry: false` here; retryDelay:0 keeps those retries instant.
const render = (ui) =>
  rtlRender(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false, retryDelay: 0 },
            mutations: { retry: false },
          },
        })
      }
    >
      {ui}
    </QueryClientProvider>,
  );
const ENV = { id: "env-1" };
const detail = ({ agentType = "voice", runTestId = "rt-1" } = {}) => ({
  overview: {
    agent_type: agentType,
    run: runTestId ? { run_test_id: runTestId } : null,
  },
  evaluations: { selected: [] },
});

beforeEach(() => {
  picker.props = null;
  getHarnessEnvironment.mockReset();
  getHarnessEnvironment.mockResolvedValue(detail());
  axios.get.mockReset();
  axios.get.mockResolvedValue({
    data: { simulate_eval_configs_detail: CONFIGS },
  });
  axios.post.mockReset();
  axios.post.mockResolvedValue({ data: {} });
  addRunEvaluation.mockReset();
  enqueueSnackbar.mockReset();
});

describe("AddEvaluationDrawer — Evaluations tab", () => {
  it("opens the product picker on this environment's run, with everything already on it marked added", async () => {
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    await screen.findByTestId("eval-picker");
    await waitFor(() => expect(picker.props.addedEvals).toHaveLength(2));
    expect(picker.props).toMatchObject({
      open: true,
      source: "simulation",
      sourceId: "rt-1",
      sourceColumns: voiceEvalColumns,
      requireInputs: true,
      addedEvals: [
        { id: "tpl-bound", name: "no_misselling", canGrade: true },
        { id: "tpl-result", name: "call_quality_score", canGrade: false },
      ],
      existingEvals: [
        { template_id: "tpl-bound", name: "no_misselling" },
        { template_id: "tpl-result", name: "call_quality_score" },
      ],
    });
    expect(picker.props.addedEvalAction).toBeFalsy();
  });

  it("offers the chat fields to a chat environment", async () => {
    getHarnessEnvironment.mockResolvedValue(detail({ agentType: "chat" }));
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    await screen.findByTestId("eval-picker");
    expect(picker.props.sourceColumns).toBe(chatEvalColumns);
  });

  it("adds through the run test's own endpoint with the picker's payload", async () => {
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    fireEvent.click(await screen.findByText("save picked"));
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(
        "/simulate/run-tests/rt-1/eval-configs/",
        {
          evaluations_config: [serializeEvalConfig(PICKED)],
        },
      ),
    );
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith("Evaluation added", {
        variant: "success",
      }),
    );
  });

  it("shows a refusal as returned and keeps the picker on its config step", async () => {
    axios.post.mockRejectedValue({
      detail:
        "An evaluation config with the name 'tone_check' already exists in this run test. Please use a different name.",
    });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    await screen.findByTestId("eval-picker");
    await expect(picker.props.onEvalAdded(PICKED)).rejects.toBeTruthy();
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "An evaluation config with the name 'tone_check' already exists in this run test. Please use a different name.",
      { variant: "error" },
    );
  });

  it("refuses an eval with no inputs mapped before sending anything", async () => {
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    await screen.findByTestId("eval-picker");
    await expect(
      picker.props.onEvalAdded({ ...PICKED, mapping: {} }),
    ).rejects.toBeTruthy();
    expect(axios.post).not.toHaveBeenCalled();
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "This evaluation has no inputs to map, so it can't run in an environment.",
      { variant: "error" },
    );
  });

  it("says so when the environment is not built yet", async () => {
    getHarnessEnvironment.mockResolvedValue(detail({ runTestId: null }));
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    expect(await screen.findByText("Not ready yet")).toBeInTheDocument();
    expect(screen.queryByTestId("eval-picker")).toBeNull();
  });

  it("does not open the picker until the run test's evals are known, and offers a retry when they can't be read", async () => {
    axios.get.mockRejectedValue({
      statusCode: 503,
      detail: "Simulations are temporarily unavailable. Please retry.",
    });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    expect(
      await screen.findByText(
        "Simulations are temporarily unavailable. Please retry.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("eval-picker")).toBeNull();
    axios.get.mockResolvedValue({
      data: { simulate_eval_configs_detail: CONFIGS },
    });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByTestId("eval-picker");
    await waitFor(() => expect(picker.props.addedEvals).toHaveLength(2));
  });

  it("shows the detail refusal with a retry", async () => {
    getHarnessEnvironment.mockRejectedValue({
      statusCode: 500,
      detail: "boom",
    });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    expect(await screen.findByText("boom")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("refreshes the Evaluations tab's list when it closes", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, retryDelay: 0 } },
    });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    rtlRender(
      <QueryClientProvider client={client}>
        <AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByText("close picker"));
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["harness-environment", "env-1"],
    });
  });

  it("closes through the host", async () => {
    const onClose = vi.fn();
    render(<AddEvaluationDrawer open env={ENV} onClose={onClose} />);
    fireEvent.click(await screen.findByText("close picker"));
    expect(onClose).toHaveBeenCalled();
  });
});

describe("AddEvaluationDrawer — inside a run", () => {
  const COUNTS = {
    queued: 12,
    skipped_existing: 3,
    skipped_in_flight: 0,
    skipped_pending: 1,
    completed_calls: 16,
  };
  const renderRun = () =>
    render(
      <AddEvaluationDrawer
        open
        env={ENV}
        executionId="ex-1"
        onClose={vi.fn()}
      />,
    );

  it("adds the pick, then grades this run with it by name, and reports the counts", async () => {
    addRunEvaluation.mockResolvedValue(COUNTS);
    renderRun();
    fireEvent.click(await screen.findByText("save picked"));
    await waitFor(() =>
      expect(addRunEvaluation).toHaveBeenCalledWith(
        "env-1",
        "ex-1",
        "tone_check",
      ),
    );
    expect(axios.post).toHaveBeenCalledWith(
      "/simulate/run-tests/rt-1/eval-configs/",
      {
        evaluations_config: [serializeEvalConfig(PICKED)],
      },
    );
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        expect.stringContaining("Reload this run to see the new verdicts."),
        { variant: "success" },
      ),
    );
  });

  it("keeps the add when grading the run fails, and says which part failed", async () => {
    addRunEvaluation.mockRejectedValue({
      detail: "Run is cancelled; nothing will be graded",
    });
    renderRun();
    await screen.findByTestId("eval-picker");
    await expect(picker.props.onEvalAdded(PICKED)).resolves.toBeUndefined();
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Added, but grading this run failed: Run is cancelled; nothing will be graded",
      { variant: "error" },
    );
  });

  it("offers Grade this run only on added evals that have inputs", async () => {
    renderRun();
    await waitFor(() => expect(picker.props?.addedEvals).toHaveLength(2));
    const { show } = picker.props.addedEvalAction;
    expect(picker.props.addedEvals.map(show)).toEqual([true, false]);
  });

  it("grades this run with an eval already on the environment", async () => {
    addRunEvaluation.mockResolvedValue(COUNTS);
    renderRun();
    await waitFor(() => expect(picker.props?.addedEvals).toHaveLength(2));
    fireEvent.click(screen.getByRole("button", { name: "Grade this run" }));
    await waitFor(() =>
      expect(addRunEvaluation).toHaveBeenCalledWith(
        "env-1",
        "ex-1",
        "no_misselling",
      ),
    );
  });

  it("says so when grading an added eval is refused", async () => {
    addRunEvaluation.mockRejectedValue({
      detail: "Run is cancelled; nothing will be graded",
    });
    renderRun();
    await waitFor(() => expect(picker.props?.addedEvals).toHaveLength(2));
    fireEvent.click(screen.getByRole("button", { name: "Grade this run" }));
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "Run is cancelled; nothing will be graded",
        { variant: "error" },
      ),
    );
  });

  it("marks the row being graded busy and holds the others", async () => {
    let resolveGrade;
    addRunEvaluation.mockReturnValue(
      new Promise((resolve) => {
        resolveGrade = resolve;
      }),
    );
    renderRun();
    await waitFor(() => expect(picker.props?.addedEvals).toHaveLength(2));
    fireEvent.click(screen.getByRole("button", { name: "Grade this run" }));
    await waitFor(() =>
      expect(picker.props.addedEvalAction).toMatchObject({
        busyName: "no_misselling",
        disabled: true,
      }),
    );
    resolveGrade(COUNTS);
  });
});

describe("AddEvaluationDrawer — load states", () => {
  const client = () =>
    new QueryClient({
      defaultOptions: {
        queries: { retry: false, retryDelay: 0 },
        mutations: { retry: false },
      },
    });
  const withClient = (c, ui) => (
    <QueryClientProvider client={c}>{ui}</QueryClientProvider>
  );

  it("warns when reopening on a list that failed to refresh", async () => {
    const c = client();
    const { rerender } = rtlRender(
      withClient(c, <AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />),
    );
    await screen.findByTestId("eval-picker");
    rerender(
      withClient(
        c,
        <AddEvaluationDrawer open={false} env={ENV} onClose={vi.fn()} />,
      ),
    );
    await act(() =>
      c.invalidateQueries({ queryKey: ["simulate-environments", "run-test"] }),
    );
    axios.get.mockRejectedValue({ statusCode: 503, detail: "busy" });
    rerender(
      withClient(c, <AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />),
    );
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "Couldn’t refresh the evaluations already added here, so that list may be out of date.",
        { variant: "warning" },
      ),
    );
    expect(picker.props.addedEvals).toHaveLength(2);
  });

  it("keeps the same picker mounted while a refresh is in flight and after it fails", async () => {
    const c = client();
    rtlRender(
      withClient(c, <AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />),
    );
    const node = await screen.findByTestId("eval-picker");
    let fail;
    axios.get.mockReturnValue(
      new Promise((_, reject) => {
        fail = reject;
      }),
    );
    act(() => {
      c.refetchQueries({
        queryKey: ["simulate-environments", "run-test", "rt-1"],
      });
    });
    await waitFor(() =>
      expect(
        c.getQueryState(["simulate-environments", "run-test", "rt-1"])
          .fetchStatus,
      ).toBe("fetching"),
    );
    expect(screen.queryByTestId("eval-picker")).toBe(node);
    await act(async () => {
      fail({ statusCode: 503, detail: "busy" });
    });
    expect(screen.getByTestId("eval-picker")).toBe(node);
  });

  it("shows a spinner, not the not-built message, while the run test's evals load", async () => {
    axios.get.mockReturnValue(new Promise(() => {}));
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    await waitFor(() => expect(axios.get).toHaveBeenCalled());
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    expect(screen.queryByText("Not ready yet")).toBeNull();
    expect(screen.queryByTestId("eval-picker")).toBeNull();
  });

  it("retries the environment itself when that read failed", async () => {
    getHarnessEnvironment.mockRejectedValue({
      statusCode: 500,
      detail: "boom",
    });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    expect(await screen.findByText("boom")).toBeInTheDocument();
    getHarnessEnvironment.mockResolvedValue(detail());
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByTestId("eval-picker");
  });

  it("one Retry reloads both reads when both failed", async () => {
    const c = client();
    c.setQueryData(["harness-environment", "env-1"], detail());
    getHarnessEnvironment.mockRejectedValue({
      statusCode: 500,
      detail: "boom",
    });
    axios.get.mockRejectedValue({ statusCode: 503, detail: "busy" });
    rtlRender(
      withClient(c, <AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />),
    );
    expect(await screen.findByText("boom")).toBeInTheDocument();
    await waitFor(() => expect(axios.get).toHaveBeenCalled());
    getHarnessEnvironment.mockResolvedValue(detail());
    axios.get.mockResolvedValue({
      data: { simulate_eval_configs_detail: CONFIGS },
    });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByTestId("eval-picker");
  });

  it("retrying only the run test does not reload the environment", async () => {
    axios.get.mockRejectedValue({ statusCode: 503, detail: "busy" });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    expect(await screen.findByText("busy")).toBeInTheDocument();
    const before = getHarnessEnvironment.mock.calls.length;
    axios.get.mockResolvedValue({
      data: { simulate_eval_configs_detail: CONFIGS },
    });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByTestId("eval-picker");
    expect(getHarnessEnvironment.mock.calls.length).toBe(before);
  });

  it("opens the picker on a run test with no evals yet", async () => {
    axios.get.mockResolvedValue({
      data: { simulate_eval_configs_detail: [] },
    });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    await screen.findByTestId("eval-picker");
    expect(picker.props.addedEvals).toEqual([]);
  });

  it("hides the picker once closed, even with everything cached", async () => {
    const c = client();
    const { rerender } = rtlRender(
      withClient(c, <AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />),
    );
    await screen.findByTestId("eval-picker");
    rerender(
      withClient(
        c,
        <AddEvaluationDrawer open={false} env={ENV} onClose={vi.fn()} />,
      ),
    );
    expect(screen.queryByTestId("eval-picker")).toBeNull();
  });

  it("does not read the run test while closed, even when the environment is cached", async () => {
    const c = client();
    c.setQueryData(["harness-environment", "env-1"], detail());
    rtlRender(
      withClient(
        c,
        <AddEvaluationDrawer open={false} env={ENV} onClose={vi.fn()} />,
      ),
    );
    await new Promise((r) => setTimeout(r, 20));
    expect(axios.get).not.toHaveBeenCalled();
  });

  // The detail query's own `enabled: open` (not just the picker's early
  // return) must gate the fetch itself, or a drawer mounted closed on every
  // run page pulls the full environment detail for nothing.
  it("does not read the environment detail while closed", async () => {
    rtlRender(
      withClient(
        client(),
        <AddEvaluationDrawer open={false} env={ENV} onClose={vi.fn()} />,
      ),
    );
    await new Promise((r) => setTimeout(r, 20));
    expect(getHarnessEnvironment).not.toHaveBeenCalled();
  });

  // `errorUpdatedAt` is what tells the effect a *new* failure happened, since
  // `refreshFailed` itself does not change value between two failed refreshes.
  // Without it, a person told about a stale list once would never be told
  // again, even after another failed background refetch.
  it("warns again on a second failed refresh, not just the first", async () => {
    const c = client();
    rtlRender(
      withClient(c, <AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />),
    );
    await screen.findByTestId("eval-picker");

    axios.get.mockRejectedValue({ statusCode: 503, detail: "busy" });
    await act(() =>
      c.refetchQueries({
        queryKey: ["simulate-environments", "run-test", "rt-1"],
      }),
    );
    await waitFor(() => expect(enqueueSnackbar).toHaveBeenCalledTimes(1));

    await new Promise((r) => setTimeout(r, 5));
    await act(() =>
      c.refetchQueries({
        queryKey: ["simulate-environments", "run-test", "rt-1"],
      }),
    );
    await waitFor(() => expect(enqueueSnackbar).toHaveBeenCalledTimes(2));
    expect(enqueueSnackbar).toHaveBeenNthCalledWith(
      2,
      "Couldn’t refresh the evaluations already added here, so that list may be out of date.",
      { variant: "warning" },
    );
  });
});
