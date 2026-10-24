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
  Alert,
  MenuItem,
  Chip,
  IconButton,
  Button,
  Divider,
} from "@mui/material";
import { Icon } from "@iconify/react";

const METRICS = [
  { value: "error_count", label: "Error Count" },
  { value: "request_count", label: "Request Count" },
  { value: "cost_total", label: "Total Cost ($)" },
  { value: "latency_avg", label: "Avg Latency (ms)" },
  { value: "tokens_total", label: "Total Tokens" },
];

const CONDITIONS = [
  { value: ">=", label: ">=" },
  { value: ">", label: ">" },
  { value: "<=", label: "<=" },
  { value: "<", label: "<" },
  { value: "==", label: "==" },
];

const CHANNEL_TYPES = [
  { value: "webhook", label: "Webhook" },
  { value: "slack", label: "Slack" },
  { value: "log", label: "Log Only" },
];

const AlertingConfigTab = ({ alerting, onChange }) => {
  const config = alerting || {};
  const rules = config.rules || [];
  const channels = config.channels || [];

  const [newChannelName, setNewChannelName] = useState("");

  const update = (key, value) => {
    onChange({ ...config, [key]: value });
  };

  // Rule handlers
  const handleAddRule = () => {
    update("rules", [
      ...rules,
      {
        name: `rule_${rules.length + 1}`,
        metric: "error_count",
        condition: ">=",
        threshold: 10,
        window: "5m",
        cooldown: "15m",
        channels: [],
      },
    ]);
  };

  const handleRuleChange = (idx, field, value) => {
    const updated = [...rules];
    updated[idx] = { ...updated[idx], [field]: value };
    update("rules", updated);
  };

  const handleRemoveRule = (idx) => {
    const updated = [...rules];
    updated.splice(idx, 1);
    update("rules", updated);
  };

  const handleToggleRuleChannel = (ruleIdx, channelName) => {
    const rule = rules[ruleIdx];
    const ruleChannels = rule.channels || [];
    const updated = ruleChannels.includes(channelName)
      ? ruleChannels.filter((c) => c !== channelName)
      : [...ruleChannels, channelName];
    handleRuleChange(ruleIdx, "channels", updated);
  };

  // Channel handlers
  const handleAddChannel = () => {
    if (!newChannelName.trim()) return;
    update("channels", [
      ...channels,
      { name: newChannelName.trim(), type: "webhook", url: "" },
    ]);
    setNewChannelName("");
  };

  const handleChannelChange = (idx, field, value) => {
    const updated = [...channels];
    updated[idx] = { ...updated[idx], [field]: value };
    update("channels", updated);
  };

  const handleRemoveChannel = (idx) => {
    const updated = [...channels];
    updated.splice(idx, 1);
    update("channels", updated);
  };

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
        <Icon icon="solar:bell-linear" width={24} />
        <Typography variant="h6">Alerting</Typography>
      </Stack>

      <Alert severity="info" sx={{ mb: 2 }}>
        Configure per-organization alert rules and notification channels. Alerts
        fire when metrics exceed thresholds within a time window.
      </Alert>

      <FormControlLabel
        control={
          <Switch
            checked={config.enabled || false}
            onChange={(e) => update("enabled", e.target.checked)}
          />
        }
        label="Enable per-org alerting"
        sx={{ mb: 2 }}
      />

      {config.enabled && (
        <Stack spacing={3}>
          {/* ===== CHANNELS ===== */}
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
              sx={{ mb: 2 }}
            >
              <Typography variant="subtitle1" fontWeight="600">
                Notification Channels
              </Typography>
            </Stack>

            {channels.length === 0 && (
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                No channels configured. Add webhook or Slack channels to receive
                alerts.
              </Typography>
            )}

            {channels.map((ch, idx) => (
              <Paper key={idx} variant="outlined" sx={{ p: 1.5, mb: 1 }}>
                <Stack
                  direction="row"
                  spacing={1.5}
                  alignItems="center"
                  flexWrap="wrap"
                >
                  <Chip
                    label={ch.name}
                    size="small"
                    color="primary"
                    variant="outlined"
                  />
                  <TextField
                    select
                    size="small"
                    label="Type"
                    value={ch.type || "webhook"}
                    onChange={(e) =>
                      handleChannelChange(idx, "type", e.target.value)
                    }
                    sx={{ width: 130 }}
                  >
                    {CHANNEL_TYPES.map((ct) => (
                      <MenuItem key={ct.value} value={ct.value}>
                        {ct.label}
                      </MenuItem>
                    ))}
                  </TextField>
                  {(ch.type === "webhook" || ch.type === "slack") && (
                    <TextField
                      size="small"
                      label="URL"
                      value={ch.url || ""}
                      onChange={(e) =>
                        handleChannelChange(idx, "url", e.target.value)
                      }
                      sx={{ flex: 1, minWidth: 250 }}
                      placeholder={
                        ch.type === "slack"
                          ? "https://hooks.slack.com/..."
                          : "https://..."
                      }
                    />
                  )}
                  <IconButton
                    sx={{ color: "status.danger.fg" }}
                    size="small"
                    onClick={() => handleRemoveChannel(idx)}
                  >
                    <Icon icon="solar:trash-bin-trash-linear" width={18} />
                  </IconButton>
                </Stack>
              </Paper>
            ))}

            <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
              <TextField
                size="small"
                placeholder="Channel name"
                value={newChannelName}
                onChange={(e) => setNewChannelName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    handleAddChannel();
                  }
                }}
                sx={{ width: 200 }}
              />
              <Button
                size="small"
                variant="outlined"
                onClick={handleAddChannel}
              >
                Add Channel
              </Button>
            </Stack>
          </Paper>

          <Divider />

          {/* ===== RULES ===== */}
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
              sx={{ mb: 2 }}
            >
              <Typography variant="subtitle1" fontWeight="600">
                Alert Rules
              </Typography>
              <Button
                size="small"
                variant="outlined"
                onClick={handleAddRule}
                startIcon={<Icon icon="solar:add-linear" width={16} />}
              >
                Add Rule
              </Button>
            </Stack>

            {rules.length === 0 && (
              <Typography variant="body2" color="text.secondary">
                No alert rules defined. Add rules to trigger alerts based on
                metrics.
              </Typography>
            )}

            {rules.map((rule, idx) => (
              <Paper key={idx} variant="outlined" sx={{ p: 1.5, mb: 1.5 }}>
                <Stack spacing={1.5}>
                  <Stack
                    direction="row"
                    spacing={1.5}
                    alignItems="center"
                    flexWrap="wrap"
                  >
                    <TextField
                      size="small"
                      label="Rule Name"
                      value={rule.name || ""}
                      onChange={(e) =>
                        handleRuleChange(idx, "name", e.target.value)
                      }
                      sx={{ width: 180 }}
                    />
                    <TextField
                      select
                      size="small"
                      label="Metric"
                      value={rule.metric || "error_count"}
                      onChange={(e) =>
                        handleRuleChange(idx, "metric", e.target.value)
                      }
                      sx={{ width: 160 }}
                    >
                      {METRICS.map((m) => (
                        <MenuItem key={m.value} value={m.value}>
                          {m.label}
                        </MenuItem>
                      ))}
                    </TextField>
                    <TextField
                      select
                      size="small"
                      label="Condition"
                      value={rule.condition || ">="}
                      onChange={(e) =>
                        handleRuleChange(idx, "condition", e.target.value)
                      }
                      sx={{ width: 90 }}
                    >
                      {CONDITIONS.map((c) => (
                        <MenuItem key={c.value} value={c.value}>
                          {c.label}
                        </MenuItem>
                      ))}
                    </TextField>
                    <TextField
                      size="small"
                      type="number"
                      label="Threshold"
                      value={rule.threshold ?? ""}
                      onChange={(e) =>
                        handleRuleChange(
                          idx,
                          "threshold",
                          Number(e.target.value),
                        )
                      }
                      sx={{ width: 110 }}
                    />
                    <TextField
                      size="small"
                      label="Window"
                      value={rule.window || "5m"}
                      onChange={(e) =>
                        handleRuleChange(idx, "window", e.target.value)
                      }
                      sx={{ width: 90 }}
                      helperText="e.g. 5m"
                    />
                    <TextField
                      size="small"
                      label="Cooldown"
                      value={rule.cooldown || "15m"}
                      onChange={(e) =>
                        handleRuleChange(idx, "cooldown", e.target.value)
                      }
                      sx={{ width: 100 }}
                      helperText="e.g. 15m"
                    />
                    <IconButton
                      sx={{ color: "status.danger.fg" }}
                      size="small"
                      onClick={() => handleRemoveRule(idx)}
                    >
                      <Icon icon="solar:trash-bin-trash-linear" width={18} />
                    </IconButton>
                  </Stack>

                  {channels.length > 0 && (
                    <Stack
                      direction="row"
                      spacing={0.5}
                      alignItems="center"
                      flexWrap="wrap"
                    >
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{ mr: 1 }}
                      >
                        Notify:
                      </Typography>
                      {channels.map((ch) => (
                        <Chip
                          key={ch.name}
                          label={ch.name}
                          size="small"
                          color={
                            (rule.channels || []).includes(ch.name)
                              ? "primary"
                              : "default"
                          }
                          variant={
                            (rule.channels || []).includes(ch.name)
                              ? "filled"
                              : "outlined"
                          }
                          onClick={() => handleToggleRuleChannel(idx, ch.name)}
                          sx={{ cursor: "pointer" }}
                        />
                      ))}
                    </Stack>
                  )}
                </Stack>
              </Paper>
            ))}
          </Paper>
        </Stack>
      )}
    </Box>
  );
};

export default AlertingConfigTab;
