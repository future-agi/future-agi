import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  within,
  act,
} from "src/utils/test-utils";

import CustomizePanel from "../CustomizePanel";

// deleteSkill is imported lazily by the skill editor; vi.mock covers that too.
const mocks = vi.hoisted(() => ({
  listSkills: vi.fn(),
  getSkill: vi.fn(),
  createSkill: vi.fn(),
  updateSkill: vi.fn(),
  deleteSkill: vi.fn(),
  fetchConnectors: vi.fn(),
  getConnector: vi.fn(),
  createConnector: vi.fn(),
  updateConnector: vi.fn(),
  deleteConnector: vi.fn(),
  authenticateConnector: vi.fn(),
  updateConnectorTools: vi.fn(),
  discoverConnectorTools: vi.fn(),
  falconAIQueryKeys: {
    connector: (id) => ["falcon-ai", "connector", id],
  },
}));

vi.mock("../../hooks/useFalconAPI", () => mocks);

const SKILL = {
  id: "skill-1",
  name: "Bug triage",
  description: "",
  icon: "mdi:bug-outline",
  instructions: "Group new errors by their likely cause.",
  trigger_phrases: ["triage errors"],
};

// The list row is a summary without instructions; the detail has them.
const SKILL_ROW = {
  id: SKILL.id,
  name: SKILL.name,
  description: SKILL.description,
  icon: SKILL.icon,
  trigger_phrases: SKILL.trigger_phrases,
};

const CONNECTOR_ROW = {
  id: "conn-1",
  name: "DeepWiki",
  server_url: "https://mcp.deepwiki.com/mcp",
  transport: "streamable_http",
  auth_type: "none",
  is_active: true,
  is_verified: true,
  tool_count: 3,
};

const CONNECTOR = {
  ...CONNECTOR_ROW,
  discovered_tools: [
    { name: "ask_question", description: "Ask about a repo." },
    { name: "read_wiki_contents", description: "View documentation." },
    { name: "read_wiki_structure", description: "List doc topics." },
  ],
  enabled_tool_names: [
    "ask_question",
    "read_wiki_contents",
    "read_wiki_structure",
  ],
};

// Shapes the axios interceptor rejects with: the error body spread flat, plus
// the transport classification.
const NETWORK_DROP = { message: "Network Error", transportCode: "ERR_NETWORK" };

const renderPanel = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <CustomizePanel />
    </QueryClientProvider>,
  );

const openSkillEditor = async () => {
  renderPanel();
  fireEvent.click(await screen.findByText(SKILL.name));
  await screen.findByText(SKILL.instructions);
  fireEvent.click(screen.getByText("Edit skill"));
  await screen.findByText("Edit Skill");
};

const openConnector = async () => {
  renderPanel();
  fireEvent.click(await screen.findByText("Connectors"));
  fireEvent.click(await screen.findByText(CONNECTOR.name));
  await screen.findByText("ask_question");
};

const dialogGone = () =>
  waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listSkills.mockResolvedValue({ results: [SKILL_ROW] });
  mocks.getSkill.mockResolvedValue({ result: SKILL });
  mocks.deleteSkill.mockResolvedValue(undefined);
  mocks.fetchConnectors.mockResolvedValue({ results: [CONNECTOR_ROW] });
  mocks.getConnector.mockResolvedValue(CONNECTOR);
  mocks.deleteConnector.mockResolvedValue(undefined);
});

describe("Customize > skill editor > Delete", () => {
  it("asks before deleting, and Cancel keeps the skill", async () => {
    await openSkillEditor();

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    // One click used to remove the skill on the spot.
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Delete skill?");
    expect(dialog).toHaveTextContent(
      "Bug triage and its instructions are removed, and Falcon stops using it. You can't undo this.",
    );
    expect(mocks.deleteSkill).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await dialogGone();
    expect(mocks.deleteSkill).not.toHaveBeenCalled();
    expect(screen.getByText("Edit Skill")).toBeInTheDocument();
  });

  it("deletes once confirmed and closes the editor", async () => {
    await openSkillEditor();

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    const reads = mocks.listSkills.mock.calls.length;
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Delete skill" }),
    );

    await waitFor(() =>
      expect(mocks.deleteSkill).toHaveBeenCalledWith(SKILL.id),
    );
    expect(mocks.deleteSkill).toHaveBeenCalledTimes(1);
    // The list is read again and the pane goes back to its empty state.
    await waitFor(() =>
      expect(mocks.listSkills.mock.calls.length).toBeGreaterThan(reads),
    );
    expect(await screen.findByText("Create new skills")).toBeInTheDocument();
    await dialogGone();
  });

  it("says the delete failed and keeps the skill open", async () => {
    mocks.deleteSkill.mockRejectedValue(NETWORK_DROP);
    await openSkillEditor();

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Delete skill" }),
    );

    await dialogGone();
    // A failure used to leave the editor exactly as it was, with no word.
    expect(
      await screen.findByText("Skill could not be deleted. Try again."),
    ).toBeInTheDocument();
    expect(screen.getByText("Edit Skill")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
  });
});

describe("Customize > connector > Delete connector", () => {
  it("says it deletes, asks first, and Cancel keeps the connector", async () => {
    await openConnector();

    // "Disconnect" deleted the connector, credentials included.
    expect(screen.queryByRole("button", { name: "Disconnect" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Delete connector" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Delete connector?");
    expect(dialog).toHaveTextContent(
      "Falcon AI will lose access to DeepWiki and its 3 tools. Its saved settings and credentials are removed and cannot be recovered.",
    );
    expect(mocks.deleteConnector).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await dialogGone();
    expect(mocks.deleteConnector).not.toHaveBeenCalled();
    expect(screen.getByText("ask_question")).toBeInTheDocument();
  });

  it("deletes once confirmed and blocks a second click", async () => {
    let release;
    mocks.deleteConnector.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    await openConnector();

    fireEvent.click(screen.getByRole("button", { name: "Delete connector" }));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", {
      name: "Delete connector",
    });
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(mocks.deleteConnector).toHaveBeenCalledWith(CONNECTOR.id),
    );
    await waitFor(() => expect(confirm).toBeDisabled());

    await act(async () => {
      release();
    });

    await dialogGone();
    await waitFor(() =>
      expect(screen.queryByText(CONNECTOR.name)).not.toBeInTheDocument(),
    );
    expect(mocks.deleteConnector).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Connect your tools")).toBeInTheDocument();
  });

  it.each([
    [
      "the server refuses",
      {
        status: false,
        detail: "Connector not found",
        message: "Connector not found",
        error: "Connector not found",
        result: "Connector not found",
        code: "not_found",
        statusCode: 404,
      },
      "Connector not found",
    ],
    [
      "the network drops",
      NETWORK_DROP,
      "Connector could not be deleted. Try again.",
    ],
  ])(
    "says the delete failed when %s, and keeps the connector",
    async (_label, rejection, message) => {
      mocks.deleteConnector.mockRejectedValue(rejection);
      await openConnector();

      fireEvent.click(screen.getByRole("button", { name: "Delete connector" }));
      const dialog = await screen.findByRole("dialog");
      fireEvent.click(
        within(dialog).getByRole("button", { name: "Delete connector" }),
      );

      await dialogGone();
      // The failure used to be swallowed with the panel left as it was.
      expect(await screen.findByRole("alert")).toHaveTextContent(message);
      expect(screen.getByText("ask_question")).toBeInTheDocument();
      expect(screen.getAllByText(CONNECTOR.name).length).toBeGreaterThan(0);
    },
  );
});
