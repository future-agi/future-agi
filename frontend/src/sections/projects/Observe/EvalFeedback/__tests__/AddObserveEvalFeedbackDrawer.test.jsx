import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const {
  mockGet,
  mockPost,
  mockEnqueue,
  storeRef,
} = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockEnqueue: vi.fn(),
  storeRef: { hook: null },
}));

// Mock the Observe store so the test owns a fresh zustand slice each run.
vi.mock("../useAddObserveEvalFeedbackStore", async () => {
  const { create: makeStore } = await import("zustand");
  const hook = makeStore((set) => ({
    addObserveEvalFeedbackTarget: null,
    setAddObserveEvalFeedbackTarget: (value) =>
      set(() => ({ addObserveEvalFeedbackTarget: value })),
  }));
  storeRef.hook = hook;
  return { __esModule: true, default: hook };
});

vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => mockGet(...args),
    post: (...args) => mockPost(...args),
  },
  endpoints: {
    project: {
      getEvalConfig: (id) => `/tracer/custom-eval-config/${id}/`,
      updateEvalTaskConfig: (id) => `/tracer/custom-eval-config/${id}/`,
      submitFeedback: "/tracer/observation-span/submit_feedback/",
      applySubmitFeedback:
        "/tracer/observation-span/submit_feedback_action_type/",
      getObserveFeedback: "/tracer/observation-span/get_feedback/",
    },
    develop: {
      eval: {
        getEvalDetail: (id) => `/model-hub/eval-templates/${id}/detail/`,
      },
    },
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: (...args) => mockEnqueue(...args),
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, sx: _sx, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

import AddObserveEvalFeedbackDrawer from "../AddObserveEvalFeedbackDrawer";

function renderWithQuery(ui) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

// output_type values below mirror the live BE wire shape — values verified
// against /tracer/trace-session/{id}/eval_logs/ on 2026-06-02:
//   - "pass_fail"     → renders Passed/Failed radio
//   - "percentage"    → renders numeric input
//   - "deterministic" → renders choices radio (choices supplied on target)
const SPAN_TARGET = {
  target_type: "span",
  observation_span_id: "span-1",
  custom_eval_config_id: "cec-1",
  name: "Toxicity",
  output_type: "pass_fail",
  value_infos: { reason: "the model used a slur" },
  eval_task_id: "task-1",
  has_error: false,
};

const TRACE_TARGET = {
  target_type: "trace",
  observation_span_id: "rootspan-99",
  trace_id: "trace-99",
  custom_eval_config_id: "cec-99",
  name: "Trace Quality",
  output_type: "percentage",
  value_infos: { reason: "trace failed midway" },
  eval_task_id: null,
  has_error: false,
};

const SESSION_TARGET = {
  target_type: "session",
  trace_session_id: "sess-5",
  custom_eval_config_id: "cec-5",
  name: "Session Coherence",
  // No output_type set → wrapper falls through to REASON free-text fallback,
  // which is the safe default for unknown / unmapped BE values.
  value_infos: { reason: "session lost context" },
  eval_task_id: "task-5",
  has_error: false,
};

describe("AddObserveEvalFeedbackDrawer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockImplementation((url) => {
      if (url.includes("/tracer/observation-span/get_feedback/")) {
        // Default: no prior feedback. Tests that want the existing-feedback
        // pre-fill override this mock per-case.
        return Promise.resolve({
          data: {
            result: {
              feedback_id: null,
              value: null,
              explanation: null,
              feedback_improvement: null,
              action_type: null,
            },
          },
        });
      }
      if (url.includes("/tracer/custom-eval-config/")) {
        return Promise.resolve({
          data: {
            result: { output_type: "Pass/Fail", choices: ["Pass", "Fail"] },
          },
        });
      }
      return Promise.resolve({ data: { result: null } });
    });
    mockPost.mockImplementation((url) => {
      if (url.endsWith("submit_feedback/")) {
        return Promise.resolve({
          data: { result: { feedback_id: "fb-new-1" } },
        });
      }
      if (url.endsWith("submit_feedback_action_type/")) {
        return Promise.resolve({
          data: { result: { status: "ok", recalculated_count: 3 } },
        });
      }
      return Promise.resolve({ data: { result: { ok: true } } });
    });
  });

  it("renders nothing while target is null", () => {
    storeRef.hook.getState().setAddObserveEvalFeedbackTarget(null);
    renderWithQuery(<AddObserveEvalFeedbackDrawer />);
    expect(screen.queryByText(/Submit feedback/i)).not.toBeInTheDocument();
  });

  it("derives templateData from target.output_type without a BE roundtrip", async () => {
    // The /tracer/custom-eval-config/{id}/ serializer does NOT include
    // output_type (only the eval_template UUID). Observe eval rows carry
    // output_type on the target directly (from EvalsTabView or the voice
    // normalizer). Wrapper synthesizes templateData without fetching;
    // verifies the Pass/Fail target renders its radios.
    storeRef.hook.getState().setAddObserveEvalFeedbackTarget(SPAN_TARGET);
    renderWithQuery(<AddObserveEvalFeedbackDrawer />);

    await waitFor(() => {
      expect(screen.getByLabelText("Passed")).toBeInTheDocument();
      expect(screen.getByLabelText("Failed")).toBeInTheDocument();
    });
    // Critical: no template fetch was made — the only GETs would be the
    // (no-op) existing-feedback fetch. Assert getEvalConfig was NOT hit.
    expect(mockGet).not.toHaveBeenCalledWith(
      expect.stringContaining("/tracer/custom-eval-config/"),
    );
  });

  it("submits stage 1 with span-shaped payload + retains existing-feedback fetch as no-op", async () => {
    storeRef.hook.getState().setAddObserveEvalFeedbackTarget(SPAN_TARGET);
    renderWithQuery(<AddObserveEvalFeedbackDrawer />);

    const user = userEvent.setup();
    await waitFor(() => {
      expect(screen.getByLabelText("Passed")).toBeInTheDocument();
    });
    await user.click(screen.getByLabelText("Failed"));
    await user.type(
      screen.getByPlaceholderText(
        /Enter what would you like to improve in the prompt/i,
      ),
      "tighten the rubric on slurs",
    );
    await user.click(screen.getByRole("button", { name: /Submit feedback/i }));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        "/tracer/observation-span/submit_feedback/",
        expect.objectContaining({
          target_type: "span",
          observation_span_id: "span-1",
          custom_eval_config_id: "cec-1",
          feedback_value: "Failed",
          feedback_explanation: "tighten the rubric on slurs",
        }),
      );
    });
    // No trace_id / trace_session_id on a span payload — the wrapper's
    // polymorphicIdsFor() must drop those keys so reject_unknown_fields=True
    // on the BE doesn't 400.
    const stage1Call = mockPost.mock.calls.find(
      ([url]) => url === "/tracer/observation-span/submit_feedback/",
    );
    expect(stage1Call[1]).not.toHaveProperty("trace_id");
    expect(stage1Call[1]).not.toHaveProperty("trace_session_id");
  });

  it("includes trace_id on a trace target payload", async () => {
    storeRef.hook.getState().setAddObserveEvalFeedbackTarget(TRACE_TARGET);
    mockGet.mockImplementation(() =>
      Promise.resolve({
        data: { result: { output_type: "score", choices: [] } },
      }),
    );
    renderWithQuery(<AddObserveEvalFeedbackDrawer />);

    const user = userEvent.setup();
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Add Number/i)).toBeInTheDocument();
    });
    await user.type(screen.getByPlaceholderText(/Add Number/i), "42");
    await user.type(
      screen.getByPlaceholderText(
        /Enter what would you like to improve in the prompt/i,
      ),
      "score should be higher",
    );
    await user.click(screen.getByRole("button", { name: /Submit feedback/i }));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        "/tracer/observation-span/submit_feedback/",
        expect.objectContaining({
          target_type: "trace",
          observation_span_id: "rootspan-99",
          trace_id: "trace-99",
          custom_eval_config_id: "cec-99",
        }),
      );
    });
  });

  it("includes trace_session_id on a session target payload", async () => {
    storeRef.hook.getState().setAddObserveEvalFeedbackTarget(SESSION_TARGET);
    mockGet.mockImplementation(() =>
      Promise.resolve({
        data: { result: { output_type: "reason", choices: [] } },
      }),
    );
    renderWithQuery(<AddObserveEvalFeedbackDrawer />);

    const user = userEvent.setup();
    await waitFor(() => {
      expect(
        screen.getByPlaceholderText(/Improve the tone and grammar/i),
      ).toBeInTheDocument();
    });
    await user.type(
      screen.getByPlaceholderText(/Improve the tone and grammar/i),
      "more coherent",
    );
    await user.type(
      screen.getByPlaceholderText(
        /Enter what would you like to improve in the prompt/i,
      ),
      "context window matters",
    );
    await user.click(screen.getByRole("button", { name: /Submit feedback/i }));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        "/tracer/observation-span/submit_feedback/",
        expect.objectContaining({
          target_type: "session",
          trace_session_id: "sess-5",
          custom_eval_config_id: "cec-5",
        }),
      );
    });
    const stage1Call = mockPost.mock.calls.find(
      ([url]) => url === "/tracer/observation-span/submit_feedback/",
    );
    expect(stage1Call[1]).not.toHaveProperty("observation_span_id");
    expect(stage1Call[1]).not.toHaveProperty("trace_id");
  });

  it("GETs existing feedback with polymorphic query params on open", async () => {
    storeRef.hook.getState().setAddObserveEvalFeedbackTarget(SPAN_TARGET);
    renderWithQuery(<AddObserveEvalFeedbackDrawer />);

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith(
        "/tracer/observation-span/get_feedback/",
        expect.objectContaining({
          params: expect.objectContaining({
            target_type: "span",
            observation_span_id: "span-1",
            custom_eval_config_id: "cec-1",
          }),
        }),
      );
    });
    // Reject_unknown_fields=True on the BE — must not send sibling-target keys.
    const getCall = mockGet.mock.calls.find(
      ([url]) => url === "/tracer/observation-span/get_feedback/",
    );
    expect(getCall[1].params).not.toHaveProperty("trace_id");
    expect(getCall[1].params).not.toHaveProperty("trace_session_id");
  });

  it("pre-fills entry stage from existing feedback + reuses feedback_id on stage 2", async () => {
    mockGet.mockImplementation((url) => {
      if (url.includes("/tracer/observation-span/get_feedback/")) {
        return Promise.resolve({
          data: {
            result: {
              feedback_id: "fb-existing-1",
              value: "Failed",
              // BE returns `explanation`; wrapper renames to `comment` to
              // match the useEvalFeedbackFlow hook's vocabulary.
              explanation: "judge missed sarcasm",
              feedback_improvement: "tighten sarcasm detection",
              action_type: null,
            },
          },
        });
      }
      return Promise.resolve({ data: { result: null } });
    });
    storeRef.hook.getState().setAddObserveEvalFeedbackTarget(SPAN_TARGET);
    renderWithQuery(<AddObserveEvalFeedbackDrawer />);

    // Entry stage opens with the prior radio selection prefilled. The
    // explanation field defaults from existingFeedback.comment (which the
    // wrapper maps from BE explanation).
    const user = userEvent.setup();
    await waitFor(() => {
      expect(screen.getByLabelText("Failed")).toBeChecked();
    });

    await user.click(screen.getByRole("button", { name: /Submit feedback/i }));

    // Stage 2 appears WITHOUT a POST to submit_feedback — the hook detected
    // existingFeedback.id and skipped the create roundtrip.
    await waitFor(() => {
      expect(screen.getByText("Re-tune")).toBeInTheDocument();
    });
    expect(mockPost).not.toHaveBeenCalledWith(
      "/tracer/observation-span/submit_feedback/",
      expect.anything(),
    );

    // Action submit forwards the existing feedback_id, not a new one.
    await user.click(screen.getByText("Re-tune"));
    await user.click(screen.getByRole("button", { name: /Continue/i }));
    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        "/tracer/observation-span/submit_feedback_action_type/",
        expect.objectContaining({
          feedback_id: "fb-existing-1",
          action_type: "retune",
        }),
      );
    });
  });

  it("offers canonical {retune, recalculate, retune_recalculate} action_type values + disables RETUNE_RECALCULATE without eval_task_id", async () => {
    storeRef.hook
      .getState()
      .setAddObserveEvalFeedbackTarget(TRACE_TARGET); // eval_task_id = null
    mockGet.mockImplementation(() =>
      Promise.resolve({
        data: { result: { output_type: "score", choices: [] } },
      }),
    );
    renderWithQuery(<AddObserveEvalFeedbackDrawer />);

    const user = userEvent.setup();
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Add Number/i)).toBeInTheDocument();
    });
    await user.type(screen.getByPlaceholderText(/Add Number/i), "10");
    await user.type(
      screen.getByPlaceholderText(
        /Enter what would you like to improve in the prompt/i,
      ),
      "x",
    );
    await user.click(screen.getByRole("button", { name: /Submit feedback/i }));

    // Stage 2 — verify the three radio inputs' canonical values + the disabled state.
    await waitFor(() => {
      expect(screen.getByText("Re-tune")).toBeInTheDocument();
    });
    const radios = screen.getAllByRole("radio");
    const values = radios.map((r) => r.getAttribute("value")).sort();
    expect(values).toEqual(
      ["recalculate", "retune", "retune_recalculate"].sort(),
    );

    const retuneRecalcRadio = radios.find(
      (r) => r.getAttribute("value") === "retune_recalculate",
    );
    expect(retuneRecalcRadio).toBeDisabled();
  });

  it("submits stage 2 with action_type=recalculate + the same target_type + ids", async () => {
    storeRef.hook.getState().setAddObserveEvalFeedbackTarget(SPAN_TARGET);
    renderWithQuery(<AddObserveEvalFeedbackDrawer />);

    const user = userEvent.setup();
    await waitFor(() => {
      expect(screen.getByLabelText("Passed")).toBeInTheDocument();
    });
    await user.click(screen.getByLabelText("Failed"));
    await user.type(
      screen.getByPlaceholderText(
        /Enter what would you like to improve in the prompt/i,
      ),
      "x",
    );
    await user.click(screen.getByRole("button", { name: /Submit feedback/i }));

    await waitFor(() => {
      expect(screen.getByText("Re-tune")).toBeInTheDocument();
    });
    await user.click(screen.getByText("Re-tune"));
    await user.click(screen.getByRole("button", { name: /Continue/i }));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        "/tracer/observation-span/submit_feedback_action_type/",
        expect.objectContaining({
          target_type: "span",
          observation_span_id: "span-1",
          custom_eval_config_id: "cec-1",
          feedback_id: "fb-new-1",
          action_type: "retune",
        }),
      );
    });
  });

  it("surfaces recalculated_count in the success toast for RETUNE_RECALCULATE", async () => {
    storeRef.hook.getState().setAddObserveEvalFeedbackTarget(SPAN_TARGET);
    renderWithQuery(<AddObserveEvalFeedbackDrawer />);

    const user = userEvent.setup();
    await waitFor(() => {
      expect(screen.getByLabelText("Passed")).toBeInTheDocument();
    });
    await user.click(screen.getByLabelText("Failed"));
    await user.type(
      screen.getByPlaceholderText(
        /Enter what would you like to improve in the prompt/i,
      ),
      "x",
    );
    await user.click(screen.getByRole("button", { name: /Submit feedback/i }));

    await waitFor(() => {
      expect(screen.getByText("Re-tune")).toBeInTheDocument();
    });
    await user.click(
      screen.getByText("Re-tune and re-calculate for this eval run"),
    );
    await user.click(screen.getByRole("button", { name: /Continue/i }));

    await waitFor(() => {
      // BE returns recalculated_count: 3 (mocked above); the toast must surface it.
      expect(mockEnqueue).toHaveBeenCalledWith(
        "Recalculating 3 evals.",
        expect.objectContaining({ variant: "success" }),
      );
    });
  });
});
