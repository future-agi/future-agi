// The one switch for Simulate's "MCP: your agent connects to us" reach (ruling
// 491 (a)). It is off by default, and off hides all of it: the reach card, the
// connect step with its copyable <base>/mcp/e/<slug> address, and the
// "Connected over MCP" version record the drawer would mint. Nothing on this
// deployment answers that address yet (it is a 404), and useMcpConnect only
// waits on a timer, so with the switch off nothing claims a connection that did
// not happen. Turn it on only together with the real MCP bridge and handshake,
// a post-launch build item.
//
// Runtime config (window.__FUTURE_AGI_CONFIG__) wins over the build-time value,
// as in src/config-global.js. Only "true" (any case) turns it on; unset, empty
// or any other value leaves it off.
export const MCP_CONNECT_SWITCH = "VITE_SIMULATE_MCP_CONNECT";

const DEFAULT_ON = false;

const text = (value) => (typeof value === "string" ? value.trim() : "");

export function readMcpConnectSwitch(runtimeConfig, buildEnv) {
  const raw =
    text(runtimeConfig?.[MCP_CONNECT_SWITCH]) ||
    text(buildEnv?.[MCP_CONNECT_SWITCH]);
  if (!raw) return DEFAULT_ON;
  return raw.toLowerCase() === "true";
}

export function isMcpConnectEnabled() {
  const runtimeConfig =
    (typeof window !== "undefined" && window.__FUTURE_AGI_CONFIG__) || {};
  let buildEnv = {};
  try {
    buildEnv = import.meta.env || {};
  } catch {
    buildEnv = {};
  }
  return readMcpConnectSwitch(runtimeConfig, buildEnv);
}
