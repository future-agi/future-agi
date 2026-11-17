import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import RequestDetailDrawer from "../RequestDetailDrawer";

// A refused request's drawer names the gateway's error code beside its
// status, and keeps the status label ("403") and the gateway's message in the
// error box. The decisions endpoint logs only "Forbidden" as its message, so
// the code is its reason. A row with no code (stored before the code was, or
// a request without one) shows none.

const { state } = vi.hoisted(() => ({ state: { detail: {} } }));

vi.mock("../hooks/useRequestDetail", () => ({
  default: () => ({
    data: { result: state.detail },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock("../../guardrails/FeedbackWidget", () => ({
  default: () => <div>Feedback widget</div>,
}));

const REFUSED = {
  id: "refused",
  request_id: "req-refused-403",
  model: "gpt-4o-mini",
  provider: "openai",
  status_code: 403,
  is_error: true,
  error_message: "Forbidden",
  error_code: "decision_refused",
  latency_ms: 4,
  input_tokens: 0,
  output_tokens: 0,
  cost: "0.000000",
  started_at: "2026-11-11T10:02:00Z",
  metadata: {},
  attempts: [],
};

describe("a refused request's drawer: the gateway's error code beside its status", () => {
  it("shows the code beside the status and keeps the status and the message", () => {
    state.detail = REFUSED;
    render(<RequestDetailDrawer open logId="refused" onClose={vi.fn()} />);
    const code = screen.getByLabelText("Error code");
    expect(code).toHaveTextContent(/^decision_refused$/);
    // Beside the status: in the same line as the 403 chip.
    expect(within(code.parentElement).getByText("403")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/^Forbidden$/);
  });

  it("shows no code for a row that has none", () => {
    state.detail = { ...REFUSED, error_code: null };
    render(<RequestDetailDrawer open logId="refused" onClose={vi.fn()} />);
    expect(screen.queryByLabelText("Error code")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/^Forbidden$/);
  });
});
