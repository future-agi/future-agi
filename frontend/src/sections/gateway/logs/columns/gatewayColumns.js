// The request explorer's Agent & billing columns, read from the log entry v2
// fields the request logs store. They are opt-in: the default table keeps
// its ten built-ins, and a saved config gains one only when the user ticks it.
// Their `gateway:` ids never collide with built-in or custom-property ids.

const GATEWAY_PREFIX = "gateway:";

export const GATEWAY_COLUMNS = [
  { key: "billingMode", label: "Billing mode", width: 120 },
  { key: "credentialType", label: "Credential type", width: 130 },
  { key: "costUpstream", label: "Upstream cost", width: 120 },
  { key: "costMarkup", label: "Markup", width: 100 },
  { key: "attemptCount", label: "Attempts", width: 90 },
  { key: "agentId", label: "Agent", width: 140 },
  { key: "agentStep", label: "Step", width: 100 },
  { key: "agentRunId", label: "Run", width: 160 },
].map((col) => ({
  ...col,
  id: `${GATEWAY_PREFIX}${col.key}`,
  kind: "gateway",
  sortable: false,
  locked: false,
}));

export const GATEWAY_BY_ID = new Map(GATEWAY_COLUMNS.map((c) => [c.id, c]));

export function isGatewayColumnId(id) {
  return typeof id === "string" && id.startsWith(GATEWAY_PREFIX);
}
