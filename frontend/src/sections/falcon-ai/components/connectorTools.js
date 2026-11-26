/**
 * Pull a human-readable reason out of a failed connector request. Shared with
 * ConnectorSettingsPage so the two surfaces don't drift out of sync.
 */
export function toolActionErrorMessage(error, fallback) {
  return (
    error?.response?.data?.detail ||
    error?.response?.data?.error ||
    error?.response?.data?.message ||
    error?.message ||
    fallback
  );
}

/**
 * The connector API stores tool permissions as a list of enabled tool *names*,
 * and an empty list means "all enabled". Resolving that sentinel to the
 * concrete set is what lets a single tool be switched off without the result
 * reading as all-on again.
 *
 * Kept out of CustomizePanel.jsx so that file only exports components and fast
 * refresh keeps working.
 */
export function resolveEnabledNames(connector) {
  // ConnectorSettingsPage renders string-shaped tools as well as objects.
  const allNames = (connector.discovered_tools || connector.tools || [])
    .map((t) => (typeof t === "string" ? t : t.name))
    .filter(Boolean);
  const stored = connector.enabled_tool_names || [];
  return stored.length > 0 ? stored : allNames;
}

/**
 * An empty enabled list is the "all tools enabled" sentinel on both sides
 * (mcp_tools.py:207), so denying the last remaining tool grants every tool
 * instead of none. Until the schema can express "none enabled" (TH-7673) the
 * write is refused.
 *
 * Both strings state the rule and stop there. Why it holds is a fact about our
 * storage, not something the user can act on, and Disconnect is not the remedy
 * it sounds like — it deletes the connector, auth and all. Shared so the
 * Customize pane and the settings page word the rule identically.
 */
export const LAST_ENABLED_TOOL_MESSAGE =
  "At least one tool must stay allowed for this connector.";

export const LAST_ENABLED_TOOL_HINT =
  "The only tool still allowed — at least one must stay on.";

/**
 * True when `toolName` is the single remaining enabled tool, i.e. the toggle
 * whose next click the guard would refuse.
 */
export function isOnlyEnabledTool(connector, toolName) {
  const enabled = resolveEnabledNames(connector);
  return enabled.length === 1 && enabled[0] === toolName;
}
