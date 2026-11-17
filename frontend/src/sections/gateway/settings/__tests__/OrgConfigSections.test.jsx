import React, { forwardRef } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, render, screen } from "src/utils/test-utils";

import OrgConfigSection from "../OrgConfigSection";
import { orgConfigSections } from "../orgConfigSections";
import { NOT_ENFORCED_LABEL } from "../enforcement";

const grid = vi.hoisted(() => ({ props: null }));
const auth = vi.hoisted(() => ({ role: "Owner" }));
const history = vi.hoisted(() => ({ props: null }));
const gatewayConfig = vi.hoisted(() => ({ current: null }));
const navigate = vi.hoisted(() => vi.fn());
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => navigate,
}));

// The org level behind the Owner or Admin checks (useGatewayAdmin).
const LEVELS = { Owner: 15, Admin: 8, Member: 3, Viewer: 1 };
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: LEVELS[auth.role] }),
}));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid(props, _ref) {
    grid.props = props;
    return <div data-testid="ag-grid" />;
  }),
}));

const editor = vi.hoisted(() => ({ props: null }));
vi.mock("../OrgConfigEditor", () => ({
  default: (props) => {
    editor.props = props;
    return props.open ? <div>editor open at {props.defaultTab}</div> : null;
  },
}));
vi.mock("../ConfigHistoryDrawer", () => ({
  default: (props) => {
    history.props = props;
    return null;
  },
}));

const org = vi.hoisted(() => ({
  data: null,
  isLoading: false,
  error: null,
  refetch: vi.fn(),
}));
vi.mock("../../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => org,
  useCreateOrgConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useProviderHealth: () => ({ data: { providers: [{ name: "openai" }] } }),
  useGatewayConfig: () => gatewayConfig.current,
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "default" }),
}));

const DEMO = {
  version: 27,
  created_at: "2026-10-20T02:44:00Z",
  change_description: "Config update",
  guardrails: { rules: [{ name: "pii-detection" }, { name: "keyword" }] },
  routing: { strategy: "least-latency", model_fallbacks: { "gpt-4o": ["x"] } },
  cache: { enabled: true, default_ttl: 300 },
  rate_limiting: { enabled: true, per_key_rpm: 600 },
  budgets: { enabled: true, org_limit: { limit: 25 } },
  alerting: { rules: [{ name: "a" }, { name: "b" }, { name: "c" }] },
  mcp: { enabled: true, servers: { crm: {}, support: {} } },
  ip_acl: {},
  model_map: {},
  model_database: {},
  audit: {},
  a2a: {},
};

// What the gateway reports it enforces (GET /-/config org_sections_enforced).
const REPORTED = {
  isLoading: false,
  data: {
    org_sections_enforced: ["budgets", "cache", "alerting", "rate_limiting"],
    org_sections_not_enforced: {
      ip_acl: "client IP unknown (trusted_proxies not set)",
    },
  },
};

describe("orgConfigSections", () => {
  it("lists every shown section with what is saved and the tab that edits it", () => {
    const rows = orgConfigSections(DEMO, REPORTED);
    const byId = Object.fromEntries(rows.map((row) => [row.id, row]));
    expect(byId.guardrails).toMatchObject({
      saved: "2 checks",
      tab: "guardrails",
    });
    expect(byId.cache).toMatchObject({ saved: "On, TTL 300", tab: "cache" });
    expect(byId.alerting.saved).toBe("3 rules");
    expect(byId.ip_acl.saved).toBe("Not set");
    expect(byId.model_map.tab).toBe("model_map");
    rows
      .filter((row) => row.gap)
      .forEach((row) => expect(row.reasons.length, row.id).toBeGreaterThan(0));
  });

  it("has no row for a hidden tab: A2A and audit", () => {
    const ids = orgConfigSections(DEMO, REPORTED).map((row) => row.id);
    expect(ids).not.toContain("a2a");
    expect(ids).not.toContain("audit");
  });

  // The gateway connects the MCP servers an organization saves on this
  // build (shell/page-status.js no longer hides the section), so the MCP
  // section has its row again. Stored MCP guardrails retain their enforcement
  // status alongside a storage-only note, and only when some are stored.
  it("shows the MCP section's servers, and no gap while it stores no guardrails", () => {
    const byId = Object.fromEntries(
      orgConfigSections(DEMO, REPORTED).map((row) => [row.id, row]),
    );
    expect(byId.mcp).toMatchObject({ saved: "On, 2 servers", tab: "mcp" });
    expect(byId.mcp.gap).toBeNull();
    expect(byId.mcp.reasons).toEqual([]);
    expect(byId.mcp.gapIds).toEqual([]);
  });

  it("keeps the MCP enforcement status with a positive storage fact", () => {
    const stored = {
      ...DEMO,
      mcp: { ...DEMO.mcp, guardrails: { enabled: true, blocked_tools: ["x"] } },
    };
    const byId = Object.fromEntries(
      orgConfigSections(stored, REPORTED).map((row) => [row.id, row]),
    );
    expect(byId.mcp).toMatchObject({ saved: "On, 2 servers", tab: "mcp" });
    expect(byId.mcp.gap.label).toBe("Partly enforced");
    expect(byId.mcp.gapIds).toEqual(["mcp.guardrails"]);
    expect(byId.mcp.reasons).toEqual([
      "Saved MCP guardrail settings are stored with your organization's configuration.",
    ]);
  });

  it("takes each reported section's state from the gateway, with its reason", () => {
    const byId = Object.fromEntries(
      orgConfigSections(DEMO, REPORTED).map((row) => [row.id, row]),
    );
    expect(byId.budgets.gap).toBeNull();
    expect(byId.alerting.gap).toBeNull();
    expect(byId.rate_limiting.gap).toBeNull();
    expect(byId.ip_acl.gap.label).toBe(NOT_ENFORCED_LABEL);
    expect(byId.ip_acl.reasons[0]).toBe(
      "IP ACL: Not enforced on this gateway: client IP unknown (trusted_proxies not set).",
    );
    expect(byId.tool_policy.gap.label).toBe(NOT_ENFORCED_LABEL);
    expect(byId.tool_policy.reasons[0]).toMatch(
      /does not run the Tool Policy plugin/,
    );
  });

  it("says enforcement is unknown when the gateway does not report it", () => {
    const byId = Object.fromEntries(
      orgConfigSections(DEMO, { isLoading: false, data: {} }).map((row) => [
        row.id,
        row,
      ]),
    );
    expect(byId.budgets.gap.label).toBe("Enforcement unknown");
    expect(byId.budgets.reasons[0]).toMatch(/enforcement unknown/);
  });

  it("names only the parts the gateway keeps and never reads", () => {
    const byId = Object.fromEntries(
      orgConfigSections(DEMO, REPORTED).map((row) => [row.id, row]),
    );
    expect(byId.routing.gap.label).toBe("Partly enforced");
    expect(byId.cache.gap.label).toBe("Partly enforced");
    expect(byId.model_map.gap).toBeNull();
    expect(byId.model_database.gap).toBeNull();
    expect(byId.cost_tracking.gap).toBeNull();
  });
});

describe("OrgConfigSection", () => {
  beforeEach(() => {
    org.data = DEMO;
    org.error = null;
    org.isLoading = false;
    org.refetch.mockReset();
    grid.props = null;
    history.props = null;
    navigate.mockReset();
    auth.role = "Owner";
    gatewayConfig.current = REPORTED;
  });

  it.each([
    ["without cached data", undefined],
    ["with a cached config", DEMO],
  ])(
    "reports a failed read %s instead of claiming defaults",
    (_label, data) => {
      org.data = data;
      org.error = { result: "Organization config is temporarily unavailable." };
      render(<OrgConfigSection />);
      expect(
        screen.getByText("Organization config could not be loaded"),
      ).toBeInTheDocument();
      expect(screen.getByText(org.error.result)).toBeInTheDocument();
      expect(
        screen.queryByText("Using the default config"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText("Custom config active"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Create Custom Config" }),
      ).not.toBeInTheDocument();
      expect(screen.queryByTestId("ag-grid")).not.toBeInTheDocument();
      act(() => screen.getByRole("button", { name: "Retry" }).click());
      expect(org.refetch).toHaveBeenCalledTimes(1);
    },
  );

  it("uses readable fallback copy for a config read error with no message", () => {
    org.data = undefined;
    org.error = { result: { code: "unavailable" } };
    render(<OrgConfigSection />);
    expect(
      screen.getByText("Organization config could not be loaded"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Retry to check which configuration is active."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Using the default config"),
    ).not.toBeInTheDocument();
  });

  it.each([
    ["Custom config active", DEMO],
    ["Using the default config", null],
  ])("shows %s only after a successful retry", (label, data) => {
    org.data = undefined;
    org.error = new Error("Config read failed");
    const { rerender } = render(<OrgConfigSection />);
    act(() => screen.getByRole("button", { name: "Retry" }).click());
    expect(org.refetch).toHaveBeenCalledTimes(1);
    org.error = null;
    org.data = data;
    rerender(<OrgConfigSection />);
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(
      screen.queryByText("Organization config could not be loaded"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Retry" }),
    ).not.toBeInTheDocument();
  });

  it("does not claim defaults while the initial read is loading", () => {
    org.data = undefined;
    org.isLoading = true;
    render(<OrgConfigSection />);
    expect(screen.getByTestId("org-config-loading")).toBeInTheDocument();
    expect(
      screen.queryByText("Using the default config"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Create Custom Config" }),
    ).not.toBeInTheDocument();
  });

  it("shows the active config and a table of its sections", () => {
    render(<OrgConfigSection />);
    expect(screen.getByText("Custom config active")).toBeInTheDocument();
    expect(screen.getByText("Version 27")).toBeInTheDocument();
    expect(screen.getByText(/Last updated/)).toBeInTheDocument();
    expect(grid.props.rowData.map((row) => row.section)).toContain("Model map");
  });

  it("centres the On this gateway cell vertically, the dash as well as the chips", () => {
    render(<OrgConfigSection />);
    const Gap = grid.props.columnDefs.find(
      (c) => c.field === "gap",
    ).cellRenderer;
    render(<Gap value={null} />);
    const dash = screen.getByText("—");
    expect(dash.parentElement).toHaveStyle({
      display: "flex",
      alignItems: "center",
      height: "100%",
    });
  });

  it("opens the editor at a section's tab from its row", () => {
    render(<OrgConfigSection />);
    const row = grid.props.rowData.find((r) => r.id === "model_map");
    act(() => grid.props.onRowClicked({ data: row }));
    expect(screen.getByText("editor open at model_map")).toBeInTheDocument();
  });

  // The editor has no MCP tab (MCP servers and their credentials are saved
  // on the MCP tools page, never in a generic config save), so the MCP row
  // opens that page's Servers tab instead of the editor.
  it("opens the MCP tools page's Servers tab from the MCP row, not the editor", () => {
    org.data = { ...DEMO, mcp: { enabled: true, servers: { crm: {} } } };
    render(<OrgConfigSection />);
    const row = grid.props.rowData.find((r) => r.id === "mcp");
    act(() => grid.props.onRowClicked({ data: row }));
    expect(navigate).toHaveBeenCalledWith({
      pathname: "/dashboard/gateway/mcp",
      search: "?tab=servers",
    });
    expect(screen.queryByText(/editor open at/)).not.toBeInTheDocument();
  });

  it("lets an owner or admin edit the config and see its history", () => {
    auth.role = "Admin";
    render(<OrgConfigSection />);
    expect(
      screen.getByRole("button", { name: "Edit Config" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "View History" }),
    ).toBeInTheDocument();
    expect(grid.props.onRowClicked).toEqual(expect.any(Function));
  });

  // Reads stay open: a member or viewer opens the config to read it (View
  // Config, never Edit); Save & Activate and Activate are Owner or Admin
  // only.
  it.each(["Member", "Viewer"])(
    "gives a %s the config and the history to read, never an Edit",
    (role) => {
      auth.role = role;
      render(<OrgConfigSection />);
      expect(screen.getByText("Custom config active")).toBeInTheDocument();
      expect(
        screen.getByText(/only an organization Owner or Admin can change it/i),
      ).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Edit Config" })).toBeNull();
      act(() => screen.getByRole("button", { name: "View Config" }).click());
      expect(screen.getByText("editor open at providers")).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "View History" }),
      ).toBeInTheDocument();
    },
  );

  it.each(["Member", "Viewer"])(
    "shows a %s Create Custom Config disabled, with the role it needs",
    (role) => {
      auth.role = role;
      org.data = null;
      render(<OrgConfigSection />);
      const create = screen.getByRole("button", {
        name: "Create Custom Config",
      });
      expect(create).toBeDisabled();
      expect(
        create.closest('[aria-label="Owner or Admin only"]'),
      ).not.toBeNull();
    },
  );

  it("keeps the history to read for a member", () => {
    auth.role = "Member";
    render(<OrgConfigSection />);
    expect(
      screen.getByRole("button", { name: "View History" }),
    ).toBeInTheDocument();
  });

  it("offers to create a config when the org uses the defaults", () => {
    org.data = null;
    render(<OrgConfigSection />);
    expect(screen.getByText("Using the default config")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create Custom Config" }),
    ).toBeInTheDocument();
  });
});
