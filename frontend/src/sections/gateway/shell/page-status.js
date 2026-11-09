// Which AI Gateway pages, tabs and controls are hidden, because the gateway
// and API this console ships with do not serve them.
//
// The entries below are omitted from navigation. Previously shipped tabs can
// instead remain visible with a disabled or read-only body at their renderer:
// - a hidden page is in no navigation, and its URL redirects to `redirectTo`
//   (query and hash kept);
// - a hidden tab is left out of its tab bar, and its URL opens the first tab;
// - a hidden control renders nothing, and its hooks send no request.
// An entry is deleted in the same change that makes its page, tab or control
// work, and the navigation comes back with it.
//
// The routes (routes/sections/dashboard.jsx), the gateway rail
// (gateway-rail.js), the MCP tab bar (mcp/tabs.js), Gateway settings and its
// Org config editor, the gateway credit hooks and the placeholder walk test
// all read this one module.

import { paths } from "src/routes/paths";

const gw = paths.dashboard.gateway;

export const HIDDEN_GATEWAY_PAGES = Object.freeze({
  // The capability matrix view has no backend (agentcc/urls_capabilities.py
  // is empty): the models catalog is the nearest page that works.
  capabilities: Object.freeze({ redirectTo: gw.models }),
  // Access, data, presets, versions and rollout policies have no views
  // (agentcc/urls_policy.py): the guardrails page is the nearest one.
  policies: Object.freeze({ redirectTo: gw.guardrails.root }),
});

// MCP tabs, by their ?tab= slug. A hidden tab is left out of the tab bar and
// its URL opens the overview tab. The MCP page is shown: the gateway connects
// the servers an organization saves (its own pool per organization), serves
// its virtual servers at /mcp/v/{id}, keeps their keys in the credential vault
// and records each tool call, and the API serves the tool-call log and the
// vault (agentcc/urls_mcp_toolcalls.py, urls_mcp_credentials.py).
export const HIDDEN_MCP_TABS = Object.freeze({
  // Connect is not listed here: its own deployment switch, client_installs in
  // the MCP status features, gates it (mcp/tabs.js shownMCPTabs). The tabs
  // below lack their UI/API.
  registry: Object.freeze({}),
  trust: Object.freeze({}),
  approvals: Object.freeze({}),
  // Resources and prompts remain visible but disabled in the MCP shell;
  // their data components are never mounted.
});

// Gateway settings > Org config sections, by section id. A hidden section has
// no editor tab and no summary row, and the editor never sends it, so a stored
// value is kept as it is. Open the editor's tabs by id, not by index.
// A2A and audit are visible read-only; the editor's explicit save allowlist
// still excludes both sections, independently of their navigation visibility.
export const HIDDEN_ORG_CONFIG_TABS = Object.freeze({});

// Controls hidden wherever they appear, by name.
// Credits are served by the billing API. Organization roles and explicit
// organization enablement gate new credit surfaces in useCentralBilling;
// purchase also needs billing_available. Existing cloud billing stays visible.
export const HIDDEN_GATEWAY_CONTROLS = Object.freeze({});

// Gateway settings tabs, by their ?tab= slug. A hidden tab is left out of the
// tab bar and its URL opens General. None is hidden: Batch jobs runs each job
// as the organization, and only an Owner or Admin can submit or cancel one.
export const HIDDEN_SETTINGS_TABS = Object.freeze({});

const has = (object, key) => Object.prototype.hasOwnProperty.call(object, key);

export function isGatewayPageHidden(pageId) {
  return has(HIDDEN_GATEWAY_PAGES, pageId);
}

export function hiddenPageRedirect(pageId) {
  return HIDDEN_GATEWAY_PAGES[pageId]?.redirectTo ?? null;
}

export function isMcpTabHidden(slug) {
  return has(HIDDEN_MCP_TABS, slug);
}

export function isOrgConfigTabHidden(sectionId) {
  return has(HIDDEN_ORG_CONFIG_TABS, sectionId);
}

export function isGatewayControlHidden(name) {
  return has(HIDDEN_GATEWAY_CONTROLS, name);
}

export function isSettingsTabHidden(slug) {
  return has(HIDDEN_SETTINGS_TABS, slug);
}
