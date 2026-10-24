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

const MCPConfigTab = ({ mcp, onChange }) => {
  const config = mcp || {};
  const servers = config.servers || {};
  const guardrails = config.guardrails || {};
  const toolRateLimits = guardrails.tool_rate_limits || {};

  // Server form state
  const [newServerId, setNewServerId] = useState("");
  const [newServerUrl, setNewServerUrl] = useState("");
  const [newServerAuthType, setNewServerAuthType] = useState("none");
  const [newServerAuthToken, setNewServerAuthToken] = useState("");
  const [newServerAuthHeader, setNewServerAuthHeader] = useState("");
  const [newServerAuthKey, setNewServerAuthKey] = useState("");

  // Guardrails state
  const [newBlockedTool, setNewBlockedTool] = useState("");
  const [newAllowedServer, setNewAllowedServer] = useState("");

  // Tool rate limits state
  const [newRateLimitTool, setNewRateLimitTool] = useState("");
  const [newRateLimitRPM, setNewRateLimitRPM] = useState("");

  const update = (key, value) => onChange({ ...config, [key]: value });
  const updateGuardrails = (key, value) =>
    update("guardrails", { ...guardrails, [key]: value });

  const blockedTools = guardrails.blocked_tools || [];
  const allowedServers = guardrails.allowed_servers || [];

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

  // Guardrail list handlers
  const addToList = (listKey, value, setter) => {
    if (!value.trim()) return;
    const current = guardrails[listKey] || [];
    if (!current.includes(value.trim())) {
      updateGuardrails(listKey, [...current, value.trim()]);
    }
    setter("");
  };

  const removeFromList = (listKey, idx) => {
    const current = [...(guardrails[listKey] || [])];
    current.splice(idx, 1);
    updateGuardrails(listKey, current);
  };

  // Tool rate limit handlers
  const addToolRateLimit = () => {
    if (!newRateLimitTool.trim() || !newRateLimitRPM) return;
    updateGuardrails("tool_rate_limits", {
      ...toolRateLimits,
      [newRateLimitTool.trim()]: Number(newRateLimitRPM),
    });
    setNewRateLimitTool("");
    setNewRateLimitRPM("");
  };

  const removeToolRateLimit = (tool) => {
    const updated = { ...toolRateLimits };
    delete updated[tool];
    updateGuardrails("tool_rate_limits", updated);
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
                          sx={{ fontFamily: "monospace" }}
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

          {/* ===== MCP GUARDRAILS ===== */}
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ mb: 1 }}
            >
              <Icon icon="mdi:shield-check-outline" width={20} />
              <Typography variant="subtitle1" fontWeight="bold">
                MCP Guardrails
              </Typography>
            </Stack>

            <Alert severity="info" sx={{ mb: 2 }}>
              Control which MCP tools can be used, restrict servers, and toggle
              input/output validation for this organization.
            </Alert>

            <FormControlLabel
              control={
                <Switch
                  checked={guardrails.enabled || false}
                  onChange={(e) =>
                    updateGuardrails("enabled", e.target.checked)
                  }
                />
              }
              label="Enable MCP guardrails"
              sx={{ mb: 2 }}
            />

            {guardrails.enabled && (
              <Stack spacing={2}>
                <Stack direction="row" spacing={2}>
                  <FormControlLabel
                    control={
                      <Switch
                        checked={guardrails.validate_inputs || false}
                        onChange={(e) =>
                          updateGuardrails("validate_inputs", e.target.checked)
                        }
                      />
                    }
                    label="Validate Inputs"
                  />
                  <FormControlLabel
                    control={
                      <Switch
                        checked={guardrails.validate_outputs || false}
                        onChange={(e) =>
                          updateGuardrails("validate_outputs", e.target.checked)
                        }
                      />
                    }
                    label="Validate Outputs"
                  />
                </Stack>

                <Divider />

                {/* Blocked Tools */}
                <Typography variant="body2" fontWeight={500}>
                  Blocked Tools
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  MCP tools that are blocked for this organization.
                </Typography>
                <Stack direction="row" spacing={0.5} flexWrap="wrap">
                  {blockedTools.map((t, i) => (
                    <Chip
                      key={i}
                      label={t}
                      size="small"
                      color="error"
                      variant="outlined"
                      onDelete={() => removeFromList("blocked_tools", i)}
                    />
                  ))}
                </Stack>
                <Stack direction="row" spacing={1}>
                  <TextField
                    size="small"
                    placeholder="Tool name (e.g. server_toolname)"
                    value={newBlockedTool}
                    onChange={(e) => setNewBlockedTool(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        addToList(
                          "blocked_tools",
                          newBlockedTool,
                          setNewBlockedTool,
                        );
                      }
                    }}
                    sx={{ width: 300 }}
                  />
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() =>
                      addToList(
                        "blocked_tools",
                        newBlockedTool,
                        setNewBlockedTool,
                      )
                    }
                  >
                    Add
                  </Button>
                </Stack>

                <Divider />

                {/* Allowed Servers */}
                <Typography variant="body2" fontWeight={500}>
                  Allowed Servers
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  Restrict which upstream MCP servers this org can access. Leave
                  empty to allow all.
                </Typography>
                <Stack direction="row" spacing={0.5} flexWrap="wrap">
                  {allowedServers.map((s, i) => (
                    <Chip
                      key={i}
                      label={s}
                      size="small"
                      color="success"
                      variant="outlined"
                      onDelete={() => removeFromList("allowed_servers", i)}
                    />
                  ))}
                </Stack>
                <Stack direction="row" spacing={1}>
                  <TextField
                    size="small"
                    placeholder="Server ID"
                    value={newAllowedServer}
                    onChange={(e) => setNewAllowedServer(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        addToList(
                          "allowed_servers",
                          newAllowedServer,
                          setNewAllowedServer,
                        );
                      }
                    }}
                    sx={{ width: 250 }}
                  />
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() =>
                      addToList(
                        "allowed_servers",
                        newAllowedServer,
                        setNewAllowedServer,
                      )
                    }
                  >
                    Add
                  </Button>
                </Stack>
              </Stack>
            )}
          </Paper>

          {/* ===== TOOL RATE LIMITS ===== */}
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ mb: 1 }}
            >
              <Icon icon="mdi:speedometer" width={20} />
              <Typography variant="subtitle1" fontWeight="bold">
                Tool Rate Limits
              </Typography>
            </Stack>

            <Alert severity="info" sx={{ mb: 2 }}>
              Set per-tool rate limits (requests per minute) for MCP tool calls.
            </Alert>

            {Object.keys(toolRateLimits).length > 0 && (
              <Stack spacing={1} sx={{ mb: 2 }}>
                {Object.entries(toolRateLimits).map(([tool, rpm]) => (
                  <Stack
                    key={tool}
                    direction="row"
                    spacing={1}
                    alignItems="center"
                  >
                    <Chip
                      label={tool}
                      size="small"
                      color="primary"
                      variant="outlined"
                    />
                    <Typography variant="body2">{rpm} RPM</Typography>
                    <IconButton
                      size="small"
                      onClick={() => removeToolRateLimit(tool)}
                    >
                      <Icon
                        icon="mdi:delete-outline"
                        width={16}
                        color="#d32f2f"
                      />
                    </IconButton>
                  </Stack>
                ))}
              </Stack>
            )}

            <Stack direction="row" spacing={1}>
              <TextField
                size="small"
                placeholder="Tool name"
                value={newRateLimitTool}
                onChange={(e) => setNewRateLimitTool(e.target.value)}
                sx={{ width: 250 }}
              />
              <TextField
                size="small"
                type="number"
                placeholder="RPM"
                value={newRateLimitRPM}
                onChange={(e) => setNewRateLimitRPM(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addToolRateLimit();
                  }
                }}
                sx={{ width: 100 }}
              />
              <Button
                size="small"
                variant="outlined"
                onClick={addToolRateLimit}
                disabled={!newRateLimitTool.trim() || !newRateLimitRPM}
              >
                Add
              </Button>
            </Stack>
          </Paper>
        </Stack>
      )}
    </Box>
  );
};

export default MCPConfigTab;
