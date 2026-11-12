// The AI Gateway's left rail: what the sidebar shows once the AI Gateway is
// chosen in the product switcher. Plain data, so the layout, the routes test
// and the placeholder walk all read the same list.
//
// `icon` names a glyph in gateway-icons.jsx. `pageId` ties an item to a
// hidden page in page-status.js: a hidden page's item is left out. `also`
// lists other paths the item owns (a tab that lives under another item's
// path), so the rail marks it there too.

import { paths } from "src/routes/paths";
import { NOT_AVAILABLE_IN_DEPLOYMENT } from "src/sections/omega/shell/agent-infrastructure-nav";

import { isGatewayPageHidden } from "./page-status";

const gw = paths.dashboard.gateway;

const RAIL = [
  {
    id: "overview",
    items: [
      { title: "Overview", path: gw.overview, icon: "overview", exact: true },
    ],
  },
  {
    id: "models",
    subheader: "Models",
    items: [
      {
        title: "Models",
        path: gw.models,
        icon: "catalog",
        // A stable destination for the catalog or the org's own models,
        // not a managed offer. Keep it visible while availability is unknown.
      },
      { title: "Auto routing", path: `${gw.models}/auto`, icon: "routing" },
    ],
  },
  {
    id: "providers",
    // Integrations (apps from the connector catalog) sits next to Providers,
    // and MCP tools beside it: one group, so the rail keeps six groups and
    // fits a 1440x960 screen (GatewayRail.fit.test.jsx).
    subheader: "Providers, Tools & Keys",
    items: [
      { title: "Providers", path: gw.providers, icon: "providers" },
      {
        title: "Integrations",
        path: gw.integrations,
        icon: "integrations",
      },
      { title: "MCP tools", path: gw.mcp, icon: "mcp", pageId: "mcp" },
      { title: "API keys", path: gw.keys, icon: "keys" },
      { title: "Fallbacks", path: gw.fallbacks, icon: "fallbacks" },
    ],
  },
  {
    id: "guardrails",
    // Named for the pages it shows: it names Policies only while the
    // Policies page is shown (page-status.js).
    subheader: isGatewayPageHidden("policies")
      ? "Guardrails"
      : "Guardrails & Policies",
    items: [
      { title: "Guardrails", path: gw.guardrails.root, icon: "guardrails" },
      {
        title: "Policies",
        path: gw.policies.root,
        icon: "policies",
        pageId: "policies",
      },
    ],
  },
  {
    id: "observability",
    subheader: "Observability",
    items: [
      { title: "Logs", path: gw.logs, icon: "logs" },
      { title: "Analytics", path: gw.analytics, icon: "analytics" },
      { title: "Monitoring", path: gw.monitoring, icon: "monitoring" },
      {
        title: "Alerts",
        path: `${gw.monitoring}/rules`,
        // The Channels tab of Alerts.
        also: [`${gw.monitoring}/channels`],
        icon: "alerts",
      },
      { title: "Sessions", path: gw.sessions, icon: "sessions" },
      { title: "Runs", path: gw.runs, icon: "runs", pageId: "runs" },
      {
        title: "Custom properties",
        path: gw.customProperties,
        icon: "properties",
      },
      { title: "Webhooks", path: gw.webhooks, icon: "webhooks" },
    ],
  },
  {
    id: "settings",
    subheader: "Settings",
    items: [
      { title: "Gateway settings", path: gw.settings, icon: "settings" },
      { title: "Budgets", path: gw.budgets, icon: "budgets" },
    ],
  },
];

/**
 * The rail's groups, without hidden pages. `extraItems` maps a group id to
 * items appended to it (the central Billing link is added this way, since
 * whether it shows depends on the user's role and the deployment).
 * `noManagedModels` still gates managed-only offers. Models itself is always
 * reachable: its page selects the catalog or the organization's own models.
 * `integrationsOff` (the catalog switch is off) keeps Integrations listed,
 * disabled, with the deployment's "Not available" line.
 */
export function gatewayRailGroups({
  extraItems = {},
  noManagedModels = false,
  integrations = false,
  integrationsOff = false,
} = {}) {
  return RAIL.map((group) => ({
    ...group,
    items: [
      ...group.items
        .filter(
          (item) =>
            (!item.pageId || !isGatewayPageHidden(item.pageId)) &&
            !(noManagedModels && item.managedOnly) &&
            (item.path !== gw.integrations || integrations || integrationsOff),
        )
        .map((item) =>
          item.path === gw.integrations && integrationsOff
            ? { ...item, disabled: true, caption: NOT_AVAILABLE_IN_DEPLOYMENT }
            : item,
        ),
      ...(extraItems[group.id] || []),
    ],
  })).filter((group) => group.items.length > 0);
}

function under(pathname, path, exact) {
  if (pathname === path || pathname === `${path}/`) return true;
  return !exact && pathname.startsWith(`${path}/`);
}

/** The longest of the item's paths the URL is under, or 0. */
function matchLength(pathname, item) {
  return [item.path, ...(item.also || [])]
    .filter((path) => under(pathname, path, item.exact))
    .reduce((longest, path) => Math.max(longest, path.length), 0);
}

/** The path of the item to mark active: the one the URL is most deeply under. */
export function activeRailPath(pathname, items) {
  let best = null;
  let bestLength = 0;
  for (const item of items) {
    const length = matchLength(pathname, item);
    if (length > bestLength) {
      best = item.path;
      bestLength = length;
    }
  }
  return best;
}
