import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import RequestDetailDrawer from "./RequestDetailDrawer";

// Fields a test adds to the log the mocked detail hook returns.
const { extraDetail } = vi.hoisted(() => ({ extraDetail: { current: {} } }));

vi.mock("./hooks/useRequestDetail", () => ({
  default: () => ({
    data: {
      result: {
        ...extraDetail.current,
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

  it("shows billing and auto routing on the Overview tab", () => {
    extraDetail.current = {
      metadata: {
        billing_mode: "managed",
        cost_base: "0.001905",
        cost_markup_percent: "5",
        cost_source: "catalog",
        auto_requested: "auto",
        auto_model: "gpt-4o-mini",
        auto_task_tier: "simple",
        auto_complexity: "4",
        auto_preference: "cost",
        auto_eligible: "3",
        auto_considered: "9",
        auto_decision: "{truncated",
      },
    };
    render(
      <RequestDetailDrawer open logId="log-detail-id" onClose={vi.fn()} />,
    );

    expect(screen.getByText("Billing")).toBeInTheDocument();
    expect(
      screen.getByText(/Managed \(FutureAGI credit\)/),
    ).toBeInTheDocument();
    expect(screen.getByText("Markup:").parentElement).toHaveTextContent("5%");
    expect(screen.getByText("Auto routing")).toBeInTheDocument();
    expect(screen.getByText("Task:").parentElement).toHaveTextContent(
      "simple (complexity 4)",
    );
    // A truncated decision document costs the ranking, not the block.
    expect(
      screen.getByText("The full ranking for this request could not be read."),
    ).toBeInTheDocument();
  });
});
