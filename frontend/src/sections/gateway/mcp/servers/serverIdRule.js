// What a new MCP server's ID may be. The server applies the same rule; keep
// this pattern in step with its copies in
// futureagi/agentcc/serializers/org_config.py (_MCP_SERVER_ID) and
// agentcc-gateway/internal/config/config.go (mcpServerIDPattern, repeated in
// agentcc-gateway/internal/mcp/tenancy/spec.go).
export const SERVER_ID_PATTERN = /^[a-z0-9][a-z0-9_-]{0,47}$/;
export const SERVER_ID_MAX_LENGTH = 48;
export const SERVER_ID_RULE =
  "Lowercase letters, digits, - and _; up to 48 characters; starts with a letter or digit.";

export function isServerIdAllowed(serverId) {
  return typeof serverId === "string" && SERVER_ID_PATTERN.test(serverId);
}

// A save the server refused for the rule above. Its error code is the generic
// "invalid", so the refusal is told apart by its fixed wording.
const SERVER_ID_REFUSAL = "a server id must match";
const REFUSAL_FIELDS = ["message", "detail", "error", "result"];

export function isServerIdRefusal(error) {
  return REFUSAL_FIELDS.some((field) => {
    const text = error?.[field];
    return typeof text === "string" && text.includes(SERVER_ID_REFUSAL);
  });
}
