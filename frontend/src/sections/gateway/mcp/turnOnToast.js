import { mcpVerdict } from "./MCPStatusBanner";

// After Turn on MCP is saved, the toast says what the server confirmed, not
// what was asked: the page re-reads the organization's saved configuration
// and the gateway's MCP status, and the toast follows the verdict the page
// then shows (mcpVerdict). An organization with no server saved reads "on"
// once its saved switch is on, as its "No servers yet" chip does.
export const MCP_TURNED_ON = "MCP is on for this organization";
export const MCP_SAVED_NOT_REPORTED =
  "MCP is saved as on. The gateway does not report it on yet.";
export const MCP_STILL_OFF = "MCP is still off for this organization.";
export const MCP_REREAD_FAILED =
  "Saved. The MCP status could not be read; reload the page to see it.";

// One re-read's data, or null when it failed or there is nothing to read.
async function reread(refetch) {
  if (typeof refetch !== "function") return null;
  try {
    const result = await refetch();
    return result && !result.isError && result.data != null
      ? result.data
      : null;
  } catch {
    return null;
  }
}

// `refetchConfig` and `refetchStatus` are the page's own query refetches.
// Resolves to the toast: { message, variant }.
export async function confirmedTurnOn({ refetchConfig, refetchStatus }) {
  const [config, status] = await Promise.all([
    reread(refetchConfig),
    reread(refetchStatus),
  ]);
  if (!config || !status) {
    return { message: MCP_REREAD_FAILED, variant: "warning" };
  }
  const verdict = mcpVerdict({
    status,
    isError: false,
    mcpConfig: config.mcp || {},
  });
  if (verdict.kind === "on" || verdict.kind === "empty") {
    return { message: MCP_TURNED_ON, variant: "success" };
  }
  if (verdict.kind === "org_off") {
    return { message: MCP_STILL_OFF, variant: "warning" };
  }
  return { message: MCP_SAVED_NOT_REPORTED, variant: "warning" };
}
