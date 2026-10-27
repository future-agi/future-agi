import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import SessionDetailDrawer from "./SessionDetailDrawer";

vi.mock("./hooks/useSessions", () => ({
  useSessionDetail: () => ({
    data: {
      id: "session-row-id",
      session_id: "gateway-session-1",
      name: "Gateway Session",
      status: "active",
      metadata: { source: "test" },
      created_at: "2026-05-21T10:00:00Z",
      stats: {
        request_count: 1,
        total_cost: "0.001",
        total_tokens: 42,
        avg_latency_ms: 123,
      },
    },
    isLoading: false,
  }),
  useSessionRequests: () => ({
    data: [
      {
        id: "request-row-id",
        model: "gpt-4o-mini",
        status_code: 502,
        is_error: true,
        latency_ms: 321,
        total_tokens: 42,
        started_at: "2026-05-21T10:01:00Z",
      },
    ],
    isLoading: false,
  }),
}));

describe("SessionDetailDrawer", () => {
  it("renders request rows using canonical snake_case API fields", () => {
    render(
      <SessionDetailDrawer open sessionId="session-row-id" onClose={vi.fn()} />,
    );
    const table = within(screen.getByRole("table"));

    expect(screen.getByText("Session Detail")).toBeInTheDocument();
    expect(screen.getByText("Gateway Session")).toBeInTheDocument();
    expect(table.getByText("gpt-4o-mini")).toBeInTheDocument();
    expect(table.getByText("502")).toBeInTheDocument();
    expect(table.getByText("321ms")).toBeInTheDocument();
    expect(table.getByText("42")).toBeInTheDocument();
    expect(table.getByText(/May 21, 2026/)).toBeInTheDocument();
  });
});
