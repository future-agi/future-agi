import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import MCPManagementSection from "./MCPManagementSection";
import { ALL_MCP_TABS, MCP_TABS, shownMCPTabs } from "./tabs";
import { HIDDEN_MCP_TABS } from "../shell/page-status";
import OverviewTab from "./overview/OverviewTab";
import ServersTab from "./servers/ServersTab";
import RegistryTab from "./registry/RegistryTab";
import ConnectTab from "./connect/ConnectTab";
import VirtualServersTab from "./virtual/VirtualServersTab";
import GuardrailsTab from "./guardrails/GuardrailsTab";
import TrustTab from "./trust/TrustTab";
import ApprovalsTab from "./approvals/ApprovalsTab";
import ToolCallsTab from "./calls/ToolCallsTab";
import CredentialsTab from "./credentials/CredentialsTab";
import PlaygroundTab from "./playground/PlaygroundTab";
import ResourcesTab from "./resources/ResourcesTab";
import PromptsTab from "./prompts/PromptsTab";
import MCPClientsTab from "./clients/MCPClientsTab";

const { statusQuery, configQuery, patchMutate, reloadMutate } = vi.hoisted(
  () => ({
    statusQuery: { current: {} },
    configQuery: { current: {} },
    patchMutate: vi.fn(),
    reloadMutate: vi.fn(),
  }),
);

// Turning MCP on writes the org's gateway config: Owner or Admin only
// (useGatewayAdmin). These cases act as an Admin unless they set a level.
const { org } = vi.hoisted(() => ({ org: { level: 8 } }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: org.level }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1", isLoading: false }),
}));
vi.mock("../providers/hooks/useGatewayConfig", async (importOriginal) => ({
  ...(await importOriginal()),
  useReloadConfig: () => ({ mutate: reloadMutate, isPending: false }),
  useGatewayConfig: () => configQuery.current,
}));
const snackbar = vi.hoisted(() => vi.fn());
vi.mock("notistack", () => ({ enqueueSnackbar: snackbar }));
vi.mock("./hooks/useMCPConfig", () => ({
  useMCPStatus: () => statusQuery.current,
}));
vi.mock("./hooks/useMCPConfigPatch", () => ({
  useMCPConfigPatch: () => ({ mutate: patchMutate, isPending: false }),
}));

// The organization stores MCP guardrails, so the Guardrails tab is in the
// bar (tabs.js shownMCPTabs); "the Guardrails tab" below covers an org that
// stores none.
const ORG_MCP = {
  enabled: true,
  servers: { "crm-tools": { url: "http://x/mcp" } },
  guardrails: { enabled: true, blocked_tools: ["shell"] },
};
// While the org's MCP is off, every tab says what adding does, under the
// banner and its Turn on MCP.
const ORG_OFF_LINE =
  "MCP is off for this organization. What you add here is saved and connects when you turn MCP on.";
// The text a tab's aria-describedby points at.
const descriptionOf = (el) =>
  document.getElementById(el.getAttribute("aria-describedby"))?.textContent;
const ORG_MCP_NO_GUARDRAILS = {
  enabled: true,
  servers: { "crm-tools": { url: "http://x/mcp" } },
};

// The tabs that show data are stubbed: this file tests the shell, and each
// tab's own tests cover its content. The placeholder tabs render for real,
// wrapped in a spy, so a test sees which module rendered the page.
const { stubTab, spyOnDefault } = vi.hoisted(() => ({
  stubTab: (name) => () => ({
    default: vi.fn(() => <div>{`${name} tab`}</div>),
  }),
  spyOnDefault: async (importOriginal) => {
    const actual = await importOriginal();
    return { ...actual, default: vi.fn(actual.default) };
  },
}));
// Shell-only fixture; the real drawer and exit transition have dedicated specs.
vi.mock("./calls/SiemExportDrawer", () => ({ default: () => null }));
vi.mock("./overview/OverviewTab", stubTab("overview"));
vi.mock("./servers/ServersTab", stubTab("servers"));
vi.mock("./guardrails/GuardrailsTab", stubTab("guardrails"));
vi.mock("./playground/PlaygroundTab", stubTab("playground"));
vi.mock("./resources/ResourcesTab", stubTab("resources"));
vi.mock("./prompts/PromptsTab", stubTab("prompts"));
vi.mock("./registry/RegistryTab", spyOnDefault);
vi.mock("./connect/ConnectTab", spyOnDefault);
vi.mock("./virtual/VirtualServersTab", stubTab("virtual"));
vi.mock("./trust/TrustTab", spyOnDefault);
// Approvals stays visible. It is a data tab now, not a
// placeholder, so it is stubbed like the other data tabs.
vi.mock("./approvals/ApprovalsTab", stubTab("approvals"));
vi.mock("./calls/ToolCallsTab", stubTab("calls"));
vi.mock("./credentials/CredentialsTab", stubTab("credentials"));
vi.mock("./clients/MCPClientsTab", stubTab("clients"));

// The Connect row moved to the test
// below: Connect is no longer dropped when client installs is off.
it.each([["clientOAuth", "clients"]])(
  "selects %s tabs from capabilities alone, without an org-config query",
  (feature, slug) => {
    const shown = shownMCPTabs({ [feature]: true }).map((tab) => tab.slug);
    expect(shown).toContain(slug);
    expect(shown).toContain("guardrails");
    // Without the capability the tab
    // stays, disabled with the reason (was: not in the list).
    expect(shownMCPTabs().find((tab) => tab.slug === slug)).toMatchObject({
      disabled: true,
      offNotice: "Not available in this deployment",
    });
  },
);

// Outside the MCP-off bar, Tool calls
// and MCP clients are always in the bar: disabled with the deployment's line
// until their own switch is affirmatively on, then enabled as before.
it.each([
  ["toolCallExplorer", "calls", "Tool calls"],
  ["clientOAuth", "clients", "MCP clients"],
])(
  "keeps the %s tab, disabled with the reason, until its switch is on",
  (feature, slug, label) => {
    const find = (flags) =>
      shownMCPTabs(flags).find((tab) => tab.slug === slug);
    const off = {
      slug,
      label,
      disabled: true,
      offNotice: "Not available in this deployment",
    };
    expect(find()).toMatchObject(off);
    for (const value of [false, undefined, null, "true", 1])
      expect(find({ [feature]: value })).toMatchObject(off);
    const on = find({ [feature]: true });
    expect(on).toMatchObject({ slug, label });
    expect(on.disabled).toBeUndefined();
    expect(on.offNotice).toBeUndefined();
  },
);

// With MCP on and the explorer, client
// OAuth and client installs off, every tab keeps its place in the bar.
it("keeps the MCP-on bar order with Tool calls and MCP clients off", () => {
  const tabs = shownMCPTabs({ credentialVault: true, virtualServers: true });
  expect(tabs.map((tab) => tab.label)).toEqual([
    "Overview",
    "Servers",
    "Virtual servers",
    "Tool calls",
    "Credentials",
    "MCP clients",
    "Guardrails",
    "Try a tool",
    "Resources",
    "Prompts",
    "Connect",
    "Approvals",
  ]);
  expect(tabs.filter((tab) => tab.offNotice).map((tab) => tab.slug)).toEqual([
    "calls",
    "clients",
    "connect",
  ]);
});

// The MCP-off bar (MCP_OFF_TABS) is unchanged: no Tool
// calls, and Manage MCP clients only when client OAuth is on.
it("leaves the MCP-off bar unchanged", () => {
  const offSlugs = [
    "overview",
    "tools",
    "servers",
    "resources",
    "prompts",
    "guardrails",
    "playground",
  ];
  const slugs = (flags) =>
    shownMCPTabs({ ...flags, runtimeOff: true }).map((tab) => tab.slug);
  expect(slugs()).toEqual(offSlugs);
  expect(slugs({ toolCallExplorer: true })).toEqual(offSlugs);
  const withClients = shownMCPTabs({ clientOAuth: true, runtimeOff: true });
  expect(withClients.map((tab) => tab.slug)).toEqual([...offSlugs, "clients"]);
  expect(withClients.at(-1)).toMatchObject({ label: "Manage MCP clients" });
  expect(withClients.at(-1).disabled).toBeUndefined();
  expect(withClients.some((tab) => tab.offNotice)).toBe(false);
});

// Outside the MCP-off bar, Connect is
// always in the bar: disabled with the deployment's line until client
// installs and virtual servers are both on, then enabled as before.
it("keeps Connect, disabled with the reason, until client installs and virtual servers are on", () => {
  const connect = (flags) =>
    shownMCPTabs(flags).find((tab) => tab.slug === "connect");
  const off = {
    disabled: true,
    offNotice: "Not available in this deployment",
  };
  expect(connect()).toMatchObject(off);
  expect(connect({ clientInstalls: true })).toMatchObject(off);
  expect(connect({ virtualServers: true })).toMatchObject(off);
  expect(
    connect({
      clientInstalls: true,
      virtualServers: true,
      virtualServersOff: true,
    }),
  ).toMatchObject(off);
  const shown = shownMCPTabs({ clientInstalls: true, virtualServers: true });
  const on = shown.find((tab) => tab.slug === "connect");
  expect(on).toMatchObject({ slug: "connect", label: "Connect" });
  expect(on.disabled).toBeUndefined();
  expect(on.offNotice).toBeUndefined();
  expect(shown.map((tab) => tab.slug)).toContain("guardrails");
  // The MCP-off bar (MCP_OFF_TABS) is unchanged: no Connect there.
  expect(
    connect({ clientInstalls: true, virtualServers: true, runtimeOff: true }),
  ).toBeUndefined();
});

const IN_DEVELOPMENT = /in development/i;
const PLACEHOLDER = "Not available on this gateway";

// Each tab's slug and the module the shell must take it from: the only
// modules the shell may import a tab from.
const FIXED_TAB_MODULES = [
  ["overview", OverviewTab],
  ["servers", ServersTab],
  ["registry", RegistryTab],
  ["connect", ConnectTab],
  ["virtual", VirtualServersTab],
  ["guardrails", GuardrailsTab],
  ["trust", TrustTab],
  ["approvals", ApprovalsTab],
  ["calls", ToolCallsTab],
  ["credentials", CredentialsTab],
  ["clients", MCPClientsTab],
  ["playground", PlaygroundTab],
  ["resources", ResourcesTab],
  ["prompts", PromptsTab],
];
// The tabs this gateway and API do not serve. They are hidden
// (shell/page-status.js): no tab in the bar, and a link to one opens the
// overview. The change that makes one work deletes it from HIDDEN_MCP_TABS
// and from this list.
// Approvals stays visible, so it is no longer listed here.
const HIDDEN_TABS = [
  ["registry", RegistryTab],
  ["trust", TrustTab],
];

const testTheme = createTheme({ palette: palette("light"), typography });

let currentLocation;
const LocationProbe = () => {
  currentLocation = useLocation();
  return null;
};

function renderShell(url) {
  return render(
    <HelmetProvider>
      <ThemeProvider theme={testTheme}>
        <MemoryRouter initialEntries={[url]}>
          <Routes>
            <Route
              path="/dashboard/gateway/mcp"
              element={<MCPManagementSection />}
            />
            <Route
              path="/dashboard/gateway/mcp/:tab"
              element={<MCPManagementSection />}
            />
          </Routes>
          <LocationProbe />
        </MemoryRouter>
      </ThemeProvider>
    </HelmetProvider>,
  );
}

describe("MCPManagementSection tab shell", () => {
  beforeEach(() => {
    org.level = 8;
    // Every capability-gated tab: client OAuth (MCP clients) and client
    // installs (Connect), each shown only on an explicit true.
    statusQuery.current = {
      data: {
        enabled: true,
        features: {
          catalog: true,
          credential_vault: true,
          virtual_servers: true,
          client_oauth: true,
          client_installs: true,
          tool_call_explorer: true,
        },
      },
      isError: false,
    };
    configQuery.current = { data: { mcp: ORG_MCP } };
    vi.clearAllMocks();
  });

  it.each([
    [15, true],
    [8, true],
    [3, false],
    [1, false],
    [null, false],
  ])("reload respects org level %s (allowed=%s)", (level, allowed) => {
    org.level = level;
    renderShell("/dashboard/gateway/mcp");
    const button = screen.getByRole("button", { name: "Reload config" });
    expect(button).toHaveProperty("disabled", !allowed);
    fireEvent.click(button);
    if (allowed)
      expect(reloadMutate).toHaveBeenCalledWith("gw-1", expect.any(Object));
    else expect(reloadMutate).not.toHaveBeenCalled();
  });

  it("keeps client revocation reachable without turning MCP on", () => {
    statusQuery.current = {
      data: {
        enabled: false,
        reason: "gateway_mcp_disabled",
        features: {
          catalog: true,
          credential_vault: true,
          virtual_servers: true,
          client_oauth: true,
        },
      },
      isError: false,
    };
    renderShell("/dashboard/gateway/mcp");
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Manage MCP clients" }));
    expect(screen.getByText("clients tab")).toBeInTheDocument();
    expect(currentLocation.search).toBe("?tab=clients");
    expect(patchMutate).not.toHaveBeenCalled();
  });

  it("keeps the MCP clients tab reachable when MCP is off for the organization", () => {
    statusQuery.current = {
      data: {
        enabled: false,
        features: {
          catalog: true,
          credential_vault: true,
          virtual_servers: true,
          client_oauth: true,
        },
      },
      isError: false,
    };
    configQuery.current = { data: { mcp: { ...ORG_MCP, enabled: false } } };
    renderShell("/dashboard/gateway/mcp");
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "MCP clients" }));
    expect(screen.getByText("clients tab")).toBeInTheDocument();
    expect(currentLocation.search).toBe("?tab=clients");
    expect(patchMutate).not.toHaveBeenCalled();
  });

  it("takes every tab from its fixed module path", () => {
    expect(ALL_MCP_TABS.map(({ slug, Content }) => [slug, Content])).toEqual(
      expect.arrayContaining(FIXED_TAB_MODULES),
    );
    expect(ALL_MCP_TABS).toHaveLength(FIXED_TAB_MODULES.length);
  });

  it("shows the available tabs and retains disabled Resources and Prompts", () => {
    expect(HIDDEN_TABS.map(([slug]) => slug).sort()).toEqual(
      Object.keys(HIDDEN_MCP_TABS).sort(),
    );
    renderShell("/dashboard/gateway/mcp");

    const labels = screen.getAllByRole("tab").map((tab) => tab.textContent);
    expect(labels).toEqual(MCP_TABS.map((tab) => tab.label));
    expect(labels).toEqual([
      "Overview",
      "Servers",
      "Virtual servers",
      "Tool calls",
      "Credentials",
      "MCP clients",
      "Guardrails",
      // It runs one MCP tool; the app's one playground is in Prompts, and the
      // server drawer's button already says "Try a tool".
      "Try a tool",
      "Resources",
      "Prompts",
      "Connect",
      // Approvals stays visible.
      "Approvals",
    ]);
  });

  it.each(HIDDEN_TABS)(
    "opens the overview for a link to the hidden %s tab",
    (slug, Module) => {
      renderShell(`/dashboard/gateway/mcp?tab=${slug}&x=1`);

      expect(screen.getByText("overview tab")).toBeInTheDocument();
      expect(screen.queryByText(PLACEHOLDER)).not.toBeInTheDocument();
      expect(screen.queryByText(/coming|in this build/i)).toBeNull();
      expect(Module).not.toHaveBeenCalled();
      expect(currentLocation.search).toBe("?x=1");
    },
  );

  // Approvals stays visible, so its link opens the Approvals
  // tab instead of redirecting to the overview.
  it("opens the Approvals tab for a link to it", () => {
    renderShell("/dashboard/gateway/mcp?tab=approvals&x=1");

    expect(
      screen.getByRole("tab", { name: "Approvals", selected: true }),
    ).toBeInTheDocument();
    expect(screen.getByText("approvals tab")).toBeInTheDocument();
    expect(ApprovalsTab).toHaveBeenCalledWith(
      { gatewayId: "gw-1" },
      expect.anything(),
    );
    expect(OverviewTab).not.toHaveBeenCalled();
    expect(currentLocation.search).toBe("?tab=approvals&x=1");
  });

  it("opens the client setup tab without activating MCP or creating a key", () => {
    renderShell("/dashboard/gateway/mcp?tab=connect");
    expect(
      screen.getByText("Create a virtual server first"),
    ).toBeInTheDocument();
    expect(ConnectTab).toHaveBeenCalledWith(
      { gatewayId: "gw-1" },
      expect.anything(),
    );
    expect(patchMutate).not.toHaveBeenCalled();
  });

  it("passes the gateway id to the tab it renders", () => {
    renderShell("/dashboard/gateway/mcp?tab=guardrails");

    expect(screen.getByText("guardrails tab")).toBeInTheDocument();
    expect(GuardrailsTab).toHaveBeenCalledWith(
      { gatewayId: "gw-1" },
      expect.anything(),
    );
  });

  it("opens the overview without a tab parameter", () => {
    renderShell("/dashboard/gateway/mcp");

    expect(screen.getByText("overview tab")).toBeInTheDocument();
  });

  it("keeps the tab in the query string when the user switches tabs", () => {
    renderShell("/dashboard/gateway/mcp");

    fireEvent.click(screen.getByRole("tab", { name: "Guardrails" }));

    expect(currentLocation.pathname).toBe("/dashboard/gateway/mcp");
    expect(new URLSearchParams(currentLocation.search).get("tab")).toBe(
      "guardrails",
    );
    expect(screen.getByText("guardrails tab")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Overview" }));

    expect(currentLocation.search).toBe("");
    expect(screen.getByText("overview tab")).toBeInTheDocument();
  });

  it("still opens links that carry the tab in the path", () => {
    renderShell("/dashboard/gateway/mcp/guardrails");

    expect(screen.getByText("guardrails tab")).toBeInTheDocument();
  });

  it("opens the servers tab for the old tools link", () => {
    renderShell("/dashboard/gateway/mcp/tools");

    expect(screen.getByText("servers tab")).toBeInTheDocument();
  });

  it("falls back to the overview for an unknown tab", () => {
    renderShell("/dashboard/gateway/mcp?tab=nope");

    expect(screen.getByText("overview tab")).toBeInTheDocument();
  });
});

// 850: a shipped tab stays reachable even without stored configuration.
// Deployment-wide MCP-off handling is covered by MCPOffState.test.jsx.
describe("the Guardrails tab", () => {
  beforeEach(() => {
    org.level = 8;
    // Every capability-gated tab: client OAuth (MCP clients) and client
    // installs (Connect), each shown only on an explicit true.
    statusQuery.current = {
      data: {
        enabled: true,
        features: {
          catalog: true,
          credential_vault: true,
          virtual_servers: true,
          client_oauth: true,
          client_installs: true,
          tool_call_explorer: true,
        },
      },
      isError: false,
    };
    vi.clearAllMocks();
  });

  it("850: stays in the bar when the organization stores no MCP guardrails", () => {
    configQuery.current = { data: { mcp: ORG_MCP_NO_GUARDRAILS } };
    renderShell("/dashboard/gateway/mcp");
    const labels = screen.getAllByRole("tab").map((tab) => tab.textContent);
    expect(labels).toContain("Guardrails");
    expect(labels).toEqual([
      "Overview",
      "Servers",
      "Virtual servers",
      "Tool calls",
      "Credentials",
      "MCP clients",
      "Guardrails",
      "Try a tool",
      "Resources",
      "Prompts",
      "Connect",
      // Approvals stays visible.
      "Approvals",
    ]);
  });

  it.each([
    ["no mcp section", {}],
    [
      "an empty guardrails object",
      { mcp: { ...ORG_MCP_NO_GUARDRAILS, guardrails: {} } },
    ],
  ])("850: stays in the bar with %s", (_, data) => {
    configQuery.current = { data };
    renderShell("/dashboard/gateway/mcp");
    expect(screen.getByRole("tab", { name: "Guardrails" })).toBeEnabled();
  });

  it.each([
    "/dashboard/gateway/mcp?tab=guardrails&x=1",
    "/dashboard/gateway/mcp/guardrails?x=1",
  ])("850: preserves an empty Guardrails deep link: %s", (url) => {
    configQuery.current = { data: { mcp: ORG_MCP_NO_GUARDRAILS } };
    renderShell(url);
    expect(screen.getByText("guardrails tab")).toBeInTheDocument();
    expect(OverviewTab).not.toHaveBeenCalled();
    expect(currentLocation.pathname + currentLocation.search).toBe(url);
  });

  it("is in the bar while stored guardrails are off", () => {
    configQuery.current = {
      data: {
        mcp: { ...ORG_MCP_NO_GUARDRAILS, guardrails: { enabled: false } },
      },
    };
    renderShell("/dashboard/gateway/mcp?tab=guardrails");
    expect(screen.getByRole("tab", { name: "Guardrails" })).toBeInTheDocument();
    expect(screen.getByText("guardrails tab")).toBeInTheDocument();
  });

  it.each([
    ["loading", { data: undefined, isLoading: true, isError: false }],
    ["failed", { data: undefined, isLoading: false, isError: true }],
  ])(
    "stays, and its link opens it, while the config read is %s",
    (_, query) => {
      configQuery.current = query;
      renderShell("/dashboard/gateway/mcp?tab=guardrails");
      expect(
        screen.getByRole("tab", { name: "Guardrails" }),
      ).toBeInTheDocument();
      expect(screen.getByText("guardrails tab")).toBeInTheDocument();
      expect(new URLSearchParams(currentLocation.search).get("tab")).toBe(
        "guardrails",
      );
    },
  );
});

describe("MCP status", () => {
  beforeEach(() => {
    org.level = 8;
    vi.clearAllMocks();
    configQuery.current = { data: { mcp: ORG_MCP } };
  });

  it("puts the page in the FA frame with an 18 px title", () => {
    statusQuery.current = { data: { enabled: true }, isError: false };
    renderShell("/dashboard/gateway/mcp");
    expect(
      screen.getByRole("heading", { level: 1, name: "MCP tools" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Reload config" }),
    ).toBeInTheDocument();
  });

  it("says MCP on only when the API says enabled: true", () => {
    statusQuery.current = { data: { enabled: true }, isError: false };
    renderShell("/dashboard/gateway/mcp?tab=registry");

    expect(screen.getByText("MCP on")).toBeInTheDocument();
    expect(screen.queryByText(IN_DEVELOPMENT)).toBeNull();
  });

  it.each([
    ["missing", {}],
    ["false", { enabled: false }],
    ["a string", { enabled: "true" }],
    ["null", { enabled: null }],
  ])("does not say on when enabled is missing or false (%s)", (_, data) => {
    statusQuery.current = { data, isError: false };
    renderShell("/dashboard/gateway/mcp?tab=registry");

    expect(screen.queryByText("MCP on")).toBeNull();
    expect(screen.queryByText(IN_DEVELOPMENT)).toBeNull();
    expect(screen.getByText(/did not report MCP as on/)).toBeInTheDocument();
  });

  // MCP-off restores the baseline shell beneath the off-state banner;
  // every other reason remains independently named in the banner.
  it.each([
    ["gateway_unreachable", /gateway could not be reached/],
    ["something_new", /did not report MCP as on/],
  ])("shows the API's reason %s", (reason, description) => {
    statusQuery.current = {
      data: { enabled: false, reason },
      isError: false,
    };
    renderShell("/dashboard/gateway/mcp?tab=registry");

    expect(screen.queryByText(IN_DEVELOPMENT)).toBeNull();
    expect(screen.getByText(reason)).toBeInTheDocument();
    expect(screen.getByText(description)).toBeInTheDocument();
  });

  it("says the status is unknown when the status request fails", () => {
    statusQuery.current = { data: undefined, isError: true };
    renderShell("/dashboard/gateway/mcp?tab=registry");

    expect(
      screen.getByText(/MCP status could not be loaded/),
    ).toBeInTheDocument();
    expect(screen.queryByText("MCP on")).toBeNull();
  });

  it("shows no verdict while the status is loading", () => {
    statusQuery.current = { data: undefined, isError: false };
    renderShell("/dashboard/gateway/mcp?tab=registry");

    expect(screen.queryByText("MCP on")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(
      screen.getByText("The Resources and Prompts tabs are disabled."),
    ).toBeInTheDocument();
  });

  describe.each([
    ["no MCP section", {}],
    ["an empty MCP section", { mcp: {} }],
    ["no servers", { mcp: { enabled: false, servers: {} } }],
    ["saved servers", { mcp: { ...ORG_MCP, enabled: false } }],
  ])("org-level opt-in with %s", (_, data) => {
    beforeEach(() => {
      statusQuery.current = { data: { enabled: false }, isError: false };
      configQuery.current = { data };
    });

    it.each([
      ["an Owner", 15],
      ["an Admin", 8],
    ])("lets %s turn MCP on explicitly", (_, level) => {
      org.level = level;
      renderShell("/dashboard/gateway/mcp");

      expect(
        screen.getByText("MCP is off for this organization"),
      ).toBeInTheDocument();
      expect(screen.queryByText("No servers yet")).toBeNull();
      expect(patchMutate).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "Turn on MCP" }));
      expect(patchMutate).toHaveBeenCalledTimes(1);
      expect(patchMutate).toHaveBeenCalledWith(
        { enabled: true },
        expect.anything(),
      );
    });

    it("says on every tab that what is added is saved and connects when MCP is on", () => {
      org.level = 8;
      const slugs = shownMCPTabs()
        .filter((tab) => !tab.disabled)
        .map((tab) => tab.slug);
      expect(slugs).toEqual(
        expect.arrayContaining(["overview", "servers", "playground"]),
      );
      for (const slug of slugs) {
        const { unmount } = renderShell(`/dashboard/gateway/mcp?tab=${slug}`);
        expect(screen.getAllByText(ORG_OFF_LINE)).toHaveLength(1);
        unmount();
      }
    });

    it("keeps the banner's Turn on MCP the first action; the line offers none", () => {
      org.level = 8;
      renderShell("/dashboard/gateway/mcp?tab=servers");
      const line = screen.getByText(ORG_OFF_LINE);
      expect(within(line).queryByRole("button")).toBeNull();
      const turnOn = screen.getAllByRole("button", { name: "Turn on MCP" });
      expect(turnOn).toHaveLength(1);
      expect(
        turnOn[0].compareDocumentPosition(line) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    });

    // The gateway lists and serves the org's tools only while MCP is on, so
    // Try a tool has nothing to run; Test connection works and is unchanged.
    it("keeps Try a tool in the bar, disabled, saying to turn on MCP to test", () => {
      org.level = 8;
      renderShell("/dashboard/gateway/mcp?tab=servers");
      const tryTab = screen.getByRole("tab", { name: "Try a tool" });
      expect(tryTab).toHaveAttribute("aria-disabled", "true");
      expect(descriptionOf(tryTab)).toBe("Turn on MCP to test");
      const calls = screen.getByRole("tab", { name: "Tool calls" });
      expect(descriptionOf(calls)).toBe("Not available in this deployment");
    });

    it("opens a Try a tool link with the reason and no tool runner", () => {
      org.level = 8;
      renderShell("/dashboard/gateway/mcp?tab=playground");
      expect(screen.getByTestId("mcp-tab-off-notice")).toHaveTextContent(
        "Turn on MCP to test",
      );
      expect(screen.queryByText("playground tab")).toBeNull();
      expect(screen.getByText(ORG_OFF_LINE)).toBeInTheDocument();
      const calls = screen.getByRole("tab", { name: "Tool calls" });
      expect(descriptionOf(calls)).toBe("Not available in this deployment");
    });

    it.each([
      ["a Member", 3],
      ["a Viewer", 1],
    ])("keeps %s read-only with the reason", (_, level) => {
      org.level = level;
      renderShell("/dashboard/gateway/mcp");

      expect(
        screen.getByText("MCP is off for this organization"),
      ).toBeInTheDocument();
      expect(screen.getByText("Owner or Admin only")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Turn on MCP" })).toBeNull();
      expect(patchMutate).not.toHaveBeenCalled();
    });
  });

  it.each([
    ["MCP is on", { enabled: true }, { mcp: ORG_MCP }],
    [
      "MCP is off on the gateway",
      { enabled: false, reason: "gateway_mcp_disabled" },
      { mcp: { ...ORG_MCP, enabled: false } },
    ],
    [
      "the gateway is unreachable",
      { enabled: false, reason: "gateway_unreachable" },
      { mcp: { ...ORG_MCP, enabled: false } },
    ],
  ])("shows no org-off line when %s", (_, status, data) => {
    org.level = 8;
    statusQuery.current = { data: status, isError: false };
    configQuery.current = { data };
    renderShell("/dashboard/gateway/mcp?tab=servers");
    expect(screen.queryByText(ORG_OFF_LINE)).toBeNull();
  });

  it.each([false, true])(
    "keeps an enabled empty org's empty state (status enabled=%s)",
    (enabled) => {
      statusQuery.current = { data: { enabled }, isError: false };
      configQuery.current = { data: { mcp: { enabled: true, servers: {} } } };
      renderShell("/dashboard/gateway/mcp");

      expect(screen.getByText("No servers yet")).toBeInTheDocument();
      expect(screen.queryByText("MCP is off for this organization")).toBeNull();
      expect(screen.queryByRole("button", { name: "Turn on MCP" })).toBeNull();
      expect(patchMutate).not.toHaveBeenCalled();
    },
  );
});

describe("MCP enable completion contract", () => {
  it.each([false, true])(
    "reports gateway_synced=%s without conflating save and application",
    (synced) => {
      org.level = 8;
      vi.clearAllMocks();
      statusQuery.current = { data: { enabled: false }, isError: false };
      configQuery.current = { data: { mcp: { ...ORG_MCP, enabled: false } } };
      renderShell("/dashboard/gateway/mcp");
      fireEvent.click(screen.getByRole("button", { name: "Turn on MCP" }));
      patchMutate.mock.calls[0][1].onSuccess({
        gateway_synced: synced,
        gateway_warning: "config push timed out",
      });
      expect(snackbar).toHaveBeenCalledTimes(1);
      expect(snackbar).toHaveBeenCalledWith(
        synced
          ? "MCP is on for this organization"
          : "Saved, but the gateway did not apply it: config push timed out",
        { variant: synced ? "success" : "warning" },
      );
      expect(screen.queryByText("MCP on")).toBeNull();
    },
  );
});
