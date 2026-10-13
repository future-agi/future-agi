import { describe, expect, it, vi } from "vitest";
import {
  render,
  screen,
  userEvent,
  waitFor,
  within,
  act,
} from "src/utils/test-utils";
import { createOmegaSquadClient } from "src/api/omega-squad/remoteClient";
import { AgentControlPanel } from "./OmegaSquadPage";
import { exportAgentDefinition } from "./agentDefinition";

function props(overrides = {}) {
  return {
    agent: null,
    detail: null,
    capabilities: { canCreateAgent: true, permittedActions: ["create"] },
    detailLoading: false,
    onCreate: vi
      .fn()
      .mockResolvedValue({ available: true, agent: { id: "agent-new" } }),
    onUpdate: vi.fn().mockResolvedValue({ available: true }),
    onControl: vi.fn(),
    client: {
      listWorkforceAgentRevisions: vi.fn(),
      listWorkforceAgentDeployments: vi.fn(),
    },
    ...overrides,
  };
}

const editor = () =>
  within(screen.getByRole("region", { name: "Create a managed agent" }));
async function start(template = "Ticket triage") {
  await userEvent.click(screen.getByRole("button", { name: "Create agent" }));
  await userEvent.click(editor().getByRole("button", { name: template }));
}
async function reviewAndCreate() {
  await userEvent.click(editor().getByRole("button", { name: "Review agent" }));
  await userEvent.click(editor().getByRole("button", { name: "Create agent" }));
}
const existingAgent = {
  id: "agent-1",
  version: 1,
  name: "Existing",
  role: "Writer",
  purpose: "Draft updates",
  responsibilities: ["Write updates"],
  workMode: "on_demand",
  status: "idle",
  examples: [],
};

const imageDigest = `sha256:${"a".repeat(64)}`;
const environmentResult = {
  available: true,
  environments: [
    {
      id: "env-research",
      name: "Research workspace",
      status: "active",
      revision: 2,
    },
    {
      id: "env-reports",
      name: "Reporting workspace",
      status: "active",
      revision: 1,
    },
    {
      id: "env-paused",
      name: "Paused workspace",
      status: "paused",
      revision: 1,
    },
  ],
};
const revisions = (environmentId, entries = [1, 2]) => ({
  available: true,
  environmentId,
  revisions: entries.map((revision) => ({
    revision,
    name: `Pinned workspace ${revision}`,
    sandboxKind: "container",
    pinnedImageDigest: imageDigest,
  })),
});
function environmentProps(overrides = {}) {
  return props({
    environmentResult,
    client: {
      listWorkforceAgentRevisions: vi.fn(),
      listWorkforceAgentDeployments: vi.fn(),
      listEnvironmentRevisions: vi.fn((id) => Promise.resolve(revisions(id))),
    },
    ...overrides,
  });
}
async function chooseEnvironment(name = "Research workspace") {
  await userEvent.click(screen.getByRole("combobox", { name: "Environment" }));
  await userEvent.click(screen.getByRole("option", { name: new RegExp(name) }));
}
async function chooseRevision(revision = 1) {
  await waitFor(() =>
    expect(
      screen.getByRole("combobox", { name: "Environment revision" }),
    ).not.toHaveAttribute("aria-disabled", "true"),
  );
  await userEvent.click(
    screen.getByRole("combobox", { name: "Environment revision" }),
  );
  await userEvent.click(
    screen.getByRole("option", { name: new RegExp(`^Revision ${revision} ·`) }),
  );
}

describe("Omega Squad managed-agent builder", () => {
  it("reads immutable revisions and creates with the exact selected environment pair", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ agent: { id: "agent-new" } }), {
        status: 201,
      }),
    );
    const api = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.test",
        getToken: () => "session",
        authMode: "bearer",
      },
      { fetchImpl },
    );
    const input = environmentProps({
      onCreate: vi.fn((payload) => api.createWorkforceAgent(payload)),
    });
    render(<AgentControlPanel {...input} />);
    await start();
    expect(screen.getByText("No environment attached")).toBeInTheDocument();
    expect(input.client.listEnvironmentRevisions).not.toHaveBeenCalled();
    await userEvent.click(
      screen.getByRole("combobox", { name: "Environment" }),
    );
    expect(
      screen.getByRole("option", { name: /Paused workspace/ }),
    ).toHaveAttribute("aria-disabled", "true");
    await userEvent.click(
      screen.getByRole("option", { name: /Research workspace/ }),
    );
    await waitFor(() =>
      expect(input.client.listEnvironmentRevisions).toHaveBeenCalledWith(
        "env-research",
      ),
    );
    expect(
      editor().getByRole("button", { name: "Review agent" }),
    ).toBeDisabled();
    await userEvent.click(
      screen.getByRole("combobox", { name: "Environment revision" }),
    );
    expect(
      screen.getByRole("option", {
        name: `Revision 1 · Pinned workspace 1 · ${imageDigest}`,
      }),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("option", {
        name: `Revision 1 · Pinned workspace 1 · ${imageDigest}`,
      }),
    );
    await userEvent.click(
      editor().getByRole("button", { name: "Review agent" }),
    );
    expect(
      editor().getByText("Research workspace · Revision 1"),
    ).toBeInTheDocument();
    expect(editor().getByText(imageDigest)).toBeInTheDocument();
    await userEvent.click(
      editor().getByRole("button", { name: "Create agent" }),
    );
    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(1));
    expect(JSON.parse(fetchImpl.mock.calls[0][1].body)).toMatchObject({
      environmentId: "env-research",
      environmentRevision: 1,
      workMode: "on_demand",
      connectorBindingIds: [],
    });
  });

  it("preserves the selected environment and edited draft after the runtime refuses it", async () => {
    const input = environmentProps({
      onCreate: vi.fn().mockResolvedValue({
        available: false,
        reason: "This revision has no supported runtime.",
      }),
    });
    render(<AgentControlPanel {...input} />);
    await start();
    await userEvent.type(
      screen.getByRole("textbox", { name: "Name" }),
      " custom",
    );
    await chooseEnvironment();
    await chooseRevision(1);
    await reviewAndCreate();
    expect(
      await screen.findByText("This revision has no supported runtime."),
    ).toBeInTheDocument();
    await userEvent.click(
      editor().getByRole("button", { name: "Back to details" }),
    );
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue(
      "Ticket triage custom",
    );
    expect(
      screen.getByRole("combobox", { name: "Environment revision" }),
    ).toHaveTextContent("Revision 1");
    await chooseRevision(2);
    await reviewAndCreate();
    expect(input.onCreate.mock.calls[1][0]).toMatchObject({
      name: "Ticket triage custom",
      environmentId: "env-research",
      environmentRevision: 2,
    });
  });

  it("ignores old revision reads when the environment selection changes", async () => {
    let resolveResearch;
    let resolveReports;
    const input = environmentProps();
    input.client.listEnvironmentRevisions.mockImplementation(
      (id) =>
        new Promise((resolve) => {
          if (id === "env-research") resolveResearch = resolve;
          else resolveReports = resolve;
        }),
    );
    render(<AgentControlPanel {...input} />);
    await start();
    await chooseEnvironment();
    await chooseEnvironment("Reporting workspace");
    await act(async () => resolveResearch(revisions("env-research", [7])));
    expect(
      editor().getByRole("button", { name: "Review agent" }),
    ).toBeDisabled();
    expect(screen.queryByText(/Revision 7/)).not.toBeInTheDocument();
    await act(async () => resolveReports(revisions("env-reports", [3])));
    await chooseRevision(3);
    await reviewAndCreate();
    expect(input.onCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        environmentId: "env-reports",
        environmentRevision: 3,
      }),
    );
  });

  it("blocks a reviewed attachment when its environment is paused without losing the draft", async () => {
    const input = environmentProps();
    const view = render(<AgentControlPanel {...input} />);
    await start();
    await chooseEnvironment();
    await chooseRevision();
    await userEvent.click(
      editor().getByRole("button", { name: "Review agent" }),
    );
    view.rerender(
      <AgentControlPanel
        {...input}
        environmentResult={{
          ...environmentResult,
          environments: environmentResult.environments.map((entry) => ({
            ...entry,
            status: "paused",
          })),
        }}
      />,
    );
    expect(
      editor().getByRole("button", { name: "Create agent" }),
    ).toBeDisabled();
    expect(
      editor().getByText(/selection can no longer be verified/),
    ).toBeInTheDocument();
    await userEvent.click(
      editor().getByRole("button", { name: "Back to details" }),
    );
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue(
      "Ticket triage",
    );
    expect(
      screen.getByRole("combobox", { name: "Environment" }),
    ).toHaveTextContent("Research workspace");
    expect(input.onCreate).not.toHaveBeenCalled();
  });

  it("keeps unchanged attachments out of updates and supports an explicit revision change or detach", async () => {
    const agent = {
      ...existingAgent,
      environment: { id: "env-research", revision: 1 },
    };
    const input = environmentProps({
      agent,
      detail: { available: true, agent },
      capabilities: { permittedActions: ["update"] },
    });
    render(<AgentControlPanel {...input} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Save agent" }));
    await waitFor(() => expect(input.onUpdate).toHaveBeenCalledTimes(1));
    expect(input.onUpdate.mock.calls[0][1]).not.toHaveProperty("environmentId");
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await chooseRevision(2);
    await userEvent.click(screen.getByRole("button", { name: "Save agent" }));
    await waitFor(() => expect(input.onUpdate).toHaveBeenCalledTimes(2));
    expect(input.onUpdate.mock.calls[1][1]).toMatchObject({
      environmentId: "env-research",
      environmentRevision: 2,
    });
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await chooseEnvironment("No environment attached");
    await userEvent.click(screen.getByRole("button", { name: "Save agent" }));
    await waitFor(() => expect(input.onUpdate).toHaveBeenCalledTimes(3));
    expect(input.onUpdate.mock.calls[2][1]).toMatchObject({
      environmentId: null,
      environmentRevision: null,
    });
  });

  it("does not expose environment selection without a readable tenant projection", async () => {
    const input = environmentProps({
      environmentResult: { available: false, reason: "Forbidden" },
    });
    render(<AgentControlPanel {...input} />);
    await start();
    expect(
      screen.queryByRole("combobox", { name: "Environment" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("No environment attached")).toBeInTheDocument();
    await reviewAndCreate();
    expect(input.client.listEnvironmentRevisions).not.toHaveBeenCalled();
    expect(input.onCreate.mock.calls[0][0]).not.toHaveProperty("environmentId");
  });

  it("preserves an attachment draft but cannot save it to a newly selected agent", async () => {
    const input = environmentProps({
      agent: existingAgent,
      detail: { available: true, agent: existingAgent },
      capabilities: { permittedActions: ["update"] },
    });
    const view = render(<AgentControlPanel {...input} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await chooseEnvironment();
    await chooseRevision();
    const otherAgent = { ...existingAgent, id: "agent-2" };
    view.rerender(
      <AgentControlPanel
        {...input}
        agent={otherAgent}
        detail={{ available: true, agent: otherAgent }}
      />,
    );
    expect(screen.getByRole("button", { name: "Save agent" })).toBeDisabled();
    expect(
      screen.getByText("Research workspace · Revision 1"),
    ).toBeInTheDocument();
    expect(input.onUpdate).not.toHaveBeenCalled();
  });

  it("rejects a mismatched revision envelope and keeps details for retry", async () => {
    const input = environmentProps();
    input.client.listEnvironmentRevisions.mockResolvedValueOnce(
      revisions("env-reports"),
    );
    render(<AgentControlPanel {...input} />);
    await start();
    await chooseEnvironment();
    expect(
      await screen.findByText(/revisions could not be verified/),
    ).toBeInTheDocument();
    expect(
      editor().getByRole("button", { name: "Review agent" }),
    ).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue(
      "Ticket triage",
    );
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await chooseRevision();
    await reviewAndCreate();
    expect(input.onCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        environmentId: "env-research",
        environmentRevision: 1,
      }),
    );
  });

  it("lets a tenant edit a starter, review it, and create through the workforce API", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ agent: { id: "new-agent" } }), {
        status: 201,
        headers: { "content-type": "application/json" },
      }),
    );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.test",
        getToken: () => "session",
        authMode: "bearer",
      },
      { fetchImpl },
    );
    const onCreate = vi.fn((input) => client.createWorkforceAgent(input));
    render(<AgentControlPanel {...props({ onCreate })} />);
    await start();
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue(
      "Ticket triage",
    );
    const goal = screen.getByRole("textbox", { name: "Goal" });
    await userEvent.clear(goal);
    await userEvent.type(goal, "Triage support requests for the tenant.");
    const responsibilities = screen.getByRole("textbox", {
      name: "Responsibilities",
    });
    await userEvent.clear(responsibilities);
    await userEvent.type(
      responsibilities,
      " Review supplied tickets  \n\n Ask for missing context",
    );
    await userEvent.click(
      editor().getByRole("button", { name: "Review agent" }),
    );
    expect(onCreate).not.toHaveBeenCalled();
    expect(editor().getByText("No connections selected")).toBeInTheDocument();
    expect(editor().getByText("No schedule")).toBeInTheDocument();
    expect(
      editor().getByText("Triage support requests for the tenant."),
    ).toBeInTheDocument();
    await userEvent.click(
      editor().getByRole("button", { name: "Create agent" }),
    );
    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(1));
    const [url, request] = fetchImpl.mock.calls[0];
    expect(url).toBe("https://omega.example.test/workspace/workforce/agents");
    expect(request.method).toBe("POST");
    expect(request.headers.Authorization).toBe("Bearer session");
    expect(JSON.parse(request.body)).toEqual({
      name: "Ticket triage",
      role: "Triage specialist",
      purpose: "Triage support requests for the tenant.",
      responsibilities: ["Review supplied tickets", "Ask for missing context"],
      workMode: "on_demand",
      connectorBindingIds: [],
    });
    await waitFor(() =>
      expect(
        screen.queryByRole("region", { name: "Create a managed agent" }),
      ).not.toBeInTheDocument(),
    );
  });

  it.each([
    [
      "a returned refusal",
      () =>
        Promise.resolve({
          available: false,
          reason: "Remote create is unavailable.",
        }),
      "Remote create is unavailable.",
    ],
    [
      "a rejected request",
      () => Promise.reject(new Error("disconnected")),
      "We couldn't save this agent. Your draft is still here; try again.",
    ],
  ])(
    "preserves the draft and permits retry after %s",
    async (_label, failure, message) => {
      const onCreate = vi
        .fn()
        .mockImplementationOnce(failure)
        .mockResolvedValue({ available: true });
      render(<AgentControlPanel {...props({ onCreate })} />);
      await start("Research analyst");
      const goal = screen.getByRole("textbox", { name: "Goal" });
      await userEvent.clear(goal);
      await userEvent.type(goal, "My carefully edited goal.");
      await reviewAndCreate();
      expect(await screen.findByText(message)).toBeInTheDocument();
      await userEvent.click(
        editor().getByRole("button", { name: "Back to details" }),
      );
      expect(screen.getByRole("textbox", { name: "Goal" })).toHaveValue(
        "My carefully edited goal.",
      );
      await reviewAndCreate();
      await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(2));
      expect(onCreate.mock.calls[0][0]).toEqual(onCreate.mock.calls[1][0]);
    },
  );

  it("starts blank, validates required details, and protects changes when switching starters", async () => {
    const input = props();
    render(<AgentControlPanel {...input} />);
    await start("Start blank");
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("");
    expect(
      editor().getByRole("button", { name: "Review agent" }),
    ).toBeDisabled();
    await userEvent.type(
      screen.getByRole("textbox", { name: "Name" }),
      "Custom agent",
    );
    await userEvent.click(
      editor().getByRole("button", { name: "Change starter" }),
    );
    await userEvent.click(
      editor().getByRole("button", { name: "Report assistant" }),
    );
    expect(
      editor().getByText(/Replace your current draft/),
    ).toBeInTheDocument();
    await userEvent.click(
      editor().getByRole("button", { name: "Keep my draft" }),
    );
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue(
      "Custom agent",
    );
    await userEvent.click(
      editor().getByRole("button", { name: "Change starter" }),
    );
    await userEvent.click(
      editor().getByRole("button", { name: "Report assistant" }),
    );
    await userEvent.click(
      editor().getByRole("button", { name: "Replace draft" }),
    );
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue(
      "Report assistant",
    );
    await userEvent.click(editor().getByRole("button", { name: "Cancel" }));
    expect(input.onCreate).not.toHaveBeenCalled();
    expect(input.onUpdate).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("region", { name: "Create a managed agent" }),
    ).not.toBeInTheDocument();
  });

  it("keeps existing edit and cancel behavior without overwriting connection fields", async () => {
    const input = props({
      agent: existingAgent,
      detail: { available: true, agent: existingAgent },
      capabilities: { permittedActions: ["update"] },
    });
    render(<AgentControlPanel {...input} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(
      screen.queryByRole("button", { name: "Ticket triage" }),
    ).not.toBeInTheDocument();
    await userEvent.type(
      screen.getByRole("textbox", { name: "Name" }),
      " draft",
    );
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(input.onUpdate).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue(
      "Existing",
    );
    const purpose = screen.getByRole("textbox", { name: "Purpose" });
    await userEvent.clear(purpose);
    await userEvent.type(purpose, "Write weekly updates");
    await userEvent.click(screen.getByRole("button", { name: "Save agent" }));
    await waitFor(() =>
      expect(input.onUpdate).toHaveBeenCalledWith(
        "agent-1",
        expect.objectContaining({ purpose: "Write weekly updates" }),
      ),
    );
    expect(input.onUpdate.mock.calls[0][1]).not.toHaveProperty(
      "connectorBindingIds",
    );
    expect(input.onCreate).not.toHaveBeenCalled();
  });

  it("edits, adds and removes examples with the current definition version", async () => {
    const agent = {
      ...existingAgent,
      version: 7,
      examples: [{ prompt: "Old prompt", response: "Old response" }],
    };
    const input = props({
      agent,
      detail: { available: true, agent },
      capabilities: { permittedActions: ["update"] },
    });
    render(<AgentControlPanel {...input} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByRole("textbox", { name: "Example prompt" })).toHaveValue(
      "Old prompt",
    );
    await userEvent.clear(
      screen.getByRole("textbox", { name: "Example prompt" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Example prompt" }),
      "New prompt",
    );
    await userEvent.click(screen.getByRole("button", { name: "Add example" }));
    const exampleRegions = screen.getAllByRole("group", { name: /Example/ });
    expect(screen.getByRole("button", { name: "Save agent" })).toBeDisabled();
    await userEvent.type(
      within(exampleRegions[1]).getByRole("textbox", {
        name: "Example prompt",
      }),
      "Second prompt",
    );
    await userEvent.type(
      within(exampleRegions[1]).getByRole("textbox", {
        name: "Example response",
      }),
      "New response",
    );
    await userEvent.click(
      within(exampleRegions[0]).getByRole("button", {
        name: "Remove example 1",
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save agent" }));
    await waitFor(() => expect(input.onUpdate).toHaveBeenCalledTimes(1));
    expect(input.onUpdate).toHaveBeenCalledWith(
      "agent-1",
      expect.objectContaining({
        expectedVersion: 7,
        examples: [{ prompt: "Second prompt", response: "New response" }],
      }),
    );
    expect(input.onUpdate.mock.calls[0][1]).not.toHaveProperty("environmentId");
    expect(input.onUpdate.mock.calls[0][1]).not.toHaveProperty(
      "connectorBindingIds",
    );
    expect(input.onControl).not.toHaveBeenCalled();
    expect(
      await screen.findByText(/Pause a running agent, then start it/),
    ).toBeInTheDocument();
  });

  it("supports an explicit empty example clear and blocks stale or incomplete details", async () => {
    const agent = {
      ...existingAgent,
      version: 3,
      examples: [{ prompt: "P", response: "R" }],
    };
    const input = props({
      agent,
      detail: { available: true, agent },
      capabilities: { permittedActions: ["update"] },
    });
    const view = render(<AgentControlPanel {...input} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.click(
      screen.getByRole("button", { name: "Remove example 1" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save agent" }));
    await waitFor(() =>
      expect(input.onUpdate).toHaveBeenCalledWith(
        "agent-1",
        expect.objectContaining({ expectedVersion: 3, examples: [] }),
      ),
    );
    input.onUpdate.mockClear();
    const stale = {
      ...agent,
      version: undefined,
      examples: [{ prompt: "P", response: "R" }],
    };
    view.rerender(
      <AgentControlPanel
        {...input}
        agent={stale}
        detail={{ available: true, agent: stale }}
      />,
    );
    await userEvent.click(view.getByRole("button", { name: "Edit" }));
    expect(
      view.getByText(/Read the latest agent definition/),
    ).toBeInTheDocument();
    expect(view.getByRole("button", { name: "Save agent" })).toBeDisabled();
  });

  it("keeps malformed server examples read-only until a complete detail refresh", async () => {
    const agent = {
      ...existingAgent,
      version: 2,
      examples: [{ prompt: "missing response" }],
    };
    const input = props({
      agent,
      detail: { available: true, agent },
      capabilities: { permittedActions: ["update"] },
    });
    render(<AgentControlPanel {...input} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(
      screen.getByText(/complete examples could not be read/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add example" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Save agent" }),
    ).not.toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Save agent" }));
    await waitFor(() =>
      expect(input.onUpdate).toHaveBeenCalledWith(
        "agent-1",
        expect.objectContaining({ expectedVersion: 2 }),
      ),
    );
    expect(input.onUpdate.mock.calls[0][1]).not.toHaveProperty("examples");
  });

  it("validates both example halves by UTF-8 bytes before creating a reviewed definition", async () => {
    const input = props();
    render(<AgentControlPanel {...input} />);
    await start("Report assistant");
    await userEvent.click(screen.getByRole("button", { name: "Add example" }));
    const prompt = screen.getByRole("textbox", { name: "Example prompt" });
    const response = screen.getByRole("textbox", { name: "Example response" });
    expect(
      editor().getByRole("button", { name: "Review agent" }),
    ).toBeDisabled();
    await userEvent.type(prompt, "A user asks for the weekly report");
    expect(screen.getByText("Enter an example response.")).toBeInTheDocument();
    await userEvent.click(response);
    await userEvent.paste("学".repeat(667));
    expect(
      screen.getByText(/response exceeds 2,000 UTF-8 bytes/),
    ).toBeInTheDocument();
    expect(
      editor().getByRole("button", { name: "Review agent" }),
    ).toBeDisabled();
    await userEvent.clear(response);
    await userEvent.type(response, "Here is a concise report with evidence.");
    await userEvent.click(
      editor().getByRole("button", { name: "Review agent" }),
    );
    expect(
      screen.getByText("Response: Here is a concise report with evidence."),
    ).toBeInTheDocument();
    expect(input.onCreate).not.toHaveBeenCalled();
    await userEvent.click(
      editor().getByRole("button", { name: "Create agent" }),
    );
    await waitFor(() =>
      expect(input.onCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          examples: [
            {
              prompt: "A user asks for the weekly report",
              response: "Here is a concise report with evidence.",
            },
          ],
        }),
      ),
    );
    expect(input.onCreate.mock.calls[0][0]).not.toHaveProperty(
      "expectedVersion",
    );
  });

  it("bounds the number of pairs and omits examples when an existing definition leaves them unchanged", async () => {
    const agent = {
      ...existingAgent,
      version: 5,
      examples: Array.from({ length: 16 }, (_, index) => ({
        prompt: `Prompt ${index}`,
        response: `Response ${index}`,
      })),
    };
    const input = props({
      agent,
      detail: { available: true, agent },
      capabilities: { permittedActions: ["update"] },
    });
    render(<AgentControlPanel {...input} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getAllByRole("group", { name: /Example/ })).toHaveLength(16);
    expect(screen.getByRole("button", { name: "Add example" })).toBeDisabled();
    await userEvent.type(
      screen.getByRole("textbox", { name: "Name" }),
      " updated",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save agent" }));
    await waitFor(() =>
      expect(input.onUpdate).toHaveBeenCalledWith(
        "agent-1",
        expect.objectContaining({
          expectedVersion: 5,
          name: "Existing updated",
        }),
      ),
    );
    expect(input.onUpdate.mock.calls[0][1]).not.toHaveProperty("examples");
  });

  it("keeps the captured version and the edited examples after a stale revision conflict", async () => {
    const agent = {
      ...existingAgent,
      version: 6,
      examples: [{ prompt: "P", response: "Old" }],
    };
    const input = props({
      agent,
      detail: { available: true, agent },
      capabilities: { permittedActions: ["update"] },
      onUpdate: vi.fn().mockResolvedValue({
        available: false,
        status: 409,
        code: "version_conflict",
        reason: "The definition changed. Refresh before saving.",
      }),
    });
    const view = render(<AgentControlPanel {...input} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.clear(
      screen.getByRole("textbox", { name: "Example response" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Example response" }),
      "My new response",
    );
    view.rerender(
      <AgentControlPanel
        {...input}
        detail={{
          available: true,
          agent: {
            ...agent,
            version: 7,
            examples: [{ prompt: "P", response: "Someone else's response" }],
          },
        }}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Save agent" }));
    await waitFor(() =>
      expect(input.onUpdate).toHaveBeenCalledWith(
        "agent-1",
        expect.objectContaining({
          expectedVersion: 6,
          examples: [{ prompt: "P", response: "My new response" }],
        }),
      ),
    );
    expect(
      await screen.findByText("The definition changed. Refresh before saving."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "Example response" }),
    ).toHaveValue("My new response");
    expect(input.onControl).not.toHaveBeenCalled();
  });

  it("keeps imported definitions editable with complete example metadata", async () => {
    const input = props();
    render(<AgentControlPanel {...input} />);
    const file = new File(
      [
        exportAgentDefinition({
          ...existingAgent,
          examples: [
            { prompt: "Imported prompt", response: "Imported response" },
          ],
        }),
      ],
      "agent.json",
      { type: "application/json" },
    );
    await userEvent.upload(
      screen.getByLabelText("Agent definition file"),
      file,
    );
    const response = await screen.findByRole("textbox", {
      name: "Example response",
    });
    expect(screen.getByRole("button", { name: "Add example" })).toBeEnabled();
    await userEvent.clear(response);
    await userEvent.type(response, "Edited imported response");
    await reviewAndCreate();
    await waitFor(() =>
      expect(input.onCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          examples: [
            { prompt: "Imported prompt", response: "Edited imported response" },
          ],
        }),
      ),
    );
    expect(input.onCreate.mock.calls[0][0]).not.toHaveProperty(
      "expectedVersion",
    );
  });

  it("retains drafts but blocks submission when permissions change or another agent is selected", async () => {
    const input = props();
    const view = render(<AgentControlPanel {...input} />);
    await start();
    view.rerender(
      <AgentControlPanel {...input} capabilities={{ permittedActions: [] }} />,
    );
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue(
      "Ticket triage",
    );
    expect(
      editor().getByRole("button", { name: "Review agent" }),
    ).toBeDisabled();
    expect(input.onCreate).not.toHaveBeenCalled();
    await userEvent.click(editor().getByRole("button", { name: "Cancel" }));
    expect(
      screen.queryByRole("button", { name: "Create agent" }),
    ).not.toBeInTheDocument();
    const editing = {
      ...input,
      agent: existingAgent,
      detail: { available: true, agent: existingAgent },
      capabilities: { permittedActions: ["update"] },
    };
    view.rerender(<AgentControlPanel {...editing} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.type(
      screen.getByRole("textbox", { name: "Name" }),
      " edited",
    );
    view.rerender(
      <AgentControlPanel
        {...editing}
        agent={{ ...existingAgent, id: "agent-2" }}
      />,
    );
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue(
      "Existing edited",
    );
    expect(screen.getByRole("button", { name: "Save agent" })).toBeDisabled();
    expect(input.onUpdate).not.toHaveBeenCalled();
  });
});
