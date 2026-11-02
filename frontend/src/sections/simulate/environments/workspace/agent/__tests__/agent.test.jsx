import PropTypes from "prop-types";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import axios from "src/utils/axios";
import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";

vi.mock("notistack", async () => {
  const actual = await vi.importActual("notistack");
  return { ...actual, enqueueSnackbar: vi.fn() };
});

vi.mock("src/api/agents/connectedAgents", () => ({
  useConnectedAgents: () => ({
    data: {
      items: [
        {
          id: "a1",
          name: "Billing line",
          modality: "voice",
          source: "vapi",
          latestLabel: "v3",
          versionCount: 3,
          environments: [{ id: "e9", name: "Billing env" }],
          environmentCount: 1,
        },
        {
          id: "a2",
          name: "Returns chat",
          modality: "text",
          source: "github",
          latestLabel: "v1",
          versionCount: 1,
          environments: [],
          environmentCount: 0,
        },
        {
          id: "agent-1",
          name: "Uber agent",
          modality: "voice",
          source: "retell",
          latestLabel: "v1",
          versionCount: 1,
          environments: [],
          environmentCount: 1,
        },
      ],
      total: 3,
    },
    isLoading: false,
  }),
}));

vi.mock("src/api/agent-definition/agent-definition-version", () => ({
  useAgentDefinitionVersions: ({ selectedAgentId }) => ({
    data:
      selectedAgentId === "a1"
        ? {
            pages: [
              {
                results: [
                  { id: "v3-id", version_number: 3, is_active: false },
                  { id: "v2-id", version_number: 2, is_active: true },
                  { id: "v1-id", version_number: 1, is_active: false },
                ],
              },
            ],
          }
        : undefined,
    isLoading: false,
  }),
}));

vi.mock("src/api/simulate-environments/prebuilt", () => ({
  usePrebuiltEnvironments: () => ({ data: [] }),
  prebuiltEnvironmentsQueryKey: () => ["prebuilt"],
}));

const { enqueueSnackbar } = await import("notistack");
const { UI_ONLY_SAVED } = await import("../../gaps/NeedsInputButton");
const { default: ChangeAgentDrawer } = await import("../ChangeAgentDrawer");
const { default: AgentLine } = await import("../../overview/AgentLine");
const { default: OverviewPanel } = await import("../../overview/OverviewPanel");

const SUBJECT = {
  id: "agent-1",
  name: "Uber agent",
  provider: "retell",
  versions_count: 1,
  active_version: "v1",
};
const VOICE_ENV = {
  id: "env-1",
  name: "Returns line",
  surface: "voice",
  testSubject: SUBJECT,
};

const withClient = (ui) => (
  <QueryClientProvider
    client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
  >
    {ui}
  </QueryClientProvider>
);

function DrawerHarness({ env }) {
  const [open, setOpen] = useState(true);
  return (
    <ChangeAgentDrawer open={open} onClose={() => setOpen(false)} env={env} />
  );
}

DrawerHarness.propTypes = { env: PropTypes.object };

const renderDrawer = (env = VOICE_ENV) =>
  renderWithRouter(withClient(<DrawerHarness env={env} />));

let spied = [];
const writes = () => {
  spied = ["post", "put", "patch", "delete"].map((m) => vi.spyOn(axios, m));
  return spied;
};

beforeEach(() => enqueueSnackbar.mockClear());
// Only the axios spies: restoring every mock would undo the setup's matchMedia.
afterEach(() => {
  spied.forEach((s) => s.mockRestore());
  spied = [];
});

describe("Change agent drawer", () => {
  it("Connect an agent lists real connected agents of the same channel with versions", () => {
    renderDrawer();
    expect(screen.getByText("Connected voice agents")).toBeInTheDocument();
    const list = screen.getAllByRole("radio");
    // Only the voice agent that isn't the one this environment tests.
    expect(list).toHaveLength(1);
    expect(
      screen.getByRole("radio", { name: /Billing line/ }),
    ).toHaveTextContent("Vapi · 3 versions · used in Billing env");
    expect(screen.queryByText("Returns chat")).toBeNull();
    expect(
      screen.getByText(/1 agent on other channels isn't listed/),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: /Billing line/ }));
    // The active version is picked by default, from the agent's real versions.
    const select = screen.getByRole("combobox", { name: "Version to test" });
    expect(select).toHaveTextContent("v2 · active");
    fireEvent.mouseDown(select);
    const options = within(screen.getByRole("listbox")).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual([
      "v3",
      "v2 · active",
      "v1",
    ]);
    expect(document.body.textContent).not.toMatch(/—/);
  });

  it("Test <name> here is flagged and sends nothing", () => {
    const spies = writes();
    renderWithRouter(
      withClient(
        <OverviewPanel
          env={{ ...MOCK_WORLD, ...VOICE_ENV, rules: [] }}
          envState={{ agent: { typeId: "phone" }, scenarios: [] }}
          patch={vi.fn()}
          onGo={vi.fn()}
          backedWorld={{ amendments: [], dependencies: [], stores: [] }}
        />,
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Agent actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Change agent/ }));

    const drawer = screen
      .getByText("Connected voice agents")
      .closest(".MuiDrawer-paper");
    expect(within(drawer).getAllByText("UI only").length).toBeGreaterThan(0);
    const confirm = within(drawer).getByRole("button", {
      name: "Pick an agent",
    });
    expect(confirm).toBeDisabled();
    fireEvent.click(
      within(drawer).getByRole("radio", { name: /Billing line/ }),
    );
    fireEvent.click(
      within(drawer).getByRole("button", { name: "Test Billing line here" }),
    );

    expect(enqueueSnackbar).toHaveBeenCalledWith(UI_ONLY_SAVED, {
      variant: "info",
    });
    spies.forEach((s) => expect(s).not.toHaveBeenCalled());
    // The real agent is still the one this environment tests.
    expect(
      screen.getByText(
        (_, el) =>
          el?.tagName === "P" && /^Tests Uber agent v1/.test(el.textContent),
      ),
    ).toBeInTheDocument();
  });

  it("Connect a new agent opens the build drawer's connect flow filtered to the channel", async () => {
    renderDrawer();
    fireEvent.click(
      screen.getByRole("button", { name: "Connect a voice agent" }),
    );
    const back = screen.queryByRole("button", { name: /^Back to / });
    expect(back).toBeNull();
    await waitFor(() =>
      expect(screen.queryByText("Connected voice agents")).toBeNull(),
    );
    expect(
      screen.getByText(
        "This world is for voice agents, so only ways to connect a voice agent are shown. It joins Agents, and this environment is built for it.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "Search ways to connect" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /Hosted platforms 7/ }));
    expect(screen.getByRole("listitem", { name: "Vapi" })).toBeInTheDocument();
    expect(
      screen.queryByRole("listitem", { name: "Salesforce Agentforce" }),
    ).toBeNull();
  });

  it("offers chat agents on a chat environment", () => {
    renderDrawer({ ...VOICE_ENV, surface: "chat" });
    expect(screen.getByText("Connected chat agents")).toBeInTheDocument();
    expect(screen.getAllByRole("radio")).toHaveLength(1);
    expect(
      screen.getByRole("radio", { name: /Returns chat/ }),
    ).toHaveTextContent("GitHub · 1 version");
  });
});

const renderLine = (subject, route = "/x", props = {}) =>
  renderWithRouter(
    withClient(
      <AgentLine
        subject={subject}
        agentMode="connect_only"
        envId="env-1"
        onChangeAgent={vi.fn()}
        {...props}
      />,
    ),
    { route },
  );

describe("Agent line flows", () => {
  it("never offers a sample Try vN or Default to production", () => {
    renderLine(
      { ...SUBJECT, versions_count: 3, active_version: "v2" },
      "/x?uiSample=agentversion",
    );
    expect(screen.queryByRole("button", { name: /^Try v/ })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Default to production" }),
    ).toBeNull();
  });

  it("states the real default agent version while versioning is on", async () => {
    vi.spyOn(axios, "get").mockResolvedValue({
      data: {
        agent: [
          { id: "av2", label: "v2", is_default: true, status: "active" },
          { id: "av1", label: "v1", status: "active" },
        ],
        environment: [],
        suite: [],
      },
    });
    renderLine(SUBJECT, "/x", { versioningEnabled: true });
    expect(
      await screen.findByText((_, el) =>
        el?.tagName === "P" && /· default v2$/.test(el.textContent),
      ),
    ).toBeInTheDocument();
    axios.get.mockRestore();
  });

  it("states no default agent version while versioning is off", () => {
    renderLine(SUBJECT);
    expect(document.body.textContent).not.toMatch(/default/);
  });

  it("Manage versions and Sync agent open the agent versions when offered", () => {
    const onManageVersions = vi.fn();
    renderLine(SUBJECT, "/x", { onManageVersions });
    fireEvent.click(screen.getByRole("button", { name: "Manage versions" }));
    expect(onManageVersions).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Agent actions" }));
    const sync = screen.getByRole("menuitem", { name: /Sync agent/ });
    expect(within(sync).queryByText("UI only")).toBeNull();
    fireEvent.click(sync);
    expect(onManageVersions).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("⋯ Sync agent opens the sync dialog flagged", () => {
    const spies = writes();
    renderLine(SUBJECT);
    fireEvent.click(screen.getByRole("button", { name: "Agent actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Sync agent/ }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Sync Uber agent")).toBeInTheDocument();
    expect(within(dialog).getByText("UI only")).toBeInTheDocument();
    expect(
      within(dialog).getByText("Connecting to Retell"),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("Comparing with v1")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Sync now" }));
    expect(enqueueSnackbar).toHaveBeenCalledWith(UI_ONLY_SAVED, {
      variant: "info",
    });
    spies.forEach((s) => expect(s).not.toHaveBeenCalled());
  });
});
