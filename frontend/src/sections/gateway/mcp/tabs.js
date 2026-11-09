// The MCP page's tabs. Each tab is the default export of its own module at a
// fixed path, so one tab's folder can change without touching this list or
// the page shell. Tabs receive `gatewayId`, and `runtimeOff` in the off shell.
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
import { isMcpTabHidden } from "../shell/page-status";
import { SavedOverview, SavedTools } from "./OffInventory";

// RL-1037: an explicit allowlist, never a filter over the evolving NEW tabs.
// v1.56.1 MCPManagementSection.jsx:176-182, in this exact order. Clients is
// the pre-existing off-shell exception at 39162ab38a:155-174. W07/W14 flags
// cannot add vault, virtual, catalogue, calls/downloads, approvals or toolkits.
const MCP_OFF_TABS = [
  { slug: "overview", label: "Overview", Content: SavedOverview }, // tag:176
  { slug: "tools", label: "Tools", Content: SavedTools }, // tag:177
  { slug: "servers", label: "Servers", Content: ServersTab }, // tag:178
  { slug: "resources", label: "Resources", Content: ResourcesTab }, // tag:179
  { slug: "prompts", label: "Prompts", Content: PromptsTab }, // tag:180
  { slug: "guardrails", label: "Guardrails", Content: GuardrailsTab }, // tag:181
  { slug: "playground", label: "Playground", Content: PlaygroundTab }, // tag:182
];

// Every tab. The tab bar shows MCP_TABS: the ones shell/page-status.js does
// not hide.
export const ALL_MCP_TABS = [
  { slug: "overview", label: "Overview", Content: OverviewTab },
  { slug: "servers", label: "Servers", Content: ServersTab },
  { slug: "virtual", label: "Virtual servers", Content: VirtualServersTab },
  { slug: "calls", label: "Tool calls", Content: ToolCallsTab },
  { slug: "credentials", label: "Credentials", Content: CredentialsTab },
  { slug: "clients", label: "MCP clients", Content: MCPClientsTab },
  { slug: "guardrails", label: "Guardrails", Content: GuardrailsTab },
  { slug: "playground", label: "Try a tool", Content: PlaygroundTab },
  // Keep these shipped tabs visible; the shell must never mount their data
  // components while disabled, including when opened through a direct link.
  {
    slug: "resources",
    label: "Resources",
    Content: ResourcesTab,
    disabled: true,
  },
  { slug: "prompts", label: "Prompts", Content: PromptsTab, disabled: true },
  { slug: "registry", label: "Registry", Content: RegistryTab },
  { slug: "connect", label: "Connect", Content: ConnectTab },
  { slug: "trust", label: "Tool revisions", Content: TrustTab },
  { slug: "approvals", label: "Approvals", Content: ApprovalsTab },
];

export const MCP_TABS = ALL_MCP_TABS.filter((tab) => !isMcpTabHidden(tab.slug));

// NEW: site claims review required for this notice.
export const DISABLED_MCP_NOTICE =
  "The Resources and Prompts tabs are disabled.";

// The query-string parameter that holds the open tab. The overview is the
// default and carries no parameter.
export const MCP_TAB_PARAM = "tab";

// Slugs from links made before this layout: the tool list now lives on the
// Servers tab.
const LEGACY_SLUGS = { tools: "servers" };

// Shipped Guardrails stays visible even with no stored settings (850).
// Only deployment capability gates change this list; config loading, errors
// and empty values do not remove a tab or rewrite its deep link.
// Calls requires the NEW explorer capability, never widening the off allowlist.
export function shownMCPTabs({
  clientOAuth = false,
  clientInstalls = false,
  toolCallExplorer = false,
  runtimeOff = false,
} = {}) {
  if (runtimeOff)
    return clientOAuth === true
      ? [
          ...MCP_OFF_TABS,
          {
            slug: "clients",
            label: "Manage MCP clients",
            Content: MCPClientsTab,
          },
        ]
      : MCP_OFF_TABS;
  return MCP_TABS.filter((tab) => {
    if (tab.slug === "calls" && toolCallExplorer !== true) return false;
    if (tab.slug === "clients" && clientOAuth !== true) return false;
    if (tab.slug === "connect" && clientInstalls !== true) return false;
    return true;
  });
}

/** Whether a ?tab= slug names a tab the bar shows (overview carries none). */
export function isVisibleMCPTab(slug, tabs = MCP_TABS) {
  const resolved = tabs.some((tab) => tab.slug === slug)
    ? slug
    : LEGACY_SLUGS[slug] || slug;
  return tabs.some((tab) => tab.slug === resolved);
}

export function resolveMCPTabIndex(slug, tabs = MCP_TABS) {
  const resolved = tabs.some((tab) => tab.slug === slug)
    ? slug
    : LEGACY_SLUGS[slug] || slug;
  return Math.max(
    tabs.findIndex((tab) => tab.slug === resolved),
    0,
  );
}
