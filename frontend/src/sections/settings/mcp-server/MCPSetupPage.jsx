import ErrorState from "src/components/error-state";
import React from "react";
import { Box, Button, Stack, Typography, useTheme } from "@mui/material";
import { LoadingScreen } from "src/components/loading-screen";
import { useMCPConfig } from "src/api/mcp";
import ClientSetupGuide from "./ClientSetupGuide";
import ToolGroupSelector from "./ToolGroupSelector";

export default function MCPSetupPage() {
  const theme = useTheme();
  // A failed read is shown on the page, once (no toast repeats it).
  const {
    data: config,
    isLoading,
    isError,
    isRefetching,
    refetch,
  } = useMCPConfig({ meta: { errorHandled: true } });

  // The loading and error screens are only for a page with nothing to show
  // yet. Once a configuration is on screen it stays there while it is read
  // again, even if that read fails, so the editor tab someone picked is kept.
  if (!config && isLoading) {
    return <LoadingScreen sx={{ height: "100%", minHeight: "60vh" }} />;
  }

  if (!config && isError) {
    return (
      <ErrorState
        role="alert"
        title="Failed to load MCP server configuration"
        detail="Check your connection and retry."
        onRetry={() => refetch()}
        retrying={isRefetching}
        sx={{ height: "auto", flexGrow: 0, py: 4 }}
      />
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
