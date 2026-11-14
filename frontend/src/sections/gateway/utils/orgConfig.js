// Every section the org-config create endpoint writes, with the camelCase name
// a response may use. Saving creates a whole new active version, so a section
// left out of the payload is reset to its default.
const WRITABLE_SECTIONS = [
  ["guardrails"],
  ["routing"],
  ["cache"],
  ["rate_limiting", "rateLimiting"],
  ["budgets"],
  ["cost_tracking", "costTracking"],
  ["ip_acl", "ipAcl"],
  ["alerting"],
  ["privacy"],
  ["tool_policy", "toolPolicy"],
  ["mcp"],
  ["a2a"],
  ["audit"],
  ["model_database", "modelDatabase"],
  ["model_map", "modelMap"],
];

/**
 * The active config's writable sections, so a save can replace one section
 * and keep the rest. Sections the config doesn't have are left out and get
 * the backend's defaults.
 */
export function writableOrgConfig(config) {
  const sections = {};
  WRITABLE_SECTIONS.forEach(([key, alias]) => {
    const value = config?.[key] ?? (alias ? config?.[alias] : undefined);
    if (value !== undefined && value !== null) sections[key] = value;
  });
  return sections;
}
