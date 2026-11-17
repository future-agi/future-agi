import { isStored, onOff } from "../../settings/ui/storedValues";

// The org's stored MCP guardrails as read-only rows. The gateway reads them
// only for counters on each request (handlers.go applyOrgMCPOverrides; it
// applies only its own tool guard), so every screen shows them as
// stored, never as options.
// Legacy keys remain inspectable as stored too; displaying them never claims enforcement.
export function mcpGuardrailRows(guardrails) {
  if (!isStored(guardrails)) return [];
  const g = guardrails;
  const rows = [];
  if (g.enabled !== undefined) {
    rows.push({ label: "MCP guardrails", value: onOff(g.enabled) });
  }
  if (g.validate_inputs !== undefined) {
    rows.push({
      label: "Validate tool inputs",
      value: onOff(g.validate_inputs),
    });
  }
  if (g.validate_outputs !== undefined) {
    rows.push({
      label: "Validate tool outputs",
      value: onOff(g.validate_outputs),
    });
  }
  if (isStored(g.blocked_tools)) {
    rows.push({
      label: "Blocked tools",
      value: g.blocked_tools.join(", "),
      mono: true,
    });
  }
  if (isStored(g.allowed_servers)) {
    rows.push({
      label: "Allowed servers",
      value: g.allowed_servers.join(", "),
      mono: true,
    });
  }
  if (isStored(g.tool_rate_limits)) {
    rows.push({
      label: "Tool limits",
      value: Object.entries(g.tool_rate_limits)
        .map(([tool, rpm]) => `${tool}: ${rpm} per minute`)
        .join(", "),
      mono: true,
    });
  }
  if (Array.isArray(g.custom_patterns) && g.custom_patterns.length) {
    rows.push({ label: "Custom injection patterns (stored only)", value: g.custom_patterns.filter((pattern) => typeof pattern === "string").join(", "), mono: true });
  }
  if (typeof g.action === "string" && g.action) {
    const names = { warn: "Warn", log: "Log", block: "Block" };
    rows.push({ label: "Action (stored only)", value: names[g.action] || g.action });
  }
  return rows;
}
