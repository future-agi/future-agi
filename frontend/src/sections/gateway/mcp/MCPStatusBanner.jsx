import React from "react";
import PropTypes from "prop-types";
import { Alert, AlertTitle, Box } from "@mui/material";
import { isMCPEnabled } from "./utils";

const ENABLED_TITLE = "MCP gateway: Enabled";
const NOT_ENABLED_TITLE = "MCP gateway: not enabled";
const STATUS_UNKNOWN_TITLE = "MCP gateway: status unknown";
const NO_SERVER_TITLE = "MCP gateway: no server configured";

const UNREACHABLE = "gateway_unreachable";

const REASON_TEXT = {
  gateway_mcp_disabled: "MCP is turned off on this gateway.",
  [UNREACHABLE]:
    "The gateway could not be reached, so its MCP status is unknown.",
};
const NO_REASON_TEXT = "The gateway did not report MCP as enabled.";
const STATUS_FAILED_TEXT = "MCP status could not be loaded.";
const NO_SERVER_TEXT = "No MCP server is configured for this organization.";

// Django answers `enabled: false` with no reason and an empty server list
// when the gateway runs MCP but the org has no MCP server of its own: the
// gateway was asked and answered, so "not enabled" would be false there.
function isOrgWithoutServers(status) {
  return (
    status?.enabled === false &&
    !status.reason &&
    Array.isArray(status.servers) &&
    status.servers.length === 0
  );
}

// The banner's title, text and severity for one status answer.
function verdict(status, isError) {
  if (isError) {
    return {
      severity: "warning",
      title: STATUS_UNKNOWN_TITLE,
      text: STATUS_FAILED_TEXT,
    };
  }
  if (isOrgWithoutServers(status)) {
    return { severity: "info", title: NO_SERVER_TITLE, text: NO_SERVER_TEXT };
  }
  const reason = status?.reason;
  return {
    severity: "warning",
    title: reason === UNREACHABLE ? STATUS_UNKNOWN_TITLE : NOT_ENABLED_TITLE,
    text: REASON_TEXT[reason] || NO_REASON_TEXT,
    reason,
  };
}

// The page's MCP verdict. It says Enabled only when the status API answers
// `enabled: true`. Otherwise it says what the answer showed: that the status
// is unknown (the read failed or the gateway could not be reached), that the
// org has no MCP server, or that the gateway did not report MCP as enabled,
// with the reason the API gave.
const MCPStatusBanner = ({ status, isError }) => {
  if (!isError && status === undefined) return null;

  if (!isError && isMCPEnabled(status)) {
    return (
      <Alert severity="success" sx={{ mb: 3 }}>
        {ENABLED_TITLE}
      </Alert>
    );
  }

  const { severity, title, text, reason } = verdict(status, isError);
  return (
    <Alert severity={severity} sx={{ mb: 3 }}>
      <AlertTitle>{title}</AlertTitle>
      {text}
      {reason && (
        <>
          {" Reason: "}
          <Box component="code" sx={{ fontFamily: "monospace" }}>
            {reason}
          </Box>
        </>
      )}
    </Alert>
  );
};

MCPStatusBanner.propTypes = {
  status: PropTypes.object,
  isError: PropTypes.bool,
};

export default MCPStatusBanner;
