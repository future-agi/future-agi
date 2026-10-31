import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, within } from "@testing-library/react";
import { renderWithRouter } from "src/utils/test-utils";
import WorldPanel from "../WorldPanel";

const enqueueSnackbar = vi.fn();
vi.mock("notistack", async (orig) => ({
  ...(await orig()),
  enqueueSnackbar: (...a) => enqueueSnackbar(...a),
  useSnackbar: () => ({ enqueueSnackbar }),
}));

const gateway = { servers: [], isLoading: false, toolsOf: () => [] };
vi.mock("../useGatewayMcp", () => ({ default: () => gateway }));

vi.mock("../../overview/CapabilityGraph", () => ({
  default: () => <div>capability-graph</div>,
}));

// Two real tools as the contract sends them: one with an entrypoint, an enum
// argument and typed arguments; one with none.
const ENV = {
  id: "env-1",
  name: "Ride line",
  surface: "voice",
  testSubject: { name: "Ride agent", active_version: "v7" },
  tools: [
    {
      name: "geocode_address",
      desc: "Turn what the caller says into an address.",
      args: ["query", "address_kind"],
      argTypes: { query: "str", address_kind: "str" },
      argValues: { address_kind: ["pickup", "dropoff"] },
    },
    { name: "end_call", desc: "End the call.", args: [] },
  ],
  toolEntrypoints: [
    { tool: "geocode_address", module: "ride.agent", callable: "Agent.geocode" },
  ],
  seed: { tables: [{ name: "riders", rows: 40, note: "12 guests" }] },
  dataSchema: { riders: { id: "uuid", first_name: "text" } },
};

const renderPanel = (props = {}, route = "/x") =>
  renderWithRouter(
    <WorldPanel
      env={ENV}
      envState={{ agent: { name: "Ride agent" } }}
      onGo={vi.fn()}
      {...props}
    />,
    { route },
  );

beforeEach(() => {
  enqueueSnackbar.mockClear();
  gateway.servers = [];
  gateway.toolsOf = () => [];
});

describe("World tab", () => {
  it('summary reads "Agent <v> calls N tools" from the real tool count', () => {
    renderPanel();
    expect(screen.getByText("Agent v7 calls 2 tools.")).toBeInTheDocument();
    // How many the world answers isn't reported, so no fit claim.
    expect(screen.queryByText(/answers every one/)).toBeNull();
  });

  it("a tool row expands to its real arguments and types; the code block is a soon chip", () => {
    renderPanel();
    expect(screen.queryByText("Arguments")).toBeNull();
    fireEvent.click(screen.getByText("geocode_address"));
    expect(screen.getByText("Arguments")).toBeInTheDocument();
    expect(screen.getByText("query")).toBeInTheDocument();
    expect(screen.getAllByText("text").length).toBe(2);
    expect(screen.getByText("pickup")).toBeInTheDocument();
    expect(screen.getByText("dropoff")).toBeInTheDocument();
    expect(screen.getByText("Code soon")).toBeInTheDocument();
    expect(screen.queryByText(/def geocode_address/)).toBeNull();
  });

  it("keeps where a tool runs when ALK reported its entrypoint", () => {
    renderPanel();
    // The chip names the callable; the module path is in its tooltip.
    expect(screen.getByText("Agent.geocode")).toBeInTheDocument();
  });

  it("puts one Tiers soon chip on the tools card; ?uiSample=world shows Vel's dial and faults", () => {
    renderPanel();
    expect(screen.getByText("Tiers soon")).toBeInTheDocument();
    expect(screen.queryByRole("radiogroup")).toBeNull();
    expect(screen.queryByText("No faults")).toBeNull();
  });

  it("?uiSample=world shows Vel's sample tools with the dial and faults, flagged", () => {
    renderPanel({}, "/x?uiSample=world");
    expect(screen.getByText("Agent v4 calls 8 tools. The world answers every one.")).toBeInTheDocument();
    expect(screen.getAllByRole("radiogroup").length).toBe(8);
    expect(screen.getAllByText("No faults").length).toBe(8);
    expect(screen.getByText(/3 recorded · 5 simulated/)).toBeInTheDocument();
    expect(screen.getAllByLabelText(/UI only/).length).toBeGreaterThan(0);
  });

  it("marks each sample tool's default tier and locks Live for a tool that writes", () => {
    renderPanel({}, "/x?uiSample=world");
    const refund = screen.getByRole("radiogroup", { name: "Fidelity for issue_refund" });
    expect(within(refund).getByRole("radio", { name: "Live" })).toHaveAttribute("aria-disabled", "true");
    expect(within(refund).getAllByLabelText("default").length).toBe(1);
    const lookup = screen.getByRole("radiogroup", { name: "Fidelity for lookup_order" });
    expect(within(lookup).getByRole("radio", { name: "Live" })).not.toHaveAttribute("aria-disabled");
  });

  it("changing the dial in sample mode sends nothing", () => {
    renderPanel({}, "/x?uiSample=world");
    const dial = screen.getAllByRole("radiogroup")[0];
    fireEvent.click(within(dial).getByRole("radio", { name: /Fixed/ }));
    expect(within(dial).getByRole("radio", { name: /Fixed/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(enqueueSnackbar).toHaveBeenCalledWith("UI only. Nothing was saved.", {
      variant: "info",
    });
  });

  it("Add tools from an MCP server opens the drawer flagged UI only; Add N tools sends nothing", () => {
    gateway.servers = [
      { id: "shop", label: "shop", host: "mcp.shop.test", status: "connected", toolCount: 2 },
    ];
    gateway.toolsOf = () => [
      { name: "get_order", desc: "An order.", readOnly: true },
      { name: "create_refund", desc: "Refund it.", readOnly: false },
    ];
    renderPanel();
    fireEvent.click(screen.getAllByText("Add tools from an MCP server")[0]);
    const drawer = screen.getByRole("presentation");
    expect(within(drawer).getByText("From your Gateway · 1")).toBeInTheDocument();
    expect(within(drawer).getAllByLabelText(/UI only/).length).toBeGreaterThan(0);
    fireEvent.click(within(drawer).getByText("shop"));
    fireEvent.click(within(drawer).getByRole("button", { name: "Add 2 tools" }));
    expect(enqueueSnackbar).toHaveBeenCalledWith("UI only. Nothing was saved.", {
      variant: "info",
    });
  });

  it("says when the Gateway has no MCP servers instead of listing sample ones", () => {
    renderPanel();
    fireEvent.click(screen.getAllByText("Add tools from an MCP server")[0]);
    const drawer = screen.getByRole("presentation");
    expect(within(drawer).getByText("From your Gateway · 0")).toBeInTheDocument();
    expect(within(drawer).getByText(/No MCP servers in your Gateway yet/)).toBeInTheDocument();
    expect(within(drawer).queryByText("Shopify")).toBeNull();
  });

  it('0 tools shows "Tools · 0" with the Add action', () => {
    renderPanel({ env: { ...ENV, tools: [], toolEntrypoints: [] } });
    expect(screen.getByText("Tools · 0")).toBeInTheDocument();
    expect(
      screen.getByText(/Ride agent declares no tools, so the world has none to answer/),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Add tools from an MCP server").length).toBeGreaterThan(0);
  });

  it("capability graph and schema card render from the contract (moved)", () => {
    renderPanel();
    expect(screen.getByText("capability-graph")).toBeInTheDocument();
    expect(screen.getByText("World state · database schema")).toBeInTheDocument();
    expect(screen.getByText("1 tables · 40 rows")).toBeInTheDocument();
    fireEvent.click(screen.getByText("riders"));
    expect(screen.getByText("first_name")).toBeInTheDocument();
  });

  it("folds Scenario checks with a soon chip; the sample lists Vel's eight", () => {
    renderPanel();
    expect(screen.getByText("Scenario checks")).toBeInTheDocument();
    expect(screen.getByText("Checks soon")).toBeInTheDocument();
    expect(screen.queryByText("reference_solution")).toBeNull();
  });

  it("the sample's Scenario checks open to Vel's eight", () => {
    renderPanel({}, "/x?uiSample=world");
    fireEvent.click(screen.getByText("Scenario checks · 8"));
    expect(screen.getByText("reference_solution")).toBeInTheDocument();
    expect(screen.getByText("exploit_control")).toBeInTheDocument();
  });

  it("uses no em dashes, real or sample", () => {
    const { unmount } = renderPanel();
    fireEvent.click(screen.getByText("geocode_address"));
    expect(document.body.textContent).not.toMatch(/—/);
    unmount();
    renderPanel({}, "/x?uiSample=world");
    fireEvent.click(screen.getByText("Scenario checks · 8"));
    fireEvent.click(screen.getByText("lookup_order"));
    expect(document.body.textContent).not.toMatch(/—/);
  });
});
