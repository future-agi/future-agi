import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderMcp } from "../testing/renderMcp";
import ApprovalsTab from "./ApprovalsTab";

// The real tab and hook; only the HTTP client and the status/role seams are
// supplied. Rejections take the shape the shared axios interceptor gives.
const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: http.get, post: http.post },
  endpoints: {
    gateway: {
      mcpApprovals: "/agentcc/mcp/approvals/",
      mcpApproval: (id) => `/agentcc/mcp/approvals/${id}/`,
      mcpApprovalDecision: (id) => `/agentcc/mcp/approvals/${id}/decision/`,
    },
  },
}));
vi.mock("../hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({ data: { features: { approvals: true } } }),
}));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));

// Why approvals are unavailable: the gateway serves no approval route (404),
// or it serves them with no working Redis connection (503).
const SERVES_NONE =
  "Approvals aren't available here: MCP is off on this deployment's gateway, or the gateway is older than approvals. If you run this deployment, turn MCP on in the gateway's configuration file (mcp.enabled: true, with its state keys), update the gateway if it is older, and restart it. Approval state could not be refreshed. Decisions are disabled until Refresh succeeds.";
const NEEDS_REDIS =
  "Approvals need Redis, and this deployment's gateway has no working Redis connection. If you run this deployment, give the gateway process its Redis connection (AGENTCC_REDIS_ADDRESS, and AGENTCC_REDIS_PASSWORD if your Redis needs one), then restart the gateway. Approval state could not be refreshed. Decisions are disabled until Refresh succeeds.";
const MISSING = "Approval not found.";
const UNCONFIRMED =
  "Approval state could not be confirmed. Refresh before retrying a decision.";
const EITHER_CASE = /Approvals aren't available here|Approvals need Redis/;

const pending = {
  approval_id: "test-approval",
  args_hash: "a".repeat(64),
  principal: { kind: "key", key_id: "person-key" },
  tool: "mock/delete_file",
  agent_run_id: "run-person",
  virtual_server_id: "review",
  status: "pending",
  requested_at: "2026-01-01T00:00:00Z",
  expires_at: "2099-01-01T00:00:00Z",
};

const refresh = () => screen.getByRole("button", { name: "Refresh" });

beforeEach(() => {
  http.get.mockReset();
  http.post.mockReset();
});

describe("approvals the gateway cannot serve or read", () => {
  it.each([
    ["the gateway serves no approval route", 404, SERVES_NONE],
    ["the gateway has no working Redis connection", 503, NEEDS_REDIS],
  ])("says why when %s", async (_label, status, words) => {
    http.get.mockResolvedValueOnce({ data: { result: [pending] } });
    renderMcp(<ApprovalsTab gatewayId="synthetic-gateway" />);
    expect(await screen.findByText("mock/delete_file")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();

    http.get.mockRejectedValue({ message: words, statusCode: status });
    fireEvent.click(refresh());

    // Why, then what the person who runs the deployment does. Refresh stays
    // and decisions stay blocked.
    expect(await screen.findByText(words)).toBeInTheDocument();
    expect(
      screen.queryByText(
        "Approval state could not be refreshed. Decisions are disabled until Refresh succeeds.",
      ),
    ).not.toBeInTheDocument();
    await waitFor(() => expect(refresh()).toBeEnabled());
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Deny" })).toBeDisabled();
    expect(http.post).not.toHaveBeenCalled();

    // Once approvals can be read again, the tab and its empty state come
    // back with the same Refresh.
    http.get.mockResolvedValue({ data: { result: [] } });
    fireEvent.click(refresh());
    expect(await screen.findByText("No approval requests")).toBeInTheDocument();
    expect(screen.queryByText(words)).not.toBeInTheDocument();
  });

  it.each([
    [
      "the server's general unavailable message",
      () =>
        http.get.mockRejectedValue({ message: UNCONFIRMED, statusCode: 503 }),
    ],
    [
      "the server's not-found message",
      () => http.get.mockRejectedValue({ message: MISSING, statusCode: 404 }),
    ],
    [
      "a list the console cannot read",
      () => http.get.mockResolvedValue({ data: {} }),
    ],
    [
      "a lost connection",
      () =>
        http.get.mockRejectedValue({
          message: "Network Error",
          transportCode: "ERR_NETWORK",
        }),
    ],
    [
      "the same sentences under another status",
      () =>
        http.get.mockRejectedValue({ message: NEEDS_REDIS, statusCode: 502 }),
    ],
    [
      "the Redis sentences under the missing-route status",
      () =>
        http.get.mockRejectedValue({ message: NEEDS_REDIS, statusCode: 404 }),
    ],
    [
      "the missing-route sentences under the Redis status",
      () =>
        http.get.mockRejectedValue({ message: SERVES_NONE, statusCode: 503 }),
    ],
  ])("keeps today's refresh text for %s", async (_label, fail) => {
    fail();
    renderMcp(<ApprovalsTab gatewayId="synthetic-gateway" />);
    expect(
      await screen.findByText(
        "Approval state could not be refreshed. Decisions are disabled until Refresh succeeds.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(EITHER_CASE)).not.toBeInTheDocument();
    await waitFor(() => expect(refresh()).toBeEnabled());
  });
});
