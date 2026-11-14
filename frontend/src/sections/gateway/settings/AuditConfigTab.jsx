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

const SEVERITIES = [
  { value: "info", label: "Info" },
  { value: "warn", label: "Warn" },
  { value: "error", label: "Error" },
  { value: "critical", label: "Critical" },
];

const CATEGORIES = ["request", "guardrail", "budget", "auth", "config"];

const SINK_TYPES = [
  { value: "stdout", label: "Stdout" },
  { value: "file", label: "File" },
  { value: "webhook", label: "Webhook" },
];

const AuditConfigTab = ({ audit, onChange }) => {
  const config = audit || {};
  const categories = config.categories || [];
  const sinks = config.sinks || [];

  const [newHeaderKey, setNewHeaderKey] = useState("");
  const [newHeaderValue, setNewHeaderValue] = useState("");

  const update = (key, value) => {
    onChange({ ...config, [key]: value });
  };

  // Category handlers
  const handleToggleCategory = (cat) => {
    const updated = categories.includes(cat)
      ? categories.filter((c) => c !== cat)
      : [...categories, cat];
    update("categories", updated);
  };

  // Sink handlers
  const handleAddSink = () => {
    update("sinks", [...sinks, { type: "stdout" }]);
  };

  const handleSinkChange = (idx, field, value) => {
    const updated = [...sinks];
    updated[idx] = { ...updated[idx], [field]: value };
    update("sinks", updated);
  };

  const handleRemoveSink = (idx) => {
    const updated = [...sinks];
    updated.splice(idx, 1);
    update("sinks", updated);
  };

  const handleAddHeader = (sinkIdx) => {
    if (!newHeaderKey.trim()) return;
    const sink = sinks[sinkIdx];
    const headers = {
      ...(sink.headers || {}),
      [newHeaderKey.trim()]: newHeaderValue,
    };
    handleSinkChange(sinkIdx, "headers", headers);
    setNewHeaderKey("");
    setNewHeaderValue("");
  };

  const handleRemoveHeader = (sinkIdx, key) => {
    const sink = sinks[sinkIdx];
    const headers = { ...(sink.headers || {}) };
    delete headers[key];
    handleSinkChange(sinkIdx, "headers", headers);
  };

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
        <Icon icon="solar:pen-new-square-linear" width={24} />
        <Typography variant="h6">Audit Logging</Typography>
      </Stack>

      <Alert severity="info" sx={{ mb: 2 }}>
        Configure per-organization audit logging. Control which events are
        logged, minimum severity level, and where audit logs are sent.
      </Alert>

      <FormControlLabel
        control={
          <Switch
            checked={config.enabled || false}
            onChange={(e) => update("enabled", e.target.checked)}
          />
        }
        label="Enable per-org audit logging"
        sx={{ mb: 2 }}
      />

      {config.enabled && (
        <Stack spacing={3}>
          {/* ===== MIN SEVERITY ===== */}
          <TextField
            select
            size="small"
            label="Minimum Severity"
            value={config.min_severity || config.minSeverity || "info"}
            onChange={(e) => update("min_severity", e.target.value)}
            sx={{ width: 200 }}
            helperText="Only log events at or above this severity"
          >
            {SEVERITIES.map((s) => (
              <MenuItem key={s.value} value={s.value}>
                {s.label}
              </MenuItem>
            ))}
          </TextField>

          {/* ===== CATEGORIES ===== */}
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="subtitle1" fontWeight={400} sx={{ mb: 1 }}>
              Event Categories
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ mb: 1.5, display: "block" }}
            >
              Select which event categories to log. Leave all unselected to log
              all categories.
            </Typography>
            <Stack direction="row" spacing={0.5} flexWrap="wrap">
              {CATEGORIES.map((cat) => (
                <Chip
                  key={cat}
                  label={cat}
                  size="small"
                  color={categories.includes(cat) ? "primary" : "default"}
                  variant={categories.includes(cat) ? "filled" : "outlined"}
                  onClick={() => handleToggleCategory(cat)}
                  sx={{ cursor: "pointer" }}
                />
              ))}
            </Stack>
          </Paper>

          <Divider />

          {/* ===== SINKS ===== */}
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
              sx={{ mb: 2 }}
            >
              <Typography variant="subtitle1" fontWeight="600">
                Audit Sinks
              </Typography>
              <Button
                size="small"
                variant="outlined"
                onClick={handleAddSink}
                startIcon={<Icon icon="solar:add-linear" width={16} />}
              >
                Add Sink
              </Button>
            </Stack>

            {sinks.length === 0 && (
              <Typography variant="body2" color="text.secondary">
                No sinks configured. Add sinks to define where audit logs are
                sent.
              </Typography>
            )}

            {sinks.map((sink, idx) => (
              <Paper key={idx} variant="outlined" sx={{ p: 1.5, mb: 1.5 }}>
                <Stack spacing={1.5}>
                  <Stack
                    direction="row"
                    spacing={1.5}
                    alignItems="center"
                    flexWrap="wrap"
                  >
                    <TextField
                      select
                      size="small"
                      label="Type"
                      value={sink.type || "stdout"}
                      onChange={(e) =>
                        handleSinkChange(idx, "type", e.target.value)
                      }
                      sx={{ width: 140 }}
                    >
                      {SINK_TYPES.map((st) => (
                        <MenuItem key={st.value} value={st.value}>
                          {st.label}
                        </MenuItem>
                      ))}
                    </TextField>

                    {sink.type === "file" && (
                      <TextField
                        size="small"
                        label="File Path"
                        value={sink.path || ""}
                        onChange={(e) =>
                          handleSinkChange(idx, "path", e.target.value)
                        }
                        sx={{ flex: 1, minWidth: 250 }}
                        placeholder="/var/log/audit.jsonl"
                      />
                    )}

                    {sink.type === "webhook" && (
                      <TextField
                        size="small"
                        label="Webhook URL"
                        value={sink.url || ""}
                        onChange={(e) =>
                          handleSinkChange(idx, "url", e.target.value)
                        }
                        sx={{ flex: 1, minWidth: 250 }}
                        placeholder="https://..."
                      />
                    )}

                    <IconButton
                      sx={{ color: "status.danger.fg" }}
                      size="small"
                      onClick={() => handleRemoveSink(idx)}
                    >
                      <Icon icon="solar:trash-bin-trash-linear" width={18} />
                    </IconButton>
                  </Stack>

                  {/* Webhook headers */}
                  {sink.type === "webhook" && (
                    <Box sx={{ pl: 2 }}>
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{ mb: 0.5, display: "block" }}
                      >
                        Headers
                      </Typography>
                      {Object.entries(sink.headers || {}).map(([key, val]) => (
                        <Stack
                          key={key}
                          direction="row"
                          spacing={1}
                          alignItems="center"
                          sx={{ mb: 0.5 }}
                        >
                          <Chip
                            label={`${key}: ${val}`}
                            size="small"
                            variant="outlined"
                            onDelete={() => handleRemoveHeader(idx, key)}
                          />
                        </Stack>
                      ))}
                      <Stack direction="row" spacing={1} sx={{ mt: 0.5 }}>
                        <TextField
                          size="small"
                          placeholder="Header name"
                          value={newHeaderKey}
                          onChange={(e) => setNewHeaderKey(e.target.value)}
                          sx={{ width: 150 }}
                        />
                        <TextField
                          size="small"
                          placeholder="Value"
                          value={newHeaderValue}
                          onChange={(e) => setNewHeaderValue(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              e.preventDefault();
                              handleAddHeader(idx);
                            }
                          }}
                          sx={{ width: 200 }}
                        />
                        <Button
                          size="small"
                          variant="outlined"
                          onClick={() => handleAddHeader(idx)}
                        >
                          Add
                        </Button>
                      </Stack>
                    </Box>
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

export default AuditConfigTab;
