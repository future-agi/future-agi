import { describe, expect, it, vi } from "vitest";
import {
  act,
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import { AgentControlPanel } from "./OmegaSquadPage";
import {
  exportAgentDefinition,
  importAgentDefinition,
} from "./agentDefinition";

const modes = ["private", "workspace", "selected"];
const directory = {
  available: true,
  schemaVersion: "omega-workforce/v1",
  truncated: false,
  candidates: [
    { accountId: "member-a", displayName: "Alex" },
    { accountId: "member-b", displayName: "Blair" },
  ],
};
const agent = {
  id: "agent-a",
  name: "Researcher",
  version: 3,
  role: "Analyst",
  purpose: "Write a report",
  responsibilities: [],
  workMode: "on_demand",
  examples: [],
  sharing: { mode: "private", accountIds: [] },
  ownerAccountId: "owner-a",
  capabilities: { canEdit: true, canManageSharing: true },
  status: "idle",
};
function props(overrides = {}) {
  return {
    agent: null,
    detail: null,
    detailLoading: false,
    capabilities: {
      canCreateAgent: true,
      permittedActions: ["create", "update"],
      sharingModes: modes,
    },
    onCreate: vi.fn().mockResolvedValue({ available: true }),
    onUpdate: vi.fn().mockResolvedValue({ available: true }),
    onControl: vi.fn(),
    client: {
      listWorkforceSharingCandidates: vi.fn().mockResolvedValue(directory),
    },
    ...overrides,
  };
}
async function start() {
  await userEvent.click(screen.getByRole("button", { name: "Create agent" }));
  await userEvent.click(
    screen.getByRole("button", { name: "Research analyst" }),
  );
}
async function create() {
  await userEvent.click(screen.getByRole("button", { name: "Review agent" }));
  await userEvent.click(
    within(
      screen.getByRole("region", { name: "Create a managed agent" }),
    ).getByRole("button", { name: "Create agent" }),
  );
}

describe("Agent sharing", () => {
  it("removes departed recipients from the verified cache after a complete directory refresh", async () => {
    const input = props();
    input.client.listWorkforceSharingCandidates
      .mockResolvedValueOnce(directory)
      .mockResolvedValue({
        ...directory,
        candidates: [directory.candidates[1]],
      });
    render(<AgentControlPanel {...input} />);
    await start();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Agent access" }),
      "selected",
    );
    await userEvent.click(
      await screen.findByRole("checkbox", { name: "Alex" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Search members" }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("checkbox", { name: "Alex" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Review agent" })).toBeDisabled();
    expect(input.onCreate).not.toHaveBeenCalled();
  });

  it("retains selected people while searching the server directory for additional recipients", async () => {
    const input = props();
    input.client.listWorkforceSharingCandidates.mockImplementation(
      ({ query }) =>
        Promise.resolve({
          ...directory,
          candidates: query
            ? [directory.candidates[1]]
            : [directory.candidates[0]],
          truncated: !query,
        }),
    );
    render(<AgentControlPanel {...input} />);
    await start();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Agent access" }),
      "selected",
    );
    await userEvent.click(
      await screen.findByRole("checkbox", { name: "Alex" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Find workspace members" }),
      "Blair",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Search members" }),
    );
    await userEvent.click(
      await screen.findByRole("checkbox", { name: "Blair" }),
    );
    expect(screen.getByRole("checkbox", { name: "Alex" })).toBeChecked();
    expect(
      input.client.listWorkforceSharingCandidates,
    ).toHaveBeenLastCalledWith({ query: "Blair" });
    await create();
    expect(input.onCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        sharing: { mode: "selected", accountIds: ["member-a", "member-b"] },
      }),
    );
  });

  it("creates private by default without reading a member directory", async () => {
    const input = props();
    render(<AgentControlPanel {...input} />);
    await start();
    expect(screen.getByRole("combobox", { name: "Agent access" })).toHaveValue(
      "private",
    );
    await create();
    expect(input.onCreate).toHaveBeenCalledWith(
      expect.objectContaining({ sharing: { mode: "private", accountIds: [] } }),
    );
    expect(input.client.listWorkforceSharingCandidates).not.toHaveBeenCalled();
  });

  it("selects only current server-listed members and reviews access before creating", async () => {
    const input = props();
    render(<AgentControlPanel {...input} />);
    await start();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Agent access" }),
      "selected",
    );
    expect(screen.getByRole("button", { name: "Review agent" })).toBeDisabled();
    await userEvent.click(
      await screen.findByRole("checkbox", { name: "Alex" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Review agent" }));
    expect(
      screen.getByText("Access: Selected people (1)."),
    ).toBeInTheDocument();
    await userEvent.click(
      within(
        screen.getByRole("region", { name: "Create a managed agent" }),
      ).getByRole("button", { name: "Create agent" }),
    );
    expect(input.onCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        sharing: { mode: "selected", accountIds: ["member-a"] },
      }),
    );
    expect(input.onControl).not.toHaveBeenCalled();
  });

  it("saves a versioned owner sharing change without deploying and omits it on unrelated edits", async () => {
    const input = props({ agent, detail: { available: true, agent } });
    render(<AgentControlPanel {...input} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Save agent" }));
    expect(input.onUpdate).toHaveBeenCalledWith(
      agent.id,
      expect.not.objectContaining({ sharing: expect.anything() }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Agent access" }),
      "workspace",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save agent" }));
    expect(input.onUpdate).toHaveBeenLastCalledWith(agent.id, {
      expectedVersion: 3,
      sharing: { mode: "workspace", accountIds: [] },
    });
    expect(input.onControl).not.toHaveBeenCalled();
  });

  it("requires the record-specific sharing capability and blocks a draft after permission is withdrawn", async () => {
    const input = props({ agent, detail: { available: true, agent } });
    const view = render(<AgentControlPanel {...input} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Agent access" }),
      "workspace",
    );
    const revoked = {
      ...agent,
      capabilities: { canEdit: true, canManageSharing: false },
    };
    view.rerender(
      <AgentControlPanel
        {...input}
        detail={{ available: true, agent: revoked }}
      />,
    );
    expect(
      screen.queryByRole("combobox", { name: "Agent access" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save agent" })).toBeDisabled();
    expect(input.onUpdate).not.toHaveBeenCalled();
  });

  it("ignores late directory responses from a previous client and keeps unsafe recipient drafts unsavable", async () => {
    const input = props();
    const view = render(<AgentControlPanel {...input} />);
    await start();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Agent access" }),
      "selected",
    );
    await userEvent.click(
      await screen.findByRole("checkbox", { name: "Alex" }),
    );
    let accept;
    const client = {
      listWorkforceSharingCandidates: vi.fn().mockImplementation(
        () =>
          new Promise((resolve) => {
            accept = resolve;
          }),
      ),
    };
    view.rerender(<AgentControlPanel {...input} client={client} />);
    expect(screen.getByRole("button", { name: "Review agent" })).toBeDisabled();
    await waitFor(() =>
      expect(client.listWorkforceSharingCandidates).toHaveBeenCalled(),
    );
    await act(async () =>
      accept({ ...directory, candidates: [directory.candidates[1]] }),
    );
    expect(screen.getByRole("button", { name: "Review agent" })).toBeDisabled();
    expect(
      screen.getByText(
        /Some saved recipients are not in the current member list/,
      ),
    ).toBeInTheDocument();
  });

  it("does not silently widen a private draft when the server stops advertising sharing", async () => {
    const input = props();
    const view = render(<AgentControlPanel {...input} />);
    await start();
    view.rerender(
      <AgentControlPanel
        {...input}
        capabilities={{ canCreateAgent: true, permittedActions: ["create"] }}
      />,
    );
    expect(screen.getByRole("button", { name: "Review agent" })).toBeDisabled();
    expect(input.onCreate).not.toHaveBeenCalled();
  });

  it("imports and duplicates definitions privately without carrying recipient identities", async () => {
    const shared = {
      ...agent,
      sharing: { mode: "selected", accountIds: ["source-native-member"] },
    };
    const exported = exportAgentDefinition(shared);
    expect(exported).not.toContain("source-native-member");
    expect(importAgentDefinition(exported).definition).not.toHaveProperty(
      "sharing",
    );
    const input = props();
    render(<AgentControlPanel {...input} />);
    await userEvent.upload(
      screen.getByLabelText("Agent definition file"),
      new File([exported], "agent.json", { type: "application/json" }),
    );
    expect(
      await screen.findByRole("combobox", { name: "Agent access" }),
    ).toHaveValue("private");
    await create();
    expect(input.onCreate).toHaveBeenCalledWith(
      expect.objectContaining({ sharing: { mode: "private", accountIds: [] } }),
    );
  });
});
