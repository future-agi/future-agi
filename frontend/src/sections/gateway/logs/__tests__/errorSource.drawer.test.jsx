import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import RequestDetailDrawer from "../RequestDetailDrawer";

// A failed request's drawer says whose words its error message is, above the
// message: the log's error_source. "Error from <provider>" for a provider's
// own error answer (Future AGI on credits), "Refused by the gateway" or
// "Gateway error" for the gateway's own words, "Cancelled by the client" when
// the caller ended the request. A log without a source (stored before it was
// recorded) shows the message alone, as before.

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

const FAILED = {
  id: "failed",
  request_id: "req-failed-400",
  model: "gpt-4o-mini",
  provider: "openai",
  status_code: 400,
  is_error: true,
  error_message: "max_tokens is too large",
  latency_ms: 40,
  input_tokens: 0,
  output_tokens: 0,
  cost: "0.000000",
  started_at: "2026-11-12T01:00:00Z",
  metadata: { billing_mode: "byok" },
  billing: { mode: "byok" },
  attempts: [],
};

const openDrawer = (detail) => {
  state.detail = detail;
  render(<RequestDetailDrawer open logId={detail.id} onClose={vi.fn()} />);
};

describe("a failed request's drawer: whose words the error is", () => {
  it.each([
    ["provider", {}, "Error from openai"],
    [
      "provider",
      { metadata: { billing_mode: "managed" }, billing: { mode: "managed" } },
      "Error from Future AGI",
    ],
    [
      "gateway",
      { status_code: 403, error_message: "Forbidden" },
      "Refused by the gateway",
    ],
    [
      "gateway",
      { status_code: 500, error_message: "internal error" },
      "Gateway error",
    ],
    [
      "client",
      { status_code: 499, error_message: "context canceled" },
      "Cancelled by the client",
    ],
  ])("%s %o reads %s above the message", (source, changes, label) => {
    const detail = { ...FAILED, ...changes, error_source: source };
    openDrawer(detail);
    expect(screen.getByTestId("error-source")).toHaveTextContent(
      new RegExp(`^${label}$`),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(detail.error_message);
  });

  it("shows the message alone for a log without a source", () => {
    openDrawer({ ...FAILED, error_source: null });
    expect(screen.queryByTestId("error-source")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      /^max_tokens is too large$/,
    );
  });
});
