import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent, within } from "@testing-library/react";
import { renderWithRouter } from "src/utils/test-utils";
import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import OverviewPanel from "../OverviewPanel";

const SUBJECT = {
  id: "agent-1",
  name: "Uber agent",
  provider: "retell",
  versions_count: 1,
  active_version: "v1",
};
const BACKED_WORLD = {
  amendments: [],
  dependencies: [],
  stores: [{ capability: "db", tables: [{ name: "orders", rows: 500 }] }],
};
const ENV = {
  ...MOCK_WORLD,
  surface: "voice",
  domain: "Logistics",
  callDirection: "inbound",
  agentMode: "connect_only",
  testSubject: SUBJECT,
  description: "A returns line for a retailer.",
  // Rule text is data; the fixture's has an em dash, so use our own here.
  rules: [
    "Refunds above $200 need supervisor approval.",
    "Never confirm identity from the phone number alone.",
  ],
};
const renderPanel = (props = {}, url = "/x") =>
  renderWithRouter(
    <OverviewPanel
      env={ENV}
      envState={{ agent: { typeId: "phone" }, scenarios: [] }}
      patch={vi.fn()}
      onGo={vi.fn()}
      agentConnected
      backedWorld={BACKED_WORLD}
      {...props}
    />,
    { route: url },
  );

describe("Overview, Vel's layout", () => {
  it("states the facts as Channel, Domain, Transports and Scenario packs", () => {
    renderPanel();
    for (const label of ["Channel", "Domain", "Transports", "Scenario packs"])
      expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByText("Phone · Inbound")).toBeInTheDocument();
    expect(screen.getByText("Packs soon")).toBeInTheDocument();
    for (const moved of ["Connector", "Source", "Mode"])
      expect(screen.queryByText(moved)).toBeNull();
  });

  it("keeps the description on one line with the full text on hover", () => {
    renderPanel();
    const line = screen.getByText("A returns line for a retailer.");
    expect(line).toHaveStyle({ whiteSpace: "nowrap" });
  });

  it("reads the agent line as Tests <name> <version> · provider · Live, with View agent", () => {
    renderPanel();
    expect(
      screen.getByText(
        (_, el) =>
          el?.tagName === "P" &&
          /^Tests Uber agent v1 · Retell[^·]* · Live$/.test(el.textContent),
      ),
    ).toBeInTheDocument();
    // With versioning off there is no default agent version to state.
    expect(screen.queryByText("Default soon")).toBeNull();
    expect(screen.queryByRole("button", { name: "Manage versions" })).toBeNull();
    expect(screen.getByRole("link", { name: /View agent/ })).toHaveAttribute(
      "href",
      expect.stringContaining("/agents/connected/agent-1"),
    );
  });

  it("opens Sync agent from the agent line's menu, flagged UI only", () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Agent actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Sync agent/ }));
    expect(
      within(screen.getByRole("dialog")).getByText("UI only"),
    ).toBeInTheDocument();
  });

  it("titles the tiles Scenarios, Evaluations, Runs, Rules and keeps the latest run", () => {
    renderPanel({
      counts: { scenarios: 2, evals: 6, runs: 11, hardRules: 8 },
      latestRun: { ordinal: 11, agentVersion: "v1" },
    });
    for (const t of ["Scenarios", "Evaluations", "Runs", "Rules"])
      expect(screen.getAllByText(t).length).toBeGreaterThan(0);
    expect(screen.queryByText("Hard rules")).toBeNull();
  });

  it("maps the real tools, rules and stores with what each became in the sandbox", () => {
    renderPanel();
    expect(screen.getByText("How the world was built")).toBeInTheDocument();
    expect(screen.queryByText("Origin soon")).toBeNull();
    expect(screen.queryByText("Target soon")).toBeNull();
    expect(screen.getByText("orders · 500")).toBeInTheDocument();
    expect(screen.getByText("orders (500)")).toBeInTheDocument();
    expect(screen.getAllByText("Config").length).toBeGreaterThan(0);
    expect(screen.getAllByText("reads from fixture state").length).toBeGreaterThan(0);
    expect(screen.getAllByText("requires supervisor approval").length).toBeGreaterThan(0);
    expect(screen.queryByText("Other parties")).toBeNull();
  });

  it("fills origins and targets with ?uiSample=world, flagged", () => {
    renderPanel({}, "/x?uiSample=world");
    expect(screen.getAllByText("Config").length).toBeGreaterThan(0);
    expect(screen.getByText("Other parties")).toBeInTheDocument();
  });

  it("says how a run reaches the agent under The world", () => {
    renderPanel();
    expect(screen.getByText("The world")).toBeInTheDocument();
    expect(
      screen.getByText(
        /runs call a test copy in your account; its tools reach your real systems/,
      ),
    ).toBeInTheDocument();
  });

  it("asks to connect an agent when none is attached", () => {
    renderPanel({ env: { ...ENV, testSubject: null }, agentConnected: false });
    expect(
      screen.getByText("Connect your agent to start testing"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Connect agent" }),
    ).toBeInTheDocument();
  });

  it("shows the agent-changed banner only with its uiSample feature", () => {
    renderPanel();
    expect(screen.queryByText(/changed since/)).toBeNull();
  });

  it("uses no em dashes", () => {
    renderPanel();
    expect(document.body.textContent).not.toMatch(/—/);
  });
});
