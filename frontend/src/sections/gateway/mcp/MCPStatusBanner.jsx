import React from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";

import { isMCPEnabled } from "./utils";
import { isMCPRuntimeOff } from "./hooks/mcpRuntimeState";
import { GATEWAY_MONO } from "../utils/fonts";
import { GATEWAY_ADMIN_ONLY } from "../constants/gatewayAdmin";

// While the org's MCP is off, every tab says what adding does there: saved
// servers, credentials and settings are kept, and connect once MCP is on.
export const MCP_ORG_OFF_LINE =
  "MCP is off for this organization. What you add here is saved and connects when you turn MCP on.";
// Try a tool runs a tool through the gateway, which lists and serves the
// org's tools only while MCP is on; its tab says so while MCP is off.
export const MCP_TURN_ON_TO_TEST = "Turn on MCP to test";

// The MCP page's verdict, from the status API (the gateway's own answer) and
// the org's saved mcp section. It says "on" only when the API answers
// enabled: true; every other state names what is true and, where the
// console can fix it, how.
export function mcpVerdict({ status, isError, mcpConfig }) {
  if (isError) {
    return {
      kind: "status_error",
      tone: "warning",
      label: "Status unknown",
      severity: "warning",
      title: "MCP status could not be loaded",
      message:
        "The status request failed. Saved settings may still be available. MCP runtime status is unknown.",
    };
  }
  if (status === undefined) return { kind: "loading" };

  const reason = status?.reason;
  if (reason === "gateway_mcp_disabled") {
    return {
      kind: "gateway_off",
      tone: "neutral",
      label: "MCP off on this gateway",
      severity: "info",
      title: "MCP is turned off on this gateway",
      message:
        "MCP is off on this gateway. Saved settings do not turn it on. The hosted profile requires MCP to remain off; self-hosted runtime configuration is managed separately.",
      reason,
    };
  }
  if (reason === "gateway_unreachable") {
    return {
      kind: "unreachable",
      tone: "error",
      label: "Gateway unreachable",
      severity: "error",
      title: "The gateway could not be reached",
      message: "MCP status is unknown until it answers.",
      reason,
    };
  }

  // A retained deployment restriction cannot be fixed by an org opt-in.
  // Report uncertainty until the runtime answers authoritatively, rather than
  // offering an action that the shell's matching runtime guard must refuse.
  if (!isMCPRuntimeOff(status)) {
    // Opt-in must be reachable before the first server is added. The gateway's
    // explicit on answer still wins over a stale saved switch.
    const enabled = isMCPEnabled(status);
    if (mcpConfig && mcpConfig.enabled !== true && !enabled) {
      return {
        kind: "org_off",
        tone: "neutral",
        label: "MCP off for this organization",
        severity: "warning",
        title: "MCP is off for this organization",
        message:
          "The gateway connects servers and serves their tools only while MCP is on.",
        action: "Turn on MCP",
      };
    }
    const servers = mcpConfig?.servers;
    const hasServers =
      servers && typeof servers === "object" && Object.keys(servers).length > 0;
    if (mcpConfig && !hasServers) {
      return { kind: "empty", tone: "neutral", label: "No servers yet" };
    }
    if (enabled) {
      return { kind: "on", tone: "healthy", label: "MCP on" };
    }
  }
  return {
    kind: "not_reported",
    tone: "warning",
    label: "Not reported",
    severity: "warning",
    title: "The gateway did not report MCP as on for this organization",
    message:
      "Servers may not be connected. Reload the config, then check again.",
    reason,
  };
}

// The banner for a state that needs attention; nothing for "on", "empty"
// or while loading (the page header's chip says those).
const MCPStatusBanner = ({
  status,
  isError,
  mcpConfig,
  onTurnOn,
  turningOn,
  onRetry,
}) => {
  const verdict = mcpVerdict({ status, isError, mcpConfig });
  if (!verdict.title) return null;
  const retry = verdict.kind === "status_error" && onRetry;

  return (
    <Alert
      severity={verdict.severity}
      action={
        verdict.action && onTurnOn ? (
          <Button
            size="small"
            color="inherit"
            variant="outlined"
            onClick={onTurnOn}
            disabled={turningOn}
          >
            {verdict.action}
          </Button>
        ) : retry ? (
          <Button
            size="small"
            color="inherit"
            variant="outlined"
            onClick={onRetry}
          >
            Retry
          </Button>
        ) : undefined
      }
      sx={{ typography: "s1", alignItems: "center" }}
    >
      <AlertTitle sx={{ typography: "s1", fontWeight: 600, mb: 0.25 }}>
        {verdict.title}
      </AlertTitle>
      {verdict.message}
      {verdict.action && !onTurnOn && (
        <Box component="span" sx={{ display: "block", mt: 0.5 }}>
          {GATEWAY_ADMIN_ONLY}
        </Box>
      )}
      {verdict.reason && (
        <>
          {" Reason: "}
          <Box component="code" sx={{ fontFamily: GATEWAY_MONO }}>
            {verdict.reason}
          </Box>
        </>
      )}
    </Alert>
  );
};

MCPStatusBanner.propTypes = {
  status: PropTypes.object,
  isError: PropTypes.bool,
  mcpConfig: PropTypes.object,
  onTurnOn: PropTypes.func,
  turningOn: PropTypes.bool,
  onRetry: PropTypes.func,
};

export default MCPStatusBanner;
