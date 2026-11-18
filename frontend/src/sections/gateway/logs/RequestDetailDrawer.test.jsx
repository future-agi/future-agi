import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import RequestDetailDrawer from "./RequestDetailDrawer";

vi.mock("./hooks/useRequestDetail", () => ({
  default: () => ({
    data: {
      result: {
        id: "log-detail-id",
        request_id: "req-detail-1234567890",
        model: "gpt-4o-mini",
        provider: "openai",
        status_code: 200,
        latency_ms: 789,
        input_tokens: 30,
        output_tokens: 40,
        cost: "0.002000",
        started_at: "2026-05-21T10:02:00Z",
        is_stream: true,
        cache_hit: true,
        fallback_used: true,
        guardrail_triggered: true,
        is_error: false,
        routing_strategy: "cost",
        resolved_model: "gpt-4o-mini-2026",
        api_key_id: "key-1",
        user_id: "user-1",
        session_id: "session-1",
        request_body: { messages: [{ role: "user", content: "hello" }] },
        response_body: { choices: [{ message: { content: "hi" } }] },
        request_headers: { "x-test": "request" },
        response_headers: { "x-test": "response" },
        guardrail_results: {
          action: "warn",
          checks: [{ name: "pii", action: "warn", latency_ms: 12 }],
        },
      },
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock("../guardrails/FeedbackWidget", () => ({
  default: () => <div>Feedback widget</div>,
}));

describe("RequestDetailDrawer", () => {
  it("renders canonical snake_case details and exposes guardrail tab", async () => {
    render(
      <RequestDetailDrawer open logId="log-detail-id" onClose={vi.fn()} />,
    );

    expect(screen.getByText("789ms")).toBeInTheDocument();
    expect(screen.getByText("30 in / 40 out")).toBeInTheDocument();
    expect(screen.getByText("Strategy:")).toBeInTheDocument();
    expect(screen.getByText(/cost/)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Guardrails" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Request" }));
    expect(screen.getByText(/hello/)).toBeInTheDocument();
    expect(screen.getByText("Request Headers")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Guardrails" }));
    expect(screen.getByText(/warned/)).toBeInTheDocument();
    expect(screen.getByText("pii")).toBeInTheDocument();
    expect(screen.getByText("Latency: 12ms")).toBeInTheDocument();
  });
});
