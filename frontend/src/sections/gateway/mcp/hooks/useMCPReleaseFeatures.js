import { useRef } from "react";
import { useMCPStatus } from "./useMCPConfig";
import { isMCPRuntimeOff } from "./mcpRuntimeState";

// NEW: minimal off-state copy; site gate review required.
export const MCP_CONNECT_OFF_NOTICE =
  "Connecting catalog apps requires the catalog, credential management and named virtual servers to be on.";

export function releaseFeatures(status) {
  // The switches are read whenever the status carries them, an unreachable
  // gateway included. Only a runtime-OFF MCP (typed or retained) or a status
  // read that is pending or failed drops them, so writes stay affirmative.
  const ready =
    !status?.isError &&
    !status?.isLoading &&
    !status?.isPending &&
    !isMCPRuntimeOff(status?.data);
  const features = ready ? status?.data?.features : undefined;
  const catalog = features?.catalog === true;
  const credentialVault = features?.credential_vault === true;
  const virtualServers = features?.virtual_servers === true;
  return {
    catalog,
    credentialVault,
    virtualServers,
    catalogConnect: catalog && credentialVault && virtualServers,
  };
}

// A switch the deployment really has off: a status that carries `features`
// says false. Loading, a failed read, an unreachable gateway or absent
// features never read as off.
export function releaseSwitchesOff(status) {
  const features = status?.data?.features;
  const off = (name) =>
    features != null &&
    typeof features === "object" &&
    features[name] === false;
  return {
    catalog: off("catalog"),
    credentialVault: off("credential_vault"),
    virtualServers: off("virtual_servers"),
  };
}

// What the page shows: every surface unless MCP is off at runtime or its
// switch is off, so loading and failed states keep their own entries, tabs
// and grid.
export function releaseShown(status) {
  const runtimeOff = isMCPRuntimeOff(status?.data);
  const off = releaseSwitchesOff(status);
  return {
    catalog: !runtimeOff && !off.catalog,
    credentialVault: !runtimeOff && !off.credentialVault,
    virtualServers: !runtimeOff && !off.virtualServers,
  };
}

// Status is organization-scoped; the backend accepts the stable default id
// for consumers outside GatewayProvider (navigation and the vault dialogs).
export function useMCPReleaseFeatures(gatewayId = "default") {
  const status = useMCPStatus(gatewayId);
  return {
    ...releaseFeatures(status),
    shown: releaseShown(status),
    off: releaseSwitchesOff(status),
  };
}

// Writes wait for the status to answer: while it loads or after its read
// failed, a write's submit stays disabled and the page's status-error state
// (with Retry) says why.
export function useMCPWriteHold(gatewayId = "default") {
  const status = useMCPStatus(gatewayId);
  const failed = Boolean(status?.isError);
  return {
    held: failed || Boolean(status?.isPending ?? status?.isLoading),
    failed,
    retry: () => status?.refetch?.(),
  };
}

// Mutation callbacks retained by a dialog must check the current render's
// capability, not the ON render that originally opened it.
export function useMCPReleaseGuard(gatewayId) {
  const features = useMCPReleaseFeatures(gatewayId);
  const current = useRef(features);
  current.current = features;
  // A read ({ read: true }) needs only a shown surface; a write needs the
  // switch affirmatively on.
  return (name, { read = false } = {}) => {
    const allowed = read
      ? current.current.shown?.[name] ?? current.current[name]
      : current.current[name];
    if (allowed !== true) {
      // NEW: minimal off-state copy; site gate review required.
      const error = new Error(
        "This MCP feature is off or its status is unavailable.",
      );
      error.code = "mcp_feature_disabled";
      throw error;
    }
  };
}
