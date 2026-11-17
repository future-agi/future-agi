/**
 * The code the connector API keeps as a connector's last error when the
 * credential saved for it can't be read here. The connector shows Pending
 * until it is connected again.
 */
export const CONNECTOR_AUTH_REQUIRED = "CONNECTOR_AUTH_REQUIRED";

/**
 * Shown in place of that code, for every kind of connector. Shared so the
 * Customize pane and the settings page word it identically.
 */
export const CONNECTOR_AUTH_REQUIRED_MESSAGE =
  "The saved credential for this connector can't be read here. Connect it again to use it.";

/**
 * What a connector's card shows for its last error: the sentence above for
 * that one code, and any other value exactly as it is.
 */
export function connectorErrorText(lastError) {
  return lastError === CONNECTOR_AUTH_REQUIRED
    ? CONNECTOR_AUTH_REQUIRED_MESSAGE
    : lastError;
}
