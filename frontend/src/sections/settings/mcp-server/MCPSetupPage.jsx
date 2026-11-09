import React from "react";
import { Alert, Box, Button, Stack, Typography, useTheme } from "@mui/material";
import { LoadingScreen } from "src/components/loading-screen";
import { useMCPConfig } from "src/api/mcp";
import ClientSetupGuide from "./ClientSetupGuide";
import ToolGroupSelector from "./ToolGroupSelector";

export default function MCPSetupPage() {
  const theme = useTheme();
  const { data: config, isLoading, isError } = useMCPConfig();

  if (isLoading) {
    return <LoadingScreen sx={{ height: "100%", minHeight: "60vh" }} />;
  }

  if (isError) {
    return (
      <Box py={4}>
        <Alert severity="error">
          Failed to load MCP server configuration. Please try again later.
        </Alert>
      </Box>
    );
  }

  return (
    <Box>
      {/* Header */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="flex-start"
        mb={theme.spacing(3)}
        gap={2}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography
            sx={{
              typography: "m2",
              fontWeight: "fontWeightSemiBold",
              color: "text.primary",
            }}
          >
            MCP Server
          </Typography>
          <Typography
            sx={{
              typography: "s1",
              fontWeight: "fontWeightRegular",
              color: "text.secondary",
              mt: theme.spacing(0.5),
            }}
          >
            Give your AI coding assistant access to evaluations, datasets,
            experiments, traces, and more — directly from your IDE.
          </Typography>
        </Box>
        <Button
          component="a"
          href={`${import.meta.env.BASE_URL}docs/mcp-server.html`}
          target="_blank"
          rel="noopener noreferrer"
          variant="outlined"
          size="small"
          sx={{ flexShrink: 0 }}
        >
          View Docs
        </Button>
      </Stack>

      {/* Setup Guide (hero — URL + IDE tabs + how it works) */}
      <ClientSetupGuide config={config} />

      {/* Tool Groups */}
      <ToolGroupSelector config={config} />
    </Box>
  );
}
