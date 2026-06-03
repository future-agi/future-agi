import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { mockGet, storeRef } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  storeRef: { hook: null },
}));

vi.mock(
  "src/sections/projects/Observe/EvalFeedback/useAddObserveEvalFeedbackStore",
  async () => {
    const { create: makeStore } = await import("zustand");
    const hook = makeStore((set) => ({
      addObserveEvalFeedbackTarget: null,
      setAddObserveEvalFeedbackTarget: (value) =>
        set(() => ({ addObserveEvalFeedbackTarget: value })),
    }));
    storeRef.hook = hook;
    return { __esModule: true, default: hook };
  },
);

vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => mockGet(...args) },
  endpoints: {
    project: { getSessionEvalLogs: (id) => `/sessions/${id}/eval_logs/` },
  },
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="iconify" data-icon={icon} />,
}));

import SessionEvalsList from "../SessionEvalsList";

function renderWithQuery(ui) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const okItem = {
  id: "log-1",
  eval_name: "Coherence",
  status: "success",
  result: "Passed",
  score: 0.92,
  reason: "session stayed on-topic",
  created_at: "2026-06-01T12:00:00Z",
  eval_id: "cec-1",
  detail: { output_type: "Pass/Fail", eval_task_id: "task-1" },
};
const errorItem = {
  id: "log-2",
  eval_name: "Toxicity",
  status: "error",
  result: null,
  score: null,
  reason: null,
  created_at: "2026-06-01T12:01:00Z",
  eval_id: "cec-2",
  detail: { output_type: "Pass/Fail", error_message: "judge timeout" },
};

describe("SessionEvalsList — Add feedback chip", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    storeRef.hook.getState().setAddObserveEvalFeedbackTarget(null);
    mockGet.mockResolvedValue({
      data: { result: { items: [okItem, errorItem], total: 2 } },
    });
  });

  it("renders a trailing chip per row", async () => {
    renderWithQuery(<SessionEvalsList sessionId="sess-5" />);
    await waitFor(() => {
      expect(screen.getAllByTestId("add-feedback-chip")).toHaveLength(2);
    });
  });

  it("dispatches a SESSION-shaped target into the Observe store on click", async () => {
    renderWithQuery(<SessionEvalsList sessionId="sess-5" />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getAllByTestId("add-feedback-chip")).toHaveLength(2);
    });
    const chips = screen.getAllByTestId("add-feedback-chip");
    await user.click(chips[0]); // the OK row's chip

    const target = storeRef.hook.getState().addObserveEvalFeedbackTarget;
    expect(target).toMatchObject({
      target_type: "session",
      trace_session_id: "sess-5",
      custom_eval_config_id: "cec-1",
      name: "Coherence",
      output_type: "Pass/Fail",
      eval_task_id: "task-1",
      has_error: false,
    });
  });

  it("disables the chip on errored rows (pointer-events: none + tooltip)", async () => {
    renderWithQuery(<SessionEvalsList sessionId="sess-5" />);
    await waitFor(() => {
      expect(screen.getAllByTestId("add-feedback-chip")).toHaveLength(2);
    });

    const chips = screen.getAllByTestId("add-feedback-chip");
    const errorChipStyle = window.getComputedStyle(chips[1]);
    expect(errorChipStyle.pointerEvents).toBe("none");
    expect(parseFloat(errorChipStyle.opacity)).toBeLessThan(1);

    // The OK row's chip stays interactive.
    const okChipStyle = window.getComputedStyle(chips[0]);
    expect(okChipStyle.pointerEvents).not.toBe("none");
  });
});
