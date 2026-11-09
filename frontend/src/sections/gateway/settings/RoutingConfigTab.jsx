/* eslint-disable react/prop-types */
import React, { useState } from "react";
import {
  Stack,
  Card,
  CardContent,
  Typography,
  MenuItem,
  FormControlLabel,
  ToggleButton,
  Collapse,
  Divider,
  Alert,
} from "@mui/material";
import Iconify from "src/components/iconify";
// Mutations use the same Owner/Admin gate as the save action. Disclosure
// headers remain ordinary controls, so every reader can inspect the config.
import {
  AdminButton as Button,
  AdminSwitch as Switch,
  AdminTextField as TextField,
  AdminChip as Chip,
  AdminIconButton as IconButton,
  AdminSlider as Slider,
  AdminToggleButtonGroup as ToggleButtonGroup,
} from "../components/GatewayWriteControls";
import FallbackChainEditor from "./FallbackChainEditor";
import {
  fallbackChainsOn,
  NO_ROUTING_STRATEGY,
  retryOnTimeout,
  ROUTING_STRATEGY_CHOICES,
  routingStrategyChoice,
} from "../fallbacks/fallbackSwitches";
import { useAvailableModels } from "../hooks/useAvailableModels";
import { storedRoutingOnly } from "./routingNotApplied";
import { useConfirmRemoval } from "../hooks/useConfirmRemoval";

// ---------- Section wrapper for collapsible cards ----------
// The header is a button that says whether the section is open; Enter or
// Space toggles it, and its icons keep their size at phone width.
const ConfigSection = ({
  title,
  subtitle,
  icon,
  children,
  defaultOpen = false,
}) => {
  const [open, setOpen] = useState(defaultOpen);
  const toggle = () => setOpen((v) => !v);
  return (
    <Card variant="outlined">
      <CardContent
        role="button"
        tabIndex={0}
        aria-expanded={open}
        sx={{
          py: 1.5,
          "&:last-child": { pb: 1.5 },
          cursor: "pointer",
          userSelect: "none",
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "primary.main",
            outlineOffset: -2,
          },
        }}
        onClick={toggle}
        onKeyDown={(e) => {
          if (e.key !== "Enter" && e.key !== " ") return;
          e.preventDefault();
          toggle();
        }}
      >
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          spacing={1}
        >
          <Stack direction="row" alignItems="center" spacing={1} minWidth={0}>
            <Iconify
              icon={icon}
              width={20}
              sx={{ color: "primary.main", flexShrink: 0 }}
            />
            <Stack minWidth={0}>
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
            sx={{ color: "text.secondary", flexShrink: 0 }}
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
// `managedOffered` false: the deployment offers no managed models, so no
// fallback can go to one.
const RoutingConfigTab = ({ routing, onChange, managedOffered = true }) => {
  const [requestRemoval, confirmation] = useConfirmRemoval();
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

  // None clears the stored strategy (the org's keys then use the first of
  // its providers by ID); clicking the chosen button again changes nothing.
  const handleStrategyChange = (_, value) => {
    if (!value) return;
    if (value === NO_ROUTING_STRATEGY) {
      const rest = { ...config };
      delete rest.strategy;
      onChange(rest);
      return;
    }
    handleChange("strategy", value);
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
  // The default model, conditional routes, race mode, scheduled completions,
  // access groups and traffic mirroring: stored values this gateway does not
  // run. They are not shown, one line says they are stored, and a save keeps
  // them unchanged (the draft is never cleared of them).
  const stored = storedRoutingOnly(config);

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
    requestRemoval(`Complexity tier ${name}`, () => {
      const tiers = { ...complexityTiers };
      delete tiers[name];
      handleNestedChange("complexity", "tiers", tiers);
    });
  };

  // Adaptive config
  const adaptive = config.adaptive || {};
  const signalWeights = adaptive.signal_weights || adaptive.signalWeights || {};

  // Provider lock config
  const providerLock = config.provider_lock || config.providerLock || {};

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
    requestRemoval(
      `Provider ${providerLock[listField]?.[idx]} from ${listField}`,
      () => {
        const current = [...(providerLock[listField] || [])];
        current.splice(idx, 1);
        handleNestedChange("provider_lock", listField, current);
      },
    );
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
    requestRemoval(
      `Circuit breaker status code ${(circuitBreaker.on_status_codes || circuitBreaker.onStatusCodes || [])[idx]}`,
      () => {
        const current = [
          ...(circuitBreaker.on_status_codes ||
            circuitBreaker.onStatusCodes ||
            []),
        ];
        current.splice(idx, 1);
        handleNestedChange("circuit_breaker", "on_status_codes", current);
      },
    );
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
    requestRemoval(
      `Retry status code ${(retryConfig.on_status_codes || retryConfig.onStatusCodes || [])[idx]}`,
      () => {
        const current = [
          ...(retryConfig.on_status_codes || retryConfig.onStatusCodes || []),
        ];
        current.splice(idx, 1);
        handleNestedChange("retry", "on_status_codes", current);
      },
    );
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
    requestRemoval(`Model timeout ${model}`, () => {
      const updated = { ...modelTimeouts };
      delete updated[model];
      handleChange("model_timeouts", updated);
    });
  };

  return (
    <Stack spacing={2}>
      {confirmation}
      {/* ===================== BASIC STRATEGY ===================== */}
      <ConfigSection
        title="Routing Strategy"
        subtitle="How requests are spread across your providers that serve a model"
        icon="mdi:swap-horizontal"
        defaultOpen
      >
        <ToggleButtonGroup
          value={routingStrategyChoice(config)}
          exclusive
          onChange={handleStrategyChange}
          size="small"
          sx={{ mb: 2, flexWrap: "wrap" }}
        >
          {ROUTING_STRATEGY_CHOICES.map((s) => (
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
                inputProps={{ "aria-label": "Fallback Enabled" }}
                checked={fallbackChainsOn(config)}
                onChange={(e) =>
                  handleChange("fallback_enabled", e.target.checked)
                }
              />
            }
            label={<Typography variant="body2">Fallback Enabled</Typography>}
          />
          <Typography variant="caption" color="text.secondary">
            Until you turn it on or off, fallback chains run whenever one
            exists.{" "}
            {managedOffered
              ? "For your organization's API keys, strategy, retry, provider failover and circuit breakers use only your own providers; a model fallback can go to a Future AGI model after your organization turns Future AGI credits on."
              : "For your organization's API keys, strategy, retry, provider failover, circuit breakers and model fallbacks use only your own providers."}
          </Typography>
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
                      aria-label={`Remove complexity tier ${name}`}
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

      <Divider sx={{ my: 1 }} />
      <Typography variant="subtitle1">Reliability & Advanced</Typography>

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
                    checked={retryOnTimeout(config)}
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
                aria-label={`Remove model timeout ${model}`}
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

      {stored.rows.length > 0 && (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="routing-stored-not-run"
        >
          This configuration also stores routing settings this gateway does not
          run (default model, conditional or scheduled routes, race mode). A
          save keeps them unchanged.
        </Typography>
      )}
    </Stack>
  );
};

export default RoutingConfigTab;
