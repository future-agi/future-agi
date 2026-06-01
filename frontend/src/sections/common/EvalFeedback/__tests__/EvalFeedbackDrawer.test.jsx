import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import EvalFeedbackDrawer from "../EvalFeedbackDrawer";

vi.mock("src/components/iconify", () => ({
  default: ({ icon, sx: _sx, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

const RETUNE_OPTIONS = [
  { value: "retune", title: "Re-tune", description: "Just retune" },
  {
    value: "recalculate_row",
    title: "Re- calculate for this row",
    description: "Retune + recalculate this row",
  },
  {
    value: "recalculate_dataset",
    title: "Re-tune and re-calculate for this dataset",
    description: "Retune + recalculate every row",
  },
];

function renderWithQuery(ui) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

function defaultProps(overrides = {}) {
  return {
    open: true,
    onClose: vi.fn(),
    target: { name: "Eval Name", value_infos: { reason: "the reason" } },
    fetchExistingFeedback: vi.fn().mockResolvedValue(null),
    existingFeedbackQueryKey: ["existing-feedback", "row-1"],
    fetchTemplate: vi
      .fn()
      .mockResolvedValue({ output_type: "reason", choices: [] }),
    templateQueryKey: ["template", "metric-1"],
    submitEntry: vi.fn().mockResolvedValue({ feedbackId: "fb-1" }),
    submitAction: vi.fn().mockResolvedValue({ status: "ok" }),
    onAnalyticsEntrySubmit: vi.fn(),
    onSubmitted: vi.fn(),
    retuneOptions: RETUNE_OPTIONS,
    ...overrides,
  };
}

describe("EvalFeedbackDrawer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the entry stage first with the target's eval name", async () => {
    const props = defaultProps();
    renderWithQuery(<EvalFeedbackDrawer {...props} />);

    expect(await screen.findByText("Eval Name")).toBeInTheDocument();
    expect(screen.getByText(/Submit feedback/i)).toBeInTheDocument();
  });

  it("renders a numeric input when outputType is 'score'", async () => {
    const props = defaultProps({
      fetchTemplate: vi
        .fn()
        .mockResolvedValue({ output_type: "score", choices: [] }),
    });
    renderWithQuery(<EvalFeedbackDrawer {...props} />);

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Add Number/i)).toBeInTheDocument();
    });
  });

  it("renders radio options when outputType is 'Pass/Fail'", async () => {
    const props = defaultProps({
      fetchTemplate: vi
        .fn()
        .mockResolvedValue({ output_type: "Pass/Fail", choices: ["Pass", "Fail"] }),
    });
    renderWithQuery(<EvalFeedbackDrawer {...props} />);

    await waitFor(() => {
      expect(screen.getByLabelText("Pass")).toBeInTheDocument();
      expect(screen.getByLabelText("Fail")).toBeInTheDocument();
    });
  });

  it("calls submitEntry then transitions to action stage on entry submit", async () => {
    const props = defaultProps({
      fetchTemplate: vi
        .fn()
        .mockResolvedValue({ output_type: "reason", choices: [] }),
    });
    const user = userEvent.setup();

    renderWithQuery(<EvalFeedbackDrawer {...props} />);

    await waitFor(() => {
      expect(
        screen.getByPlaceholderText(/Improve the tone and grammar/i),
      ).toBeInTheDocument();
    });

    await user.type(
      screen.getByPlaceholderText(/Improve the tone and grammar/i),
      "right value",
    );
    await user.type(
      screen.getByPlaceholderText(
        /Enter what would you like to improve in the prompt/i,
      ),
      "be stricter on hedged refusals",
    );
    await user.click(screen.getByRole("button", { name: /Submit feedback/i }));

    await waitFor(() => {
      expect(props.submitEntry).toHaveBeenCalledTimes(1);
    });
    expect(props.onAnalyticsEntrySubmit).toHaveBeenCalledTimes(1);

    // Action stage rendered
    await waitFor(() => {
      expect(screen.getByText(/Your feedback is submitted/i)).toBeInTheDocument();
      expect(screen.getByText("Re-tune")).toBeInTheDocument();
    });
  });

  it("calls submitAction then onSubmitted + onClose on action submit", async () => {
    const props = defaultProps();
    const user = userEvent.setup();

    renderWithQuery(<EvalFeedbackDrawer {...props} />);

    // Stage 1
    await waitFor(() => {
      expect(
        screen.getByPlaceholderText(/Improve the tone and grammar/i),
      ).toBeInTheDocument();
    });
    await user.type(
      screen.getByPlaceholderText(/Improve the tone and grammar/i),
      "right value",
    );
    await user.type(
      screen.getByPlaceholderText(
        /Enter what would you like to improve in the prompt/i,
      ),
      "explanation text",
    );
    await user.click(screen.getByRole("button", { name: /Submit feedback/i }));

    // Stage 2
    await waitFor(() => {
      expect(screen.getByText("Re-tune")).toBeInTheDocument();
    });
    await user.click(screen.getByText("Re-tune"));
    await user.click(screen.getByRole("button", { name: /Continue/i }));

    await waitFor(() => {
      expect(props.submitAction).toHaveBeenCalledTimes(1);
    });
    const callArg = props.submitAction.mock.calls[0][0];
    expect(callArg.feedbackId).toBe("fb-1");
    expect(callArg.actionValue).toBe("retune");

    await waitFor(() => {
      expect(props.onSubmitted).toHaveBeenCalledTimes(1);
      expect(props.onClose).toHaveBeenCalledTimes(1);
    });
  });

  it("skips submitEntry when existingFeedback.id is present and forwards entryPayload to submitAction", async () => {
    const props = defaultProps({
      fetchExistingFeedback: vi.fn().mockResolvedValue({
        id: "existing-fb-1",
        value: "old value",
        comment: "old comment",
        action_type: "retune",
      }),
    });
    const user = userEvent.setup();

    renderWithQuery(<EvalFeedbackDrawer {...props} />);

    // Pre-filled fields are visible
    await waitFor(() => {
      expect(screen.getByDisplayValue("old value")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /Submit feedback/i }));

    // Should jump straight to action stage without calling submitEntry
    await waitFor(() => {
      expect(screen.getByText(/Your feedback is submitted/i)).toBeInTheDocument();
    });
    expect(props.submitEntry).not.toHaveBeenCalled();

    await user.click(screen.getByText("Re-tune"));
    await user.click(screen.getByRole("button", { name: /Continue/i }));

    await waitFor(() => {
      expect(props.submitAction).toHaveBeenCalledTimes(1);
    });
    const callArg = props.submitAction.mock.calls[0][0];
    expect(callArg.feedbackId).toBe("existing-fb-1");
    expect(callArg.entryPayload).toEqual({
      value: "old value",
      explanation: "old comment",
    });
  });
});
