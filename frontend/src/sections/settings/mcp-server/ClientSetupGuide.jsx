import React, { useState, useMemo } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Card,
  Chip,
  IconButton,
  InputAdornment,
  Tab,
  Tabs,
  TextField,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "notistack";
import { deploymentMcpUrl } from "src/utils/deploymentMcpUrl";
import { buildMcpClientConfigs } from "./clientSetupSnippets";

CopyButton.propTypes = {
  text: PropTypes.string,
};

function CopyButton({ text }) {
  const theme = useTheme();

  const handleCopy = () => {
    navigator.clipboard.writeText(text).then(() => {
      enqueueSnackbar("Copied to clipboard", { variant: "success" });
    });
  };

  return (
    <Tooltip title="Copy">
      <IconButton
        size="small"
        onClick={handleCopy}
        sx={{
          position: "absolute",
          top: 8,
          right: 8,
          color: theme.palette.text.secondary,
          "&:hover": { bgcolor: alpha(theme.palette.text.primary, 0.08) },
        }}
      >
        <Iconify icon="ph:copy" width={16} />
      </IconButton>
    </Tooltip>
  );
}

TabPanel.propTypes = {
  children: PropTypes.node,
  value: PropTypes.number,
  index: PropTypes.number,
};

function TabPanel({ children, value, index }) {
  if (value !== index) return null;
  return <Box sx={{ pt: 2 }}>{children}</Box>;
}

const STEPS = [
  {
    title: "Install",
    desc: "Click the button below or copy the config manually",
  },
  {
    title: "Authorize",
    desc: "OAuth login opens in your browser automatically",
  },
  {
    title: "Start using",
    desc: "Ask your AI assistant about your evaluations, traces, and more",
  },
];

ClientSetupGuide.propTypes = {
  config: PropTypes.object,
};

export default function ClientSetupGuide({ config }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const [tab, setTab] = useState(0);

  // Dark mode = monochrome (white primary), Light mode = purple primary
  const accent = theme.palette.primary.main;
  const accentContrast = theme.palette.primary.contrastText;

  // The endpoint this deployment really serves (GET /mcp/config/ mcp_url,
  // the same base its OAuth metadata and 401 advertise). When the server
  // reports none, this app's own API base from config plus /mcp: never a
  // hard-coded production host (ruling 380).
  const mcpUrl = config?.mcp_url || deploymentMcpUrl();

  const handleCopyUrl = () => {
    navigator.clipboard.writeText(mcpUrl).then(() => {
      enqueueSnackbar("MCP URL copied to clipboard", { variant: "success" });
    });
  };

  const configs = useMemo(() => buildMcpClientConfigs(mcpUrl), [mcpUrl]);

  const deepLinks = useMemo(() => {
    // Cursor expects base64-encoded JSON config
    // https://cursor.com/docs/context/mcp/install-links
    const cursorConfig = btoa(JSON.stringify({ type: "http", url: mcpUrl }));

    // VS Code uses vscode:mcp/install?<url-encoded JSON with name+config>
    // https://code.visualstudio.com/api/extension-guides/ai/mcp
    const vscodeConfig = encodeURIComponent(
      JSON.stringify({ name: "futureagi", type: "http", url: mcpUrl }),
    );

    return {
      cursor: `cursor://anysphere.cursor-deeplink/mcp/install?name=futureagi&config=${cursorConfig}`,
      vscode: `vscode:mcp/install?${vscodeConfig}`,
    };
  }, [mcpUrl]);

  const handleOpenDeepLink = (url, label) => {
    window.open(url, "_self");
    enqueueSnackbar(
      `Opening ${label}... If nothing happened, make sure ${label} is installed.`,
      { variant: "info", autoHideDuration: 5000 },
    );
  };

  const handleCopyCommand = (text, label) => {
    navigator.clipboard.writeText(text).then(() => {
      enqueueSnackbar(`${label} command copied — paste it in your terminal`, {
        variant: "success",
      });
    });
  };

  const tabItems = [
    {
      label: "Cursor",
      icon: "simple-icons:cursor",
      filePath: "~/.cursor/mcp.json",
      code: configs.cursor,
      deepLink: deepLinks.cursor,
      actionLabel: "Open in Cursor",
      actionIcon: "ph:arrow-square-out-bold",
    },
    {
      label: "Claude Code",
      icon: "simple-icons:anthropic",
      filePath: "Run in terminal",
      code: configs.claudeCode,
      copyCommand: true,
      actionLabel: "Copy command",
      actionIcon: "ph:terminal-bold",
    },
    {
      label: "VS Code",
      icon: "simple-icons:visualstudiocode",
      filePath: ".vscode/settings.json",
      code: configs.vscode,
      deepLink: deepLinks.vscode,
      actionLabel: "Open in VS Code",
      actionIcon: "ph:arrow-square-out-bold",
    },
    {
      label: "Claude Desktop",
      icon: "simple-icons:anthropic",
      filePath: "claude_desktop_config.json",
      code: configs.claudeDesktop,
    },
    {
      label: "Windsurf",
      icon: "ph:wind-bold",
      filePath: "~/.codeium/windsurf/mcp_config.json",
      code: configs.windsurf,
    },
  ];

  // Theme-aware styles
  const codeBlockSx = {
    bgcolor: isDark ? alpha("#000", 0.3) : "#1e1e1e",
    color: isDark ? theme.palette.text.primary : "#d4d4d4",
    p: 2,
    borderRadius: 1,
    fontFamily: "monospace",
    fontSize: 13,
    overflow: "auto",
    whiteSpace: "pre",
    position: "relative",
    lineHeight: 1.6,
    border: "1px solid",
    borderColor: "divider",
  };

  const actionBannerSx = {
    display: "flex",
    alignItems: "center",
    gap: 1.5,
    p: 2,
    mb: 2,
    borderRadius: 1,
    border: "1px solid",
    borderColor: alpha(accent, isDark ? 0.3 : 0.4),
    bgcolor: alpha(accent, isDark ? 0.08 : 0.04),
  };

  if (!mcpUrl) {
    return (
      <Alert severity="info" sx={{ mb: 3 }} data-testid="mcp-url-unknown">
        This deployment does not report its MCP endpoint. Set BASE_URL to the
        API&apos;s public URL to connect an IDE.
      </Alert>
    );
  }

  return (
    <Card variant="outlined" sx={{ p: 3, mb: 3 }}>
      {/* Header */}
      <Typography
        sx={{
          typography: "m2",
          fontWeight: "fontWeightSemiBold",
          color: "text.primary",
          mb: 0.5,
        }}
      >
        Connect Your IDE
      </Typography>
      <Typography sx={{ typography: "s1", color: "text.secondary", mb: 2.5 }}>
        All you need is this URL. Authentication happens automatically via
        OAuth.
      </Typography>

      {/* MCP URL */}
      <TextField
        value={mcpUrl}
        fullWidth
        size="small"
        inputProps={{ "aria-label": "MCP server URL" }}
        InputProps={{
          readOnly: true,
          sx: {
            fontFamily: "monospace",
            fontSize: 13,
            bgcolor: alpha(theme.palette.text.primary, 0.04),
          },
          endAdornment: (
            <InputAdornment position="end">
              <Tooltip title="Copy URL">
                <IconButton size="small" onClick={handleCopyUrl}>
                  <Iconify icon="ph:copy" width={18} />
                </IconButton>
              </Tooltip>
            </InputAdornment>
          ),
        }}
        sx={{ maxWidth: 560, mb: 3 }}
      />

      {/* How it works — 3 steps */}
      <Box sx={{ display: "flex", gap: 2, mb: 3, flexWrap: "wrap" }}>
        {STEPS.map((step, i) => (
          <Box
            key={i}
            sx={{
              flex: "1 1 160px",
              display: "flex",
              alignItems: "flex-start",
              gap: 1.5,
              p: 1.5,
              borderRadius: 1,
              border: "1px solid",
              borderColor: "divider",
            }}
          >
            <Box
              sx={{
                width: 28,
                height: 28,
                borderRadius: "50%",
                bgcolor: accent,
                color: accentContrast,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                flexShrink: 0,
                fontSize: 13,
                fontWeight: 700,
              }}
            >
              {i + 1}
            </Box>
            <Box>
              <Typography
                sx={{
                  typography: "s1",
                  fontWeight: "fontWeightSemiBold",
                  color: "text.primary",
                  lineHeight: 1.3,
                }}
              >
                {step.title}
              </Typography>
              <Typography
                sx={{
                  typography: "s2",
                  color: "text.secondary",
                  lineHeight: 1.4,
                  mt: 0.25,
                }}
              >
                {step.desc}
              </Typography>
            </Box>
          </Box>
        ))}
      </Box>

      {/* IDE Tabs */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          borderBottom: 1,
          borderColor: "divider",
        }}
      >
        <Tabs
          value={tab}
          onChange={(_, v) => setTab(v)}
          sx={{
            "& .MuiTab-root": {
              textTransform: "none",
              minHeight: 40,
              fontWeight: 500,
              gap: 0.75,
            },
          }}
        >
          {tabItems.map((item, i) => (
            <Tab
              key={i}
              icon={<Iconify icon={item.icon} width={16} />}
              iconPosition="start"
              label={item.label}
            />
          ))}
        </Tabs>
        <Chip
          icon={<Iconify icon="ph:shield-check" width={14} />}
          label="OAuth 2.0"
          size="small"
          color="success"
          variant="outlined"
          sx={{ mr: 1 }}
        />
      </Box>

      {tabItems.map((item, i) => (
        <TabPanel key={i} value={tab} index={i}>
          {/* One-click install banner */}
          {(item.deepLink || item.copyCommand) && (
            <Box sx={actionBannerSx}>
              <Iconify
                icon={item.icon}
                width={24}
                sx={{ color: accent, flexShrink: 0 }}
              />
              <Box flex={1}>
                <Typography
                  sx={{
                    typography: "s1",
                    fontWeight: "fontWeightSemiBold",
                    color: "text.primary",
                  }}
                >
                  {item.deepLink
                    ? `One-click install for ${item.label}`
                    : "Run this command in your terminal"}
                </Typography>
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                  {item.deepLink
                    ? `This will open ${item.label} and add FutureAGI as an MCP server automatically.`
                    : "This registers FutureAGI as an MCP server in Claude Code."}
                </Typography>
              </Box>
              <Button
                variant="contained"
                size="small"
                startIcon={<Iconify icon={item.actionIcon} width={16} />}
                onClick={() =>
                  item.deepLink
                    ? handleOpenDeepLink(item.deepLink, item.label)
                    : handleCopyCommand(item.code, item.label)
                }
                sx={{
                  flexShrink: 0,
                  textTransform: "none",
                  fontWeight: 600,
                  bgcolor: accent,
                  color: accentContrast,
                  "&:hover": { bgcolor: alpha(accent, 0.85) },
                }}
              >
                {item.actionLabel}
              </Button>
            </Box>
          )}

          {/* Manual config */}
          <Typography
            sx={{
              typography: "s2",
              color: "text.disabled",
              mb: 0.5,
              fontWeight: "fontWeightMedium",
            }}
          >
            {item.deepLink || item.copyCommand
              ? "Or add manually:"
              : "Add to your config file:"}
          </Typography>
          <Typography
            sx={{
              typography: "s2",
              color: "text.disabled",
              mb: 1,
              fontFamily: "monospace",
              fontSize: 12,
            }}
          >
            {item.filePath}
          </Typography>
          <Box sx={codeBlockSx}>
            <CopyButton text={item.code} />
            {item.code}
          </Box>
        </TabPanel>
      ))}
    </Card>
  );
}
