import { isOrgConfigTabHidden } from "../shell/page-status";

// The editor's tabs, by id. `enforced` names the org sections a tab edits
// that the gateway reports on in org_sections_enforced. A tab hidden in
// shell/page-status.js (HIDDEN_ORG_CONFIG_TABS) is left out. MCP has no tab:
// the gateway does apply an organization's MCP servers, and they and their
// credentials are saved on the MCP page, not in this editor. A2A and Audit
// show stored values read-only and are never part of a save. They are appended
// so callers opening an existing tab by number keep the same target (Routing
// is 2, Cache 3, Model Database 8, Model Map 9).
export const ORG_CONFIG_EDITOR_TABS = [
  { id: "providers", label: "Providers" },
  { id: "guardrails", label: "Guardrails" },
  { id: "routing", label: "Routing" },
  { id: "cache", label: "Cache", enforced: ["cache"] },
  { id: "rate_limiting", label: "Rate Limiting", enforced: ["rate_limiting"] },
  { id: "budgets", label: "Budgets", enforced: ["budgets"] },
  { id: "security", label: "Security", enforced: ["ip_acl", "tool_policy"] },
  { id: "alerting", label: "Alerting", enforced: ["alerting"] },
  { id: "model_database", label: "Model Database" },
  { id: "model_map", label: "Model Map" },
  { id: "a2a", label: "A2A" },
  { id: "audit", label: "Audit" },
];

export const VISIBLE_TABS = ORG_CONFIG_EDITOR_TABS.filter(
  (tab) => !isOrgConfigTabHidden(tab.id),
);

// RL-917: Cache stays visible for every gateway, including an unknown/off
// report. The editor disables its controls and omits edits only while off.
export function editorTabsFor() {
  return VISIBLE_TABS;
}

// A tab id, or a tab's place among the tabs shown, as the id of a tab this
// editor shows: an unknown or hidden tab opens Providers.
export function editorTabId(defaultTab) {
  const id =
    typeof defaultTab === "number" ? VISIBLE_TABS[defaultTab]?.id : defaultTab;
  return VISIBLE_TABS.some((tab) => tab.id === id) ? id : VISIBLE_TABS[0].id;
}
