import { replaceEqualDeep } from "@tanstack/react-query";
import { isMCPTurnedOff } from "../utils";

// Django's ordinary boolean status (including an empty/org-off result) and
// its legacy non-org-scoped response prove the MCP status route answered.
// Typed unreachable/unknown reasons and malformed flags cannot prove a change.
function reportsRuntimeAvailable(status) {
  return (
    typeof status?.enabled === "boolean" &&
    (status.reason == null || status.reason === "gateway_not_org_scoped")
  );
}

export function isMCPRuntimeOff(status) {
  return (
    isMCPTurnedOff(status) ||
    (!reportsRuntimeAvailable(status) &&
      status?.lastKnownDeploymentOff === true)
  );
}

// Retain only the restriction, not a stale status verdict. Store it atomically
// with each result in the gateway's query cache so shell/list observers and
// captured manual refetches agree, even after unmounting. A cold unknown stays
// unknown, and an authoritative runtime response releases the restriction.
export function retainMCPRuntimeRestriction(previous, status) {
  const retained =
    isMCPRuntimeOff(previous) &&
    !isMCPTurnedOff(status) &&
    !reportsRuntimeAvailable(status)
      ? { ...status, lastKnownDeploymentOff: true }
      : status;
  return replaceEqualDeep(previous, retained);
}
