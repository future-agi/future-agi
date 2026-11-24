import { describe, it, expect, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { render } from "src/utils/test-utils";
import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import OverviewPanel from "../OverviewPanel";

vi.mock(
  "src/api/simulate-environments/designReview",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useDesignReview: vi.fn(() => ({ data: undefined })),
  }),
);

// The drawer's real-data panel has its own tests; here it only has to mount.
vi.mock("../../agents/AgentVersionsPanel", () => ({
  default: ({ env }) => <div data-testid="agent-versions-panel">{env.id}</div>,
}));
import { HardRulesCard } from "../OverviewCards";

const connectedState = {
  agent: { via: "endpoint", typeId: "livekit", values: {} },
  scenarios: [],
};

const renderPanel = (props = {}) =>
  render(
    <OverviewPanel
      env={MOCK_WORLD}
      envState={connectedState}
      onGo={vi.fn()}
      agentConnected
      {...props}
    />,
  );

describe("OverviewPanel", () => {
  it("shows the description and the two facts (Channel, Connector)", () => {
    renderPanel();

    expect(screen.getByText(MOCK_WORLD.description)).toBeInTheDocument();
    expect(screen.getByText("Channel")).toBeInTheDocument();
    expect(screen.getByText("Connector")).toBeInTheDocument();
    // The invented Transports + Scenario-packs facts are gone, and so is
    // Domain: it read a fixture lookup table, not anything the backend sends.
    expect(screen.queryByText("Transports")).toBeNull();
    expect(screen.queryByText("Scenario packs")).toBeNull();
    expect(screen.queryByText("Domain")).toBeNull();
    expect(screen.queryByText("E-commerce")).toBeNull();
    // fact values: channel from the surface, connector from agent.typeId.
    expect(screen.getByText("Voice")).toBeInTheDocument();
    expect(screen.getByText("LiveKit")).toBeInTheDocument();
  });

  // The Tools and Hard-rules cards were removed from the Overview panel as
  // duplicates of the Contract tab, so their panel-level tests went with them.
  // ToolsCard/HardRulesCard are still exercised where they render (the direct
  // HardRulesCard test below; ToolsCard on the Contract tab).

  it("renders each hard rule as text, with no invented origin chip", () => {
    render(<HardRulesCard env={MOCK_WORLD} />);

    // Every rule is listed…
    MOCK_WORLD.rules.forEach((rule) =>
      expect(screen.getByText(rule)).toBeInTheDocument(),
    );
    // …but ALK reports no per-rule origin, and the chips used to be derived by
    // position (first two CODE, last PROSE, rest PROMPT). None of them ship.
    expect(screen.queryByText("Code")).toBeNull();
    expect(screen.queryByText("Prompt")).toBeNull();
    expect(screen.queryByText("Prose")).toBeNull();
  });

  it("renders real §6 world content for a backed env: empty states + real deps", () => {
    renderPanel({
      backedWorld: {
        stores: [],
        amendments: [],
        dependencies: [
          {
            name: "postgres",
            kind: "datastore",
            what: "Stores rider rows",
            used_by: ["book_ride"],
          },
        ],
      },
    });

    // Empty §6 arrays render honest empty states, not fixture rows. The stores
    // read-out went with SourceToSandboxMap, whose origins and sandbox targets
    // were derived by position and keyword rather than reported by ALK.
    expect(screen.queryByText("Amendments")).toBeNull();
    expect(screen.queryByText(/No stores seeded/)).toBeNull();
    // "What it depends on" populates from real contract.dependencies.
    expect(screen.getByText("postgres")).toBeInTheDocument();
    expect(screen.getByText("Stores rider rows")).toBeInTheDocument();
    // Actors group is gone.
    expect(screen.queryByText("Actors")).toBeNull();
  });

  const BACKED_WORLD = { stores: [], amendments: [], dependencies: [] };

  it("shows each amendment with its reason beside the use cases", () => {
    renderPanel({
      backedWorld: {
        ...BACKED_WORLD,
        amendments: [
          { subject: "refund_window", note: "The prompt says 30 days" },
        ],
      },
    });

    expect(screen.getByText("Amendments")).toBeInTheDocument();
    expect(screen.getByText("refund_window")).toBeInTheDocument();
    expect(screen.getByText("The prompt says 30 days")).toBeInTheDocument();
  });

  it("lists six use cases and keeps the rest behind a more link", async () => {
    const useCases = Array.from({ length: 8 }, (_, i) => `Use case ${i + 1}`);
    renderPanel({
      env: { ...MOCK_WORLD, useCases },
      backedWorld: BACKED_WORLD,
    });

    useCases
      .slice(0, 6)
      .forEach((u) => expect(screen.getByText(u)).toBeInTheDocument());
    expect(screen.queryByText("Use case 7")).toBeNull();
    await userEvent.hover(screen.getByText("+2 more"));
    expect(await screen.findByText("Use case 7")).toBeInTheDocument();
    expect(screen.getByText("Use case 8")).toBeInTheDocument();
  });

  it("badges a hard rule only with the origin ALK reported for it", () => {
    render(
      <HardRulesCard
        env={{
          ...MOCK_WORLD,
          ruleDetails: [
            {
              text: MOCK_WORLD.rules[0],
              origin: {
                kind: "provider_system_prompt",
                field: "system_prompt",
              },
            },
          ],
        }}
      />,
    );

    expect(screen.getAllByText("System prompt")).toHaveLength(1);
    expect(screen.getByText("system_prompt")).toBeInTheDocument();
  });

  it("lists the services the agent calls apart from what the environment builds", () => {
    renderPanel({
      backedWorld: {
        ...BACKED_WORLD,
        runtimeDependencies: [
          {
            name: "retell",
            kind: "transport",
            what: "Retell voice platform",
            used_by: [],
          },
          {
            name: "elevenlabs",
            kind: "transport",
            what: "Voice synthesis",
            used_by: ["response_engine"],
          },
        ],
      },
    });

    expect(screen.queryByText(/No dependencies recorded/)).toBeNull();
    expect(screen.getByText("Services the agent calls")).toBeInTheDocument();
    expect(
      screen.queryByText("Built and torn down with the environment"),
    ).toBeNull();
    expect(screen.getByText("retell")).toBeInTheDocument();
    expect(screen.getByText("used by response_engine")).toBeInTheDocument();
    expect(screen.getAllByText(/^used by/)).toHaveLength(1);
  });

  it("leaves What it depends on out when both lists are empty", () => {
    renderPanel({
      backedWorld: { ...BACKED_WORLD, runtimeDependencies: [] },
    });

    expect(screen.queryByText("What it depends on")).toBeNull();
    expect(screen.queryByText(/No dependencies recorded/)).toBeNull();
  });

  it("counts each starting-data collection and shows the data on request", async () => {
    const user = userEvent.setup();
    renderPanel({
      env: {
        ...MOCK_WORLD,
        baseEnvironment: {
          patients: [{ id: "p1" }, { id: "p2" }],
          notes: "free text",
        },
      },
      backedWorld: BACKED_WORLD,
    });

    expect(screen.getAllByText("Seeded data")).toHaveLength(1);
    expect(screen.getByText("Starting data")).toBeInTheDocument();
    expect(screen.getByText("patients")).toBeInTheDocument();
    expect(
      screen.getByText("2 rows, rebuilt fresh for every task."),
    ).toBeInTheDocument();
    expect(screen.queryByText("notes")).toBeNull();
    expect(screen.queryByText("base_environment")).toBeNull();
    await user.click(
      screen.getByRole("button", { name: "Show the starting data" }),
    );
    expect(await screen.findByText("base_environment")).toBeInTheDocument();
    expect(screen.getByText(/"id": "p1"/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Hide the starting data" }),
    ).toHaveAttribute("aria-expanded", "true");
  });

  it("shows no starting data for an environment that has none", () => {
    renderPanel({ backedWorld: BACKED_WORLD });
    expect(screen.getAllByText("Seeded data")).toHaveLength(1);
    expect(
      screen.queryByRole("button", { name: "Show the starting data" }),
    ).toBeNull();
  });

  it("adds the real domain, direction, source and mode to the facts of a backed voice env", () => {
    renderPanel({
      env: {
        ...MOCK_WORLD,
        surface: "voice",
        domain: "Logistics",
        callDirection: "inbound",
        source: { kind: "github", repository: "acme/ride-agent", ref: "main" },
        agentMode: "connect_only",
      },
      backedWorld: BACKED_WORLD,
    });
    expect(screen.getByText("Logistics")).toBeInTheDocument();
    expect(screen.getByText("Inbound")).toBeInTheDocument();
    expect(screen.getByText("acme/ride-agent@main")).toBeInTheDocument();
    expect(screen.getByText("Connect only")).toBeInTheDocument();
  });

  it("leaves out direction for a chat env and empty facts entirely", () => {
    renderPanel({
      env: {
        ...MOCK_WORLD,
        surface: "chat",
        domain: null,
        callDirection: "inbound",
        source: { kind: "provider" },
        agentMode: null,
      },
      backedWorld: BACKED_WORLD,
    });
    expect(screen.queryByText("Direction")).toBeNull();
    expect(screen.queryByText("Domain")).toBeNull();
    expect(screen.queryByText("Mode")).toBeNull();
    expect(screen.getByText("Hosted platform")).toBeInTheDocument();
  });

  it("shows the seeded tables under The world for a backed env", () => {
    renderPanel({
      env: {
        ...MOCK_WORLD,
        seed: {
          tables: [
            { name: "bookings", rows: 900, note: "world_db · postgres" },
          ],
        },
      },
      backedWorld: BACKED_WORLD,
    });
    expect(screen.getByText("Seeded data")).toBeInTheDocument();
    expect(screen.getByText("bookings")).toBeInTheDocument();
    expect(screen.getByText("900")).toBeInTheDocument();
  });

  it("shows a seeded table the API sent without a row count, without breaking", () => {
    renderPanel({
      env: {
        ...MOCK_WORLD,
        seed: { tables: [{ name: "bookings", rows: 900 }, { name: "fares" }] },
      },
      backedWorld: BACKED_WORLD,
    });
    expect(screen.getByText("fares")).toBeInTheDocument();
    expect(screen.getByText("900")).toBeInTheDocument();
    // The total counts only the rows the API reported.
    expect(
      screen.getByText("900 rows, rebuilt fresh for every task."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/NaN/)).toBeNull();
  });

  it("leaves the harness's own tables out, and Seeded data with them when nothing else is seeded", () => {
    renderPanel({
      env: {
        ...MOCK_WORLD,
        seed: {
          tables: [
            { name: "_alk_tool_trace", rows: 0 },
            { name: "harness_seed_sentinel", rows: 1 },
          ],
        },
      },
      backedWorld: BACKED_WORLD,
    });
    expect(screen.queryByText("Seeded data")).toBeNull();
    expect(screen.queryByText(/No rows are seeded/)).toBeNull();
    expect(screen.queryByText("_alk_tool_trace")).toBeNull();
    expect(screen.queryByText("harness_seed_sentinel")).toBeNull();
  });

  it("puts Seeded data before What it depends on under The world", () => {
    renderPanel({
      env: {
        ...MOCK_WORLD,
        seed: { tables: [{ name: "bookings", rows: 900 }] },
      },
      backedWorld: BACKED_WORLD,
    });
    const heading = screen.getByText("The world");
    const seeded = screen.getByText("Seeded data");
    const depends = screen.getByText("What it depends on");
    expect(
      heading.compareDocumentPosition(seeded) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      seeded.compareDocumentPosition(depends) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("says there is no world to reset for a built env with no tables and no stores", () => {
    renderPanel({
      env: {
        ...MOCK_WORLD,
        detailReady: true,
        buildStatus: "ready",
        seed: undefined,
        worldTables: [],
        stores: [],
        tools: [{ name: "book_ride" }],
        toolStatus: [{ tool: "book_ride", status: "in_process" }],
      },
      backedWorld: {
        ...BACKED_WORLD,
        dependencies: [
          { name: "postgres", kind: "datastore", what: "Rows", used_by: [] },
        ],
      },
    });
    expect(screen.getByText("No world to reset")).toBeInTheDocument();
    expect(screen.queryByText(/No tables or stores are seeded/)).toBeNull();
    expect(screen.queryByText("Seeded data")).toBeNull();
    expect(screen.getByText("What it depends on")).toBeInTheDocument();
    expect(screen.getByText("postgres")).toBeInTheDocument();
  });

  it("treats a connect-only agent as having no world, whatever the harness stored", () => {
    renderPanel({
      env: {
        ...MOCK_WORLD,
        detailReady: true,
        buildStatus: "building",
        agentMode: "connect_only",
        connector: "vapi",
        source: { kind: "provider" },
        seed: undefined,
        stores: [
          {
            capability: "harness",
            tables: [
              { name: "harness_calls", rows: 0, internal: true },
              { name: "harness_turns", rows: 0, internal: true },
            ],
          },
        ],
        toolStatus: [{ tool: "book_ride", status: "in_process" }],
        testSubject: { id: "a", name: "Ride bot", active_version: "v1" },
      },
      backedWorld: BACKED_WORLD,
    });
    expect(screen.getByText("No world to reset")).toBeInTheDocument();
    expect(screen.queryByText("Simulated")).toBeNull();
    expect(screen.queryByText(/Seeded data/)).toBeNull();
    expect(screen.queryByText("Stores")).toBeNull();
    expect(
      screen.getByText(
        "· Vapi · Answered by your systems · default: production",
      ),
    ).toBeInTheDocument();
  });

  it("says nothing about the world in the agent line until the world is written", () => {
    renderPanel({
      env: {
        ...MOCK_WORLD,
        buildStatus: "building",
        source: { kind: "github", repository: "acme/ride-agent" },
        testSubject: { id: "a", name: "Ride bot", active_version: "v1" },
      },
      backedWorld: BACKED_WORLD,
      waits: { world: { phase: "Writing the world", now: true } },
    });
    expect(
      screen.getByText("· GitHub · default: production"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Simulated/)).toBeNull();
    expect(screen.getByTestId("overview-world-loading")).toBeInTheDocument();
  });

  it("shows no no-world card for a template, which has no built detail", () => {
    renderPanel({ env: { ...MOCK_WORLD, worldTables: [], stores: [] } });
    expect(screen.queryByText("No world to reset")).toBeNull();
  });

  it("says which version runs by default for a pinned agent", () => {
    renderPanel({
      env: {
        ...MOCK_WORLD,
        agentPolicy: "pinned",
        testSubject: {
          id: "a",
          name: "Ride bot",
          provider: "livekit_bridge",
          versions_count: 1,
          active_version: "v1",
        },
      },
      backedWorld: BACKED_WORLD,
    });
    expect(
      screen.getByText("· LiveKit · Simulated · default v1"),
    ).toBeInTheDocument();
  });

  it("names a code agent by where it was read from, with its commit", () => {
    renderPanel({
      env: {
        ...MOCK_WORLD,
        source: {
          kind: "github",
          repository: "acme/ride-agent",
          ref: "main",
          commit_sha: "abcdef1234567",
        },
        testSubject: { id: "a", name: "Ride bot", active_version: "v3" },
      },
      backedWorld: BACKED_WORLD,
    });
    expect(
      screen.getByText("Ride bot v3 · main @ abcdef1"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("· GitHub · Simulated · default: production"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Connector")).toBeNull();
  });

  it("reads the connector off the environment while the build has no agent state", () => {
    renderPanel({
      envState: { scenarios: [] },
      env: { ...MOCK_WORLD, connector: "vapi", source: { kind: "provider" } },
      backedWorld: BACKED_WORLD,
    });
    expect(screen.getByText("Connector")).toBeInTheDocument();
    expect(screen.getByText("Vapi")).toBeInTheDocument();
  });

  it("leaves the connector out rather than printing a dash when it is unknown", () => {
    renderPanel({
      envState: { scenarios: [] },
      env: { ...MOCK_WORLD, source: { kind: "provider" } },
      backedWorld: BACKED_WORLD,
    });
    expect(screen.queryByText("Connector")).toBeNull();
  });

  it("leaves the use cases card out when there are none", () => {
    renderPanel({
      env: { ...MOCK_WORLD, useCases: [] },
      backedWorld: BACKED_WORLD,
    });
    expect(screen.queryByText("Use cases")).toBeNull();
  });

  it("names the agent's platform and version, with a link to the agent", () => {
    renderPanel({
      envState: {
        ...connectedState,
        agent: { ...connectedState.agent, typeId: "retell" },
      },
      env: {
        ...MOCK_WORLD,
        testSubject: {
          id: "agent-1",
          name: "Single Prompt Agent",
          provider: "retell",
          versions_count: 2,
          active_version: "v2",
        },
      },
      backedWorld: BACKED_WORLD,
    });
    expect(screen.getByText("Single Prompt Agent v2")).toBeInTheDocument();
    expect(
      screen.getByText("· Retell · Simulated · default: production"),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /View agent/ })).toHaveAttribute(
      "href",
      // The Connected agent page, which every org can open; the old Agent
      // Definition page is hidden from orgs restricted to Environments.
      "/dashboard/agents/connected/agent-1",
    );
  });

  describe("Manage versions", () => {
    const versioned = {
      ...MOCK_WORLD,
      id: "env-1",
      versioning: { enabled: true },
    };
    const openMenu = () =>
      userEvent.click(
        screen.getByRole("button", { name: "More agent actions" }),
      );
    const manage = () =>
      screen.queryByRole("menuitem", { name: "Manage versions" });

    it("opens the agent versions in a drawer while versioning is on", async () => {
      const user = userEvent.setup();
      renderPanel({ env: versioned });
      expect(screen.queryByTestId("agent-versions-panel")).toBeNull();

      await user.click(
        screen.getByRole("button", { name: "More agent actions" }),
      );
      await user.click(manage());
      expect(screen.getByTestId("agent-versions-panel")).toHaveTextContent(
        "env-1",
      );
      expect(
        screen.getByRole("heading", { level: 2, name: "Test subject" }),
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          "Manage the agent versions running against this environment",
        ),
      ).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Close" }));
      await waitFor(() =>
        expect(screen.queryByTestId("agent-versions-panel")).toBeNull(),
      );
    });

    it("offers nothing while versioning is off", () => {
      renderPanel({ env: { ...versioned, versioning: { enabled: false } } });
      expect(
        screen.queryByRole("button", { name: "More agent actions" }),
      ).toBeNull();
    });

    it("keeps the prompt but not Manage versions on a locked template", async () => {
      renderPanel({
        env: { ...versioned, systemPrompt: "Be kind." },
        locked: true,
      });
      await openMenu();
      expect(
        await screen.findByRole("menuitem", { name: "View prompt" }),
      ).toBeInTheDocument();
      expect(manage()).toBeNull();
    });
  });
});
