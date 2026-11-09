import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import { HelmetProvider } from "react-helmet-async";
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
      {
        id: "managed-row-id",
        model: "deepseek/deepseek-v4.1-flash",
        provider: "openrouter",
        billing_mode: "managed",
        status_code: 200,
        latency_ms: 80,
        total_tokens: 7,
        started_at: "2026-05-21T10:02:00Z",
      },
      {
        id: "own-row-id",
        model: "mistralai/ministral-8b",
        provider: "openrouter",
        billing_mode: "byok",
        status_code: 200,
        latency_ms: 90,
        total_tokens: 9,
        started_at: "2026-05-21T10:03:00Z",
      },
    ],
    isLoading: false,
  }),
}));

describe("SessionDetailDrawer", () => {
  it("renders request rows using canonical snake_case API fields", () => {
    render(
      <HelmetProvider>
        <SessionDetailDrawer
          open
          sessionId="session-row-id"
          onClose={vi.fn()}
        />
      </HelmetProvider>,
    );
    const list = within(
      screen.getByRole("list", { name: "Requests in this session" }),
    );

    expect(
      screen.getByRole("heading", { name: "Gateway Session" }),
    ).toBeInTheDocument();
    expect(screen.getByText("gateway-session-1")).toBeInTheDocument();
    expect(list.getByText("gpt-4o-mini")).toBeInTheDocument();
    expect(list.getByText("502")).toBeInTheDocument();
    expect(list.getByText(/321 ms/)).toBeInTheDocument();
    expect(list.getByText(/42 tokens/)).toBeInTheDocument();
    expect(screen.getByText("source")).toBeInTheDocument();
  });

  // A managed request names Future AGI
  // as its provider; an own OpenRouter key keeps its name.
  it("names a managed request's provider Future AGI", () => {
    render(
      <HelmetProvider>
        <SessionDetailDrawer
          open
          sessionId="session-row-id"
          onClose={vi.fn()}
        />
      </HelmetProvider>,
    );
    const [, managed, own] = within(
      screen.getByRole("list", { name: "Requests in this session" }),
    ).getAllByRole("listitem");
    expect(managed).toHaveTextContent("· Future AGI");
    expect(managed).not.toHaveTextContent("openrouter");
    expect(own).toHaveTextContent("· openrouter");
  });
});
