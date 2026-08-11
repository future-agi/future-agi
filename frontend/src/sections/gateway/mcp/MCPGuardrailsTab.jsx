/* eslint-disable react/prop-types */
import React, { useState, useEffect, useMemo } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  FormControlLabel,
  IconButton,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { useUpdateMCPGuardrails } from "./hooks/useMCPConfig";
import { getMCPServerId } from "./utils";

const MCPGuardrailsTab = ({ config, mcpStatus, gatewayId }) => {
  const updateMutation = useUpdateMCPGuardrails();

  const mcpConfig = config?.mcp || {};
  const guardrailsConfig = mcpConfig.guardrails || {};

  const [form, setForm] = useState({
    enabled: false,
    blockedTools: [],
    allowedServers: [],
    validateInputs: false,
    validateOutputs: false,
    customPatterns: [],
    toolRateLimits: {},
  });
  const [isDirty, setIsDirty] = useState(false);
  const [newRateTool, setNewRateTool] = useState("");
  const [newRateLimit, setNewRateLimit] = useState("");

  useEffect(() => {
    setForm({
      enabled: guardrailsConfig.enabled || false,
      blockedTools: guardrailsConfig.blocked_tools || [],
      allowedServers: guardrailsConfig.allowed_servers || [],
      validateInputs: guardrailsConfig.validate_inputs || false,
      validateOutputs: guardrailsConfig.validate_outputs || false,
      customPatterns: guardrailsConfig.custom_patterns || [],
      toolRateLimits: guardrailsConfig.tool_rate_limits || {},
    });
    setIsDirty(false);
  }, [
    guardrailsConfig.enabled,
    guardrailsConfig.blocked_tools?.length,
    guardrailsConfig.allowed_servers?.length,
    guardrailsConfig.validate_inputs,
    guardrailsConfig.validate_outputs,
    guardrailsConfig.custom_patterns?.length,
    JSON.stringify(guardrailsConfig.tool_rate_limits),
  ]);

  const connectedServers = useMemo(() => {
    const statusServers = mcpStatus?.servers || [];
    return statusServers.map((s) => getMCPServerId(s)).filter(Boolean);
  }, [mcpStatus]);

  const handleToggle = (field) => (e) => {
    setForm((prev) => ({ ...prev, [field]: e.target.checked }));
    setIsDirty(true);
  };

  const handleAddRateLimit = () => {
    if (!newRateTool.trim() || !newRateLimit.trim()) return;
    const limit = parseInt(newRateLimit, 10);
    if (isNaN(limit) || limit <= 0) {
      enqueueSnackbar("Rate limit must be a positive number", {
        variant: "warning",
      });
      return;
    }
    setForm((prev) => ({
      ...prev,
      toolRateLimits: { ...prev.toolRateLimits, [newRateTool.trim()]: limit },
    }));
    setNewRateTool("");
    setNewRateLimit("");
    setIsDirty(true);
  };

  const handleRemoveRateLimit = (tool) => {
    setForm((prev) => {
      const updated = { ...prev.toolRateLimits };
      delete updated[tool];
      return { ...prev, toolRateLimits: updated };
    });
    setIsDirty(true);
  };

  const handleSave = () => {
    const guardrailConfig = {
      enabled: form.enabled,
      blocked_tools: form.blockedTools,
      allowed_servers: form.allowedServers,
      validate_inputs: form.validateInputs,
      validate_outputs: form.validateOutputs,
      custom_patterns: form.customPatterns,
      tool_rate_limits: form.toolRateLimits,
    };

    updateMutation.mutate(
      { gatewayId, config: guardrailConfig },
      {
        onSuccess: () => {
          enqueueSnackbar("MCP guardrails updated", { variant: "success" });
          setIsDirty(false);
        },
        onError: () => {
          enqueueSnackbar("Failed to update guardrails", { variant: "error" });
        },
      },
    );
  };

  return (
    <Box>
      {isDirty && (
        <Alert
          severity="warning"
          sx={{ mb: 2 }}
          action={
            <Button
              size="small"
              variant="contained"
              onClick={handleSave}
              disabled={updateMutation.isPending}
            >
              {updateMutation.isPending ? "Saving..." : "Save Changes"}
            </Button>
          }
        >
          You have unsaved changes.
        </Alert>
      )}

      <Stack spacing={3}>
        <Card>
          <CardContent>
            <Typography variant="subtitle1" fontWeight={700} gutterBottom>
              General Settings
            </Typography>
            <Stack spacing={1}>
              <FormControlLabel
                control={
                  <Switch
                    checked={form.enabled}
                    onChange={handleToggle("enabled")}
                  />
                }
                label="Enable MCP Guardrails"
              />
              <FormControlLabel
                control={
                  <Switch
                    checked={form.validateInputs}
                    onChange={handleToggle("validateInputs")}
                    disabled={!form.enabled}
                  />
                }
                label="Validate tool inputs (check for injection patterns)"
              />
              <FormControlLabel
                control={
                  <Switch
                    checked={form.validateOutputs}
                    onChange={handleToggle("validateOutputs")}
                    disabled={!form.enabled}
                  />
                }
                label="Validate tool outputs"
              />
            </Stack>
          </CardContent>
        </Card>

        <Card>
          <CardContent>
            <Typography variant="subtitle1" fontWeight={700} gutterBottom>
              Blocked Tools
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Tools in this list will be blocked from execution. Use the
              namespaced format (e.g., &quot;server_toolname&quot;).
            </Typography>
            <Autocomplete
              multiple
              freeSolo
              options={[]}
              value={form.blockedTools}
              onChange={(_, newValue) => {
                setForm((prev) => ({ ...prev, blockedTools: newValue }));
                setIsDirty(true);
              }}
              disabled={!form.enabled}
              renderTags={(value, getTagProps) =>
                value.map((option, index) => (
                  <Chip
                    label={option}
                    size="small"
                    color="error"
                    variant="outlined"
                    {...getTagProps({ index })}
                    key={option}
                  />
                ))
              }
              renderInput={(params) => (
                <TextField
                  {...params}
                  size="small"
                  placeholder="Type a tool name and press Enter..."
                />
              )}
            />
          </CardContent>
        </Card>

        <Card>
          <CardContent>
            <Typography variant="subtitle1" fontWeight={700} gutterBottom>
              Allowed Servers
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              If set, only tools from these servers will be accessible. Leave
              empty to allow all connected servers.
            </Typography>
            <Autocomplete
              multiple
              freeSolo
              options={connectedServers}
              value={form.allowedServers}
              onChange={(_, newValue) => {
                setForm((prev) => ({ ...prev, allowedServers: newValue }));
                setIsDirty(true);
              }}
              disabled={!form.enabled}
              renderTags={(value, getTagProps) =>
                value.map((option, index) => (
                  <Chip
                    label={option}
                    size="small"
                    color="primary"
                    variant="outlined"
                    {...getTagProps({ index })}
                    key={option}
                  />
                ))
              }
              renderInput={(params) => (
                <TextField
                  {...params}
                  size="small"
                  placeholder="Type a server ID or select from connected..."
                />
              )}
            />
          </CardContent>
        </Card>

        <Card>
          <CardContent>
            <Typography variant="subtitle1" fontWeight={700} gutterBottom>
              Custom Injection Patterns
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Add custom regex patterns to detect injection attacks in tool
              arguments and outputs. These are checked alongside the 8 built-in
              patterns.
            </Typography>
            <Autocomplete
              multiple
              freeSolo
              options={[]}
              value={form.customPatterns}
              onChange={(_, newValue) => {
                setForm((prev) => ({ ...prev, customPatterns: newValue }));
                setIsDirty(true);
              }}
              disabled={!form.enabled}
              renderTags={(value, getTagProps) =>
                value.map((option, index) => (
                  <Chip
                    label={option}
                    size="small"
                    color="warning"
                    variant="outlined"
                    {...getTagProps({ index })}
                    key={option}
                    sx={{ fontFamily: "monospace", fontSize: 12 }}
                  />
                ))
              }
              renderInput={(params) => (
                <TextField
                  {...params}
                  size="small"
                  placeholder="Type a regex pattern and press Enter..."
                  helperText="Example: (?i)\bpassword\b or .*secret.*"
                />
              )}
            />
          </CardContent>
        </Card>

        <Card>
          <CardContent>
            <Typography variant="subtitle1" fontWeight={700} gutterBottom>
              Per-Tool Rate Limits
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Limit how many times a specific tool can be called per minute.
            </Typography>

            {Object.keys(form.toolRateLimits).length > 0 && (
              <Stack spacing={1} sx={{ mb: 2 }}>
                {Object.entries(form.toolRateLimits).map(([tool, limit]) => (
                  <Stack
                    key={tool}
                    direction="row"
                    alignItems="center"
                    spacing={1}
                    sx={{
                      px: 1.5,
                      py: 0.75,
                      borderRadius: 1,
                      bgcolor: "action.hover",
                    }}
                  >
                    <Typography
                      variant="body2"
                      fontWeight={600}
                      sx={{ fontFamily: "monospace", fontSize: 13, flex: 1 }}
                    >
                      {tool}
                    </Typography>
                    <Chip
                      label={`${limit}/min`}
                      size="small"
                      color="info"
                      variant="outlined"
                    />
                    <IconButton
                      size="small"
                      onClick={() => handleRemoveRateLimit(tool)}
                      disabled={!form.enabled}
                    >
                      <Iconify icon="mdi:close" width={16} />
                    </IconButton>
                  </Stack>
                ))}
              </Stack>
            )}

            <Stack direction="row" spacing={1} alignItems="flex-start">
              <TextField
                size="small"
                label="Tool Name"
                value={newRateTool}
                onChange={(e) => setNewRateTool(e.target.value)}
                disabled={!form.enabled}
                placeholder="server_toolname"
                sx={{ flex: 2 }}
              />
              <TextField
                size="small"
                label="Max/min"
                value={newRateLimit}
                onChange={(e) => setNewRateLimit(e.target.value)}
                disabled={!form.enabled}
                placeholder="10"
                type="number"
                sx={{ flex: 1 }}
              />
              <Button
                variant="outlined"
                size="small"
                onClick={handleAddRateLimit}
                disabled={
                  !form.enabled || !newRateTool.trim() || !newRateLimit.trim()
                }
                sx={{ mt: 0.5 }}
              >
                Add
              </Button>
            </Stack>
          </CardContent>
        </Card>
      </Stack>

      {!isDirty && (
        <Box sx={{ mt: 3, display: "flex", justifyContent: "flex-end" }}>
          <Button
            variant="contained"
            startIcon={<Iconify icon="mdi:content-save" width={18} />}
            onClick={handleSave}
            disabled={updateMutation.isPending}
          >
            {updateMutation.isPending ? "Saving..." : "Save Guardrails"}
          </Button>
        </Box>
      )}
    </Box>
  );
};

export default MCPGuardrailsTab;
