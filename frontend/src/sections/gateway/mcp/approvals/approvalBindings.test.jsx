import React from "react";
import { beforeEach, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { renderMcp as render } from "../testing/renderMcp";
import ApprovalsTab from "./ApprovalsTab";

const state = vi.hoisted(() => ({ rows: [], decide: vi.fn() }));
vi.mock("../hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({ data: { features: { approvals: true } } }),
}));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));
vi.mock("./useApprovals", () => ({
  approvalRoutesReady: () => true,
  useApprovalDecision: () => ({ mutateAsync: state.decide, isPending: false }),
  useApprovals: () => ({ data: state.rows }),
}));
beforeEach(() => {
  state.decide.mockReset().mockResolvedValue({});
  state.rows = ["a", "b"].map((suffix) => ({
    approval_id: `review-${suffix}`,
    tool: "mock/delete_file",
    agent_run_id: "same-run",
    virtual_server_id: "review",
    status: "pending",
    requested_at: "2026-01-01T00:00:00Z",
    expires_at: "2099-01-01T00:00:00Z",
    args_hash: suffix.repeat(64),
    principal: {
      kind: suffix === "a" ? "key" : "oauth",
      key_id: `key-${suffix}`,
      user_id: `user-${suffix}`,
      client_id: `client-${suffix}`,
    },
    arguments: { secret: "never-render-raw-arguments" },
  }));
});
it("distinguishes argument and principal bindings in cards and confirmation and posts only the reviewed ID", async () => {
  render(<ApprovalsTab gatewayId="gw" />);
  const cards = screen.getAllByRole("article");
  for (const [index, suffix] of ["a", "b"].entries()) {
    const text = cards[index].textContent;
    for (const value of [
      suffix.repeat(64),
      `key-${suffix}`,
      `user-${suffix}`,
      `client-${suffix}`,
      state.rows[index].principal.kind,
    ])
      expect(text).toContain(value);
    expect(text).not.toContain("never-render-raw-arguments");
  }
  fireEvent.click(within(cards[1]).getByRole("button", { name: "Approve" }));
  const dialog = screen.getByRole("dialog");
  for (const value of ["b".repeat(64), "oauth", "key-b", "user-b", "client-b"])
    expect(dialog).toHaveTextContent(value);
  expect(dialog).not.toHaveTextContent("a".repeat(64));
  expect(dialog).not.toHaveTextContent("never-render-raw-arguments");
  expect(state.decide).not.toHaveBeenCalled();
  fireEvent.click(
    within(dialog).getByRole("button", { name: "Confirm approval" }),
  );
  await waitFor(() =>
    expect(state.decide).toHaveBeenCalledExactlyOnceWith({
      approvalId: "review-b",
      decision: "approve",
    }),
  );
});
it("requires a reviewable fingerprint and principal before enabling approval", () => {
  state.rows[0].args_hash = "";
  state.rows[1].principal = null;
  render(<ApprovalsTab gatewayId="gw" />);
  for (const button of screen.getAllByRole("button", { name: "Approve" }))
    expect(button).toBeDisabled();
});
it("does not approve a binding that changed while confirmation was open", () => {
  let refresh;
  const Refreshable = () => {
    const [, update] = React.useReducer((n) => n + 1, 0);
    refresh = update;
    return <ApprovalsTab gatewayId="gw" />;
  };
  render(<Refreshable />);
  fireEvent.click(
    within(screen.getAllByRole("article")[0]).getByRole("button", {
      name: "Approve",
    }),
  );
  state.rows = [{ ...state.rows[0], args_hash: "c".repeat(64) }];
  act(() => refresh());
  fireEvent.click(screen.getByRole("button", { name: "Confirm approval" }));
  expect(state.decide).not.toHaveBeenCalled();
});
