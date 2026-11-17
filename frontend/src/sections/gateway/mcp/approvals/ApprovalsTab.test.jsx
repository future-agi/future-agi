import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderMcp as render } from "../testing/renderMcp";
import ApprovalsTab from "./ApprovalsTab";
import ApprovalPolicyFields from "../virtual/ApprovalPolicyFields";
import ToolLimitsFields from "../virtual/ToolLimitsFields";
import { approvalPatch } from "../virtual/approvalPolicy";
import { formFromEntry, virtualServerPatch } from "../virtual/virtualServers";

// Component-only seams. The unmocked registry has its own integration gate.
const state = vi.hoisted(() => ({
  enabled: true,
  admin: true,
  ready: true,
  error: false,
  decide: vi.fn(),
  refetch: vi.fn(),
}));
vi.mock("../hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({ data: { features: { approvals: state.enabled } } }),
}));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({
    canEdit: state.admin,
    disabledReason: "Only an organization Owner or Admin can decide.",
  }),
}));
vi.mock("./useApprovals", () => ({
  approvalRoutesReady: () => state.ready,
  useApprovalDecision: () => ({ mutateAsync: state.decide, isPending: false }),
  useApprovals: () => ({
    isError: state.error,
    refetch: state.refetch,
    data: [
      {
        approval_id: "test-approval",
        args_hash: "a".repeat(64),
        principal: { kind: "key", key_id: "person-key" },
        tool: "mock/delete_file",
        agent_run_id: "run-person",
        virtual_server_id: "review",
        status: "pending",
        requested_at: "2026-01-01T00:00:00Z",
        expires_at: "2099-01-01T00:00:00Z",
      },
    ],
  }),
}));

beforeEach(() => {
  state.enabled = true;
  state.admin = true;
  state.ready = true;
  state.error = false;
  state.decide.mockReset().mockResolvedValue({});
});

describe("approval component boundaries", () => {
  it("discloses an unconfirmed capability instead of rendering an empty tab", () => {
    state.enabled = false;
    render(<ApprovalsTab gatewayId="gw" />);
    expect(
      screen.getByText(/Approval capability could not be confirmed/),
    ).toBeInTheDocument();
    expect(state.decide).not.toHaveBeenCalled();
  });
  it("never substitutes an endpoint when the endpoint registry has no approval routes", () => {
    state.ready = false;
    render(<ApprovalsTab gatewayId="gw" />);
    expect(screen.getByText(/routes are not installed/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Approve" }),
    ).not.toBeInTheDocument();
    expect(state.decide).not.toHaveBeenCalled();
  });
  it("allows Member and Viewer to read but not decide", () => {
    state.admin = false;
    render(<ApprovalsTab gatewayId="gw" />);
    expect(screen.getByText("mock/delete_file")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Deny" })).toBeDisabled();
  });
  it("requires explicit confirmation and sends no org or actor", async () => {
    render(<ApprovalsTab gatewayId="gw" />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(state.decide).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm approval" }));
    await waitFor(() =>
      expect(state.decide).toHaveBeenCalledWith({
        approvalId: "test-approval",
        decision: "approve",
      }),
    );
  });
  it("does not display raw transport failures or replay a failed decision", async () => {
    state.decide.mockRejectedValue(
      new Error("synthetic-private-transport-sentinel"),
    );
    render(<ApprovalsTab gatewayId="gw" />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm approval" }));
    await waitFor(() =>
      expect(
        screen.getByText(/decision could not be confirmed/),
      ).toBeInTheDocument(),
    );
    expect(
      screen.queryByText(/synthetic-private-transport-sentinel/),
    ).not.toBeInTheDocument();
    expect(state.decide).toHaveBeenCalledTimes(1);
  });
  it("blocks decisions after a failed refresh", () => {
    state.error = true;
    render(<ApprovalsTab gatewayId="gw" />);
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
  });
  it("keeps every per-tool rule control read-only", () => {
    const change = vi.fn();
    render(
      <ToolLimitsFields
        disabled
        budgets={{ per_tool: { "mock/get_weather": { rpm: 1 } } }}
        onChange={change}
      />,
    );
    screen
      .getAllByRole("textbox")
      .forEach((input) => expect(input).toBeDisabled());
    screen
      .getAllByRole("button")
      .forEach((button) => expect(button).toBeDisabled());
    expect(change).not.toHaveBeenCalled();
  });
  it("keeps policy inputs read-only and never emits an initial write", () => {
    const change = vi.fn();
    render(
      <ApprovalPolicyFields
        disabled
        policy={{ require: "listed", tools: ["mock/delete_file"] }}
        onChange={change}
      />,
    );
    screen
      .getAllByRole("textbox")
      .forEach((input) => expect(input).toBeDisabled());
    expect(change).not.toHaveBeenCalled();
  });
  it("edits a selected-tool policy and includes only its explicit config patch", () => {
    const change = vi.fn();
    const policy = {
      require: "listed",
      tools: ["mock/echo"],
      validity: "once",
      ttl_seconds: 300,
    };
    render(<ApprovalPolicyFields policy={policy} onChange={change} />);
    expect(change).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Approval tool patterns"), {
      target: { value: "mock/delete_file" },
    });
    fireEvent.change(screen.getByLabelText("Approval expiry (seconds)"), {
      target: { value: "30" },
    });
    const expected = {
      require: "listed",
      tools: ["mock/delete_file"],
      validity: "once",
      ttl_seconds: 30,
    };
    expect(change).toHaveBeenLastCalledWith(expected, "");
    const stored = { approval: policy, members: ["mock"] };
    const form = {
      ...formFromEntry(stored, "review"),
      approvalPatch: expected,
    };
    const result = virtualServerPatch(form, stored).virtual_servers.review;
    expect(result.approval).toEqual(expected);
    expect(result).not.toHaveProperty("budgets");
  });
  it("validates once-only policies without defaulting any limit", () => {
    expect(
      approvalPatch({
        require: "listed",
        tools: "mock/delete_file",
        validity: "once",
        ttl: "30",
      }),
    ).toEqual({
      require: "listed",
      tools: ["mock/delete_file"],
      validity: "once",
      ttl_seconds: 30,
    });
    expect(() =>
      approvalPatch({
        require: "all",
        tools: "mock/*",
        validity: "once",
        ttl: "30",
      }),
    ).toThrow();
    expect(() =>
      approvalPatch({
        require: "listed",
        tools: "",
        validity: "once",
        ttl: "30",
      }),
    ).toThrow();
    expect(() =>
      approvalPatch({
        require: "listed",
        tools: "mock/delete_file",
        validity: "step",
        ttl: "30",
      }),
    ).toThrow();
  });
});
