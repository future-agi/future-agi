/* eslint-disable react/prop-types */
import React, { useState } from "react";
import {
  Stack,
  Card,
  CardContent,
  Typography,
  TextField,
  MenuItem,
  FormControlLabel,
  Switch,
  Button,
  ToggleButton,
  ToggleButtonGroup,
  Chip,
  IconButton,
  Collapse,
  Divider,
  Slider,
  Alert,
} from "@mui/material";
import Iconify from "src/components/iconify";
import FallbackChainEditor from "./FallbackChainEditor";
import { useAvailableModels } from "../hooks/useAvailableModels";
import {
  DEFAULT_ROUTING_STRATEGY,
  ROUTING_STRATEGY_OPTIONS,
} from "../constants/routing";

// ---------- Section wrapper for collapsible cards ----------
const ConfigSection = ({
  title,
  subtitle,
  icon,
  children,
  defaultOpen = false,
}) => {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <Card variant="outlined">
      <CardContent
        sx={{
          py: 1.5,
          "&:last-child": { pb: 1.5 },
          cursor: "pointer",
          userSelect: "none",
        }}
        onClick={() => setOpen((v) => !v)}
      >
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
        >
          <Stack direction="row" alignItems="center" spacing={1}>
            <Iconify icon={icon} width={20} sx={{ color: "primary.main" }} />
            <Stack>
              <Typography variant="subtitle2">{title}</Typography>
              {subtitle && (
                <Typography variant="caption" color="text.secondary">
                  {subtitle}
                </Typography>
              )}
            </Stack>
          </Stack>
          <Iconify
            icon={open ? "mdi:chevron-up" : "mdi:chevron-down"}
            width={20}
            sx={{ color: "text.secondary" }}
          />
        </Stack>
      </CardContent>
      <Collapse in={open}>
        <Divider />
        <CardContent sx={{ pt: 2 }} onClick={(e) => e.stopPropagation()}>
          {children}
        </CardContent>
      </Collapse>
    </Card>
  );
};

// ---------- Main component ----------
const RoutingConfigTab = ({ routing, onChange }) => {
  const availableModels = useAvailableModels();
  const config = routing || {};
  const fallbacks = config.model_fallbacks || config.modelFallbacks || {};

  const handleChange = (field, value) => {
    onChange({ ...config, [field]: value });
  };

  const handleNestedChange = (section, field, value) => {
    const current = config[section] || {};
    onChange({ ...config, [section]: { ...current, [field]: value } });
  };

  const handleStrategyChange = (_, value) => {
    if (value) handleChange("strategy", value);
  };

  // Fallback chain handlers
  const handleFallbackChange = (primaryModel, newFallbacks, oldPrimary) => {
    const updated = { ...fallbacks };
    if (oldPrimary && oldPrimary !== primaryModel) {
      delete updated[oldPrimary];
    }
    updated[primaryModel] = newFallbacks;
    onChange({ ...config, model_fallbacks: updated });
  };

  const handleAddChain = () => {
    onChange({
      ...config,
      model_fallbacks: { ...fallbacks, "": [] },
    });
  };

  const handleRemoveChain = (primary) => {
    const updated = { ...fallbacks };
    delete updated[primary];
    onChange({ ...config, model_fallbacks: updated });
  };

  const fallbackEntries = Object.entries(fallbacks);

  // Complexity config
  const complexity = config.complexity || {};
  const complexityTiers = complexity.tiers || {};
  const complexityWeights = complexity.weights || {};

  const handleComplexityTierChange = (tierName, field, value) => {
    const tiers = { ...complexityTiers };
    tiers[tierName] = { ...(tiers[tierName] || {}), [field]: value };
    handleNestedChange("complexity", "tiers", tiers);
  };

  const handleAddComplexityTier = () => {
    const tiers = { ...complexityTiers };
    const name = `tier_${Object.keys(tiers).length + 1}`;
    tiers[name] = { max_score: 50, model: "", provider: "" };
    handleNestedChange("complexity", "tiers", tiers);
  };

  const handleRemoveComplexityTier = (name) => {
    const tiers = { ...complexityTiers };
    delete tiers[name];
    handleNestedChange("complexity", "tiers", tiers);
  };

  // Fastest config
  const fastest = config.fastest || {};

  // Scheduled config
  const scheduled = config.scheduled || {};

  // Adaptive config
  const adaptive = config.adaptive || {};
  const signalWeights = adaptive.signal_weights || adaptive.signalWeights || {};

  // Provider lock config
  const providerLock = config.provider_lock || config.providerLock || {};

  // Access groups config
  const accessGroups = config.access_groups || config.accessGroups || {};

  // ---------- Provider lock list handlers ----------
  const [newAllowed, setNewAllowed] = useState("");
  const [newDenied, setNewDenied] = useState("");

  const handleAddToList = (listField, value, setter) => {
    if (!value.trim()) return;
    const current = providerLock[listField] || [];
    if (!current.includes(value.trim())) {
      handleNestedChange("provider_lock", listField, [
        ...current,
        value.trim(),
      ]);
    }
    setter("");
  };

  const handleRemoveFromList = (listField, idx) => {
    const current = [...(providerLock[listField] || [])];
    current.splice(idx, 1);
    handleNestedChange("provider_lock", listField, current);
  };

  // Conditional routes config
  const conditionalRoutes =
    config.conditional_routes || config.conditionalRoutes || [];

  const handleAddConditionalRoute = () => {
    const routes = [
      ...conditionalRoutes,
      { field: "", operator: "$eq", value: "", target: "" },
    ];
    handleChange("conditional_routes", routes);
  };

  const handleConditionalRouteChange = (idx, field, value) => {
    const routes = [...conditionalRoutes];
    routes[idx] = { ...routes[idx], [field]: value };
    handleChange("conditional_routes", routes);
  };

  const handleRemoveConditionalRoute = (idx) => {
    const routes = [...conditionalRoutes];
    routes.splice(idx, 1);
    handleChange("conditional_routes", routes);
  };

  // Circuit breaker config
  const circuitBreaker = config.circuit_breaker || config.circuitBreaker || {};
  const [newCBStatusCode, setNewCBStatusCode] = useState("");

  const handleAddCBStatusCode = () => {
    const code = Number(newCBStatusCode);
    if (!code || code < 100 || code > 599) return;
    const current =
      circuitBreaker.on_status_codes || circuitBreaker.onStatusCodes || [];
    if (!current.includes(code)) {
      handleNestedChange("circuit_breaker", "on_status_codes", [
        ...current,
        code,
      ]);
    }
    setNewCBStatusCode("");
  };

  const handleRemoveCBStatusCode = (idx) => {
    const current = [
      ...(circuitBreaker.on_status_codes || circuitBreaker.onStatusCodes || []),
    ];
    current.splice(idx, 1);
    handleNestedChange("circuit_breaker", "on_status_codes", current);
  };

  // Retry config
  const retryConfig = config.retry || {};
  const [newRetryStatusCode, setNewRetryStatusCode] = useState("");

  const handleAddRetryStatusCode = () => {
    const code = Number(newRetryStatusCode);
    if (!code || code < 100 || code > 599) return;
    const current =
      retryConfig.on_status_codes || retryConfig.onStatusCodes || [];
    if (!current.includes(code)) {
      handleNestedChange("retry", "on_status_codes", [...current, code]);
    }
    setNewRetryStatusCode("");
  };

  const handleRemoveRetryStatusCode = (idx) => {
    const current = [
      ...(retryConfig.on_status_codes || retryConfig.onStatusCodes || []),
    ];
    current.splice(idx, 1);
    handleNestedChange("retry", "on_status_codes", current);
  };

  // Mirror config
  const mirrorConfig = config.mirror || {};
  const mirrorRules = mirrorConfig.rules || [];

  const handleAddMirrorRule = () => {
    const rules = [
      ...mirrorRules,
      {
        source_model: "",
        target_provider: "",
        target_model: "",
        sample_rate: 1.0,
      },
    ];
    onChange({ ...config, mirror: { ...mirrorConfig, rules } });
  };

  const handleMirrorRuleChange = (idx, field, value) => {
    const rules = [...mirrorRules];
    rules[idx] = { ...rules[idx], [field]: value };
    onChange({ ...config, mirror: { ...mirrorConfig, rules } });
  };

  const handleRemoveMirrorRule = (idx) => {
    const rules = [...mirrorRules];
    rules.splice(idx, 1);
    onChange({ ...config, mirror: { ...mirrorConfig, rules } });
  };

  // Model timeouts config
  const modelTimeouts = config.model_timeouts || config.modelTimeouts || {};
  const [newTimeoutModel, setNewTimeoutModel] = useState("");
  const [newTimeoutValue, setNewTimeoutValue] = useState("");

  const handleAddModelTimeout = () => {
    if (!newTimeoutModel.trim() || !newTimeoutValue.trim()) return;
    const updated = {
      ...modelTimeouts,
      [newTimeoutModel.trim()]: newTimeoutValue.trim(),
    };
    handleChange("model_timeouts", updated);
    setNewTimeoutModel("");
    setNewTimeoutValue("");
  };

  const handleRemoveModelTimeout = (model) => {
    const updated = { ...modelTimeouts };
    delete updated[model];
    handleChange("model_timeouts", updated);
  };

  // ---------- Access group handlers ----------
  const [newGroupName, setNewGroupName] = useState("");
  const [newModelInput, setNewModelInput] = useState({});

  const handleAddGroup = () => {
    if (!newGroupName.trim()) return;
    const groups = { ...accessGroups };
    groups[newGroupName.trim()] = { models: [], description: "" };
    handleChange("access_groups", groups);
    setNewGroupName("");
  };

  const handleRemoveGroup = (name) => {
    const groups = { ...accessGroups };
    delete groups[name];
    handleChange("access_groups", groups);
  };

  const handleGroupField = (name, field, value) => {
    const groups = { ...accessGroups };
    groups[name] = { ...(groups[name] || {}), [field]: value };
    handleChange("access_groups", groups);
  };

  const handleAddModelToGroup = (groupName) => {
    const model = (newModelInput[groupName] || "").trim();
    if (!model) return;
    const group = accessGroups[groupName] || {};
    const models = [...(group.models || [])];
    if (!models.includes(model)) {
      models.push(model);
      handleGroupField(groupName, "models", models);
    }
    setNewModelInput((prev) => ({ ...prev, [groupName]: "" }));
  };

  const handleRemoveModelFromGroup = (groupName, idx) => {
    const group = accessGroups[groupName] || {};
    const models = [...(group.models || [])];
    models.splice(idx, 1);
    handleGroupField(groupName, "models", models);
  };

  return (
    <Stack spacing={2}>
      {/* ===================== BASIC STRATEGY ===================== */}
      <ConfigSection
        title="Routing Strategy"
        subtitle="Select load balancing strategy and default model"
        icon="mdi:swap-horizontal"
        defaultOpen
      >
        <ToggleButtonGroup
          value={config.strategy || DEFAULT_ROUTING_STRATEGY}
          exclusive
          onChange={handleStrategyChange}
          size="small"
          sx={{ mb: 2 }}
        >
          {ROUTING_STRATEGY_OPTIONS.map((s) => (
            <ToggleButton key={s.value} value={s.value}>
              {s.label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>

        <Stack direction="row" spacing={2} alignItems="center" flexWrap="wrap">
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={
                  config.fallback_enabled ?? config.fallbackEnabled ?? false
                }
                onChange={(e) =>
                  handleChange("fallback_enabled", e.target.checked)
                }
              />
            }
            label={<Typography variant="body2">Fallback Enabled</Typography>}
          />
          <TextField
            select
            size="small"
            label="Default Model"
            value={config.default_model || config.defaultModel || ""}
            onChange={(e) => handleChange("default_model", e.target.value)}
            sx={{ minWidth: 200 }}
          >
            <MenuItem value="">
              <em>None</em>
            </MenuItem>
            {availableModels.map((m) => (
              <MenuItem key={m} value={m}>
                {m}
              </MenuItem>
            ))}
          </TextField>
        </Stack>
      </ConfigSection>

      {/* ===================== FALLBACK CHAINS ===================== */}
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="subtitle1">Model Fallback Chains</Typography>
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="mdi:plus" width={18} />}
          onClick={handleAddChain}
        >
          Add Fallback Chain
        </Button>
      </Stack>

      {fallbackEntries.length === 0 && (
        <Card variant="outlined">
          <CardContent sx={{ textAlign: "center", py: 3 }}>
            <Typography variant="body2" color="text.secondary">
              No fallback chains configured. Add one to define model fallback
              behavior.
            </Typography>
          </CardContent>
        </Card>
      )}

      {fallbackEntries.map(([primary, fbs]) => (
        <FallbackChainEditor
          key={primary}
          primaryModel={primary}
          fallbacks={fbs}
          models={availableModels}
          onChange={(newPrimary, newFbs) =>
            handleFallbackChange(newPrimary, newFbs, primary)
          }
          onRemoveChain={() => handleRemoveChain(primary)}
        />
      ))}

      <Divider sx={{ my: 1 }} />
      <Typography variant="subtitle1">Advanced Routing</Typography>

      {/* ===================== COMPLEXITY ROUTING ===================== */}
      <ConfigSection
        title="Complexity Routing"
        subtitle="Route prompts to different models based on estimated complexity"
        icon="mdi:chart-bell-curve-cumulative"
      >
        <Stack spacing={2}>
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={complexity.enabled || false}
                onChange={(e) =>
                  handleNestedChange("complexity", "enabled", e.target.checked)
                }
              />
            }
            label={
              <Typography variant="body2">Enable Complexity Routing</Typography>
            }
          />

          {complexity.enabled && (
            <>
              <TextField
                size="small"
                label="Default Tier"
                value={complexity.default_tier || complexity.defaultTier || ""}
                onChange={(e) =>
                  handleNestedChange(
                    "complexity",
                    "default_tier",
                    e.target.value,
                  )
                }
                sx={{ maxWidth: 200 }}
                helperText="Fallback tier when score cannot be determined"
              />

              <Typography variant="body2" fontWeight={500}>
                Scoring Weights
              </Typography>
              <Stack direction="row" spacing={2} flexWrap="wrap">
                {[
                  "token_count",
                  "vocabulary",
                  "structure",
                  "context_switches",
                ].map((w) => (
                  <TextField
                    key={w}
                    size="small"
                    type="number"
                    label={w.replace(/_/g, " ")}
                    value={complexityWeights[w] ?? ""}
                    onChange={(e) => {
                      const weights = { ...complexityWeights };
                      weights[w] = Number(e.target.value);
                      handleNestedChange("complexity", "weights", weights);
                    }}
                    sx={{ width: 130 }}
                    inputProps={{ step: 0.1, min: 0, max: 1 }}
                  />
                ))}
              </Stack>

              <Stack
                direction="row"
                justifyContent="space-between"
                alignItems="center"
              >
                <Typography variant="body2" fontWeight={500}>
                  Tiers
                </Typography>
                <Button
                  size="small"
                  onClick={handleAddComplexityTier}
                  startIcon={<Iconify icon="mdi:plus" width={16} />}
                >
                  Add Tier
                </Button>
              </Stack>

              {Object.entries(complexityTiers).map(([name, tier]) => (
                <Card key={name} variant="outlined" sx={{ p: 1.5 }}>
                  <Stack
                    direction="row"
                    spacing={1.5}
                    alignItems="center"
                    flexWrap="wrap"
                  >
                    <Chip
                      label={name}
                      size="small"
                      color="primary"
                      variant="outlined"
                    />
                    <TextField
                      size="small"
                      type="number"
                      label="Max Score"
                      value={tier.max_score ?? tier.maxScore ?? ""}
                      onChange={(e) =>
                        handleComplexityTierChange(
                          name,
                          "max_score",
                          Number(e.target.value),
                        )
                      }
                      sx={{ width: 100 }}
                    />
                    <TextField
                      select
                      size="small"
                      label="Model"
                      value={tier.model || ""}
                      onChange={(e) =>
                        handleComplexityTierChange(
                          name,
                          "model",
                          e.target.value,
                        )
                      }
                      sx={{ minWidth: 180 }}
                    >
                      {availableModels.map((m) => (
                        <MenuItem key={m} value={m}>
                          {m}
                        </MenuItem>
                      ))}
                    </TextField>
                    <TextField
                      size="small"
                      label="Provider"
                      value={tier.provider || ""}
                      onChange={(e) =>
                        handleComplexityTierChange(
                          name,
                          "provider",
                          e.target.value,
                        )
                      }
                      sx={{ width: 140 }}
                    />
                    <IconButton
                      size="small"
                      onClick={() => handleRemoveComplexityTier(name)}
                    >
                      <Iconify
                        icon="mdi:delete-outline"
                        width={18}
                        color="error.main"
                      />
                    </IconButton>
                  </Stack>
                </Card>
              ))}
            </>
          )}
        </Stack>
      </ConfigSection>

      {/* ===================== FASTEST RESPONSE ===================== */}
      <ConfigSection
        title="Fastest Response (Race Mode)"
        subtitle="Send request to multiple providers simultaneously, use first response"
        icon="mdi:flash"
      >
        <Stack spacing={2}>
          <Alert severity="info" variant="outlined" sx={{ mb: 1 }}>
            Race mode sends the same request to multiple providers in parallel.
            The first response wins. Use strategy &quot;fastest&quot; to
            activate.
          </Alert>
          <Stack direction="row" spacing={2} flexWrap="wrap">
            <TextField
              size="small"
              type="number"
              label="Max Concurrent"
              value={fastest.max_concurrent ?? fastest.maxConcurrent ?? 3}
              onChange={(e) =>
                handleNestedChange(
                  "fastest",
                  "max_concurrent",
                  Number(e.target.value),
                )
              }
              sx={{ width: 140 }}
              helperText="Max parallel requests"
              inputProps={{ min: 2, max: 10 }}
            />
            <TextField
              size="small"
              label="Cancel Delay"
              value={fastest.cancel_delay ?? fastest.cancelDelay ?? "100ms"}
              onChange={(e) =>
                handleNestedChange("fastest", "cancel_delay", e.target.value)
              }
              sx={{ width: 140 }}
              helperText="e.g. 100ms, 1s"
            />
          </Stack>
          <TextField
            size="small"
            label="Excluded Providers"
            value={(
              fastest.excluded_providers ||
              fastest.excludedProviders ||
              []
            ).join(", ")}
            onChange={(e) =>
              handleNestedChange(
                "fastest",
                "excluded_providers",
                e.target.value
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean),
              )
            }
            helperText="Comma-separated provider names to exclude from race"
            fullWidth
          />
        </Stack>
      </ConfigSection>

      {/* ===================== SCHEDULED COMPLETIONS ===================== */}
      <ConfigSection
        title="Scheduled Completions"
        subtitle="Queue requests for future execution at a specified time"
        icon="mdi:clock-outline"
      >
        <Stack spacing={2}>
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={scheduled.enabled || false}
                onChange={(e) =>
                  handleNestedChange("scheduled", "enabled", e.target.checked)
                }
              />
            }
            label={
              <Typography variant="body2">
                Enable Scheduled Completions
              </Typography>
            }
          />

          {scheduled.enabled && (
            <Stack direction="row" spacing={2} flexWrap="wrap">
              <TextField
                size="small"
                type="number"
                label="Max Pending Jobs"
                value={
                  scheduled.max_pending_jobs ?? scheduled.maxPendingJobs ?? 1000
                }
                onChange={(e) =>
                  handleNestedChange(
                    "scheduled",
                    "max_pending_jobs",
                    Number(e.target.value),
                  )
                }
                sx={{ width: 150 }}
              />
              <TextField
                size="small"
                label="Result TTL"
                value={scheduled.result_ttl ?? scheduled.resultTtl ?? "24h"}
                onChange={(e) =>
                  handleNestedChange("scheduled", "result_ttl", e.target.value)
                }
                sx={{ width: 120 }}
                helperText="e.g. 24h, 1h"
              />
              <TextField
                size="small"
                label="Max Schedule Ahead"
                value={
                  scheduled.max_schedule_ahead ??
                  scheduled.maxScheduleAhead ??
                  "168h"
                }
                onChange={(e) =>
                  handleNestedChange(
                    "scheduled",
                    "max_schedule_ahead",
                    e.target.value,
                  )
                }
                sx={{ width: 160 }}
                helperText="Max future window"
              />
              <TextField
                size="small"
                type="number"
                label="Retry Attempts"
                value={scheduled.retry_attempts ?? scheduled.retryAttempts ?? 3}
                onChange={(e) =>
                  handleNestedChange(
                    "scheduled",
                    "retry_attempts",
                    Number(e.target.value),
                  )
                }
                sx={{ width: 130 }}
              />
              <TextField
                size="small"
                type="number"
                label="Worker Count"
                value={scheduled.worker_count ?? scheduled.workerCount ?? 4}
                onChange={(e) =>
                  handleNestedChange(
                    "scheduled",
                    "worker_count",
                    Number(e.target.value),
                  )
                }
                sx={{ width: 130 }}
              />
            </Stack>
          )}
        </Stack>
      </ConfigSection>

      {/* ===================== ADAPTIVE ROUTING ===================== */}
      <ConfigSection
        title="Adaptive Routing"
        subtitle="Automatically adjust provider weights based on real-time performance"
        icon="mdi:auto-fix"
      >
        <Stack spacing={2}>
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={adaptive.enabled || false}
                onChange={(e) =>
                  handleNestedChange("adaptive", "enabled", e.target.checked)
                }
              />
            }
            label={
              <Typography variant="body2">Enable Adaptive Routing</Typography>
            }
          />

          {adaptive.enabled && (
            <>
              <Stack direction="row" spacing={2} flexWrap="wrap">
                <TextField
                  size="small"
                  type="number"
                  label="Learning Requests"
                  value={
                    adaptive.learning_requests ??
                    adaptive.learningRequests ??
                    100
                  }
                  onChange={(e) =>
                    handleNestedChange(
                      "adaptive",
                      "learning_requests",
                      Number(e.target.value),
                    )
                  }
                  sx={{ width: 150 }}
                  helperText="Warmup requests"
                />
                <TextField
                  size="small"
                  label="Update Interval"
                  value={
                    adaptive.update_interval ?? adaptive.updateInterval ?? "30s"
                  }
                  onChange={(e) =>
                    handleNestedChange(
                      "adaptive",
                      "update_interval",
                      e.target.value,
                    )
                  }
                  sx={{ width: 140 }}
                  helperText="e.g. 30s, 1m"
                />
                <TextField
                  size="small"
                  type="number"
                  label="Smoothing Factor"
                  value={
                    adaptive.smoothing_factor ?? adaptive.smoothingFactor ?? 0.3
                  }
                  onChange={(e) =>
                    handleNestedChange(
                      "adaptive",
                      "smoothing_factor",
                      Number(e.target.value),
                    )
                  }
                  sx={{ width: 150 }}
                  inputProps={{ step: 0.05, min: 0, max: 1 }}
                />
                <TextField
                  size="small"
                  type="number"
                  label="Min Weight"
                  value={adaptive.min_weight ?? adaptive.minWeight ?? 0.05}
                  onChange={(e) =>
                    handleNestedChange(
                      "adaptive",
                      "min_weight",
                      Number(e.target.value),
                    )
                  }
                  sx={{ width: 120 }}
                  inputProps={{ step: 0.01, min: 0, max: 1 }}
                />
              </Stack>

              <Typography variant="body2" fontWeight={500}>
                Signal Weights
              </Typography>
              <Typography variant="caption" color="text.secondary">
                How much each performance signal contributes to scoring (0-1)
              </Typography>
              <Stack direction="row" spacing={3} sx={{ px: 1 }}>
                {[
                  { key: "latency", label: "Latency", defaultVal: 0.4 },
                  { key: "error_rate", label: "Error Rate", defaultVal: 0.4 },
                  { key: "cost", label: "Cost", defaultVal: 0.2 },
                ].map(({ key, label, defaultVal }) => (
                  <Stack key={key} sx={{ flex: 1 }}>
                    <Typography variant="caption" gutterBottom>
                      {label}:{" "}
                      {Number(signalWeights[key] ?? defaultVal).toFixed(2)}
                    </Typography>
                    <Slider
                      size="small"
                      value={Number(signalWeights[key] ?? defaultVal)}
                      min={0}
                      max={1}
                      step={0.05}
                      onChange={(_, val) => {
                        const sw = { ...signalWeights, [key]: val };
                        handleNestedChange("adaptive", "signal_weights", sw);
                      }}
                    />
                  </Stack>
                ))}
              </Stack>
            </>
          )}
        </Stack>
      </ConfigSection>

      {/* ===================== PROVIDER LOCKING ===================== */}
      <ConfigSection
        title="Provider Locking"
        subtitle="Restrict which providers can be used for requests"
        icon="mdi:lock-outline"
      >
        <Stack spacing={2}>
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={providerLock.enabled || false}
                onChange={(e) =>
                  handleNestedChange(
                    "provider_lock",
                    "enabled",
                    e.target.checked,
                  )
                }
              />
            }
            label={
              <Typography variant="body2">Enable Provider Locking</Typography>
            }
          />

          {providerLock.enabled && (
            <>
              {/* Allowed providers */}
              <Typography variant="body2" fontWeight={500}>
                Allowed Providers
              </Typography>
              <Typography variant="caption" color="text.secondary">
                Only these providers will be used. Leave empty to allow all.
              </Typography>
              <Stack direction="row" spacing={1} flexWrap="wrap">
                {(
                  providerLock.allowed_providers ||
                  providerLock.allowedProviders ||
                  []
                ).map((p, i) => (
                  <Chip
                    key={i}
                    label={p}
                    size="small"
                    color="success"
                    variant="outlined"
                    onDelete={() =>
                      handleRemoveFromList("allowed_providers", i)
                    }
                  />
                ))}
              </Stack>
              <Stack direction="row" spacing={1}>
                <TextField
                  size="small"
                  placeholder="Provider name"
                  value={newAllowed}
                  onChange={(e) => setNewAllowed(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      handleAddToList(
                        "allowed_providers",
                        newAllowed,
                        setNewAllowed,
                      );
                    }
                  }}
                  sx={{ width: 200 }}
                />
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() =>
                    handleAddToList(
                      "allowed_providers",
                      newAllowed,
                      setNewAllowed,
                    )
                  }
                >
                  Add
                </Button>
              </Stack>

              <Divider />

              {/* Denied providers */}
              <Typography variant="body2" fontWeight={500}>
                Denied Providers
              </Typography>
              <Typography variant="caption" color="text.secondary">
                These providers will never be used, even if in the allowed list.
              </Typography>
              <Stack direction="row" spacing={1} flexWrap="wrap">
                {(
                  providerLock.deny_providers ||
                  providerLock.denyProviders ||
                  []
                ).map((p, i) => (
                  <Chip
                    key={i}
                    label={p}
                    size="small"
                    color="error"
                    variant="outlined"
                    onDelete={() => handleRemoveFromList("deny_providers", i)}
                  />
                ))}
              </Stack>
              <Stack direction="row" spacing={1}>
                <TextField
                  size="small"
                  placeholder="Provider name"
                  value={newDenied}
                  onChange={(e) => setNewDenied(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      handleAddToList(
                        "deny_providers",
                        newDenied,
                        setNewDenied,
                      );
                    }
                  }}
                  sx={{ width: 200 }}
                />
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() =>
                    handleAddToList("deny_providers", newDenied, setNewDenied)
                  }
                >
                  Add
                </Button>
              </Stack>
            </>
          )}
        </Stack>
      </ConfigSection>

      {/* ===================== ACCESS GROUPS ===================== */}
      <ConfigSection
        title="Model Access Groups"
        subtitle="Define named groups of models that can be referenced by alias"
        icon="mdi:account-group-outline"
      >
        <Stack spacing={2}>
          <Stack direction="row" spacing={1}>
            <TextField
              size="small"
              placeholder="Group name (e.g. premium, standard)"
              value={newGroupName}
              onChange={(e) => setNewGroupName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  handleAddGroup();
                }
              }}
              sx={{ width: 280 }}
            />
            <Button
              size="small"
              variant="outlined"
              onClick={handleAddGroup}
              startIcon={<Iconify icon="mdi:plus" width={16} />}
            >
              Add Group
            </Button>
          </Stack>

          {Object.keys(accessGroups).length === 0 && (
            <Typography variant="body2" color="text.secondary">
              No access groups defined. Groups let you organize models into
              tiers (e.g. premium, standard) and control access per API key.
            </Typography>
          )}

          {Object.entries(accessGroups).map(([name, group]) => (
            <Card key={name} variant="outlined" sx={{ p: 1.5 }}>
              <Stack spacing={1.5}>
                <Stack
                  direction="row"
                  justifyContent="space-between"
                  alignItems="center"
                >
                  <Chip label={name} size="small" color="primary" />
                  <IconButton
                    size="small"
                    onClick={() => handleRemoveGroup(name)}
                  >
                    <Iconify
                      icon="mdi:delete-outline"
                      width={18}
                      color="error.main"
                    />
                  </IconButton>
                </Stack>
                <TextField
                  size="small"
                  label="Description"
                  value={group.description || ""}
                  onChange={(e) =>
                    handleGroupField(name, "description", e.target.value)
                  }
                  fullWidth
                />
                <Typography variant="caption" fontWeight={500}>
                  Models
                </Typography>
                <Stack direction="row" spacing={0.5} flexWrap="wrap">
                  {(group.models || []).map((m, i) => (
                    <Chip
                      key={i}
                      label={m}
                      size="small"
                      variant="outlined"
                      onDelete={() => handleRemoveModelFromGroup(name, i)}
                    />
                  ))}
                </Stack>
                <Stack direction="row" spacing={1}>
                  <TextField
                    select
                    size="small"
                    label="Add model"
                    value={newModelInput[name] || ""}
                    onChange={(e) =>
                      setNewModelInput((prev) => ({
                        ...prev,
                        [name]: e.target.value,
                      }))
                    }
                    sx={{ minWidth: 200 }}
                  >
                    {availableModels.map((m) => (
                      <MenuItem key={m} value={m}>
                        {m}
                      </MenuItem>
                    ))}
                  </TextField>
                  <Button
                    size="small"
                    onClick={() => handleAddModelToGroup(name)}
                  >
                    Add
                  </Button>
                </Stack>
              </Stack>
            </Card>
          ))}
        </Stack>
      </ConfigSection>

      <Divider sx={{ my: 1 }} />
      <Typography variant="subtitle1">Reliability & Advanced</Typography>

      {/* ===================== CONDITIONAL ROUTES ===================== */}
      <ConfigSection
        title="Conditional Routes"
        subtitle="Route requests to specific targets based on metadata field matching"
        icon="mdi:source-branch"
      >
        <Stack spacing={2}>
          <Alert severity="info" variant="outlined" sx={{ mb: 1 }}>
            Define rules to route requests based on metadata fields. Rules are
            evaluated in order; first match wins.
          </Alert>

          {conditionalRoutes.length === 0 && (
            <Typography variant="body2" color="text.secondary">
              No conditional routes defined. Add rules to route based on
              metadata fields like model, user, or custom properties.
            </Typography>
          )}

          {conditionalRoutes.map((route, idx) => (
            <Card key={idx} variant="outlined" sx={{ p: 1.5 }}>
              <Stack
                direction="row"
                spacing={1.5}
                alignItems="center"
                flexWrap="wrap"
              >
                <TextField
                  size="small"
                  label="Field"
                  value={route.field || ""}
                  onChange={(e) =>
                    handleConditionalRouteChange(idx, "field", e.target.value)
                  }
                  sx={{ width: 160 }}
                  helperText="e.g. model, user, metadata.env"
                />
                <TextField
                  select
                  size="small"
                  label="Operator"
                  value={route.operator || "$eq"}
                  onChange={(e) =>
                    handleConditionalRouteChange(
                      idx,
                      "operator",
                      e.target.value,
                    )
                  }
                  sx={{ width: 120 }}
                >
                  {["$eq", "$ne", "$in", "$regex", "$gt", "$lt"].map((op) => (
                    <MenuItem key={op} value={op}>
                      {op}
                    </MenuItem>
                  ))}
                </TextField>
                <TextField
                  size="small"
                  label="Value"
                  value={
                    typeof route.value === "object"
                      ? JSON.stringify(route.value)
                      : route.value || ""
                  }
                  onChange={(e) => {
                    let val = e.target.value;
                    if (route.operator === "$in") {
                      try {
                        val = JSON.parse(val);
                      } catch {
                        /* keep as string */
                      }
                    }
                    handleConditionalRouteChange(idx, "value", val);
                  }}
                  sx={{ width: 180 }}
                  helperText={route.operator === "$in" ? '["a","b"]' : ""}
                />
                <TextField
                  size="small"
                  label="Target Provider"
                  value={route.target || ""}
                  onChange={(e) =>
                    handleConditionalRouteChange(idx, "target", e.target.value)
                  }
                  sx={{ width: 160 }}
                />
                <IconButton
                  size="small"
                  onClick={() => handleRemoveConditionalRoute(idx)}
                >
                  <Iconify
                    icon="mdi:delete-outline"
                    width={18}
                    color="error.main"
                  />
                </IconButton>
              </Stack>
            </Card>
          ))}

          <Button
            size="small"
            variant="outlined"
            onClick={handleAddConditionalRoute}
            startIcon={<Iconify icon="mdi:plus" width={16} />}
          >
            Add Route Rule
          </Button>
        </Stack>
      </ConfigSection>

      {/* ===================== CIRCUIT BREAKER ===================== */}
      <ConfigSection
        title="Circuit Breaker"
        subtitle="Automatically stop sending requests to failing providers"
        icon="mdi:electric-switch"
      >
        <Stack spacing={2}>
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={circuitBreaker.enabled || false}
                onChange={(e) =>
                  handleNestedChange(
                    "circuit_breaker",
                    "enabled",
                    e.target.checked,
                  )
                }
              />
            }
            label={
              <Typography variant="body2">Enable Circuit Breaker</Typography>
            }
          />

          {circuitBreaker.enabled && (
            <>
              <Stack direction="row" spacing={2} flexWrap="wrap">
                <TextField
                  size="small"
                  type="number"
                  label="Failure Threshold"
                  value={
                    circuitBreaker.failure_threshold ??
                    circuitBreaker.failureThreshold ??
                    5
                  }
                  onChange={(e) =>
                    handleNestedChange(
                      "circuit_breaker",
                      "failure_threshold",
                      Number(e.target.value),
                    )
                  }
                  sx={{ width: 150 }}
                  helperText="Failures to open circuit"
                />
                <TextField
                  size="small"
                  type="number"
                  label="Success Threshold"
                  value={
                    circuitBreaker.success_threshold ??
                    circuitBreaker.successThreshold ??
                    3
                  }
                  onChange={(e) =>
                    handleNestedChange(
                      "circuit_breaker",
                      "success_threshold",
                      Number(e.target.value),
                    )
                  }
                  sx={{ width: 150 }}
                  helperText="Successes to close circuit"
                />
                <TextField
                  size="small"
                  label="Cooldown"
                  value={circuitBreaker.cooldown || "30s"}
                  onChange={(e) =>
                    handleNestedChange(
                      "circuit_breaker",
                      "cooldown",
                      e.target.value,
                    )
                  }
                  sx={{ width: 120 }}
                  helperText="e.g. 30s, 1m"
                />
              </Stack>

              <Typography variant="body2" fontWeight={500}>
                Trigger on Status Codes
              </Typography>
              <Stack direction="row" spacing={0.5} flexWrap="wrap">
                {(
                  circuitBreaker.on_status_codes ||
                  circuitBreaker.onStatusCodes ||
                  []
                ).map((code, i) => (
                  <Chip
                    key={i}
                    label={code}
                    size="small"
                    color="error"
                    variant="outlined"
                    onDelete={() => handleRemoveCBStatusCode(i)}
                  />
                ))}
              </Stack>
              <Stack direction="row" spacing={1}>
                <TextField
                  size="small"
                  type="number"
                  placeholder="Status code (e.g. 429)"
                  value={newCBStatusCode}
                  onChange={(e) => setNewCBStatusCode(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      handleAddCBStatusCode();
                    }
                  }}
                  sx={{ width: 180 }}
                />
                <Button
                  size="small"
                  variant="outlined"
                  onClick={handleAddCBStatusCode}
                >
                  Add
                </Button>
              </Stack>
            </>
          )}
        </Stack>
      </ConfigSection>

      {/* ===================== RETRY CONFIG ===================== */}
      <ConfigSection
        title="Retry Configuration"
        subtitle="Automatically retry failed requests with exponential backoff"
        icon="mdi:refresh"
      >
        <Stack spacing={2}>
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={retryConfig.enabled || false}
                onChange={(e) =>
                  handleNestedChange("retry", "enabled", e.target.checked)
                }
              />
            }
            label={<Typography variant="body2">Enable Retries</Typography>}
          />

          {retryConfig.enabled && (
            <>
              <Stack direction="row" spacing={2} flexWrap="wrap">
                <TextField
                  size="small"
                  type="number"
                  label="Max Retries"
                  value={retryConfig.max_retries ?? retryConfig.maxRetries ?? 3}
                  onChange={(e) =>
                    handleNestedChange(
                      "retry",
                      "max_retries",
                      Number(e.target.value),
                    )
                  }
                  sx={{ width: 120 }}
                  inputProps={{ min: 1, max: 10 }}
                />
                <TextField
                  size="small"
                  label="Initial Delay"
                  value={
                    retryConfig.initial_delay ??
                    retryConfig.initialDelay ??
                    "500ms"
                  }
                  onChange={(e) =>
                    handleNestedChange("retry", "initial_delay", e.target.value)
                  }
                  sx={{ width: 130 }}
                  helperText="e.g. 500ms, 1s"
                />
                <TextField
                  size="small"
                  label="Max Delay"
                  value={retryConfig.max_delay ?? retryConfig.maxDelay ?? "30s"}
                  onChange={(e) =>
                    handleNestedChange("retry", "max_delay", e.target.value)
                  }
                  sx={{ width: 120 }}
                  helperText="e.g. 30s, 1m"
                />
                <TextField
                  size="small"
                  type="number"
                  label="Multiplier"
                  value={retryConfig.multiplier ?? 2.0}
                  onChange={(e) =>
                    handleNestedChange(
                      "retry",
                      "multiplier",
                      Number(e.target.value),
                    )
                  }
                  sx={{ width: 120 }}
                  inputProps={{ step: 0.5, min: 1 }}
                  helperText="Backoff factor"
                />
              </Stack>

              <FormControlLabel
                control={
                  <Switch
                    size="small"
                    checked={
                      retryConfig.on_timeout ?? retryConfig.onTimeout ?? true
                    }
                    onChange={(e) =>
                      handleNestedChange(
                        "retry",
                        "on_timeout",
                        e.target.checked,
                      )
                    }
                  />
                }
                label={
                  <Typography variant="body2">Retry on Timeout</Typography>
                }
              />

              <Typography variant="body2" fontWeight={500}>
                Retry on Status Codes
              </Typography>
              <Stack direction="row" spacing={0.5} flexWrap="wrap">
                {(
                  retryConfig.on_status_codes ||
                  retryConfig.onStatusCodes ||
                  []
                ).map((code, i) => (
                  <Chip
                    key={i}
                    label={code}
                    size="small"
                    color="warning"
                    variant="outlined"
                    onDelete={() => handleRemoveRetryStatusCode(i)}
                  />
                ))}
              </Stack>
              <Stack direction="row" spacing={1}>
                <TextField
                  size="small"
                  type="number"
                  placeholder="Status code (e.g. 429, 500)"
                  value={newRetryStatusCode}
                  onChange={(e) => setNewRetryStatusCode(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      handleAddRetryStatusCode();
                    }
                  }}
                  sx={{ width: 200 }}
                />
                <Button
                  size="small"
                  variant="outlined"
                  onClick={handleAddRetryStatusCode}
                >
                  Add
                </Button>
              </Stack>
            </>
          )}
        </Stack>
      </ConfigSection>

      {/* ===================== TRAFFIC MIRRORING ===================== */}
      <ConfigSection
        title="Traffic Mirroring"
        subtitle="Mirror requests to secondary providers for comparison and testing"
        icon="mdi:content-copy"
      >
        <Stack spacing={2}>
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={mirrorConfig.enabled || false}
                onChange={(e) =>
                  onChange({
                    ...config,
                    mirror: { ...mirrorConfig, enabled: e.target.checked },
                  })
                }
              />
            }
            label={
              <Typography variant="body2">Enable Traffic Mirroring</Typography>
            }
          />

          {mirrorConfig.enabled && (
            <>
              <Alert severity="info" variant="outlined" sx={{ mb: 1 }}>
                Mirror rules send a copy of matching requests to a secondary
                provider. The primary response is returned to the client; the
                mirror response is logged for comparison.
              </Alert>

              {mirrorRules.length === 0 && (
                <Typography variant="body2" color="text.secondary">
                  No mirror rules defined. Add rules to specify which models to
                  mirror and to which target.
                </Typography>
              )}

              {mirrorRules.map((rule, idx) => (
                <Card key={idx} variant="outlined" sx={{ p: 1.5 }}>
                  <Stack
                    direction="row"
                    spacing={1.5}
                    alignItems="center"
                    flexWrap="wrap"
                  >
                    <TextField
                      select
                      size="small"
                      label="Source Model"
                      value={rule.source_model || ""}
                      onChange={(e) =>
                        handleMirrorRuleChange(
                          idx,
                          "source_model",
                          e.target.value,
                        )
                      }
                      sx={{ minWidth: 180 }}
                    >
                      {availableModels.map((m) => (
                        <MenuItem key={m} value={m}>
                          {m}
                        </MenuItem>
                      ))}
                    </TextField>
                    <TextField
                      size="small"
                      label="Target Provider"
                      value={rule.target_provider || rule.targetProvider || ""}
                      onChange={(e) =>
                        handleMirrorRuleChange(
                          idx,
                          "target_provider",
                          e.target.value,
                        )
                      }
                      sx={{ width: 150 }}
                    />
                    <TextField
                      size="small"
                      label="Target Model"
                      value={rule.target_model || rule.targetModel || ""}
                      onChange={(e) =>
                        handleMirrorRuleChange(
                          idx,
                          "target_model",
                          e.target.value,
                        )
                      }
                      sx={{ width: 180 }}
                    />
                    <TextField
                      size="small"
                      type="number"
                      label="Sample Rate"
                      value={rule.sample_rate ?? 1.0}
                      onChange={(e) =>
                        handleMirrorRuleChange(
                          idx,
                          "sample_rate",
                          Number(e.target.value),
                        )
                      }
                      sx={{ width: 120 }}
                      inputProps={{ step: 0.1, min: 0, max: 1 }}
                      helperText="0-1 (1 = 100%)"
                    />
                    <IconButton
                      size="small"
                      onClick={() => handleRemoveMirrorRule(idx)}
                    >
                      <Iconify
                        icon="mdi:delete-outline"
                        width={18}
                        color="error.main"
                      />
                    </IconButton>
                  </Stack>
                </Card>
              ))}

              <Button
                size="small"
                variant="outlined"
                onClick={handleAddMirrorRule}
                startIcon={<Iconify icon="mdi:plus" width={16} />}
              >
                Add Mirror Rule
              </Button>
            </>
          )}
        </Stack>
      </ConfigSection>

      {/* ===================== MODEL TIMEOUTS ===================== */}
      <ConfigSection
        title="Model Timeouts"
        subtitle="Set per-model request timeout overrides"
        icon="mdi:timer-outline"
      >
        <Stack spacing={2}>
          <Alert severity="info" variant="outlined" sx={{ mb: 1 }}>
            Override the default request timeout for specific models. Useful for
            models that require longer processing (e.g. vision, code
            generation).
          </Alert>

          {Object.keys(modelTimeouts).length === 0 && (
            <Typography variant="body2" color="text.secondary">
              No model-specific timeouts. All models use the default timeout.
            </Typography>
          )}

          {Object.entries(modelTimeouts).map(([model, timeout]) => (
            <Stack
              key={model}
              direction="row"
              spacing={1.5}
              alignItems="center"
            >
              <Chip
                label={model}
                size="small"
                color="primary"
                variant="outlined"
              />
              <Typography variant="body2" color="text.secondary">
                {timeout}
              </Typography>
              <IconButton
                size="small"
                onClick={() => handleRemoveModelTimeout(model)}
              >
                <Iconify
                  icon="mdi:delete-outline"
                  width={18}
                  color="error.main"
                />
              </IconButton>
            </Stack>
          ))}

          <Stack direction="row" spacing={1}>
            <TextField
              select
              size="small"
              label="Model"
              value={newTimeoutModel}
              onChange={(e) => setNewTimeoutModel(e.target.value)}
              sx={{ minWidth: 200 }}
            >
              {availableModels.map((m) => (
                <MenuItem key={m} value={m}>
                  {m}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              size="small"
              label="Timeout"
              placeholder="e.g. 60s, 2m"
              value={newTimeoutValue}
              onChange={(e) => setNewTimeoutValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  handleAddModelTimeout();
                }
              }}
              sx={{ width: 120 }}
            />
            <Button
              size="small"
              variant="outlined"
              onClick={handleAddModelTimeout}
            >
              Add
            </Button>
          </Stack>
        </Stack>
      </ConfigSection>
    </Stack>
  );
};

export default RoutingConfigTab;
