import React from "react";
import { Link, Stack, Typography } from "@mui/material";

const MCPDeploymentRemedy = () => (
  <Stack
    spacing={0.5}
    sx={{
      maxWidth: 560,
      minWidth: 0,
      width: "100%",
      overflowWrap: "anywhere",
    }}
  >
    <Typography variant="body2" color="text.secondary">
      For a self-hosted gateway, ask your operator to configure
      AGENTCC_MCP_STATE_KEYS, set mcp.enabled: true and restart the gateway.
    </Typography>
    <Link
      href="https://github.com/future-agi/future-agi/blob/55cb111a056ddf520e89ffb5a576ce1a7ff449ce/docs/gateway/mcp.md#run-it-on-your-own-gateway"
      target="_blank"
      rel="noopener noreferrer"
      variant="body2"
      underline="always"
    >
      Self-hosted MCP setup guide
    </Link>
  </Stack>
);

export default MCPDeploymentRemedy;
