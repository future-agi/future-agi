// The rows of Gateway settings > Org config: every section of the
// organization's gateway config, what it holds, which editor tab edits it,
// and where this gateway does not apply it. A section the gateway reports on
// (sectionEnforcement.js) reads the gateway's own org_sections_enforced; the
// rest of a row's gaps come from enforcement.js. A section whose editor tab
// is hidden (shell/page-status.js HIDDEN_ORG_CONFIG_TABS) has no row.

import { paths } from "src/routes/paths";

import { isOrgConfigTabHidden } from "../shell/page-status";
import {
  GUARDRAILS_OFF_BANNER,
  guardrailsAreOff,
} from "../utils/guardrailPipeline";
import { ENFORCEMENT, NOT_ENFORCED_LABEL, hasEnforcement } from "./enforcement";
import { isStored } from "./ui/storedValues";
import {
  enforcementSentence,
  enforcementUnknownCause,
  notEnforcedReason,
  sectionEnforcement,
} from "./sectionEnforcement";

const isPlainObject = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);

const count = (value) => {
  if (Array.isArray(value)) return value.length;
  if (isPlainObject(value)) return Object.keys(value).length;
  return 0;
};

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function read(config, key, alias) {
  return config?.[key] ?? (alias ? config?.[alias] : undefined) ?? {};
}

// "Not set" for an empty section, "Off" / "On" by its enabled switch,
// otherwise "Set".
function genericState(section) {
  if (!isPlainObject(section) || count(section) === 0) return "Not set";
  if (section.enabled === false) return "Off";
  if (section.enabled === true) return "On";
  return "Set";
}

// `tab`: the editor tab id (OrgConfigEditor ORG_CONFIG_EDITOR_TABS).
// `reported`: the section key the gateway reports on in
// org_sections_enforced. `gaps`: enforcement.js ids for the parts of the
// section the gateway keeps but never reads (or a function of the stored
// section giving them, for a part that is a gap only once something is
// stored in it); `partial` when the rest of the section works.
const SECTIONS = [
  {
    id: "guardrails",
    partial: true,
    // Off as a whole when the gateway's guardrail pipeline is off
    // (guardrail_pipeline.enabled false): no check runs at all.
    pipeline: true,
    section: "Guardrails",
    tab: "guardrails",
    gaps: ["guardrails.failOpen", "guardrails.pipelineMode"],
    saved: (s) => {
      const checks = count(s.rules) || count(s.checks);
      return checks ? plural(checks, "check") : genericState(s);
    },
  },
  {
    id: "routing",
    partial: true,
    section: "Routing and fallbacks",
    tab: "routing",
    gaps: [
      "routing.defaultModel",
      "routing.conditionalRoutes",
      "routing.unsupportedKeys",
      "routing.fastest",
    ],
    saved: (s) => {
      const parts = [];
      if (s.strategy) parts.push(String(s.strategy).replace(/[_-]/g, " "));
      const chains = count(s.model_fallbacks || s.modelFallbacks);
      if (chains) parts.push(plural(chains, "fallback chain"));
      return parts.length ? parts.join(", ") : genericState(s);
    },
  },
  {
    id: "cache",
    partial: true,
    section: "Cache",
    tab: "cache",
    reported: "cache",
    gaps: ["cache.backends"],
    saved: (s) => {
      const ttl = s.default_ttl ?? s.ttl;
      if (s.enabled && ttl) return `On, TTL ${ttl}`;
      return genericState(s);
    },
  },
  {
    id: "rate_limiting",
    alias: "rateLimiting",
    section: "Rate limiting",
    tab: "rate_limiting",
    reported: "rate_limiting",
  },
  { id: "budgets", section: "Budgets", tab: "budgets", reported: "budgets" },
  {
    id: "cost_tracking",
    alias: "costTracking",
    section: "Custom pricing",
    tab: "budgets",
  },
  {
    id: "ip_acl",
    alias: "ipAcl",
    section: "IP access list",
    tab: "security",
    reported: "ip_acl",
  },
  {
    id: "tool_policy",
    alias: "toolPolicy",
    section: "Tool policy",
    tab: "security",
    reported: "tool_policy",
  },
  { id: "privacy", section: "Privacy", tab: "security" },
  {
    id: "alerting",
    section: "Alerting",
    tab: "alerting",
    reported: "alerting",
    saved: (s) => {
      const rules = count(s.rules);
      return rules ? plural(rules, "rule") : genericState(s);
    },
  },
  {
    id: "mcp",
    partial: true,
    section: "MCP",
    tab: "mcp",
    // The editor has no MCP tab: servers and their credentials are saved on
    // the MCP tools page, so the row opens that page's Servers tab.
    opens: { pathname: paths.dashboard.gateway.mcp, search: "?tab=servers" },
    // Stored MCP guardrails are kept and not applied; with none stored
    // nothing in the section goes unapplied.
    gaps: (s) => (isStored(s.guardrails) ? ["mcp.guardrails"] : []),
    saved: (s) => {
      const servers = count(s.servers);
      if (s.enabled && servers) return `On, ${plural(servers, "server")}`;
      return genericState(s);
    },
  },
  {
    id: "model_database",
    alias: "modelDatabase",
    section: "Model database",
    tab: "model_database",
    saved: (s) => {
      const models = count(s.models) || count(s.overrides);
      return models ? plural(models, "override") : genericState(s);
    },
  },
  {
    id: "model_map",
    alias: "modelMap",
    section: "Model map",
    tab: "model_map",
    saved: (s) => (count(s) ? plural(count(s), "mapping") : "Not set"),
  },
].filter((def) => !isOrgConfigTabHidden(def.tab));

// A row's gap on this gateway: the whole section not enforced, part of it
// stored only ("Partly enforced"), or enforcement not reported.
function gapFor(state, ids, partial) {
  if (state === "not_enforced") {
    return { label: NOT_ENFORCED_LABEL, tone: "neutral" };
  }
  if (ids.length) {
    return partial
      ? { label: "Partly enforced", tone: "warning" }
      : { label: NOT_ENFORCED_LABEL, tone: "neutral" };
  }
  if (state === "unknown") {
    return { label: "Enforcement unknown", tone: "neutral" };
  }
  return null;
}

// `gatewayConfigQuery` is the gateway config read (useGatewayConfig); while
// it is pending, a section the gateway reports on shows no gap of its own.
export function orgConfigSections(config, gatewayConfigQuery) {
  return SECTIONS.map((def) => {
    const section = read(config, def.id, def.alias);
    if (def.pipeline && guardrailsAreOff(gatewayConfigQuery)) {
      return {
        id: def.id,
        section: def.section,
        tab: def.tab,
        saved: (def.saved || genericState)(section),
        gap: { label: NOT_ENFORCED_LABEL, tone: "neutral" },
        reasons: [GUARDRAILS_OFF_BANNER],
        gapIds: [],
      };
    }
    const gaps =
      typeof def.gaps === "function" ? def.gaps(section) : def.gaps || [];
    const ids = gaps.filter(hasEnforcement);
    const state = def.reported
      ? sectionEnforcement(gatewayConfigQuery, def.reported)
      : null;
    const reported =
      state === "not_enforced" || state === "unknown"
        ? [
            enforcementSentence(
              def.reported,
              state,
              notEnforcedReason(gatewayConfigQuery, def.reported),
              enforcementUnknownCause(gatewayConfigQuery),
            ),
          ]
        : [];
    return {
      id: def.id,
      section: def.section,
      tab: def.tab,
      opens: def.opens ?? null,
      saved: (def.saved || genericState)(section),
      gap: gapFor(state, ids, def.partial),
      reasons: [...reported, ...ids.map((id) => ENFORCEMENT[id].reason)],
      gapIds: ids,
    };
  });
}
