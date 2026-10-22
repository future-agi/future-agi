// The MCP page's tabs. Each tab is the default export of its own module at a
// fixed path, so later work replaces one tab's folder without touching the
// page shell. Every tab receives only `gatewayId` and loads its own data.
//
// The Virtual Servers, Registry, Connect, Trust, Approvals and Tool Calls tabs
// (virtual/, registry/, connect/, trust/, approvals/, calls/) are placeholders,
// hidden for the launch: they are not listed, so a link to one opens the
// overview. List a tab here again only with its real content.
import OverviewTab from "./overview/OverviewTab";
import ServersTab from "./servers/ServersTab";
import GuardrailsTab from "./guardrails/GuardrailsTab";
import PlaygroundTab from "./playground/PlaygroundTab";
import ResourcesTab from "./resources/ResourcesTab";
import PromptsTab from "./prompts/PromptsTab";

export const MCP_TABS = [
  { slug: "overview", label: "Overview", Content: OverviewTab },
  { slug: "servers", label: "Servers", Content: ServersTab },
  { slug: "guardrails", label: "Guardrails", Content: GuardrailsTab },
  { slug: "playground", label: "Playground", Content: PlaygroundTab },
  { slug: "resources", label: "Resources", Content: ResourcesTab },
  { slug: "prompts", label: "Prompts", Content: PromptsTab },
];

// The query-string parameter that holds the open tab. The overview is the
// default and carries no parameter.
export const MCP_TAB_PARAM = "tab";

// Other slugs a link may carry, and the tab each opens: the tool list
// lives on the Servers tab.
const LEGACY_SLUGS = { tools: "servers" };

export function resolveMCPTabIndex(slug) {
  const resolved = LEGACY_SLUGS[slug] || slug;
  return Math.max(
    MCP_TABS.findIndex((tab) => tab.slug === resolved),
    0,
  );
}
