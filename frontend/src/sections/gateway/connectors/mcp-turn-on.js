// While MCP is off for the organization, connecting an app or saving its
// OAuth app turns MCP on: the server runs one rule for the key add, the
// sign-in add and the OAuth app save (connector_catalog.mcp_turn_on). The
// drawer says so before the click, from the organization's saved MCP
// setting (the gateway config the MCP page reads). Every sentence and label
// is here, so a change of wording is one line.
export const CONNECT_TURNS_MCP_ON =
  "Adding this connector turns MCP on for the organization.";
export const SAVE_TURNS_MCP_ON =
  "Saving this OAuth app turns MCP on for the organization.";
export const CONNECT_AND_TURN_ON = "Connect and turn on MCP";
export const SAVE_AND_TURN_ON = "Save and turn on MCP";
// While the setting loads, the action waits. When it cannot be read, an
// Owner or Admin (who may turn MCP on) keeps the ordinary label, and the line
// above the action says what that click does if MCP is off: a connect on the
// connect form, a save on the OAuth app panel. Anyone else reads nothing
// about MCP, as when the setting is read.
export const MCP_MAY_TURN_ON =
  "If MCP is off for this organization, connecting turns it on.";
export const SAVE_MAY_TURN_ON =
  "If MCP is off for this organization, saving this OAuth app turns it on.";
// The frontend's copy of the server's refusal: shown only when the server
// refuses a turn-on (MCP_TURN_ON_REFUSED_CODE), never drawn in advance.
export const MCP_TURN_ON_REFUSED_CODE = "connector_mcp_turn_on_not_allowed";
export const MCP_TURN_ON_REFUSED =
  "MCP is off for this organization. Ask an owner or admin to turn it on.";

export function activatesSaved(count) {
  return `Turning MCP on also activates the ${count} ${count === 1 ? "server" : "servers"} already saved.`;
}

// The organization's MCP switch, from a useGatewayConfig read:
// - "loading" until the config is read, and "unread" when the read failed;
// - "off" when `mcp.enabled` is not true (never set, set off, or off with
//   servers saved), with `saved` the servers the turn-on activates (a server
//   whose own `enabled` is false stays off);
// - "on".
export function mcpSwitch({ data, isError } = {}) {
  if (isError) return { state: "unread", saved: 0 };
  if (!data) return { state: "loading", saved: 0 };
  const mcp = data.mcp && typeof data.mcp === "object" ? data.mcp : {};
  if (mcp.enabled === true) return { state: "on", saved: 0 };
  const servers =
    mcp.servers && typeof mcp.servers === "object"
      ? Object.values(mcp.servers)
      : [];
  return {
    state: "off",
    saved: servers.filter(
      (server) =>
        server && typeof server === "object" && server.enabled !== false,
    ).length,
  };
}

// Whether a connect form waits for the setting before it may save: only
// while it loads (a failed read does not hold the action).
export function waitsForMCPSwitch(mcp) {
  return !mcp || mcp.state === "loading";
}

// The refusal's own sentence for its code; null for any other error.
export function turnOnRefusal(error) {
  return error?.code === MCP_TURN_ON_REFUSED_CODE ? MCP_TURN_ON_REFUSED : null;
}
