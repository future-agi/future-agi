import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";

import { render } from "src/utils/test-utils";
import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import OverviewPanel from "../OverviewPanel";
import { HardRulesCard } from "../OverviewCards";

const connectedState = {
  agent: { via: "endpoint", typeId: "livekit", values: {} },
  agentVersions: [{ label: "v1", note: "First build." }],
  activeAgentVersion: "v1",
  scenarios: [],
};

const renderPanel = (props = {}) =>
  render(
    <OverviewPanel
      env={MOCK_WORLD}
      envState={connectedState}
      patch={vi.fn()}
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
    expect(screen.queryByText("CODE")).toBeNull();
    expect(screen.queryByText("PROMPT")).toBeNull();
    expect(screen.queryByText("PROSE")).toBeNull();
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
    expect(screen.getByText(/No amendments/)).toBeInTheDocument();
    expect(screen.queryByText(/No stores seeded/)).toBeNull();
    // "What it depends on" populates from real contract.dependencies.
    expect(screen.getByText("postgres")).toBeInTheDocument();
    expect(screen.getByText("Stores rider rows")).toBeInTheDocument();
    // Actors group is gone.
    expect(screen.queryByText("Actors")).toBeNull();
  });

  const BACKED_WORLD = { stores: [], amendments: [], dependencies: [] };

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
    expect(screen.getByText(/^900 rows fill/)).toBeInTheDocument();
    expect(screen.queryByText(/NaN/)).toBeNull();
  });

  it("leaves the harness's own tables out of Seeded data, and the card with them", () => {
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
    expect(screen.queryByText("harness_seed_sentinel")).toBeNull();
  });

  it("names an agent-definition provider the way the Agents pages do", () => {
    renderPanel({
      env: {
        ...MOCK_WORLD,
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
    expect(screen.getByText("LiveKit · 1 version")).toBeInTheDocument();
  });

  it("names the agent's provider and versions, with a link to the agent", () => {
    renderPanel({
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
    expect(screen.getByText("Retell · 2 versions")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /View agent/ })).toHaveAttribute(
      "href",
      // The Connected agent page, which every org can open; the old Agent
      // Definition page is hidden from orgs restricted to Environments.
      "/dashboard/agents/connected/agent-1",
    );
  });

  // The "Manage versions" agent card + its version drawer are commented out
  // (picked up later), so their tests — attach-agent disabled, open the drawer,
  // seeded-baseline copy — were removed with the feature.
});
