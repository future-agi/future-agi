/**
 * The connector API stores tool permissions as a list of enabled tool *names*,
 * and an empty list means "all enabled". Resolving that sentinel to the
 * concrete set is what lets a single tool be switched off without the result
 * reading as all-on again.
 *
 * Kept out of CustomizePanel.jsx so that file only exports components and fast
 * refresh keeps working.
 */
/**
 * Pull a human-readable reason out of a failed connector request. Mirrors
 * `getActionErrorMessage` in ConnectorSettingsPage, which is not exported.
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

export function resolveEnabledNames(connector) {
  const allNames = (connector.discovered_tools || connector.tools || [])
    .map((t) => t.name)
    .filter(Boolean);
  const stored = connector.enabled_tool_names || [];
  return stored.length > 0 ? stored : allNames;
}
