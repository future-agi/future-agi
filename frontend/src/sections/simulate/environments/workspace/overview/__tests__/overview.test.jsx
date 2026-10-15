import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

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
    />
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

  it("shows the authored domain and item-level origins", async () => {
    renderPanel({
      env: {
        ...MOCK_WORLD,
        domainTag: { id: "guest-booking", label: "Guest Booking" },
        ruleDetails: [
          {
            text: MOCK_WORLD.rules[0],
            origin: { kind: "provider_system_prompt", field: "system_prompt" },
          },
        ],
        useCases: ["Book a guest ride"],
        useCaseDetails: [
          {
            text: "Book a guest ride",
            origin: { kind: "provider_tool_list", field: "book_ride" },
          },
        ],
      },
    });

    expect(screen.getByText("Domain")).toBeInTheDocument();
    expect(screen.getByText("Guest Booking")).toBeInTheDocument();
    expect(screen.getByText("SYSTEM PROMPT")).toBeInTheDocument();
    expect(screen.getByText("TOOL LIST")).toBeInTheDocument();
    expect(screen.getByText("book_ride")).toBeInTheDocument();
    await userEvent.hover(screen.getByText("SYSTEM PROMPT"));
    expect(await screen.findByText("system_prompt")).toBeInTheDocument();
  });

  it("renders each hard rule as text, with no invented origin chip", () => {
    render(<HardRulesCard env={MOCK_WORLD} />);

    // Every rule is listed…
    MOCK_WORLD.rules.forEach((rule) =>
      expect(screen.getByText(rule)).toBeInTheDocument(),
    );
    // A legacy environment with no reported origins still gets no fabricated badges.
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
          { name: "postgres", kind: "datastore", what: "Stores rider rows", used_by: ["book_ride"] },
        ],
      },
    });

    // Empty §6 arrays render honest empty states, not fixture rows.
    expect(screen.getByText(/No amendments/)).toBeInTheDocument();
    expect(screen.getByText(/No stores seeded/)).toBeInTheDocument();
    // "What it depends on" populates from real contract.dependencies.
    expect(screen.getByText("postgres")).toBeInTheDocument();
    expect(screen.getByText("Stores rider rows")).toBeInTheDocument();
    // Actors group is gone.
    expect(screen.queryByText("Actors")).toBeNull();
  });

  it("lists the services the agent calls apart from what the environment builds", () => {
    renderPanel({
      backedWorld: {
        stores: [],
        amendments: [],
        dependencies: [],
        runtimeDependencies: [
          { name: "retell", kind: "transport", what: "Retell voice platform", used_by: [] },
          { name: "elevenlabs", kind: "transport", what: "Voice synthesis", used_by: ["response_engine"] },
        ],
      },
    });

    expect(screen.queryByText(/No dependencies recorded/)).toBeNull();
    expect(screen.getByText("Services the agent calls")).toBeInTheDocument();
    expect(screen.queryByText("Built and torn down with the environment")).toBeNull();
    expect(screen.getByText("retell")).toBeInTheDocument();
    expect(screen.getByText("used by response_engine")).toBeInTheDocument();
    expect(screen.getAllByText(/^used by/)).toHaveLength(1);
  });

  it("says no dependencies were recorded when both lists are empty", () => {
    renderPanel({
      backedWorld: { stores: [], amendments: [], dependencies: [], runtimeDependencies: [] },
    });

    expect(screen.getByText(/No dependencies recorded/)).toBeInTheDocument();
  });

  // The "Manage versions" agent card + its version drawer are commented out
  // (picked up later), so their tests — attach-agent disabled, open the drawer,
  // seeded-baseline copy — were removed with the feature.
});
