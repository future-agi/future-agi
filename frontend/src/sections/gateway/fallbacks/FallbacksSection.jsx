/* eslint-disable react/prop-types */
import React, { useState, useMemo, useCallback, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Typography,
  Stack,
  Card,
  CardContent,
  Button,
  Switch,
  FormControlLabel,
  TextField,
  MenuItem,
  Chip,
  IconButton,
  Collapse,
  Divider,
  Skeleton,
  Grid,
  Tooltip,
  Alert,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import Iconify from "src/components/iconify";
import SectionHeader from "../components/SectionHeader";
import PageErrorState from "../components/PageErrorState";
import { GATEWAY_ICONS } from "../constants/gatewayIcons";
import { useFallbackConfig } from "./hooks/useFallbackConfig";
import { useProviderHealth } from "../providers/hooks/useGatewayConfig";
import { useGatewayContext } from "../context/useGatewayContext";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_FAILOVER_CODES = [429, 500, 502, 503, 504];
const DEFAULT_RETRY_CODES = [429, 500, 502, 503];
const DEFAULT_CB_CODES = [500, 502, 503, 504];

// ---------------------------------------------------------------------------
// Collapsible ConfigSection
// ---------------------------------------------------------------------------

const ConfigSection = ({
  title,
  subtitle,
  icon,
  children,
  defaultOpen = false,
  enabled,
  onToggle,
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
          <Stack direction="row" alignItems="center" spacing={1}>
            {onToggle !== undefined && (
              <FormControlLabel
                control={
                  <Switch
                    size="small"
                    checked={Boolean(enabled)}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => onToggle(e.target.checked)}
                  />
                }
                label=""
                sx={{ mr: 0 }}
              />
            )}
            <Iconify
              icon={open ? "mdi:chevron-up" : "mdi:chevron-down"}
              width={20}
              sx={{ color: "text.secondary" }}
            />
          </Stack>
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

// ---------------------------------------------------------------------------
// StatusCodeChips — reusable chip-based code editor
// ---------------------------------------------------------------------------

const StatusCodeChips = ({ codes, onChange, defaults }) => {
  const [input, setInput] = useState("");

  const handleAdd = () => {
    const num = Number(input.trim());
    if (!num || codes.includes(num)) return;
    onChange([...codes, num]);
    setInput("");
  };

  const handleRemove = (code) => {
    onChange(codes.filter((c) => c !== code));
  };

  const handleReset = () => {
    onChange([...defaults]);
  };

  return (
    <Stack spacing={1}>
      <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
        {codes.map((code) => (
          <Chip
            key={code}
            label={code}
            size="small"
            onDelete={() => handleRemove(code)}
            color={code === 429 ? "warning" : "error"}
            variant="outlined"
          />
        ))}
        {codes.length === 0 && (
          <Typography variant="caption" color="text.secondary">
            No status codes configured
          </Typography>
        )}
      </Stack>
      <Stack direction="row" spacing={1} alignItems="center">
        <TextField
          size="small"
          placeholder="Add code..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleAdd()}
          sx={{ width: 120 }}
          type="number"
        />
        <Button size="small" onClick={handleAdd} disabled={!input.trim()}>
          Add
        </Button>
        <Button
          size="small"
          variant="text"
          color="inherit"
          onClick={handleReset}
        >
          Reset defaults
        </Button>
      </Stack>
    </Stack>
  );
};

// ---------------------------------------------------------------------------
// FallbackChainEditor — inline chain editor
// ---------------------------------------------------------------------------

const FallbackChainEditor = ({
  primaryModel,
  fallbacks,
  onChange,
  onRemove,
  models,
}) => {
  const handlePrimaryChange = (e) => {
    onChange(e.target.value, fallbacks);
  };

  const handleFallbackChange = (index, value) => {
    const updated = [...fallbacks];
    updated[index] = value;
    onChange(primaryModel, updated);
  };

  const handleAddFallback = () => {
    onChange(primaryModel, [...fallbacks, ""]);
  };

  const handleRemoveFallback = (index) => {
    onChange(
      primaryModel,
      fallbacks.filter((_, i) => i !== index),
    );
  };

  return (
    <Card
      variant="outlined"
      sx={{
        mb: 1.5,
        bgcolor: "action.hover",
        border: "1px dashed",
        borderColor: "divider",
      }}
    >
      <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          flexWrap="wrap"
          useFlexGap
        >
          <Chip
            label="Primary"
            size="small"
            color="primary"
            variant="outlined"
            sx={{ fontWeight: 600 }}
          />
          <TextField
            select
            size="small"
            value={primaryModel}
            onChange={handlePrimaryChange}
            sx={{ minWidth: 180 }}
          >
            {models.map((m) => (
              <MenuItem key={m} value={m}>
                {m}
              </MenuItem>
            ))}
          </TextField>

          {fallbacks.map((fb, i) => (
            <React.Fragment key={i}>
              <Iconify
                icon="mdi:arrow-right-bold"
                width={20}
                sx={{ color: "text.secondary" }}
              />
              <Chip
                label={`#${i + 1}`}
                size="small"
                variant="outlined"
                sx={{ fontWeight: 600, minWidth: 32 }}
              />
              <TextField
                select
                size="small"
                value={fb}
                onChange={(e) => handleFallbackChange(i, e.target.value)}
                sx={{ minWidth: 180 }}
              >
                {models.map((m) => (
                  <MenuItem key={m} value={m}>
                    {m}
                  </MenuItem>
                ))}
              </TextField>
              <IconButton
                size="small"
                onClick={() => handleRemoveFallback(i)}
                sx={{ color: "text.secondary" }}
              >
                <Iconify icon="mdi:close-circle-outline" width={18} />
              </IconButton>
            </React.Fragment>
          ))}

          <Button
            size="small"
            onClick={handleAddFallback}
            startIcon={<Iconify icon="mdi:plus" width={16} />}
            sx={{ textTransform: "none" }}
          >
            Add Fallback
          </Button>

          {onRemove && (
            <Tooltip title="Remove this chain">
              <IconButton
                size="small"
                onClick={onRemove}
                aria-label="Remove this chain"
                sx={{ ml: "auto", color: "error.main" }}
              >
                <Iconify icon="mdi:delete-outline" width={20} />
              </IconButton>
            </Tooltip>
          )}
        </Stack>
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// ModelTimeoutsEditor
// ---------------------------------------------------------------------------

const ModelTimeoutsEditor = ({ timeouts, onChange, models }) => {
  const [newModel, setNewModel] = useState("");
  const [newTimeout, setNewTimeout] = useState("");

  const entries = Object.entries(timeouts || {});

  const handleAdd = () => {
    if (!newModel || !newTimeout.trim()) return;
    onChange({ ...timeouts, [newModel]: newTimeout.trim() });
    setNewModel("");
    setNewTimeout("");
  };

  const handleRemove = (model) => {
    const updated = { ...timeouts };
    delete updated[model];
    onChange(updated);
  };

  return (
    <Stack spacing={1.5}>
      {entries.length > 0 ? (
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          {entries.map(([model, timeout]) => (
            <Chip
              key={model}
              label={`${model}: ${timeout}`}
              onDelete={() => handleRemove(model)}
              variant="outlined"
              sx={{ fontFamily: "monospace" }}
            />
          ))}
        </Stack>
      ) : (
        <Typography variant="body2" color="text.secondary">
          No model-specific timeouts. Gateway defaults apply.
        </Typography>
      )}
      <Stack direction="row" spacing={1} alignItems="center">
        <TextField
          select
          size="small"
          value={newModel}
          onChange={(e) => setNewModel(e.target.value)}
          sx={{ minWidth: 180 }}
          label="Model"
        >
          {models.map((m) => (
            <MenuItem key={m} value={m}>
              {m}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          size="small"
          value={newTimeout}
          onChange={(e) => setNewTimeout(e.target.value)}
          placeholder="e.g. 60s, 300s"
          label="Timeout"
          sx={{ width: 140 }}
        />
        <Button
          size="small"
          onClick={handleAdd}
          disabled={!newModel || !newTimeout.trim()}
        >
          Add
        </Button>
      </Stack>
    </Stack>
  );
};

// ---------------------------------------------------------------------------
// Main: FallbacksSection
// ---------------------------------------------------------------------------

const FallbacksSection = () => {
  const navigate = useNavigate();
  const { routing, isLoading, error, refetch, saveRouting, isSaving } =
    useFallbackConfig();
  const { gatewayId } = useGatewayContext();
  const { data: providerHealth } = useProviderHealth(gatewayId);

  const availableModels = useMemo(() => {
    const providers = providerHealth?.providers || [];
    const modelSet = new Set();
    const list = Array.isArray(providers)
      ? providers
      : Object.values(providers);
    list.forEach((p) => {
      if (Array.isArray(p?.models)) p.models.forEach((m) => modelSet.add(m));
    });
    return Array.from(modelSet).sort();
  }, [providerHealth]);

  const noModelsConfigured = availableModels.length === 0;

  // Local draft state — initialized from server data
  const [draft, setDraft] = useState(null);
  const [hasChanges, setHasChanges] = useState(false);

  // Initialize draft when routing data loads, and re-sync after save
  useEffect(() => {
    if (routing && (!draft || !hasChanges)) {
      setDraft({ ...routing });
      setHasChanges(false);
    }
  }, [routing]); // eslint-disable-line react-hooks/exhaustive-deps

  // Helpers to update draft
  const updateDraft = useCallback((field, value) => {
    setDraft((prev) => ({ ...prev, [field]: value }));
    setHasChanges(true);
  }, []);

  const updateNested = useCallback((section, field, value) => {
    setDraft((prev) => ({
      ...prev,
      [section]: { ...(prev[section] || {}), [field]: value },
    }));
    setHasChanges(true);
  }, []);

  // Fallback chain helpers
  const fallbackChains = useMemo(() => {
    const fb = draft?.model_fallbacks || draft?.modelFallbacks || {};
    return Object.entries(fb);
  }, [draft]);

  const handleChainChange = useCallback(
    (newPrimary, newFallbacks, oldPrimary) => {
      const fb = { ...(draft?.model_fallbacks || draft?.modelFallbacks || {}) };
      if (oldPrimary && oldPrimary !== newPrimary) {
        delete fb[oldPrimary];
      }
      fb[newPrimary] = newFallbacks;
      updateDraft("model_fallbacks", fb);
    },
    [draft, updateDraft],
  );

  const handleRemoveChain = useCallback(
    (primary) => {
      const fb = { ...(draft?.model_fallbacks || draft?.modelFallbacks || {}) };
      delete fb[primary];
      updateDraft("model_fallbacks", fb);
    },
    [draft, updateDraft],
  );

  const handleAddChain = useCallback(() => {
    const fb = { ...(draft?.model_fallbacks || draft?.modelFallbacks || {}) };
    // Find a model not already used as a primary
    const used = new Set(Object.keys(fb));
    const firstAvailable = availableModels.find((m) => !used.has(m));
    if (!firstAvailable) return; // all models already have chains
    fb[firstAvailable] = [];
    updateDraft("model_fallbacks", fb);
  }, [draft, availableModels, updateDraft]);

  // Save handler
  const handleSave = async () => {
    try {
      await saveRouting(draft);
      setHasChanges(false);
    } catch {
      // error handled by mutation
    }
  };

  // Reset handler
  const handleReset = () => {
    setDraft({ ...routing });
    setHasChanges(false);
  };

  // Convenience getters
  const failover = draft?.failover || {};
  const retry = draft?.retry || {};
  const circuitBreaker = draft?.circuit_breaker || draft?.circuitBreaker || {};
  const modelTimeouts = draft?.model_timeouts || draft?.modelTimeouts || {};

  // -------------------------------------------------------------------------
  // Loading state
  // -------------------------------------------------------------------------
  if (isLoading) {
    return (
      <Box p={3}>
        <Stack direction="row" justifyContent="space-between" mb={3}>
          <Skeleton width={280} height={40} />
          <Skeleton width={100} height={36} variant="rounded" />
        </Stack>
        <Stack spacing={2}>
          {[...Array(4)].map((_, i) => (
            <Skeleton key={i} variant="rounded" height={80} />
          ))}
        </Stack>
      </Box>
    );
  }

  // -------------------------------------------------------------------------
  // Error state
  // -------------------------------------------------------------------------
  if (error) {
    return (
      <Box p={3}>
        <PageErrorState
          message={`Failed to load config: ${error.message}`}
          onRetry={refetch}
        />
      </Box>
    );
  }

  if (!draft) return null;

  // -------------------------------------------------------------------------
  // No models configured — guide user to set up providers first
  // -------------------------------------------------------------------------
  if (noModelsConfigured) {
    return (
      <Box p={3}>
        <SectionHeader
          icon={GATEWAY_ICONS.fallbacks}
          title="Fallbacks & Reliability"
          subtitle="Configure model fallback chains and provider reliability settings"
          actions={[]}
        />
        <Card sx={{ mt: 3 }}>
          <CardContent>
            <Stack alignItems="center" spacing={2} py={4}>
              <Iconify
                icon="solar:shield-warning-bold-duotone"
                width={56}
                sx={{ color: "text.disabled" }}
              />
              <Typography variant="h6" color="text.secondary">
                No models configured yet
              </Typography>
              <Typography
                variant="body2"
                color="text.secondary"
                textAlign="center"
                maxWidth={460}
              >
                Fallback chains route requests to backup models when the primary
                model is unavailable. To get started, add a provider with at
                least one model in the <strong>Providers</strong> tab.
              </Typography>
              <Button
                variant="contained"
                color="primary"
                startIcon={<Iconify icon="solar:add-circle-bold" />}
                onClick={() => navigate("/dashboard/gateway/providers")}
              >
                Configure Providers
              </Button>
            </Stack>
          </CardContent>
        </Card>
      </Box>
    );
  }

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------
  return (
    <Box p={3}>
      <SectionHeader
        icon={GATEWAY_ICONS.fallbacks}
        title="Fallbacks & Reliability"
        subtitle="Configure model fallback chains and provider reliability settings"
        actions={[
          ...(hasChanges
            ? [
                {
                  label: "Discard",
                  variant: "outlined",
                  color: "inherit",
                  size: "small",
                  onClick: handleReset,
                },
              ]
            : []),
        ]}
      />

      {hasChanges && (
        <Alert severity="info" sx={{ mb: 2 }} icon={false}>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
          >
            <Typography variant="body2">
              You have unsaved changes. Save to apply them to the gateway.
            </Typography>
            <LoadingButton
              loading={isSaving}
              variant="contained"
              size="small"
              onClick={handleSave}
              startIcon={<Iconify icon="mdi:content-save-outline" width={18} />}
            >
              Save & Apply
            </LoadingButton>
          </Stack>
        </Alert>
      )}

      <Stack spacing={2}>
        {/* ---- Model Fallback Chains ---- */}
        <ConfigSection
          title="Model Fallback Chains"
          subtitle="When all providers for a model fail, try the next model in the chain"
          icon="mdi:link-variant"
          defaultOpen
          enabled={draft.fallback_enabled ?? draft.fallbackEnabled ?? true}
          onToggle={(v) => updateDraft("fallback_enabled", v)}
        >
          <Stack spacing={2}>
            {/* Default model */}
            <Stack direction="row" spacing={2} alignItems="center">
              <Typography variant="body2" sx={{ minWidth: 120 }}>
                Default Model
              </Typography>
              <TextField
                select
                size="small"
                value={draft.default_model || draft.defaultModel || ""}
                onChange={(e) => updateDraft("default_model", e.target.value)}
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

            <Divider />

            {/* Chains */}
            {fallbackChains.length === 0 ? (
              <Box py={3} textAlign="center">
                <Iconify
                  icon="mdi:link-variant-off"
                  width={40}
                  sx={{ color: "text.disabled", mb: 1 }}
                />
                <Typography variant="body2" color="text.secondary" mb={2}>
                  No fallback chains configured. When a model&apos;s providers
                  all fail, the request will fail.
                </Typography>
              </Box>
            ) : (
              fallbackChains.map(([primary, fbs]) => (
                <FallbackChainEditor
                  key={primary}
                  primaryModel={primary}
                  fallbacks={fbs || []}
                  models={availableModels}
                  onChange={(newPrimary, newFallbacks) =>
                    handleChainChange(newPrimary, newFallbacks, primary)
                  }
                  onRemove={() => handleRemoveChain(primary)}
                />
              ))
            )}

            <Button
              variant="outlined"
              size="small"
              onClick={handleAddChain}
              startIcon={<Iconify icon="mdi:plus" width={18} />}
              sx={{ alignSelf: "flex-start", textTransform: "none" }}
            >
              Add Fallback Chain
            </Button>
          </Stack>
        </ConfigSection>

        {/* ---- Provider Failover ---- */}
        <ConfigSection
          title="Provider Failover"
          subtitle="Automatically try a different provider when one fails"
          icon="mdi:swap-horizontal-bold"
          enabled={failover.enabled ?? true}
          onToggle={(v) => updateNested("failover", "enabled", v)}
        >
          <Stack spacing={2}>
            <Grid container spacing={2}>
              <Grid item xs={12} sm={4}>
                <TextField
                  label="Max Attempts"
                  type="number"
                  size="small"
                  fullWidth
                  value={failover.max_attempts ?? failover.maxAttempts ?? 3}
                  onChange={(e) =>
                    updateNested(
                      "failover",
                      "max_attempts",
                      Number(e.target.value),
                    )
                  }
                  inputProps={{ min: 1, max: 10 }}
                />
              </Grid>
              <Grid item xs={12} sm={4}>
                <TextField
                  label="Per-Attempt Timeout"
                  size="small"
                  fullWidth
                  value={
                    failover.per_attempt_timeout ??
                    failover.perAttemptTimeout ??
                    ""
                  }
                  onChange={(e) =>
                    updateNested(
                      "failover",
                      "per_attempt_timeout",
                      e.target.value,
                    )
                  }
                  placeholder="e.g. 30s"
                />
              </Grid>
              <Grid item xs={12} sm={4}>
                <FormControlLabel
                  control={
                    <Switch
                      size="small"
                      checked={
                        failover.on_timeout ?? failover.onTimeout ?? true
                      }
                      onChange={(e) =>
                        updateNested("failover", "on_timeout", e.target.checked)
                      }
                    />
                  }
                  label={
                    <Typography variant="body2">Failover on timeout</Typography>
                  }
                />
              </Grid>
            </Grid>

            <Box>
              <Typography variant="body2" fontWeight={500} mb={1}>
                Trigger on Status Codes
              </Typography>
              <StatusCodeChips
                codes={
                  failover.on_status_codes ??
                  failover.onStatusCodes ??
                  DEFAULT_FAILOVER_CODES
                }
                onChange={(codes) =>
                  updateNested("failover", "on_status_codes", codes)
                }
                defaults={DEFAULT_FAILOVER_CODES}
              />
            </Box>
          </Stack>
        </ConfigSection>

        {/* ---- Retry Configuration ---- */}
        <ConfigSection
          title="Retry"
          subtitle="Retry failed requests with exponential backoff before failing over"
          icon="mdi:refresh"
          enabled={retry.enabled ?? false}
          onToggle={(v) => updateNested("retry", "enabled", v)}
        >
          <Stack spacing={2}>
            <Grid container spacing={2}>
              <Grid item xs={6} sm={3}>
                <TextField
                  label="Max Retries"
                  type="number"
                  size="small"
                  fullWidth
                  value={retry.max_retries ?? retry.maxRetries ?? 2}
                  onChange={(e) =>
                    updateNested("retry", "max_retries", Number(e.target.value))
                  }
                  inputProps={{ min: 1, max: 5 }}
                />
              </Grid>
              <Grid item xs={6} sm={3}>
                <TextField
                  label="Initial Delay"
                  size="small"
                  fullWidth
                  value={retry.initial_delay ?? retry.initialDelay ?? "500ms"}
                  onChange={(e) =>
                    updateNested("retry", "initial_delay", e.target.value)
                  }
                  placeholder="e.g. 500ms"
                />
              </Grid>
              <Grid item xs={6} sm={3}>
                <TextField
                  label="Max Delay"
                  size="small"
                  fullWidth
                  value={retry.max_delay ?? retry.maxDelay ?? "10s"}
                  onChange={(e) =>
                    updateNested("retry", "max_delay", e.target.value)
                  }
                  placeholder="e.g. 10s"
                />
              </Grid>
              <Grid item xs={6} sm={3}>
                <TextField
                  label="Backoff Multiplier"
                  type="number"
                  size="small"
                  fullWidth
                  value={retry.multiplier ?? 2.0}
                  onChange={(e) =>
                    updateNested("retry", "multiplier", Number(e.target.value))
                  }
                  inputProps={{ min: 1, max: 10, step: 0.5 }}
                />
              </Grid>
            </Grid>

            <FormControlLabel
              control={
                <Switch
                  size="small"
                  checked={retry.on_timeout ?? retry.onTimeout ?? true}
                  onChange={(e) =>
                    updateNested("retry", "on_timeout", e.target.checked)
                  }
                />
              }
              label={<Typography variant="body2">Retry on timeout</Typography>}
            />

            <Box>
              <Typography variant="body2" fontWeight={500} mb={1}>
                Retry on Status Codes
              </Typography>
              <StatusCodeChips
                codes={
                  retry.on_status_codes ??
                  retry.onStatusCodes ??
                  DEFAULT_RETRY_CODES
                }
                onChange={(codes) =>
                  updateNested("retry", "on_status_codes", codes)
                }
                defaults={DEFAULT_RETRY_CODES}
              />
            </Box>
          </Stack>
        </ConfigSection>

        {/* ---- Circuit Breaker ---- */}
        <ConfigSection
          title="Circuit Breaker"
          subtitle="Temporarily remove unhealthy providers from the routing pool"
          icon="mdi:electric-switch"
          enabled={circuitBreaker.enabled ?? false}
          onToggle={(v) => updateNested("circuit_breaker", "enabled", v)}
        >
          <Stack spacing={2}>
            <Grid container spacing={2}>
              <Grid item xs={6} sm={4}>
                <TextField
                  label="Failure Threshold"
                  type="number"
                  size="small"
                  fullWidth
                  value={
                    circuitBreaker.failure_threshold ??
                    circuitBreaker.failureThreshold ??
                    5
                  }
                  onChange={(e) =>
                    updateNested(
                      "circuit_breaker",
                      "failure_threshold",
                      Number(e.target.value),
                    )
                  }
                  helperText="Consecutive failures to open circuit"
                  inputProps={{ min: 1, max: 100 }}
                />
              </Grid>
              <Grid item xs={6} sm={4}>
                <TextField
                  label="Success Threshold"
                  type="number"
                  size="small"
                  fullWidth
                  value={
                    circuitBreaker.success_threshold ??
                    circuitBreaker.successThreshold ??
                    2
                  }
                  onChange={(e) =>
                    updateNested(
                      "circuit_breaker",
                      "success_threshold",
                      Number(e.target.value),
                    )
                  }
                  helperText="Successes in half-open to close"
                  inputProps={{ min: 1, max: 50 }}
                />
              </Grid>
              <Grid item xs={12} sm={4}>
                <TextField
                  label="Cooldown"
                  size="small"
                  fullWidth
                  value={circuitBreaker.cooldown ?? "30s"}
                  onChange={(e) =>
                    updateNested("circuit_breaker", "cooldown", e.target.value)
                  }
                  helperText="Time before retrying open circuit"
                  placeholder="e.g. 30s"
                />
              </Grid>
            </Grid>

            <Box>
              <Typography variant="body2" fontWeight={500} mb={1}>
                Trigger on Status Codes
              </Typography>
              <StatusCodeChips
                codes={
                  circuitBreaker.on_status_codes ??
                  circuitBreaker.onStatusCodes ??
                  DEFAULT_CB_CODES
                }
                onChange={(codes) =>
                  updateNested("circuit_breaker", "on_status_codes", codes)
                }
                defaults={DEFAULT_CB_CODES}
              />
            </Box>
          </Stack>
        </ConfigSection>

        {/* ---- Model Timeouts ---- */}
        <ConfigSection
          title="Model Timeouts"
          subtitle="Override default timeout per model (e.g. longer for reasoning models)"
          icon="mdi:timer-outline"
        >
          <ModelTimeoutsEditor
            timeouts={modelTimeouts}
            models={availableModels}
            onChange={(t) => updateDraft("model_timeouts", t)}
          />
        </ConfigSection>
      </Stack>

      {/* Sticky save bar */}
      {hasChanges && (
        <Box
          sx={{
            position: "sticky",
            bottom: 16,
            mt: 3,
            p: 2,
            bgcolor: "background.paper",
            borderRadius: 1,
            border: 1,
            borderColor: "primary.main",
            boxShadow: 3,
          }}
        >
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
          >
            <Typography variant="body2" fontWeight={500}>
              Unsaved changes will be applied to the live gateway
            </Typography>
            <Stack direction="row" spacing={1}>
              <Button
                variant="outlined"
                size="small"
                color="inherit"
                onClick={handleReset}
              >
                Discard
              </Button>
              <LoadingButton
                loading={isSaving}
                variant="contained"
                size="small"
                onClick={handleSave}
                startIcon={
                  <Iconify icon="mdi:content-save-outline" width={18} />
                }
              >
                Save & Apply
              </LoadingButton>
            </Stack>
          </Stack>
        </Box>
      )}
    </Box>
  );
};

export default FallbacksSection;
