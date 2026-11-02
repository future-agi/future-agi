import { describe, it, expect, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { render } from "src/utils/test-utils";
import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import OverviewPanel from "../OverviewPanel";

// The drawer's real-data panel has its own tests; here it only has to mount.
vi.mock("../../agents/AgentVersionsPanel", () => ({
  default: ({ env }) => (
    <div data-testid="agent-versions-panel">{env.id}</div>
  ),
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
  it("shows the description and Vel's four facts", () => {
    renderPanel();
    expect(screen.getByText(MOCK_WORLD.description)).toBeInTheDocument();
    for (const label of ["Channel", "Domain", "Transports", "Scenario packs"])
      expect(screen.getByText(label)).toBeInTheDocument();
    // Connector moved to Settings → Build details; it reads as a transport here.
    expect(screen.queryByText("Connector")).toBeNull();
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
    // The map lists the real stores; none came back, so it says so.
    expect(screen.getByText(/No stores seeded/)).toBeInTheDocument();
    // "What it depends on" populates from real contract.dependencies.
    expect(screen.getByText("postgres")).toBeInTheDocument();
    expect(screen.getByText("Stores rider rows")).toBeInTheDocument();
    // Actors group is gone.
    expect(screen.queryByText("Actors")).toBeNull();
  });

  const BACKED_WORLD = { stores: [], amendments: [], dependencies: [] };

  it("states the real domain and the transport with its direction for a backed voice env", () => {
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
    expect(screen.getByText("LiveKit · Inbound")).toBeInTheDocument();
    // Source and mode moved to Settings → Build details.
    expect(screen.queryByText("acme/ride-agent@main")).toBeNull();
    expect(screen.queryByText("Connect only")).toBeNull();
  });

  it("leaves the direction out of a chat env's transport and dashes a missing domain", () => {
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
    expect(screen.getByText("LiveKit")).toBeInTheDocument();
    expect(screen.queryByText(/Inbound/)).toBeNull();
    expect(screen.getAllByText("-").length).toBeGreaterThan(0);
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
    expect(
      screen.getByText((_, el) => el?.tagName === "P" && /^Tests Ride bot v1 · LiveKit/.test(el.textContent)),
    ).toBeInTheDocument();
  });

  it("names the agent's provider, with a link to the agent", () => {
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
    expect(
      screen.getByText((_, el) => el?.tagName === "P" && /^Tests Single Prompt Agent v2 · Retell/.test(el.textContent)),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /View agent/ })).toHaveAttribute(
      "href",
      // The Connected agent page, which every org can open, on its
      // environments tab with this environment, as in Vel's prototype.
      expect.stringMatching(/^\/dashboard\/agents\/connected\/agent-1\?tab=environments&env=/),
    );
  });

  describe("Manage versions", () => {
    const versioned = {
      ...MOCK_WORLD,
      id: "env-1",
      versioning: { enabled: true },
    };
    const manage = () =>
      screen.queryByRole("button", { name: "Manage versions" });

    it("opens the agent versions in a drawer while versioning is on", async () => {
      const user = userEvent.setup();
      renderPanel({ env: versioned });
      expect(screen.queryByTestId("agent-versions-panel")).toBeNull();

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
      expect(manage()).toBeNull();
      renderPanel();
      expect(manage()).toBeNull();
    });

    it("offers nothing on a locked template", () => {
      renderPanel({ env: versioned, locked: true });
      expect(manage()).toBeNull();
    });
  });
});
