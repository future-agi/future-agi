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

const SecurityConfigTab = ({
  ipAcl,
  privacy,
  toolPolicy,
  onIpAclChange,
  onPrivacyChange,
  onToolPolicyChange,
}) => {
  const aclConfig = ipAcl || {};
  const privacyConfig = privacy || {};
  const toolConfig = toolPolicy || {};

  // IP ACL state
  const [newAllowIP, setNewAllowIP] = useState("");
  const [newDenyIP, setNewDenyIP] = useState("");

  // Privacy state
  const [newPatternName, setNewPatternName] = useState("");
  const [newPatternRegex, setNewPatternRegex] = useState("");

  // Tool policy state
  const [newAllowTool, setNewAllowTool] = useState("");
  const [newDenyTool, setNewDenyTool] = useState("");

  // IP ACL handlers
  const updateAcl = (key, value) =>
    onIpAclChange({ ...aclConfig, [key]: value });
  const allowList = aclConfig.allow || [];
  const denyList = aclConfig.deny || [];

  const addIP = (listKey, value, setter) => {
    if (!value.trim()) return;
    const current = aclConfig[listKey] || [];
    if (!current.includes(value.trim())) {
      updateAcl(listKey, [...current, value.trim()]);
    }
    setter("");
  };

  const removeIP = (listKey, idx) => {
    const current = [...(aclConfig[listKey] || [])];
    current.splice(idx, 1);
    updateAcl(listKey, current);
  };

  // Privacy handlers
  const updatePrivacy = (key, value) =>
    onPrivacyChange({ ...privacyConfig, [key]: value });
  const patterns =
    privacyConfig.redact_patterns || privacyConfig.redactPatterns || [];

  const addPattern = () => {
    if (!newPatternName.trim() || !newPatternRegex.trim()) return;
    const updated = [
      ...patterns,
      { name: newPatternName.trim(), pattern: newPatternRegex.trim() },
    ];
    updatePrivacy("redact_patterns", updated);
    setNewPatternName("");
    setNewPatternRegex("");
  };

  const removePattern = (idx) => {
    const updated = [...patterns];
    updated.splice(idx, 1);
    updatePrivacy("redact_patterns", updated);
  };

  // Tool policy handlers
  const updateTool = (key, value) =>
    onToolPolicyChange({ ...toolConfig, [key]: value });
  const toolAllow = toolConfig.allow || [];
  const toolDeny = toolConfig.deny || [];

  const addTool = (listKey, value, setter) => {
    if (!value.trim()) return;
    const current = toolConfig[listKey] || [];
    if (!current.includes(value.trim())) {
      updateTool(listKey, [...current, value.trim()]);
    }
    setter("");
  };

  const removeTool = (listKey, idx) => {
    const current = [...(toolConfig[listKey] || [])];
    current.splice(idx, 1);
    updateTool(listKey, current);
  };

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
        <Icon icon="solar:shield-keyhole-linear" width={24} />
        <Typography variant="h6">Security</Typography>
      </Stack>

      {/* ===== IP ACL ===== */}
      <Paper variant="outlined" sx={{ p: 2, mb: 3 }}>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
          <Icon icon="solar:global-linear" width={20} />
          <Typography variant="subtitle1" fontWeight="600">
            IP Access Control
          </Typography>
        </Stack>

        <Alert severity="info" sx={{ mb: 2 }}>
          Restrict API access by client IP address. Deny list takes precedence
          over allow list. Supports CIDR notation (e.g. 10.0.0.0/8).
        </Alert>

        <FormControlLabel
          control={
            <Switch
              checked={aclConfig.enabled || false}
              onChange={(e) => updateAcl("enabled", e.target.checked)}
            />
          }
          label="Enable IP ACL"
          sx={{ mb: 2 }}
        />

        {aclConfig.enabled && (
          <Stack spacing={2}>
            <Typography variant="body2" fontWeight={500}>
              Allow List
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Only IPs/CIDRs in this list are allowed. Leave empty to allow all.
            </Typography>
            <Stack direction="row" spacing={0.5} flexWrap="wrap">
              {allowList.map((ip, i) => (
                <Chip
                  key={i}
                  label={ip}
                  size="small"
                  color="success"
                  variant="outlined"
                  onDelete={() => removeIP("allow", i)}
                />
              ))}
            </Stack>
            <Stack direction="row" spacing={1}>
              <TextField
                size="small"
                placeholder="IP or CIDR"
                value={newAllowIP}
                onChange={(e) => setNewAllowIP(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addIP("allow", newAllowIP, setNewAllowIP);
                  }
                }}
                sx={{ width: 220 }}
              />
              <Button
                size="small"
                variant="outlined"
                onClick={() => addIP("allow", newAllowIP, setNewAllowIP)}
              >
                Add
              </Button>
            </Stack>

            <Divider />

            <Typography variant="body2" fontWeight={500}>
              Deny List
            </Typography>
            <Typography variant="caption" color="text.secondary">
              IPs/CIDRs in this list are always blocked, even if in the allow
              list.
            </Typography>
            <Stack direction="row" spacing={0.5} flexWrap="wrap">
              {denyList.map((ip, i) => (
                <Chip
                  key={i}
                  label={ip}
                  size="small"
                  color="error"
                  variant="outlined"
                  onDelete={() => removeIP("deny", i)}
                />
              ))}
            </Stack>
            <Stack direction="row" spacing={1}>
              <TextField
                size="small"
                placeholder="IP or CIDR"
                value={newDenyIP}
                onChange={(e) => setNewDenyIP(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addIP("deny", newDenyIP, setNewDenyIP);
                  }
                }}
                sx={{ width: 220 }}
              />
              <Button
                size="small"
                variant="outlined"
                onClick={() => addIP("deny", newDenyIP, setNewDenyIP)}
              >
                Add
              </Button>
            </Stack>
          </Stack>
        )}
      </Paper>

      {/* ===== PRIVACY ===== */}
      <Paper variant="outlined" sx={{ p: 2, mb: 3 }}>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
          <Icon icon="solar:eye-closed-linear" width={20} />
          <Typography variant="subtitle1" fontWeight="600">
            Privacy & PII Redaction
          </Typography>
        </Stack>

        <Alert severity="info" sx={{ mb: 2 }}>
          Control PII redaction in request/response logging. Define custom regex
          patterns to mask sensitive data.
        </Alert>

        <FormControlLabel
          control={
            <Switch
              checked={privacyConfig.enabled || false}
              onChange={(e) => updatePrivacy("enabled", e.target.checked)}
            />
          }
          label="Enable privacy mode"
          sx={{ mb: 2 }}
        />

        {privacyConfig.enabled && (
          <Stack spacing={2}>
            <TextField
              select
              size="small"
              label="Redaction Mode"
              value={privacyConfig.mode || "patterns"}
              onChange={(e) => updatePrivacy("mode", e.target.value)}
              sx={{ width: 200 }}
            >
              <MenuItem value="full">Full (redact all PII)</MenuItem>
              <MenuItem value="patterns">Patterns (custom regex only)</MenuItem>
              <MenuItem value="none">None (logging only)</MenuItem>
            </TextField>

            <Typography variant="body2" fontWeight={400}>
              Redact Patterns
            </Typography>

            {patterns.length === 0 && (
              <Typography variant="body2" color="text.secondary">
                No custom patterns. Add regex patterns to mask specific data.
              </Typography>
            )}

            {patterns.map((p, idx) => (
              <Stack key={idx} direction="row" spacing={1} alignItems="center">
                <Chip
                  label={p.name}
                  size="small"
                  color="secondary"
                  variant="outlined"
                />
                <Typography variant="caption" sx={{ fontFamily: "monospace" }}>
                  {p.pattern}
                </Typography>
                <IconButton
                  sx={{ color: "status.danger.fg" }}
                  size="small"
                  onClick={() => removePattern(idx)}
                >
                  <Icon icon="solar:trash-bin-trash-linear" width={16} />
                </IconButton>
              </Stack>
            ))}

            <Stack direction="row" spacing={1}>
              <TextField
                size="small"
                placeholder="Pattern name"
                value={newPatternName}
                onChange={(e) => setNewPatternName(e.target.value)}
                sx={{ width: 150 }}
              />
              <TextField
                size="small"
                placeholder="Regex pattern"
                value={newPatternRegex}
                onChange={(e) => setNewPatternRegex(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addPattern();
                  }
                }}
                sx={{ width: 250 }}
              />
              <Button size="small" variant="outlined" onClick={addPattern}>
                Add
              </Button>
            </Stack>
          </Stack>
        )}
      </Paper>

      {/* ===== TOOL POLICY ===== */}
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
          <Icon icon="solar:toolbox-linear" width={20} />
          <Typography variant="subtitle1" fontWeight="600">
            Tool / Function Calling Policy
          </Typography>
        </Stack>

        <Alert severity="info" sx={{ mb: 2 }}>
          Control which tools/functions can be used in requests. Strip or reject
          requests with unauthorized tools.
        </Alert>

        <FormControlLabel
          control={
            <Switch
              checked={toolConfig.enabled || false}
              onChange={(e) => updateTool("enabled", e.target.checked)}
            />
          }
          label="Enable tool policy"
          sx={{ mb: 2 }}
        />

        {toolConfig.enabled && (
          <Stack spacing={2}>
            <Stack direction="row" spacing={2}>
              <TextField
                select
                size="small"
                label="Default Action"
                value={
                  toolConfig.default_action ||
                  toolConfig.defaultAction ||
                  "strip"
                }
                onChange={(e) => updateTool("default_action", e.target.value)}
                sx={{ width: 160 }}
              >
                <MenuItem value="strip">Strip (remove tools)</MenuItem>
                <MenuItem value="reject">Reject (return error)</MenuItem>
              </TextField>
              <TextField
                size="small"
                type="number"
                label="Max Tools per Request"
                value={
                  toolConfig.max_tools_per_request ??
                  toolConfig.maxToolsPerRequest ??
                  ""
                }
                onChange={(e) =>
                  updateTool(
                    "max_tools_per_request",
                    Number(e.target.value) || 0,
                  )
                }
                helperText="0 = unlimited"
                sx={{ width: 180 }}
              />
            </Stack>

            <Typography variant="body2" fontWeight={500}>
              Allowed Tools
            </Typography>
            <Stack direction="row" spacing={0.5} flexWrap="wrap">
              {toolAllow.map((t, i) => (
                <Chip
                  key={i}
                  label={t}
                  size="small"
                  color="success"
                  variant="outlined"
                  onDelete={() => removeTool("allow", i)}
                />
              ))}
            </Stack>
            <Stack direction="row" spacing={1}>
              <TextField
                size="small"
                placeholder="Tool/function name"
                value={newAllowTool}
                onChange={(e) => setNewAllowTool(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addTool("allow", newAllowTool, setNewAllowTool);
                  }
                }}
                sx={{ width: 250 }}
              />
              <Button
                size="small"
                variant="outlined"
                onClick={() => addTool("allow", newAllowTool, setNewAllowTool)}
              >
                Add
              </Button>
            </Stack>

            <Divider />

            <Typography variant="body2" fontWeight={500}>
              Denied Tools
            </Typography>
            <Stack direction="row" spacing={0.5} flexWrap="wrap">
              {toolDeny.map((t, i) => (
                <Chip
                  key={i}
                  label={t}
                  size="small"
                  color="error"
                  variant="outlined"
                  onDelete={() => removeTool("deny", i)}
                />
              ))}
            </Stack>
            <Stack direction="row" spacing={1}>
              <TextField
                size="small"
                placeholder="Tool/function name"
                value={newDenyTool}
                onChange={(e) => setNewDenyTool(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addTool("deny", newDenyTool, setNewDenyTool);
                  }
                }}
                sx={{ width: 250 }}
              />
              <Button
                size="small"
                variant="outlined"
                onClick={() => addTool("deny", newDenyTool, setNewDenyTool)}
              >
                Add
              </Button>
            </Stack>
          </Stack>
        )}
      </Paper>
    </Box>
  );
};

export default SecurityConfigTab;
