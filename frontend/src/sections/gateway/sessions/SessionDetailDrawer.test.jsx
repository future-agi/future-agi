import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import { HelmetProvider } from "react-helmet-async";
import SessionDetailDrawer from "./SessionDetailDrawer";

const queries = vi.hoisted(() => ({ detail: {}, requests: {} }));
vi.mock("./hooks/useSessions", () => ({
  useSessionDetail: () => queries.detail,
  useSessionRequests: () => queries.requests,
}));

beforeEach(() => {
  queries.detail = {
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
    isFetching: false,
    error: null,
    refetch: vi.fn().mockResolvedValue({}),
  };
  queries.requests = {
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
    isFetching: false,
    error: null,
    refetch: vi.fn().mockResolvedValue({}),
  };
});

function renderDrawer() {
  return render(
    <HelmetProvider>
      <SessionDetailDrawer open sessionId="session-row-id" onClose={vi.fn()} />
    </HelmetProvider>,
  );
}

describe("SessionDetailDrawer", () => {
  it("renders request rows using canonical snake_case API fields", () => {
    renderDrawer();
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
    expect(screen.getByRole("link", { name: "Open in Logs" })).toHaveAttribute(
      "href",
      "/dashboard/gateway/logs?session_id=gateway-session-1",
    );
  });

  it("discloses the latest-100 subset and links the public session ID to all logs", () => {
    queries.detail.data.stats.request_count = 250;
    queries.requests.data = Array.from({ length: 100 }, (_, i) => ({
      ...queries.requests.data[0],
      id: `request-${i}`,
    }));
    renderDrawer();
    expect(
      screen.getByText(/Showing latest 100 of 250 requests/),
    ).toHaveTextContent("oldest first within this subset");
    expect(
      screen.getByRole("link", { name: "View all in Logs" }),
    ).toHaveAttribute(
      "href",
      "/dashboard/gateway/logs?session_id=gateway-session-1",
    );
  });

  it("does not label a complete short session as a subset", () => {
    queries.detail.data.stats.request_count = 3;
    renderDrawer();
    expect(screen.queryByText(/within this subset/)).not.toBeInTheDocument();
  });

  // A managed request names Future AGI as its provider; an own key keeps its name.
  it("names a managed request's provider Future AGI", () => {
    renderDrawer();
    const [, managed, own] = within(
      screen.getByRole("list", { name: "Requests in this session" }),
    ).getAllByRole("listitem");
    expect(managed).toHaveTextContent("· Future AGI");
    expect(managed).not.toHaveTextContent("openrouter");
    expect(own).toHaveTextContent("· openrouter");
  });

  it.each([400, 401, 403, 500, 503, 504, undefined])(
    "does not infer not-found from a message with normalized status %s",
    (statusCode) => {
      queries.detail.data = undefined;
      queries.detail.error = { statusCode, message: "Session not found" };
      renderDrawer();
      expect(
        screen.getByText(/Session details could not be loaded/),
      ).toBeInTheDocument();
      expect(
        screen.queryByText(/Session not found|may have been deleted/),
      ).not.toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Retry details" }),
      ).toBeEnabled();
      expect(
        screen.getByRole("list", { name: "Requests in this session" }),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("link", { name: "Open in Logs" }),
      ).not.toBeInTheDocument();
    },
  );

  // The shared axios client rejects HTTP failures with statusCode, not response.
  it.each([
    { statusCode: 404, detail: "Not found." },
    { response: { status: 404 } },
    { statusCode: undefined, response: { status: 404 } },
    { statusCode: 404, response: { status: 503 } },
  ])("calls a confirmed 404 not found and retains requests: %j", (error) => {
    queries.detail.data = undefined;
    queries.detail.error = error;
    renderDrawer();
    expect(screen.getByText("Session not found")).toBeInTheDocument();
    expect(
      screen.getByText("The API could not find this session."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Session details could not be loaded"),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry details" })).toBeEnabled();
    expect(
      screen.getByRole("list", { name: "Requests in this session" }),
    ).toBeInTheDocument();
  });

  it.each([400, 503])(
    "prefers normalized status %s over a nested 404",
    (statusCode) => {
      queries.detail.data = undefined;
      queries.detail.error = { statusCode, response: { status: 404 } };
      renderDrawer();
      expect(
        screen.getByText("Session details could not be loaded"),
      ).toBeInTheDocument();
      expect(screen.queryByText("Session not found")).not.toBeInTheDocument();
    },
  );

  it("keeps the retrieve view's normalized missing-object 400 generic", () => {
    // AgentccSessionViewSet.retrieve catches get_object failures and calls
    // GeneralMethods.bad_request(str(error)); it does not return HTTP 404.
    const message = "No AgentccSession matches the given query.";
    queries.detail.data = undefined;
    queries.detail.error = {
      status: false,
      type: "validation_error",
      code: "invalid",
      detail: message,
      message,
      error: message,
      result: message,
      statusCode: 400,
      transportCode: "ERR_BAD_REQUEST",
      responseHeaders: { "x-request-id": undefined },
    };
    renderDrawer();
    expect(
      screen.getByText("Session details could not be loaded"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Session not found")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry details" })).toBeEnabled();
  });

  it("does not label a normalized requests 404 as a missing session", () => {
    queries.requests.data = undefined;
    queries.requests.error = { statusCode: 404, detail: "Not found." };
    renderDrawer();
    expect(
      screen.getByText("Requests could not be loaded"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Session not found")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Retry requests" }),
    ).toBeEnabled();
    expect(
      screen.getByRole("list", { name: "Session summary" }),
    ).toBeInTheDocument();
  });

  it("does not claim an empty session when requests have never loaded", () => {
    queries.requests.data = undefined;
    queries.requests.error = new Error("Offline");
    renderDrawer();
    expect(
      screen.getByText(/Requests could not be loaded/),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("No requests in this session yet."),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Retry requests" }),
    ).toBeEnabled();
    expect(
      screen.getByRole("list", { name: "Session summary" }),
    ).toBeInTheDocument();
  });

  it("marks a previously empty requests response stale instead of claiming it is still empty", () => {
    queries.requests.data = [];
    queries.requests.error = new Error("Offline");
    renderDrawer();
    expect(screen.getByText(/Requests may be stale/)).toBeInTheDocument();
    expect(
      screen.queryByText("No requests in this session yet."),
    ).not.toBeInTheDocument();
  });

  it.each([{ statusCode: 404 }, { response: { status: 404 } }])(
    "keeps prior details on a later 404 but marks them stale: %j",
    (error) => {
      queries.detail.error = error;
      renderDrawer();
      expect(
        screen.getByText(/Session details may be stale/),
      ).toBeInTheDocument();
      expect(screen.queryByText("Session not found")).not.toBeInTheDocument();
      expect(
        screen.getByRole("list", { name: "Session summary" }),
      ).toBeInTheDocument();
    },
  );

  it("shows the empty requests message only after a successful empty response", () => {
    queries.requests.data = [];
    renderDrawer();
    expect(
      screen.getByText("No requests in this session yet."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Retry requests" }),
    ).not.toBeInTheDocument();
  });
});
