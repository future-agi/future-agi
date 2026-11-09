/* eslint-disable react/prop-types */
import React, { useState } from "react";
import {
  Box,
  Stack,
  Typography,
  TextField,
  Switch,
  FormControlLabel,
  Paper,
  Divider,
  Alert,
  MenuItem,
  Chip,
  IconButton,
  Button,
} from "@mui/material";
import { Icon } from "@iconify/react";
import { hasStoredCredential } from "../mcp/utils";
import { GATEWAY_MONO } from "../utils/fonts";
import { mcpGuardrailRows } from "../mcp/guardrails/storedGuardrails";
import RecordedOnly from "./ui/RecordedOnly";

// The org's MCP guardrails are not options: the gateway applies
// only its own MCP tool guard. What is stored shows read-only beside the chip, and
// the editor saves it back unchanged. The servers stay editable.
const MCPConfigTab = ({ mcp, onChange }) => {
  const config = mcp || {};
  const servers = config.servers || {};

  // Server form state
  const [newServerId, setNewServerId] = useState("");
  const [newServerUrl, setNewServerUrl] = useState("");
  const [newServerAuthType, setNewServerAuthType] = useState("none");
  const [newServerAuthToken, setNewServerAuthToken] = useState("");
  const [newServerAuthHeader, setNewServerAuthHeader] = useState("");
  const [newServerAuthKey, setNewServerAuthKey] = useState("");

  const update = (key, value) => onChange({ ...config, [key]: value });

  // Server handlers
  const addServer = () => {
    if (!newServerId.trim() || !newServerUrl.trim()) return;
    // An org config never starts a local process: stdio servers come only
    // from the operator's gateway config.
    const serverCfg = {
      url: newServerUrl.trim(),
      transport: "http",
    };
    if (newServerAuthType !== "none") {
      serverCfg.auth = { type: newServerAuthType };
      if (newServerAuthType === "bearer" && newServerAuthToken.trim()) {
        serverCfg.auth.token = newServerAuthToken.trim();
      }
      if (newServerAuthType === "api_key") {
        if (newServerAuthHeader.trim())
          serverCfg.auth.header = newServerAuthHeader.trim();
        if (newServerAuthKey.trim())
          serverCfg.auth.key = newServerAuthKey.trim();
      }
    }
    update("servers", { ...servers, [newServerId.trim()]: serverCfg });
    setNewServerId("");
    setNewServerUrl("");
    setNewServerAuthType("none");
    setNewServerAuthToken("");
    setNewServerAuthHeader("");
    setNewServerAuthKey("");
  };

  const removeServer = (id) => {
    const updated = { ...servers };
    delete updated[id];
    update("servers", updated);
  };

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
        <Icon icon="mdi:connection" width={24} />
        <Typography variant="h6">MCP Configuration</Typography>
      </Stack>

      <FormControlLabel
        control={
          <Switch
            checked={config.enabled || false}
            onChange={(e) => update("enabled", e.target.checked)}
          />
        }
        label="Enable per-org MCP config"
        sx={{ mb: 2 }}
      />

      {config.enabled && (
        <Stack spacing={3}>
          {/* ===== MCP SERVERS ===== */}
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ mb: 1 }}
            >
              <Icon icon="mdi:server-network" width={20} />
              <Typography variant="subtitle1" fontWeight="bold">
                MCP Servers
              </Typography>
            </Stack>

            <Alert severity="info" sx={{ mb: 2 }}>
              Configure upstream MCP servers for this organization. These
              override or extend the gateway-level MCP servers.
            </Alert>

            {/* Existing servers */}
            {Object.keys(servers).length > 0 && (
              <Stack spacing={1} sx={{ mb: 2 }}>
                {Object.entries(servers).map(([id, srv]) => (
                  <Paper
                    key={id}
                    variant="outlined"
                    sx={{ p: 1.5, bgcolor: "action.hover" }}
                  >
                    <Stack
                      direction="row"
                      justifyContent="space-between"
                      alignItems="center"
                    >
                      <Stack spacing={0.5}>
                        <Stack direction="row" spacing={1} alignItems="center">
                          <Chip
                            label={id}
                            size="small"
                            color="primary"
                            variant="outlined"
                          />
                          <Chip
                            label={srv.transport || "http"}
                            size="small"
                            variant="outlined"
                          />
                          {srv.auth?.type && srv.auth.type !== "none" && (
                            <Chip
                              label={srv.auth.type}
                              size="small"
                              color="warning"
                              variant="outlined"
                            />
                          )}
                          {((srv.auth?.type && srv.auth.type !== "none") ||
                            srv.credential) && (
                            <Chip
                              label={
                                hasStoredCredential(srv)
                                  ? "Credential: set"
                                  : "Credential: not set"
                              }
                              size="small"
                              variant="outlined"
                            />
                          )}
                        </Stack>
                        <Typography
                          variant="caption"
                          sx={{ fontFamily: GATEWAY_MONO }}
                        >
                          {srv.url}
                        </Typography>
                      </Stack>
                      <IconButton size="small" onClick={() => removeServer(id)}>
                        <Icon
                          icon="mdi:delete-outline"
                          width={18}
                          color="#d32f2f"
                        />
                      </IconButton>
                    </Stack>
                  </Paper>
                ))}
              </Stack>
            )}

            {/* Add server form */}
            <Divider sx={{ my: 1 }} />
            <Typography variant="body2" fontWeight={500} sx={{ mt: 1, mb: 1 }}>
              Add Server
            </Typography>
            <Stack spacing={1.5}>
              <Stack direction="row" spacing={1}>
                <TextField
                  size="small"
                  label="Server ID"
                  placeholder="e.g. my-tools"
                  value={newServerId}
                  onChange={(e) => setNewServerId(e.target.value)}
                  sx={{ width: 180 }}
                />
                <TextField
                  size="small"
                  label="URL"
                  placeholder="http://localhost:3001/mcp"
                  value={newServerUrl}
                  onChange={(e) => setNewServerUrl(e.target.value)}
                  sx={{ flex: 1 }}
                />
              </Stack>
              <Stack direction="row" spacing={1}>
                <TextField
                  select
                  size="small"
                  label="Auth Type"
                  value={newServerAuthType}
                  onChange={(e) => setNewServerAuthType(e.target.value)}
                  sx={{ width: 140 }}
                >
                  <MenuItem value="none">None</MenuItem>
                  <MenuItem value="bearer">Bearer Token</MenuItem>
                  <MenuItem value="api_key">API Key</MenuItem>
                </TextField>
              </Stack>
              {newServerAuthType === "bearer" && (
                <TextField
                  size="small"
                  label="Bearer Token"
                  type="password"
                  value={newServerAuthToken}
                  onChange={(e) => setNewServerAuthToken(e.target.value)}
                  sx={{ width: 350 }}
                />
              )}
              {newServerAuthType === "api_key" && (
                <Stack direction="row" spacing={1}>
                  <TextField
                    size="small"
                    label="Header Name"
                    placeholder="X-API-Key"
                    value={newServerAuthHeader}
                    onChange={(e) => setNewServerAuthHeader(e.target.value)}
                    sx={{ width: 180 }}
                  />
                  <TextField
                    size="small"
                    label="API Key"
                    type="password"
                    value={newServerAuthKey}
                    onChange={(e) => setNewServerAuthKey(e.target.value)}
                    sx={{ width: 250 }}
                  />
                </Stack>
              )}
              <Button
                size="small"
                variant="outlined"
                onClick={addServer}
                disabled={!newServerId.trim() || !newServerUrl.trim()}
                sx={{ alignSelf: "flex-start" }}
              >
                Add Server
              </Button>
            </Stack>
          </Paper>
        </Stack>
      )}
      <RecordedOnly
        ids={["mcp.guardrails"]}
        rows={mcpGuardrailRows(config.guardrails)}
        title="MCP guardrails"
        testId="mcp-guardrails-recorded-only"
        sx={{ mt: 3 }}
      />
    </Box>
  );
};

export default MCPConfigTab;
