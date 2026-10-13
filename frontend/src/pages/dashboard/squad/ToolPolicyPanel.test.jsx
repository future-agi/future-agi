import { describe, expect, it, vi } from "vitest";
import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";
import ToolPolicyPanel from "./ToolPolicyPanel";

const policy = {
  schemaVersion: "omega-workforce/v1",
  agentId: "agent-1",
  currentVersion: 4,
  deployedVersion: 3,
  toolPolicy: {
    protocol: "omega-workforce-tool-policy/v1",
    defaultMode: "ask",
    tools: { "orders.read": { mode: "auto", reason: "Read-only reports" } },
  },
  deployedToolPolicy: {
    protocol: "omega-workforce-tool-policy/v1",
    defaultMode: "ask",
    tools: { "orders.read": { mode: "ask" } },
  },
  tools: [
    {
      toolId: "orders.read",
      mode: "auto",
      effectiveMode: "ask",
      configured: true,
      available: true,
      readOnly: true,
    },
    {
      toolId: "orders.write",
      mode: "ask",
      effectiveMode: "ask",
      configured: false,
      available: true,
      mandatoryApproval: true,
    },
  ],
};

describe("ToolPolicyPanel", () => {
  it("loads the server catalog and shows authored, deployed, effective and floor state", async () => {
    const client = {
      getWorkforceAgentTools: vi
        .fn()
        .mockResolvedValue({ available: true, ...policy }),
      updateWorkforceAgent: vi.fn(),
    };
    render(<ToolPolicyPanel agentId="agent-1" client={client} />);
    await waitFor(() =>
      expect(client.getWorkforceAgentTools).toHaveBeenCalledWith("agent-1"),
    );
    expect(await screen.findByText("Tool policy")).toBeInTheDocument();
    expect(screen.getByText("orders.read")).toBeInTheDocument();
    expect(screen.getAllByText("Effective: Ask")).toHaveLength(2);
    expect(screen.getByText("Mandatory approval")).toBeInTheDocument();
    expect(
      screen.getByText(/Authored revision 4 · Deployed revision 3/),
    ).toBeInTheDocument();
    expect(screen.getByText("Saved: Auto")).toBeInTheDocument();
    expect(screen.getAllByText("Deployed: Ask")).toHaveLength(2);
    expect(
      screen.getByText(
        /Mandatory approval and operator restrictions always apply/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Read-only reports")).toBeInTheDocument();
  });

  it("PATCHes only expectedVersion and toolPolicy, retaining a local draft after a typed conflict", async () => {
    const client = {
      getWorkforceAgentTools: vi
        .fn()
        .mockResolvedValue({ available: true, ...policy }),
      updateWorkforceAgent: vi.fn().mockResolvedValue({
        available: false,
        code: "conflict",
        reason: "The agent changed while you were editing.",
        status: 409,
      }),
    };
    render(<ToolPolicyPanel agentId="agent-1" client={client} canEdit />);
    await screen.findByText("orders.write");
    const selectors = screen.getAllByRole("combobox");
    await userEvent.selectOptions(selectors[0], "deny");
    await userEvent.click(
      screen.getByRole("button", { name: "Save tool policy" }),
    );
    await waitFor(() =>
      expect(client.updateWorkforceAgent).toHaveBeenCalledWith(
        "agent-1",
        {
          expectedVersion: 4,
          toolPolicy: {
            protocol: "omega-workforce-tool-policy/v1",
            defaultMode: "deny",
            tools: {
              "orders.read": { mode: "auto", reason: "Read-only reports" },
            },
          },
        },
        expect.any(String),
      ),
    );
    expect(
      await screen.findByText(/agent changed while you were editing/),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("combobox")[0]).toHaveValue("deny");
  });

  it("keeps the panel read-only for a non-editor", async () => {
    const client = {
      getWorkforceAgentTools: vi
        .fn()
        .mockResolvedValue({ available: true, ...policy }),
      updateWorkforceAgent: vi.fn(),
    };
    render(
      <ToolPolicyPanel agentId="agent-1" client={client} canEdit={false} />,
    );
    await screen.findByText("orders.read");
    expect(
      screen.getByText(/do not have permission to edit/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save tool policy" }),
    ).toBeDisabled();
    expect(client.updateWorkforceAgent).not.toHaveBeenCalled();
  });

  it("reads back a saved policy and version before confirming success", async () => {
    const desired = { ...policy.toolPolicy, defaultMode: "deny" };
    const client = {
      getWorkforceAgentTools: vi
        .fn()
        .mockResolvedValueOnce({ available: true, ...policy })
        .mockResolvedValueOnce({
          available: true,
          ...policy,
          currentVersion: 5,
          toolPolicy: desired,
          tools: policy.tools.map((row) => ({
            ...row,
            effectiveMode: row.toolId === "orders.write" ? "deny" : "ask",
          })),
        }),
      updateWorkforceAgent: vi
        .fn()
        .mockResolvedValue({
          available: true,
          schemaVersion: "omega-workforce/v1",
          agent: { id: "agent-1" },
        }),
    };
    const onSaved = vi.fn();
    render(
      <ToolPolicyPanel
        agentId="agent-1"
        client={client}
        canEdit
        onSaved={onSaved}
      />,
    );
    await screen.findByText("orders.read");
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Default tool mode" }),
      "deny",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Save tool policy" }),
    );
    expect(
      await screen.findByText(/Tool policy saved and read back/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Authored revision 5/)).toBeInTheDocument();
    expect(client.getWorkforceAgentTools).toHaveBeenCalledTimes(2);
    expect(onSaved).toHaveBeenCalledTimes(1);
  });

  it("refreshes after a conflict without losing edits or silently advancing the draft version", async () => {
    const client = {
      getWorkforceAgentTools: vi
        .fn()
        .mockResolvedValue({ available: true, ...policy }),
      updateWorkforceAgent: vi
        .fn()
        .mockResolvedValue({
          available: false,
          code: "conflict",
          reason: "Version changed.",
          status: 409,
        }),
    };
    render(<ToolPolicyPanel agentId="agent-1" client={client} canEdit />);
    await screen.findByText("orders.read");
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Default tool mode" }),
      "deny",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Save tool policy" }),
    );
    await screen.findByText(/Version changed/);
    client.getWorkforceAgentTools.mockResolvedValueOnce({
      available: true,
      ...policy,
      currentVersion: 6,
    });
    await userEvent.click(
      screen.getByRole("button", { name: "Refresh policy" }),
    );
    await screen.findByText(/draft is retained against revision 4/);
    expect(
      screen.getByRole("combobox", { name: "Default tool mode" }),
    ).toHaveValue("deny");
    expect(
      screen.getByRole("button", { name: "Save tool policy" }),
    ).toBeDisabled();
    await userEvent.click(
      screen.getByRole("button", { name: "Apply draft to latest revision" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Save tool policy" }),
    );
    await waitFor(() =>
      expect(client.updateWorkforceAgent).toHaveBeenLastCalledWith(
        "agent-1",
        expect.objectContaining({ expectedVersion: 6 }),
        expect.any(String),
      ),
    );
  });

  it("preserves a deny entry for a tool absent from the current catalog when editing the default", async () => {
    const client = {
      getWorkforceAgentTools: vi
        .fn()
        .mockResolvedValue({
          available: true,
          ...policy,
          toolPolicy: {
            ...policy.toolPolicy,
            tools: {
              ...policy.toolPolicy.tools,
              "old.tool": { mode: "deny", reason: "Retired connector" },
            },
          },
        }),
      updateWorkforceAgent: vi
        .fn()
        .mockResolvedValue({
          available: false,
          code: "conflict",
          reason: "Version changed.",
        }),
    };
    render(<ToolPolicyPanel agentId="agent-1" client={client} canEdit />);
    await screen.findByText("old.tool");
    expect(screen.getByText("Not in current catalog")).toBeInTheDocument();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Default tool mode" }),
      "deny",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Save tool policy" }),
    );
    await waitFor(() =>
      expect(client.updateWorkforceAgent).toHaveBeenCalledWith(
        "agent-1",
        expect.objectContaining({
          toolPolicy: expect.objectContaining({
            tools: expect.objectContaining({
              "old.tool": { mode: "deny", reason: "Retired connector" },
            }),
          }),
        }),
        expect.any(String),
      ),
    );
  });

  it.each([
    { agentId: "foreign-agent" },
    { currentVersion: 0 },
    { schemaVersion: "unknown/v1" },
    { toolPolicy: { protocol: "unknown/v1", tools: {} } },
    { tools: [{ ...policy.tools[0], effectiveMode: "unrestricted" }] },
  ])(
    "refuses malformed or foreign projections without exposing editor values: %j",
    async (change) => {
      const client = {
        getWorkforceAgentTools: vi
          .fn()
          .mockResolvedValue({ available: true, ...policy, ...change }),
        updateWorkforceAgent: vi.fn(),
      };
      render(<ToolPolicyPanel agentId="agent-1" client={client} canEdit />);
      await screen.findByText(/INVALID_RESPONSE/);
      expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    },
  );

  it("drops a late read or save from a previous client scope", async () => {
    let resolveRead;
    let resolveSave;
    const first = {
      getWorkforceAgentTools: vi
        .fn()
        .mockResolvedValueOnce({ available: true, ...policy })
        .mockImplementation(
          () =>
            new Promise((resolve) => {
              resolveRead = resolve;
            }),
        ),
      updateWorkforceAgent: vi.fn(
        () =>
          new Promise((resolve) => {
            resolveSave = resolve;
          }),
      ),
    };
    const second = {
      getWorkforceAgentTools: vi
        .fn()
        .mockResolvedValue({
          available: true,
          ...policy,
          agentId: "agent-2",
          toolPolicy: { protocol: "omega-workforce-tool-policy/v1", tools: {} },
          tools: [],
        }),
      updateWorkforceAgent: vi.fn(),
    };
    const onSaved = vi.fn();
    const view = render(
      <ToolPolicyPanel
        agentId="agent-1"
        client={first}
        canEdit
        onSaved={onSaved}
      />,
    );
    await screen.findByText("orders.read");
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Default tool mode" }),
      "deny",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Save tool policy" }),
    );
    view.rerender(
      <ToolPolicyPanel
        agentId="agent-2"
        client={second}
        canEdit
        onSaved={onSaved}
      />,
    );
    await waitFor(() =>
      expect(second.getWorkforceAgentTools).toHaveBeenCalledWith("agent-2"),
    );
    await screen.findByText(/No tools are currently reported/);
    await act(async () => resolveSave({ available: true }));
    expect(onSaved).not.toHaveBeenCalled();
    expect(first.getWorkforceAgentTools).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("orders.read")).not.toBeInTheDocument();
    view.rerender(<ToolPolicyPanel agentId="agent-1" client={first} canEdit />);
    await waitFor(() => expect(resolveRead).toBeTypeOf("function"));
    view.rerender(
      <ToolPolicyPanel agentId="agent-2" client={second} canEdit />,
    );
    await act(async () => resolveRead({ available: true, ...policy }));
    expect(screen.queryByText("orders.read")).not.toBeInTheDocument();
  });

  it("does not confirm an ambiguous successful PATCH when canonical readback differs", async () => {
    const client = {
      getWorkforceAgentTools: vi
        .fn()
        .mockResolvedValueOnce({ available: true, ...policy })
        .mockResolvedValueOnce({
          available: true,
          ...policy,
          currentVersion: 5,
        }),
      updateWorkforceAgent: vi.fn().mockResolvedValue({ available: true }),
    };
    render(<ToolPolicyPanel agentId="agent-1" client={client} canEdit />);
    await screen.findByText("orders.read");
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Default tool mode" }),
      "deny",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Save tool policy" }),
    );
    expect(await screen.findByText(/SAVE_UNVERIFIED/)).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "Default tool mode" }),
    ).toHaveValue("deny");
    expect(
      screen.getByRole("button", { name: "Save tool policy" }),
    ).toBeDisabled();
  });
});
