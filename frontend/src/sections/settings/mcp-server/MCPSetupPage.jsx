import React from "react";
import {
  Alert,
  Box,
  Button,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import SvgColor from "src/components/svg-color";
import { LoadingScreen } from "src/components/loading-screen";
import { useMCPConfig } from "src/api/mcp";
import ClientSetupGuide from "./ClientSetupGuide";
import ToolGroupSelector from "./ToolGroupSelector";

export default function MCPSetupPage() {
  const theme = useTheme();
  const { data: config, isLoading, isError } = useMCPConfig();

  if (isLoading) {
    return (
      <LoadingScreen sx={{ height: "100%", minHeight: "60vh" }} />
    );
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
          variant="outlined"
          size="small"
          sx={{
            borderRadius: "4px",
            height: "30px",
            px: "4px",
            width: "105px",
            flexShrink: 0,
          }}
          onClick={() => {
            window.open(
              "https://docs.futureagi.com/docs/tracing/auto/mcp/",
              "_blank",
            );
          }}
        >
          <SvgColor
            src="/assets/icons/agent/docs.svg"
            sx={{ height: 16, width: 16, mr: 1 }}
          />
          <Typography typography="s2" fontWeight="fontWeightMedium">
            View Docs
          </Typography>
        </Button>
      </Stack>

      {/* Setup Guide (hero — URL + IDE tabs + how it works) */}
      <ClientSetupGuide config={config} />

      {/* Tool Groups */}
      <ToolGroupSelector config={config} />
    </Box>
  );
}
