import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { render } from "src/utils/test-utils";
import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import OverviewPanel from "../OverviewPanel";
import { HardRulesCard } from "../OverviewCards";

const connectedState = {
  agent: { via: "endpoint", values: {} },
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
  it("shows the description and the four facts", () => {
    renderPanel();

    expect(screen.getByText(MOCK_WORLD.description)).toBeInTheDocument();
    expect(screen.getByText("Channel")).toBeInTheDocument();
    expect(screen.getByText("Domain")).toBeInTheDocument();
    expect(screen.getByText("Transports")).toBeInTheDocument();
    expect(screen.getByText("Scenario packs")).toBeInTheDocument();
    // fact values read from the surface + domain fixtures
    expect(screen.getByText("Voice")).toBeInTheDocument();
    expect(screen.getByText("E-commerce")).toBeInTheDocument();
  });

  it("renders the tools list with monospace names and an arguments column", () => {
    renderPanel();

    // the tool name also appears in the capability graph; the description and
    // the arguments column are unique to the Tools card.
    expect(screen.getAllByText("verify_identity").length).toBeGreaterThan(0);
    expect(screen.getByText("Match a caller to an account before touching it.")).toBeInTheDocument();
    expect(screen.getByText("phone, postcode")).toBeInTheDocument();
    expect(screen.getByText("no arguments")).toBeInTheDocument();
  });

  it("shows 'No tools yet' when the agent is not connected", () => {
    renderPanel({ agentConnected: false });

    expect(screen.getByText("No tools yet")).toBeInTheDocument();
    // the tool inventory row (its description) is gone, even though the graph
    // still names the tools.
    expect(
      screen.queryByText("Match a caller to an account before touching it.")
    ).not.toBeInTheDocument();
  });

  it("renders each hard rule with its provenance origin chip", () => {
    // Scoped to the card: SourceToSandboxMap re-lists the same rules on the
    // panel with mapped origins (code→POLICY.YAML), so a panel-wide count would
    // double these. This test is about HardRulesCard's own chips.
    render(<HardRulesCard env={MOCK_WORLD} />);

    // MOCK_WORLD's five rules map to CODE, CODE, PROMPT, PROMPT, PROSE
    expect(screen.getAllByText("CODE")).toHaveLength(2);
    expect(screen.getAllByText("PROMPT")).toHaveLength(2);
    expect(screen.getByText("PROSE")).toBeInTheDocument();
  });

  it("lists the seeded tables with their row counts", () => {
    renderPanel();

    expect(screen.getByText("customers")).toBeInTheDocument();
    expect(screen.getByText("240")).toBeInTheDocument();
    expect(screen.getByText("orders")).toBeInTheDocument();
    expect(screen.getByText("610")).toBeInTheDocument();
  });

  it("keeps 'Attach agent' disabled behind the coming-soon tooltip when no agent is attached", () => {
    renderPanel({
      envState: { ...connectedState, agent: null },
      agentConnected: false,
    });

    expect(screen.getByRole("button", { name: /attach agent/i })).toBeDisabled();
  });

  it("opens the version-management drawer from 'Manage versions' when unlocked", async () => {
    const user = userEvent.setup();
    renderPanel();

    const button = screen.getByRole("button", { name: /manage versions/i });
    expect(button).toBeEnabled();

    await user.click(button);
    expect(await screen.findByText("Version history")).toBeInTheDocument();
  });

  it("shows the seeded-baseline copy and 'Fork to edit' when locked", async () => {
    const user = userEvent.setup();
    const onFork = vi.fn();
    renderPanel({ locked: true, onFork });

    expect(
      screen.getByText(/Seeded baseline shipped with this template/)
    ).toBeInTheDocument();

    const fork = screen.getByRole("button", { name: /fork to edit/i });
    await user.click(fork);
    expect(onFork).toHaveBeenCalledTimes(1);
  });
});
