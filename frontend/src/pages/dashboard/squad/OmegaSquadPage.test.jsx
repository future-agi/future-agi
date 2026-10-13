import { describe, expect, it, vi } from "vitest";
import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";

const host = vi.hoisted(() => ({
  config: {},
  auth: {},
  organization: {},
  workspace: {},
}));
vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => host.auth,
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => host.organization,
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => host.workspace,
}));

const client = {
  getCapabilities: vi.fn(),
  getFleetHealth: vi.fn(),
  listApprovals: vi.fn(),
  listEnvironments: vi.fn(),
  listSchedules: vi.fn(),
  listConnectors: vi.fn(),
  listMemoryScopes: vi.fn(),
  getMemoryGraph: vi.fn(),
  getMemoryTimeline: vi.fn(),
  getTenantAudit: vi.fn(),
  getTenantAuditIntegrity: vi.fn(),
  listConversations: vi.fn(),
  getWorkforceAgent: vi.fn(),
  createConversation: vi.fn(),
  getConversation: vi.fn(),
  getConversationControlStatus: vi.fn(),
  getWorkforceAgentRunIdentity: vi.fn(),
  getConversationTree: vi.fn(),
  listConversationApprovals: vi.fn(),
  getConversationObservations: vi.fn(),
  openConversationEvents: vi.fn(),
  sendConversationMessage: vi.fn(),
};

vi.mock("src/api/omega-squad/remoteClient", () => ({
  createOmegaSquadClient: vi.fn(() => client),
  readOmegaSquadConfig: () => ({
    baseUrl: "https://omega.example.test",
    ...host.config,
  }),
}));

const baseFleet = () => ({
  available: true,
  schemaVersion: "omega-workforce/v1",
  agents: [
    {
      id: "agent-1",
      name: "Researcher",
      role: "Analyst",
      purpose: "Investigate questions",
      status: "idle",
    },
  ],
  health: {
    total: 1,
    byStatus: {
      idle: 1,
      working: 0,
      paused: 0,
      needs_attention: 0,
      offline: 0,
    },
  },
  capabilities: { canCreateAgent: false, permittedActions: [] },
});

function deferred() {
  let resolve;
  const promise = new Promise((accept) => {
    resolve = accept;
  });
  return { promise, resolve };
}

function streamResponse(id, { signal }) {
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(
        new TextEncoder().encode(
          `id: event-${id}\nevent: progress\ndata: streaming ${id}\n\n`,
        ),
      );
      signal.addEventListener("abort", () => controller.close(), {
        once: true,
      });
    },
  });
  return {
    available: true,
    response: new Response(body, {
      headers: { "content-type": "text/event-stream" },
    }),
  };
}

function resetClient() {
  host.config = {};
  host.auth = {};
  host.organization = {};
  host.workspace = {};
  Object.values(client).forEach((method) => method.mockReset());
  client.getCapabilities.mockResolvedValue({
    available: true,
    features: { society: false },
  });
  client.getFleetHealth.mockResolvedValue(baseFleet());
  client.listApprovals.mockResolvedValue({ available: true, approvals: [] });
  client.listEnvironments.mockResolvedValue({
    available: true,
    environments: [],
  });
  client.listSchedules.mockResolvedValue({ available: true, schedules: [] });
  client.listConnectors.mockResolvedValue({ available: true, connectors: [] });
  client.listMemoryScopes.mockResolvedValue({ available: true, scopes: [] });
  client.getMemoryGraph.mockResolvedValue({ available: true, graph: {} });
  client.getMemoryTimeline.mockResolvedValue({ available: true, events: [] });
  client.getTenantAudit.mockResolvedValue({ available: true, entries: [] });
  client.getTenantAuditIntegrity.mockResolvedValue({
    available: true,
    integrity: {},
  });
  client.listConversations.mockResolvedValue({
    available: true,
    conversations: [],
  });
  client.getWorkforceAgent.mockResolvedValue({
    available: true,
    agent: baseFleet().agents[0],
  });
  client.getConversation.mockImplementation(async (id) => ({
    available: true,
    conversation: {
      id,
      status: "running",
      input: `Details of ${id}`,
      messages: [
        {
          id: `${id}-answer`,
          conversationId: id,
          role: "agent",
          text: `Result ${id}`,
        },
      ],
    },
  }));
  client.getConversationControlStatus.mockResolvedValue({
    available: true,
    control: { runStatus: "running" },
  });
  client.getWorkforceAgentRunIdentity.mockResolvedValue({
    available: true,
    identity: { agentId: "agent-1" },
  });
  client.getConversationTree.mockResolvedValue({ available: true, tree: [] });
  client.listConversationApprovals.mockResolvedValue({
    available: true,
    approvals: [],
  });
  client.getConversationObservations.mockResolvedValue({
    available: true,
    observations: [],
  });
  client.openConversationEvents.mockImplementation(streamResponse);
}

describe("Omega Squad remote run launch", () => {
  it.each(["task-canonical", undefined])(
    "reads run identity only using the server-reported managed task id (%s)",
    async (taskId) => {
      resetClient();
      client.listConversations.mockResolvedValue({
        available: true,
        conversations: [
          { id: "conv-a", title: "Model answer", status: "done" },
        ],
      });
      client.getConversation.mockResolvedValue({
        available: true,
        conversation: {
          id: "conv-a",
          status: "done",
          managedExecution: taskId ? { taskId } : undefined,
        },
      });
      const { default: OmegaSquadPage } = await import("./OmegaSquadPage");
      render(<OmegaSquadPage />);
      await userEvent.click(
        await screen.findByRole("button", { name: "Details" }),
      );
      if (taskId)
        await waitFor(() =>
          expect(client.getWorkforceAgentRunIdentity).toHaveBeenCalledWith(
            "agent-1",
            taskId,
          ),
        );
      else {
        expect(
          await screen.findByText(
            "Run identity is unavailable until the server reports a managed task id.",
          ),
        ).toBeInTheDocument();
        expect(client.getWorkforceAgentRunIdentity).not.toHaveBeenCalled();
      }
      expect(client.getWorkforceAgentRunIdentity).not.toHaveBeenCalledWith(
        "agent-1",
        "conv-a",
      );
    },
  );

  it("keeps the selected agent's saved and deployed model when a previous detail request finishes late", async () => {
    resetClient();
    const late = deferred();
    const first = {
      ...baseFleet().agents[0],
      modelId: "model-a",
      deployedModelId: "model-a",
    };
    const second = {
      ...first,
      id: "agent-2",
      name: "Writer",
      modelId: "model-b",
      deployedModelId: "model-c",
    };
    client.getFleetHealth.mockResolvedValue({
      ...baseFleet(),
      agents: [first, second],
    });
    client.getWorkforceAgent.mockImplementation((id) =>
      id === first.id
        ? late.promise
        : Promise.resolve({ available: true, agent: second }),
    );
    const { default: OmegaSquadPage } = await import("./OmegaSquadPage");
    render(<OmegaSquadPage />);
    await waitFor(() =>
      expect(client.getWorkforceAgent).toHaveBeenCalledWith(first.id),
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Managed agent" }),
      second.id,
    );
    expect(
      await screen.findByText(
        /Saved model: model-b. Active deployment model: model-c/,
      ),
    ).toBeInTheDocument();
    await act(async () => late.resolve({ available: true, agent: first }));
    expect(
      screen.getByText(
        /Saved model: model-b. Active deployment model: model-c/,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Saved model: model-a/)).not.toBeInTheDocument();
  });

  it("continues a completed conversation and reconnects its events without losing rejected drafts", async () => {
    resetClient();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    try {
      client.listConversations.mockResolvedValue({
        available: true,
        conversations: [
          { id: "run-done", title: "Completed answer", status: "done" },
        ],
      });
      client.getConversation.mockResolvedValue({
        available: true,
        conversation: { id: "run-done", status: "done" },
        messages: [
          {
            id: "answer",
            conversationId: "run-done",
            role: "agent",
            text: "42",
          },
        ],
      });
      client.sendConversationMessage
        .mockRejectedValueOnce(new Error("offline"))
        .mockResolvedValueOnce({ available: true });
      const { default: OmegaSquadPage } = await import("./OmegaSquadPage");
      render(<OmegaSquadPage />);
      await userEvent.click(
        await screen.findByRole("button", { name: "Details" }),
      );
      await screen.findByText("42");
      const input = screen.getByRole("textbox", { name: "Follow-up or steer" });
      await userEvent.type(input, "What is one less?");
      await userEvent.click(
        screen.getByRole("button", { name: "Send", exact: true }),
      );
      expect(
        await screen.findByText(/couldn't send this follow-up/),
      ).toBeInTheDocument();
      expect(input).toHaveValue("What is one less?");
      expect(client.openConversationEvents).toHaveBeenCalledTimes(1);
      const firstSignal = client.openConversationEvents.mock.calls[0][1].signal;
      await userEvent.click(
        screen.getByRole("button", { name: "Send", exact: true }),
      );
      await waitFor(() =>
        expect(client.openConversationEvents).toHaveBeenCalledTimes(2),
      );
      expect(firstSignal.aborted).toBe(true);
      expect(client.sendConversationMessage).toHaveBeenLastCalledWith(
        "run-done",
        "What is one less?",
        expect.objectContaining({ idempotencyKey: expect.any(String) }),
      );
      expect(
        client.sendConversationMessage.mock.calls[1][2].idempotencyKey,
      ).toBe(client.sendConversationMessage.mock.calls[0][2].idempotencyKey);
      expect(input).toHaveValue("");
    } finally {
      confirm.mockRestore();
    }
  });

  it("clears tenant state, closes streams, and drops late responses when the host workspace changes", async () => {
    resetClient();
    host.config.allowHostToken = true;
    host.auth = { authenticated: true, user: { id: "account-a" } };
    host.organization = { currentOrganizationId: "org-a", isReady: true };
    host.workspace = { currentWorkspaceId: "workspace-a", isReady: true };
    const oldDetail = deferred();
    client.listConversations.mockResolvedValue({
      available: true,
      conversations: [{ id: "run-a", title: "Tenant A run" }],
    });
    client.getConversation.mockReturnValue(oldDetail.promise);
    const { default: OmegaSquadPage } = await import("./OmegaSquadPage");
    const { createOmegaSquadClient } = await import(
      "src/api/omega-squad/remoteClient"
    );
    const { rerender } = render(<OmegaSquadPage />);
    await screen.findByText("Tenant A run");
    await userEvent.click(screen.getByRole("button", { name: "Details" }));
    await waitFor(() =>
      expect(client.openConversationEvents).toHaveBeenCalled(),
    );
    const oldSignal = client.openConversationEvents.mock.calls.at(-1)[1].signal;
    expect(oldSignal.aborted).toBe(false);
    await userEvent.type(
      screen.getByRole("textbox", { name: "Task" }),
      "Tenant A draft",
    );

    const nextFleet = deferred();
    client.getFleetHealth.mockReturnValue(nextFleet.promise);
    client.listConversations.mockResolvedValue({
      available: true,
      conversations: [],
    });
    host.workspace = { currentWorkspaceId: "workspace-b", isReady: true };
    rerender(<OmegaSquadPage />);
    expect(oldSignal.aborted).toBe(true);
    expect(screen.queryByText("Tenant A run")).not.toBeInTheDocument();
    expect(
      screen.queryByDisplayValue("Tenant A draft"),
    ).not.toBeInTheDocument();
    expect(createOmegaSquadClient).toHaveBeenLastCalledWith(
      expect.objectContaining({
        organizationId: "org-a",
        workspaceId: "workspace-b",
        scopeReady: true,
      }),
    );
    await act(async () => {
      oldDetail.resolve({
        available: true,
        conversation: { id: "run-a", input: "Private tenant A result" },
      });
      nextFleet.resolve(baseFleet());
    });
    expect(
      screen.queryByText("Private tenant A result"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Tenant A run")).not.toBeInTheDocument();
  });

  it("discards an older fleet response while the organization selection is pending", async () => {
    resetClient();
    host.config.allowHostToken = true;
    host.auth = { authenticated: true, user: { id: "account-a" } };
    host.organization = { currentOrganizationId: "org-a", isReady: true };
    host.workspace = { currentWorkspaceId: "workspace-a", isReady: true };
    const oldFleet = deferred();
    client.getFleetHealth
      .mockReturnValueOnce(oldFleet.promise)
      .mockResolvedValue({ ...baseFleet(), agents: [] });
    const { default: OmegaSquadPage } = await import("./OmegaSquadPage");
    const { createOmegaSquadClient } = await import(
      "src/api/omega-squad/remoteClient"
    );
    const { rerender } = render(<OmegaSquadPage />);
    host.organization = { currentOrganizationId: "org-a", isReady: false };
    rerender(<OmegaSquadPage />);
    expect(createOmegaSquadClient).toHaveBeenLastCalledWith(
      expect.objectContaining({
        organizationId: null,
        workspaceId: null,
        scopeReady: false,
      }),
    );
    await act(async () => {
      oldFleet.resolve({
        ...baseFleet(),
        agents: [{ ...baseFleet().agents[0], name: "Prior tenant agent" }],
      });
    });
    expect(screen.queryByText("Prior tenant agent")).not.toBeInTheDocument();
  });

  it("opens and streams the real created id before a slow list refresh and preserves it through refresh", async () => {
    resetClient();
    const refreshed = deferred();
    const detail = deferred();
    const oldRun = { id: "run-old", status: "completed", title: "Earlier run" };
    client.createConversation.mockResolvedValue({
      available: true,
      conversation: {
        id: "run-new",
        title: "Newly accepted run",
        status: "running",
      },
    });
    client.listConversations
      .mockResolvedValueOnce({ available: true, conversations: [oldRun] })
      .mockReturnValueOnce(refreshed.promise)
      .mockResolvedValue({ available: true, conversations: [oldRun] });
    client.getConversation.mockReturnValueOnce(detail.promise);
    const { default: OmegaSquadPage } = await import("./OmegaSquadPage");
    render(<OmegaSquadPage />);
    expect(await screen.findByText("Earlier run")).toBeInTheDocument();
    await userEvent.type(
      screen.getByRole("textbox", { name: "Task" }),
      "Investigate the latest incident",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Start remote run" }),
    );
    await waitFor(() =>
      expect(client.createConversation).toHaveBeenCalledWith(
        "Investigate the latest incident",
        expect.objectContaining({ agentId: "agent-1", mode: "agent" }),
      ),
    );
    await waitFor(() =>
      expect(client.getConversation).toHaveBeenCalledWith("run-new"),
    );
    await waitFor(() =>
      expect(client.openConversationEvents).toHaveBeenCalledWith(
        "run-new",
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ),
    );
    expect(screen.getByText("Newly accepted run")).toBeInTheDocument();
    expect(screen.getByText("Earlier run")).toBeInTheDocument();
    await act(async () => {
      detail.resolve({
        available: true,
        conversation: {
          id: "run-new",
          status: "running",
          input: "New run detail",
        },
      });
      refreshed.resolve({ available: true, conversations: [oldRun] });
    });
    expect(await screen.findByText("streaming run-new")).toBeInTheDocument();
    expect(screen.getByText("New run detail")).toBeInTheDocument();
    expect(screen.getByText("Newly accepted run")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Refresh runs" }));
    await waitFor(() =>
      expect(client.listConversations).toHaveBeenCalledTimes(3),
    );
    expect(screen.getByText("streaming run-new")).toBeInTheDocument();
    expect(client.openConversationEvents).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("textbox", { name: "Task" })).toHaveValue("");
  });

  it("refreshes the selected transcript after a completion event without replacing another run", async () => {
    resetClient();
    let emit;
    client.listConversations.mockResolvedValue({
      available: true,
      conversations: [{ id: "run-a", title: "Run A", status: "running" }],
    });
    client.getConversation.mockResolvedValue({
      available: true,
      conversation: { id: "run-a", status: "running" },
      messages: [],
    });
    client.openConversationEvents.mockImplementation((_id, { signal }) => ({
      available: true,
      response: new Response(
        new ReadableStream({
          start(controller) {
            emit = (event) =>
              controller.enqueue(
                new TextEncoder().encode(
                  `event: ${event}\ndata: {"type":"${event}"}\n\n`,
                ),
              );
            signal.addEventListener("abort", () => controller.close(), {
              once: true,
            });
          },
        }),
        { headers: { "content-type": "text/event-stream" } },
      ),
    }));
    const { default: OmegaSquadPage } = await import("./OmegaSquadPage");
    render(<OmegaSquadPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Details" }),
    );
    await waitFor(() =>
      expect(client.openConversationEvents).toHaveBeenCalled(),
    );
    await screen.findByText("No conversation messages are available yet.");
    client.getConversation.mockResolvedValue({
      available: true,
      conversation: { id: "run-a", status: "done" },
      messages: [
        { id: "answer", conversationId: "run-a", role: "agent", text: "42" },
      ],
    });
    client.listConversations.mockResolvedValue({
      available: true,
      conversations: [{ id: "run-a", title: "Run A", status: "done" }],
    });
    // Many streamed chunks and the terminal event must coalesce into one background refresh.
    await act(async () => {
      for (let i = 0; i < 30; i += 1) emit("message_delta");
      emit("message_complete");
      emit("done");
    });
    expect(await screen.findByText("42")).toBeInTheDocument();
    expect(client.getConversation).toHaveBeenCalledTimes(2);
    expect(client.listConversations).toHaveBeenCalledTimes(2);
    expect(screen.getByText("run-a · done")).toBeInTheDocument();
    expect(client.openConversationEvents).toHaveBeenCalledTimes(1);
  });

  it("keeps the task text when remote creation is refused", async () => {
    resetClient();
    client.createConversation.mockResolvedValue({
      available: false,
      reason: "Agent is paused.",
    });
    const { default: OmegaSquadPage } = await import("./OmegaSquadPage");
    render(<OmegaSquadPage />);
    const task = await screen.findByRole("textbox", { name: "Task" });
    await userEvent.type(task, "Do not lose this request");
    await userEvent.click(
      screen.getByRole("button", { name: "Start remote run" }),
    );
    await waitFor(() =>
      expect(screen.getByText("Agent is paused.")).toBeInTheDocument(),
    );
    expect(screen.getByRole("textbox", { name: "Task" })).toHaveValue(
      "Do not lose this request",
    );
    expect(client.getConversation).not.toHaveBeenCalled();
  });

  it("keeps a failed request draft and recovers the start button after an exception", async () => {
    resetClient();
    client.createConversation.mockRejectedValue(new Error("disconnected"));
    const { default: OmegaSquadPage } = await import("./OmegaSquadPage");
    render(<OmegaSquadPage />);
    await userEvent.type(
      await screen.findByRole("textbox", { name: "Task" }),
      "Keep my task",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Start remote run" }),
    );
    expect(
      await screen.findByText(/couldn't start this remote run/),
    ).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Task" })).toHaveValue(
      "Keep my task",
    );
    expect(
      screen.getByRole("button", { name: "Start remote run" }),
    ).toBeEnabled();
    expect(client.openConversationEvents).not.toHaveBeenCalled();
  });

  it("never invents a conversation id when a successful envelope omits it", async () => {
    resetClient();
    client.createConversation.mockResolvedValue({
      available: true,
      conversation: { title: "Incomplete response" },
    });
    const { default: OmegaSquadPage } = await import("./OmegaSquadPage");
    render(<OmegaSquadPage />);
    await userEvent.type(
      await screen.findByRole("textbox", { name: "Task" }),
      "Keep my task",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Start remote run" }),
    );
    expect(
      await screen.findByText(/without returning a conversation id/),
    ).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Task" })).toHaveValue(
      "Keep my task",
    );
    expect(client.getConversation).not.toHaveBeenCalled();
    expect(client.openConversationEvents).not.toHaveBeenCalled();
  });

  it("ignores late creation and list results after switching agents", async () => {
    resetClient();
    const creation = deferred();
    const firstList = deferred();
    client.getFleetHealth.mockResolvedValue({
      ...baseFleet(),
      agents: [
        ...baseFleet().agents,
        { id: "agent-2", name: "Writer", status: "idle" },
      ],
    });
    client.listConversations.mockImplementation(({ agentId }) =>
      agentId === "agent-1"
        ? firstList.promise
        : Promise.resolve({
            available: true,
            conversations: [
              { id: "writer-run", title: "Writer run", status: "running" },
            ],
          }),
    );
    client.createConversation.mockReturnValue(creation.promise);
    const { default: OmegaSquadPage } = await import("./OmegaSquadPage");
    render(<OmegaSquadPage />);
    const task = await screen.findByRole("textbox", { name: "Task" });
    await userEvent.type(task, "Investigate the incident");
    await userEvent.click(
      screen.getByRole("button", { name: "Start remote run" }),
    );
    await waitFor(() =>
      expect(client.createConversation).toHaveBeenCalledTimes(1),
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Managed agent" }),
      "agent-2",
    );
    expect(await screen.findByText("Writer run")).toBeInTheDocument();
    await userEvent.clear(task);
    await userEvent.type(task, "New writer request");
    await act(async () => {
      creation.resolve({
        available: true,
        conversation: { id: "stale-created", title: "Stale created" },
      });
      firstList.resolve({
        available: true,
        conversations: [{ id: "stale-listed", title: "Stale listed" }],
      });
    });
    expect(screen.getByText("Writer run")).toBeInTheDocument();
    expect(screen.queryByText("Stale created")).not.toBeInTheDocument();
    expect(screen.queryByText("Stale listed")).not.toBeInTheDocument();
    expect(task).toHaveValue("New writer request");
    expect(client.getConversation).not.toHaveBeenCalled();
    expect(client.openConversationEvents).not.toHaveBeenCalled();
  });

  it("does not let a late detail response replace a subsequently selected run", async () => {
    resetClient();
    const lateDetail = deferred();
    client.listConversations.mockResolvedValue({
      available: true,
      conversations: [
        { id: "run-first", title: "First run" },
        { id: "run-second", title: "Second run" },
      ],
    });
    client.getConversation.mockImplementation((id) =>
      id === "run-first"
        ? lateDetail.promise
        : Promise.resolve({
            available: true,
            conversation: {
              id,
              title: "Selected second detail",
              status: "running",
            },
          }),
    );
    const { default: OmegaSquadPage } = await import("./OmegaSquadPage");
    render(<OmegaSquadPage />);
    await screen.findByText("First run");
    const details = screen.getAllByRole("button", { name: "Details" });
    await userEvent.click(details[0]);
    await waitFor(() =>
      expect(client.openConversationEvents).toHaveBeenCalledWith(
        "run-first",
        expect.any(Object),
      ),
    );
    const oldSignal = client.openConversationEvents.mock.calls[0][1].signal;
    await userEvent.click(details[1]);
    expect(
      await screen.findByText("Selected second detail"),
    ).toBeInTheDocument();
    expect(oldSignal.aborted).toBe(true);
    await act(async () => {
      lateDetail.resolve({
        available: true,
        conversation: { id: "run-first", title: "Stale first detail" },
      });
    });
    expect(screen.queryByText("Stale first detail")).not.toBeInTheDocument();
    expect(screen.getByText("Selected second detail")).toBeInTheDocument();
    expect(await screen.findByText("streaming run-second")).toBeInTheDocument();
    expect(screen.queryByText("streaming run-first")).not.toBeInTheDocument();
  });
});
