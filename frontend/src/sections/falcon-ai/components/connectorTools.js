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
 * The connector API stores tool permissions as a literal list of enabled
 * tool names - discovery always seeds it with every discovered tool, so an
 * empty list means the user disabled all of them, not "unset". Do not expand
 * it to the full tool set here.
 *
 * Kept out of CustomizePanel.jsx so that file only exports components and fast
 * refresh keeps working.
 */
export function resolveEnabledNames(connector) {
  return connector.enabled_tool_names || [];
}
