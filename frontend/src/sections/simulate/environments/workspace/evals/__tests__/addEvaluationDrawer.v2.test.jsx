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
// The drawer asks whether error localization is available here; the real
// hook would read deployment info through the axios mock below.
const errorLocalization = vi.hoisted(() => ({ available: true }));
vi.mock("src/hooks/useErrorLocalization", () => ({
  useErrorLocalizationAvailable: () => errorLocalization.available,
}));
// The picker's localizer toggle is also inert while agentic evals are locked.
const agentEval = vi.hoisted(() => ({ locked: false }));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureLocked: () => ({ locked: agentEval.locked }),
}));

vi.mock("src/sections/common/EvalPicker", async () => ({
  serializeEvalConfig: (
    await vi.importActual("src/sections/common/EvalPicker/serializeEvalConfig")
  ).serializeEvalConfig,
  // Exposes the multi-select flow: ticking, the header action, the progress
  // slot and the config screen's primary button.
  EvalPickerDrawer: (props) => {
    picker.props = props;
    if (!props.open) return null;
    const pick = props.initialEval;
    return (
      <div data-testid="eval-picker">
        {props.multiSelect && (
          <>
            <button
              type="button"
              onClick={() =>
                props.onToggleSelect({ id: "tpl-a", name: "tone_a" })
              }
            >
              tick a
            </button>
            <button
              type="button"
              onClick={() =>
                props.onToggleSelect({ id: "tpl-b", name: "tone_b" })
              }
            >
              tick b
            </button>
          </>
        )}
        {props.headerAction}
        {props.progress}
        {pick && (
          <button
            type="button"
            onClick={() =>
              props
                .onEvalAdded({
                  templateId: pick.id,
                  name: pick.name,
                  mapping: { output: "call.transcript" },
                  config: {},
                })
                .catch(() => {})
            }
          >
            {props.primaryLabel || "save"}
          </button>
        )}
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
  updateAppliedEvaluation: vi.fn(),
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
    editable: true,
  },
  {
    id: "c2",
    name: "call_quality_score",
    template_id: "tpl-result",
    mapping: {},
    editable: false,
  },
];

const axios = (await import("src/utils/axios")).default;
const { addRunEvaluation, getHarnessEnvironment, updateAppliedEvaluation } =
  await import("src/api/simulate-environments/harnessEnvironments");
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
  errorLocalization.available = true;
  agentEval.locked = false;
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
  updateAppliedEvaluation.mockReset();
  enqueueSnackbar.mockReset();
});

describe("Add evaluations: tick several, map each", () => {
  it("tick several evals, map each with Next, finish with Add N evaluations (one real POST per eval)", async () => {
    const onClose = vi.fn();
    render(<AddEvaluationDrawer open env={ENV} onClose={onClose} />);
    fireEvent.click(await screen.findByText("tick a"));
    fireEvent.click(screen.getByText("tick b"));
    fireEvent.click(
      screen.getByRole("button", { name: "Add Evaluations (2)" }),
    );
    expect(screen.getByText("0%")).toBeInTheDocument();
    expect(screen.getByText("(1/2)")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("(2/2)")).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add 2 evaluations" }));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
    expect(
      axios.post.mock.calls.map(([, b]) => b.evaluations_config[0].name),
    ).toEqual(["tone_a", "tone_b"]);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("stays on the eval whose save the server refused", async () => {
    axios.post.mockRejectedValueOnce(new Error("nope"));
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    fireEvent.click(await screen.findByText("tick a"));
    fireEvent.click(screen.getByText("tick b"));
    fireEvent.click(
      screen.getByRole("button", { name: "Add Evaluations (2)" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    expect(screen.getByText("(1/2)")).toBeInTheDocument();
  });

  it("editing one eval stays single (no ticking)", async () => {
    render(
      <AddEvaluationDrawer
        open
        env={ENV}
        onClose={vi.fn()}
        editingEvalId="c1"
      />,
    );
    await screen.findByTestId("eval-picker");
    expect(picker.props.multiSelect).toBeFalsy();
  });
});
