import React from "react";
import { Card, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

// The MCP page's body on a deployment that runs with MCP off. It is a state,
// not an error: nothing failed, and there is nothing to manage here.
const MCPOffState = () => (
  <Card variant="outlined">
    <Stack alignItems="center" textAlign="center" spacing={1} py={8} px={3}>
      <Iconify
        icon="mdi:power-plug-off-outline"
        width={48}
        sx={{ color: "text.disabled", mb: 1 }}
      />
      <Typography variant="h6" color="text.secondary">
        MCP is turned off for this deployment
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ maxWidth: 440 }}>
        MCP servers, tools, resources and prompts are not available here.
      </Typography>
    </Stack>
  </Card>
);

export default MCPOffState;
