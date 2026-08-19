import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, IconButton, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { mcpConfig, mcpPythonSnippet } from "../../_mock/agentTypes";
import { SectionCard } from "../../components/primitives";

/**
 * "Your agent connects to us."
 *
 * The inverse of the connection form: instead of giving us an address we can
 * reach, the user points their own MCP client at the environment. It costs one
 * paste, works with any framework that speaks MCP, and — the actual reason it
 * earns a place here — needs no inbound route to the user's agent at all. An
 * agent on a laptop or behind a VPN can be tested this way and cannot be
 * tested any other way.
 */
const TABS = [
  { id: "mcp", label: "MCP client", icon: "solar:widget-4-linear" },
  { id: "python", label: "Python", icon: "solar:code-square-linear" },
];

export default function McpConnect({ env, type, onConnect, testing }) {
  const [tab, setTab] = useState("mcp");
  const snippet = tab === "mcp" ? mcpConfig(env) : mcpPythonSnippet(env);

  return (
    <SectionCard title="Connect your agent to this environment">
      <Box sx={{ p: 2.5 }}>
        <Typography sx={{ typography: "s2", color: "text.secondary", mb: 2 }}>
          We publish {env.name} as MCP tools. Point your agent at the address below and it
          can act in the environment — nothing of yours has to be reachable from our side.
        </Typography>

        <Stack direction="row" spacing={0.75} sx={{ mb: 1.5 }}>
          {TABS.map((t) => (
            <Button
              key={t.id}
              size="small"
              onClick={() => setTab(t.id)}
              startIcon={<Iconify icon={t.icon} width={14} />}
              sx={{
                typography: "s2", fontWeight: 600, px: 1.25,
                color: tab === t.id ? "primary.main" : "text.secondary",
                border: "1px solid",
                borderColor: tab === t.id ? "primary.main" : "divider",
                bgcolor: (th) => tab === t.id
                  ? alpha(th.palette.primary.main, th.palette.mode === "dark" ? 0.12 : 0.05)
                  : "transparent",
              }}
            >
              {t.label}
            </Button>
          ))}
        </Stack>

        <CodeBlock value={snippet} />

        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 1 }}>
          {tab === "mcp"
            ? "Works in Claude Desktop, Cursor, VS Code or any MCP-capable client."
            : "Any framework — the session yields the environment's tools."}
        </Typography>

        {/*
          The bit that makes this an eval rather than a sandbox. Worth stating
          on the screen, because a user who connects once and wanders will get
          results that cannot be compared between runs.
        */}
        <Stack
          direction="row"
          spacing={1.25}
          sx={{
            mt: 2.25, p: 1.75, borderRadius: 1.25,
            border: "1px solid", borderColor: "divider", bgcolor: "background.neutral",
          }}
        >
          <Iconify icon="solar:refresh-circle-linear" width={17} sx={{ color: "text.subtitle", flexShrink: 0, mt: "1px" }} />
          <Box>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              One session per scenario
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              We hand your agent a <Box component="span" sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}>Scenario-Id</Box>{" "}
              per task and restore the world in between, so every run is scored against the
              same starting point.
            </Typography>
          </Box>
        </Stack>
      </Box>

      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Button
          variant="contained"
          color="primary"
          disabled={testing}
          onClick={onConnect}
          startIcon={
            <Iconify icon={testing ? "solar:refresh-linear" : "solar:plug-circle-linear"} width={16} />
          }
          // The label beside it is long enough to squeeze this button until it
          // wraps mid-phrase; keep it on one line and let the text give way.
          sx={{ typography: "s2", fontWeight: 700, flexShrink: 0, whiteSpace: "nowrap" }}
        >
          {testing ? "Waiting for your agent…" : "Wait for connection"}
        </Button>
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          {testing
            ? "Listening for the first handshake from your client."
            : `Paste the config, start your agent, then we'll confirm ${type.label.toLowerCase()} is through.`}
        </Typography>
      </Stack>
    </SectionCard>
  );
}

McpConnect.propTypes = {
  env: PropTypes.object.isRequired,
  type: PropTypes.object.isRequired,
  onConnect: PropTypes.func,
  testing: PropTypes.bool,
};

function CodeBlock({ value }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };

  return (
    <Box sx={{ position: "relative" }}>
      <Box
        component="pre"
        sx={{
          m: 0, px: 1.75, py: 1.5, borderRadius: 1,
          border: "1px solid", borderColor: "divider",
          bgcolor: "background.neutral",
          overflowX: "auto",
          typography: "s2",
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          color: "text.primary",
          lineHeight: 1.65,
        }}
      >
        {value}
      </Box>
      <Tooltip title={copied ? "Copied" : "Copy"} arrow>
        <IconButton
          size="small"
          onClick={copy}
          sx={{ position: "absolute", top: 6, right: 6, bgcolor: "background.paper" }}
        >
          <Iconify
            icon={copied ? "solar:check-circle-bold" : "solar:copy-linear"}
            width={15}
            sx={{ color: copied ? "primary.main" : "text.subtitle" }}
          />
        </IconButton>
      </Tooltip>
    </Box>
  );
}
CodeBlock.propTypes = { value: PropTypes.string };
