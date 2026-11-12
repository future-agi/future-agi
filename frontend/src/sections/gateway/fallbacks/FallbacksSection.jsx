/* eslint-disable react/prop-types */
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Typography,
  Stack,
  Paper,
  Button,
  Switch,
  FormControlLabel,
  TextField,
  MenuItem,
  Chip,
  IconButton,
  Collapse,
  Divider,
  Grid,
  Skeleton,
  Tooltip,
  Alert,
} from "@mui/material";
import Section from "src/components/section";
import { LoadingButton } from "@mui/lab";
import SvgColor from "src/components/svg-color";
import { paths } from "src/routes/paths";
import { GatewayEmptyState, GatewayPageFrame } from "../components/frame";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import { useConfirmRemoval } from "../hooks/useConfirmRemoval";
import {
  fallbackChainsOn,
  failoverOnTimeout,
  providerFailoverOn,
  retryOnTimeout,
} from "./fallbackSwitches";
import { useFallbackConfig } from "./hooks/useFallbackConfig";
import {
  useProviderHealth,
  useReloadConfig,
} from "../providers/hooks/useGatewayConfig";
import { useGatewayContext } from "../context/useGatewayContext";
import { Glyph } from "../settings/ui";
import { GATEWAY_MONO } from "../utils/fonts";

import { MAX_MODEL_FALLBACKS, FALLBACK_LIMIT_MESSAGE } from "./fallbackLimits";
import FallbackChainWarning from "./FallbackChainWarning";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_FAILOVER_CODES = [429, 500, 502, 503, 504];
// The gateway's own defaults when a list is unset (routing.DefaultFailover-,
// DefaultRetry- and DefaultCBStatusCodes), so an unset list shows what runs.
const DEFAULT_RETRY_CODES = [429, 500, 502, 503, 504];
const DEFAULT_CB_CODES = [429, 500, 502, 503, 504];

// Fallbacks save the org's gateway config, as the Org config editor does:
// only an organization Owner or Admin changes them (useGatewayAdmin). For
// anyone else every control on the page reads the config and is disabled,
// each switch and button saying the role it needs.
const ReadOnly = createContext(false);

// ---------------------------------------------------------------------------
// Collapsible ConfigSection
// ---------------------------------------------------------------------------

const ConfigSection = ({
  title,
  subtitle,
  children,
  defaultOpen = false,
  enabled,
  onToggle,
  note,
  testId,
}) => {
  const [open, setOpen] = useState(defaultOpen);
  const bodyId = useId();
  const readOnly = useContext(ReadOnly);
  const toggle = () => setOpen((v) => !v);
  // A click anywhere on the header opens the section (a mouse convenience).
  // The chevron is the accessible toggle, and the on/off switch sits beside
  // it, never inside a button (axe nested-interactive).
  return (
    <Section
      variant="panel"
      component="section"
      data-testid={testId}
      sx={{ p: 0, overflow: "hidden" }}
    >
      <Box
        onClick={toggle}
        sx={{
          px: { xs: 2, sm: 3 },
          py: 1.5,
          cursor: "pointer",
          userSelect: "none",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 1.5,
        }}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography component="h2" typography="s1" fontWeight={600}>
            {title}
          </Typography>
          {subtitle && (
            <Typography typography="s2" color="text.secondary">
              {subtitle}
            </Typography>
          )}
        </Box>
        <Stack direction="row" alignItems="center" spacing={1} flexShrink={0}>
          {onToggle !== undefined && (
            <GatewayAdminOnly allowed={!readOnly}>
              <Switch
                size="small"
                checked={Boolean(enabled)}
                disabled={readOnly}
                inputProps={{ "aria-label": `${title} on` }}
                onClick={(e) => e.stopPropagation()}
                onChange={(e) => onToggle(e.target.checked)}
              />
            </GatewayAdminOnly>
          )}
          <IconButton
            size="small"
            aria-label={title}
            aria-expanded={open}
            aria-controls={bodyId}
            onClick={(e) => {
              e.stopPropagation();
              toggle();
            }}
          >
            <SvgColor
              src="/assets/icons/custom/lucide--chevron-down.svg"
              sx={{
                width: 16,
                height: 16,
                color: "text.secondary",
                transform: open ? "rotate(180deg)" : "none",
              }}
            />
          </IconButton>
        </Stack>
      </Box>
      {note && (
        <Box
          sx={{ px: { xs: 2, sm: 3 }, pb: 1.5 }}
          onClick={(e) => e.stopPropagation()}
        >
          {note}
        </Box>
      )}
      <Collapse in={open} id={bodyId}>
        <Divider />
        <Box
          sx={{ px: { xs: 2, sm: 3 }, py: 2 }}
          onClick={(e) => e.stopPropagation()}
        >
          {children}
        </Box>
      </Collapse>
    </Section>
  );
};

// Every switch shows the value the gateway runs for the org's keys
// (fallbackSwitches.js): the chains run when fallback_enabled is set
// on, or, unset, whenever a chain exists; provider failover runs only when
// failover.enabled is on. The Default Model control is not offered: the
// gateway does not apply it, and a stored value is kept as it is.

// ---------------------------------------------------------------------------
// StatusCodeChips — reusable chip-based code editor
// ---------------------------------------------------------------------------

const StatusCodeChips = ({ codes, onChange, defaults }) => {
  const [requestRemoval, confirmation] = useConfirmRemoval("Save & apply");
  const [input, setInput] = useState("");
  const readOnly = useContext(ReadOnly);

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
      {confirmation}
      <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
        {codes.map((code) => (
          <Chip
            key={code}
            label={code}
            size="small"
            onDelete={
              readOnly
                ? undefined
                : () =>
                    requestRemoval(`Status code ${code}`, () =>
                      handleRemove(code),
                    )
            }
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
          inputProps={{ "aria-label": "Add a status code" }}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleAdd()}
          sx={{ width: 120 }}
          type="number"
          disabled={readOnly}
        />
        <Button
          size="small"
          onClick={handleAdd}
          disabled={readOnly || !input.trim()}
        >
          Add
        </Button>
        <Button
          size="small"
          variant="text"
          color="inherit"
          onClick={handleReset}
          disabled={readOnly}
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
  usedPrimaries,
}) => {
  const [requestRemoval, confirmation] = useConfirmRemoval("Save & apply");
  const [newFallback, setNewFallback] = useState("");
  const readOnly = useContext(ReadOnly);
  const primaryChoices = Array.from(new Set([primaryModel, ...models]));
  const handlePrimaryChange = (e) => {
    const next = e.target.value;
    if (!next.trim() || (next !== primaryModel && usedPrimaries.includes(next)))
      return;
    onChange(next, fallbacks);
  };

  const handleFallbackChange = (index, value) => {
    if (!value.trim()) return;
    const updated = [...fallbacks];
    updated[index] = value;
    onChange(primaryModel, updated);
  };

  const atLimit = fallbacks.length >= MAX_MODEL_FALLBACKS;
  const handleAddFallback = () => {
    if (atLimit || !newFallback.trim() || !models.includes(newFallback)) return;
    onChange(primaryModel, [...fallbacks, newFallback]);
    setNewFallback("");
  };

  const handleRemoveFallback = (index) => {
    onChange(
      primaryModel,
      fallbacks.filter((_, i) => i !== index),
    );
  };

  return (
    <Section
      variant="panel"
      sx={{
        p: 0,
        mb: 1.5,
        bgcolor: "action.hover",
        border: "1px dashed",
        borderColor: "divider",
      }}
    >
      {confirmation}
      <Box sx={{ px: 2, py: 1.5 }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          flexWrap="wrap"
          useFlexGap
        >
          {/* Each step stays on one line; a narrow screen breaks the chain
              only between steps, never inside one. */}
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            flexWrap="nowrap"
            sx={{ minWidth: 0 }}
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
              disabled={readOnly}
              inputProps={{ "aria-label": "Primary model" }}
              sx={{ minWidth: 180 }}
            >
              {primaryChoices.map((m) => (
                <MenuItem
                  key={m}
                  value={m}
                  disabled={m !== primaryModel && usedPrimaries.includes(m)}
                >
                  {m}
                  {m !== primaryModel && usedPrimaries.includes(m)
                    ? " (already has a chain)"
                    : ""}
                </MenuItem>
              ))}
            </TextField>
          </Stack>

          {fallbacks.map((fb, i) => (
            <Stack
              key={i}
              data-testid="fallback-step"
              direction="row"
              alignItems="center"
              spacing={1}
              flexWrap="nowrap"
              sx={{ minWidth: 0, maxWidth: "100%" }}
            >
              <Glyph
                name="arrow"
                sx={{ color: "text.secondary", flexShrink: 0 }}
              />
              <Chip
                label={`#${i + 1}`}
                size="small"
                variant="outlined"
                sx={{ fontWeight: 600, minWidth: 32, flexShrink: 0 }}
              />
              <TextField
                select
                size="small"
                value={fb}
                onChange={(e) => handleFallbackChange(i, e.target.value)}
                disabled={readOnly}
                inputProps={{ "aria-label": `Fallback ${i + 1}` }}
                // At phone width the select gives way, not the step number.
                sx={{
                  minWidth: { xs: 0, sm: 180 },
                  flex: { xs: 1, sm: "0 0 auto" },
                }}
              >
                {Array.from(new Set([fb, ...models])).map((m) => (
                  <MenuItem key={m} value={m}>
                    {m}
                  </MenuItem>
                ))}
              </TextField>
              <GatewayAdminOnly allowed={!readOnly}>
                <IconButton
                  size="small"
                  aria-label={`Remove fallback ${i + 1}`}
                  onClick={() =>
                    requestRemoval(`Fallback ${fb} from ${primaryModel}`, () =>
                      handleRemoveFallback(i),
                    )
                  }
                  disabled={readOnly}
                  sx={{ color: "text.secondary", flexShrink: 0 }}
                >
                  <Glyph name="close" />
                </IconButton>
              </GatewayAdminOnly>
            </Stack>
          ))}

          <TextField
            select
            size="small"
            label="New fallback"
            inputProps={{ "aria-label": `New fallback for ${primaryModel}` }}
            value={models.includes(newFallback) ? newFallback : ""}
            onChange={(e) => setNewFallback(e.target.value)}
            disabled={readOnly || atLimit || models.length === 0}
            sx={{ minWidth: 180 }}
          >
            <MenuItem value="" disabled>
              Choose a model
            </MenuItem>
            {models.map((model) => (
              <MenuItem key={model} value={model}>
                {model}
              </MenuItem>
            ))}
          </TextField>
          <GatewayAdminOnly allowed={!readOnly}>
            <Button
              size="small"
              onClick={handleAddFallback}
              disabled={
                readOnly ||
                atLimit ||
                !newFallback.trim() ||
                !models.includes(newFallback)
              }
              startIcon={<Glyph name="add" />}
            >
              Add fallback
            </Button>
          </GatewayAdminOnly>

          {onRemove &&
            (readOnly ? (
              <Box sx={{ ml: "auto" }}>
                <GatewayAdminOnly allowed={false}>
                  <IconButton
                    size="small"
                    aria-label="Remove this chain"
                    disabled
                  >
                    <Glyph name="delete" />
                  </IconButton>
                </GatewayAdminOnly>
              </Box>
            ) : (
              <Tooltip title="Remove this chain">
                <IconButton
                  size="small"
                  onClick={() =>
                    requestRemoval(`Fallback chain ${primaryModel}`, onRemove)
                  }
                  aria-label="Remove this chain"
                  sx={{ ml: "auto", color: "error.main" }}
                >
                  <Glyph name="delete" />
                </IconButton>
              </Tooltip>
            ))}
        </Stack>
        {!atLimit && !readOnly && (
          <Typography variant="caption" color="text.secondary">
            Choose a model, then select Add fallback to append it. Only added
            models are saved.
          </Typography>
        )}
        <FallbackChainWarning
          fallbacks={fallbacks}
          onTrim={() =>
            onChange(primaryModel, fallbacks.slice(0, MAX_MODEL_FALLBACKS))
          }
        />
        {atLimit && (
          <Typography variant="caption" color="text.secondary" role="status">
            {FALLBACK_LIMIT_MESSAGE}
          </Typography>
        )}
      </Box>
    </Section>
  );
};

// ---------------------------------------------------------------------------
// ModelTimeoutsEditor
// ---------------------------------------------------------------------------

const ModelTimeoutsEditor = ({ timeouts, onChange, models }) => {
  const [requestRemoval, confirmation] = useConfirmRemoval("Save & apply");
  const readOnly = useContext(ReadOnly);
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
      {confirmation}
      {entries.length > 0 ? (
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          {entries.map(([model, timeout]) => (
            <Chip
              key={model}
              label={`${model}: ${timeout}`}
              onDelete={
                readOnly
                  ? undefined
                  : () =>
                      requestRemoval(`Timeout for ${model}`, () =>
                        handleRemove(model),
                      )
              }
              variant="outlined"
              sx={{ fontFamily: GATEWAY_MONO }}
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
          disabled={readOnly}
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
          disabled={readOnly}
          sx={{ width: 140 }}
        />
        <Button
          size="small"
          onClick={handleAdd}
          disabled={readOnly || !newModel || !newTimeout.trim()}
        >
          Add
        </Button>
      </Stack>
    </Stack>
  );
};

// The reload mutation belongs to the recovery control, not to model discovery
// or the config draft. It is only needed after a stored, unconfirmed save.
const RetryApplyButton = ({ canEdit, disabled, busy, onApply }) => {
  const reloadConfig = useReloadConfig();
  return (
    <GatewayAdminOnly allowed={canEdit}>
      <LoadingButton
        size="small"
        color="inherit"
        loading={busy === "apply" || reloadConfig.isPending}
        disabled={disabled || reloadConfig.isPending}
        onClick={() => onApply(reloadConfig)}
      >
        Retry apply
      </LoadingButton>
    </GatewayAdminOnly>
  );
};

// ---------------------------------------------------------------------------
// Main: FallbacksSection
// ---------------------------------------------------------------------------

const FallbacksSection = () => {
  // Saving fallbacks writes the org's gateway config: Owner or Admin only.
  const { canEdit } = useGatewayAdmin();
  const navigate = useNavigate();
  const { routing, isLoading, error, refetch, saveRouting, isSaving } =
    useFallbackConfig();
  const { gatewayId } = useGatewayContext();
  const {
    data: providerHealth,
    isPending: providersPending,
    isLoading: providersLoading,
    isFetching: providersFetching,
    error: providerError,
    refetch: refetchProviders,
  } = useProviderHealth(gatewayId);
  const discoveryPending =
    providersPending || providersLoading || (!providerHealth && !providerError);

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

  const noModelsConfigured =
    !discoveryPending &&
    !providersFetching &&
    !providerError &&
    Boolean(providerHealth) &&
    availableModels.length === 0;

  // Local draft and the last stored snapshot are separate from apply evidence.
  const [draft, setDraft] = useState(null);
  const [hasChanges, setHasChanges] = useState(false);
  const [applyStatus, setApplyStatus] = useState(null);
  const [saveFailed, setSaveFailed] = useState(false);
  const [applyFailed, setApplyFailed] = useState(false);
  const [busy, setBusy] = useState(null);
  const inFlight = useRef(false);
  const draftRevision = useRef(0);
  const savedDraft = useRef(null);

  // Initialize draft when routing data loads, and re-sync after save
  useEffect(() => {
    if (routing && !inFlight.current && (!draft || !hasChanges)) {
      savedDraft.current = { ...routing };
      setDraft(savedDraft.current);
      setHasChanges(false);
    }
  }, [routing]); // eslint-disable-line react-hooks/exhaustive-deps

  // Helpers to update draft
  const updateDraft = useCallback((field, value) => {
    draftRevision.current += 1;
    setSaveFailed(false);
    setDraft((prev) => ({ ...prev, [field]: value }));
    setHasChanges(true);
  }, []);

  const updateNested = useCallback((section, field, value) => {
    draftRevision.current += 1;
    setSaveFailed(false);
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
      if (
        !newPrimary.trim() ||
        (newPrimary !== oldPrimary &&
          Object.prototype.hasOwnProperty.call(fb, newPrimary))
      )
        return;
      updateDraft(
        "model_fallbacks",
        Object.fromEntries(
          Object.entries(fb).map(([primary, backups]) =>
            primary === oldPrimary
              ? [newPrimary, newFallbacks]
              : [primary, backups],
          ),
        ),
      );
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

  const unusedPrimary = availableModels.find(
    (model) =>
      model.trim() && !fallbackChains.some(([primary]) => primary === model),
  );
  const handleAddChain = () => {
    if (!unusedPrimary) return;
    updateDraft("model_fallbacks", {
      ...(draft?.model_fallbacks || draft?.modelFallbacks || {}),
      [unusedPrimary]: [],
    });
  };

  const handleSave = async () => {
    if (!canEdit || inFlight.current || isSaving) return;
    const revision = draftRevision.current;
    const snapshot = draft;
    inFlight.current = true;
    setBusy("save");
    setSaveFailed(false);
    try {
      const result = await saveRouting(snapshot);
      savedDraft.current = snapshot;
      // Edits made while the request was in flight are still unsaved.
      if (revision === draftRevision.current) setHasChanges(false);
      if (result?.gateway_synced === true) {
        setApplyStatus("applied");
        setApplyFailed(false);
      } else if (result?.gateway_synced === false) {
        setApplyStatus("not-applied");
      } else if (result) {
        setApplyStatus((previous) =>
          previous === "not-applied" ? previous : "unconfirmed",
        );
      }
    } catch {
      setSaveFailed(true);
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  };

  const handleApply = async (reloadConfig) => {
    if (
      !canEdit ||
      inFlight.current ||
      isSaving ||
      reloadConfig.isPending ||
      !gatewayId
    )
      return;
    inFlight.current = true;
    setBusy("apply");
    setApplyFailed(false);
    try {
      // Reload pushes the stored config, never a second mutation of the draft.
      const result = await reloadConfig.mutateAsync(gatewayId);
      if (result?.gateway_synced === true) setApplyStatus("applied");
      else setApplyFailed(true);
    } catch {
      setApplyFailed(true);
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  };

  const handleReset = () => {
    if (inFlight.current) return;
    draftRevision.current += 1;
    setDraft({ ...(savedDraft.current || routing) });
    setHasChanges(false);
    setSaveFailed(false);
  };

  // Convenience getters
  const failover = draft?.failover || {};
  const retry = draft?.retry || {};
  const circuitBreaker = draft?.circuit_breaker || draft?.circuitBreaker || {};
  const modelTimeouts = draft?.model_timeouts || draft?.modelTimeouts || {};

  const frame = (content) => (
    <GatewayPageFrame
      title="Fallbacks"
      description="What the gateway tries when a model or provider fails: fallback chains, provider failover, retry, the circuit breaker and per-model timeouts."
    >
      {content}
    </GatewayPageFrame>
  );

  if (isLoading && !hasChanges && !applyStatus) {
    return frame(
      <Stack spacing={2} data-testid="fallbacks-loading">
        {[...Array(5)].map((_, i) => (
          <Skeleton key={i} variant="rounded" height={64} />
        ))}
      </Stack>,
    );
  }

  if (error && !hasChanges && !applyStatus) {
    return frame(
      <GatewayEmptyState
        icon="fallbacks"
        title="Couldn't load the fallback config"
        description={error.message || "The config request failed."}
        actionLabel="Retry"
        onAction={() => refetch?.()}
      />,
    );
  }

  if (!draft) return null;

  if (
    noModelsConfigured &&
    fallbackChains.length === 0 &&
    !hasChanges &&
    !applyStatus
  ) {
    return frame(
      <GatewayEmptyState
        icon="fallbacks"
        title="No models to fall back to yet"
        description="Fallback chains send a request to a backup model when the primary one fails. Add a provider with at least one model first."
        actionLabel="Open Providers"
        onAction={() => navigate(paths.dashboard.gateway.providers)}
      />,
    );
  }

  const chainsOn = fallbackChainsOn(draft);

  return frame(
    <ReadOnly.Provider value={!canEdit}>
      <Stack spacing={2}>
        {applyStatus && applyStatus !== "applied" && (
          <Alert
            severity="warning"
            action={
              <RetryApplyButton
                canEdit={canEdit}
                busy={busy}
                disabled={!canEdit || Boolean(busy) || isSaving || !gatewayId}
                onApply={handleApply}
              />
            }
          >
            <Typography typography="s1" fontWeight={600}>
              {applyStatus === "not-applied"
                ? "Not applied"
                : "Apply not confirmed"}
            </Typography>
            Your fallback config is saved, but the gateway has not confirmed
            applying it. Retry apply reloads the stored config; it does not save
            newer edits.
            {applyFailed &&
              " The gateway still has not confirmed applying the config. You can retry when it is reachable."}
          </Alert>
        )}
        {applyStatus === "applied" && (
          <Alert severity="success" role="status">
            Saved config applied.{" "}
            {hasChanges && "Your newer edits still need to be saved."}
          </Alert>
        )}
        {saveFailed && (
          <Alert severity="error">
            Couldn&apos;t save the fallback config. Your draft is still here.
            Check the save error and try again.
          </Alert>
        )}
        {error && (
          <Alert
            severity="warning"
            action={
              <Button size="small" color="inherit" onClick={() => refetch?.()}>
                Retry config read
              </Button>
            }
          >
            Couldn&apos;t refresh the saved config. Your draft and last save
            outcome are kept.
          </Alert>
        )}
        {providerError ? (
          <Alert
            severity="warning"
            action={
              <Button
                color="inherit"
                size="small"
                disabled={providersFetching}
                onClick={() => refetchProviders?.()}
              >
                Retry model discovery
              </Button>
            }
          >
            Couldn&apos;t load available models. Your chains are kept; retry to
            refresh the model choices.
            {providersFetching && " Retrying model discovery…"}
          </Alert>
        ) : (
          (discoveryPending || providersFetching) && (
            <Alert severity="info" role="status">
              Loading available models. Your chains are kept while the model
              list refreshes.
            </Alert>
          )
        )}
        {/* ---- Model Fallback Chains ---- */}
        <ConfigSection
          title="Model fallback chains"
          subtitle="When all providers for a model fail, try the next model in the chain"
          testId="fallbacks-section-chains"
          defaultOpen
          enabled={chainsOn}
          onToggle={(v) => updateDraft("fallback_enabled", v)}
          note={
            !chainsOn && fallbackChains.length > 0 ? (
              <Typography typography="s2" color="warning.main">
                Saved but not tried: fallbacks are off. Turn the switch on and
                save to use these chains.
              </Typography>
            ) : null
          }
        >
          <Stack spacing={2}>
            <Typography typography="s2" color="text.secondary">
              Until you turn this switch on or off, chains run whenever one
              exists. A request moves down its chain on the Provider failover
              status codes (429, 500, 502, 503 and 504 when none are set).
            </Typography>

            {/* Chains */}
            {fallbackChains.length === 0 ? (
              <Box py={2}>
                <Typography typography="s2" color="text.secondary">
                  No fallback chains yet. When a model&apos;s providers all
                  fail, the request will fail.
                </Typography>
              </Box>
            ) : (
              fallbackChains.map(([primary, fbs]) => (
                <FallbackChainEditor
                  key={primary}
                  primaryModel={primary}
                  fallbacks={fbs || []}
                  models={availableModels}
                  usedPrimaries={fallbackChains.map(([model]) => model)}
                  onChange={(newPrimary, newFallbacks) =>
                    handleChainChange(newPrimary, newFallbacks, primary)
                  }
                  onRemove={() => handleRemoveChain(primary)}
                />
              ))
            )}

            <Box sx={{ alignSelf: "flex-start" }}>
              <GatewayAdminOnly allowed={canEdit}>
                <Button
                  variant="outlined"
                  size="small"
                  onClick={handleAddChain}
                  disabled={!canEdit || !unusedPrimary}
                  startIcon={<Glyph name="add" />}
                >
                  Add fallback chain
                </Button>
              </GatewayAdminOnly>
              {!unusedPrimary && availableModels.length > 0 && (
                <Typography
                  typography="s2"
                  color="text.secondary"
                  sx={{ mt: 1 }}
                >
                  Every available model already has a chain. Edit an existing
                  chain or remove one to use its primary.
                </Typography>
              )}
              {noModelsConfigured && (
                <Typography
                  typography="s2"
                  color="text.secondary"
                  sx={{ mt: 1 }}
                >
                  No available primary models. Add a provider model to create
                  another chain; your stored chains are kept.
                </Typography>
              )}
            </Box>
          </Stack>
        </ConfigSection>

        {/* ---- Provider failover ---- */}
        <ConfigSection
          title="Provider failover"
          subtitle="When one of your providers fails, try another of your providers that serves the model"
          testId="fallbacks-section-failover"
          enabled={providerFailoverOn(draft)}
          onToggle={(v) => updateNested("failover", "enabled", v)}
        >
          <Stack spacing={2}>
            <Grid container spacing={2}>
              <Grid item xs={12} sm={4}>
                <TextField
                  disabled={!canEdit}
                  label="Max attempts"
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
                  disabled={!canEdit}
                  label="Per-attempt timeout"
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
                    <GatewayAdminOnly allowed={canEdit}>
                      <Switch
                        size="small"
                        disabled={!canEdit}
                        checked={failoverOnTimeout(draft)}
                        inputProps={{ "aria-label": "Failover on timeout" }}
                        onChange={(e) =>
                          updateNested(
                            "failover",
                            "on_timeout",
                            e.target.checked,
                          )
                        }
                      />
                    </GatewayAdminOnly>
                  }
                  label={
                    <Typography typography="s1">Failover on timeout</Typography>
                  }
                />
              </Grid>
            </Grid>

            <Box>
              <Typography typography="s1" fontWeight={500} mb={1}>
                Trigger on status codes
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

        {/* ---- Retry ---- */}
        <ConfigSection
          title="Retry"
          subtitle="Retry failed requests with exponential backoff before failing over"
          testId="fallbacks-section-retry"
          enabled={retry.enabled ?? false}
          onToggle={(v) => updateNested("retry", "enabled", v)}
        >
          <Stack spacing={2}>
            <Grid container spacing={2}>
              <Grid item xs={6} sm={3}>
                <TextField
                  disabled={!canEdit}
                  label="Max retries"
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
                  disabled={!canEdit}
                  label="Initial delay"
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
                  disabled={!canEdit}
                  label="Max delay"
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
                  disabled={!canEdit}
                  label="Backoff multiplier"
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
                <GatewayAdminOnly allowed={canEdit}>
                  <Switch
                    size="small"
                    disabled={!canEdit}
                    checked={retryOnTimeout(draft)}
                    inputProps={{ "aria-label": "Retry on timeout" }}
                    onChange={(e) =>
                      updateNested("retry", "on_timeout", e.target.checked)
                    }
                  />
                </GatewayAdminOnly>
              }
              label={<Typography typography="s1">Retry on timeout</Typography>}
              sx={{ alignSelf: "flex-start" }}
            />

            <Box>
              <Typography typography="s1" fontWeight={500} mb={1}>
                Retry on status codes
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

        {/* ---- Circuit breaker ---- */}
        <ConfigSection
          title="Circuit breaker"
          subtitle="Stop calling a provider for a model after repeated failures, until the cooldown passes"
          testId="fallbacks-section-breaker"
          enabled={circuitBreaker.enabled ?? false}
          onToggle={(v) => updateNested("circuit_breaker", "enabled", v)}
        >
          <Stack spacing={2}>
            <Grid container spacing={2}>
              <Grid item xs={6} sm={4}>
                <TextField
                  disabled={!canEdit}
                  label="Failure threshold"
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
                  disabled={!canEdit}
                  label="Success threshold"
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
                  disabled={!canEdit}
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
              <Typography typography="s1" fontWeight={500} mb={1}>
                Trigger on status codes
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
          title="Model timeouts"
          subtitle="Override default timeout per model (e.g. longer for reasoning models)"
          testId="fallbacks-section-timeouts"
        >
          <ModelTimeoutsEditor
            timeouts={modelTimeouts}
            models={availableModels}
            onChange={(t) => updateDraft("model_timeouts", t)}
          />
        </ConfigSection>
      </Stack>

      {/* Sticky save bar: the page's one save. */}
      {hasChanges && (
        <Paper
          variant="outlined"
          sx={{
            position: "sticky",
            bottom: 16,
            p: { xs: 1.5, sm: 2 },
            borderRadius: 2,
            borderColor: "primary.main",
            boxShadow: (theme) => theme.customShadows?.z8 || theme.shadows[3],
          }}
        >
          <Stack
            direction={{ xs: "column", sm: "row" }}
            justifyContent="space-between"
            alignItems={{ xs: "stretch", sm: "center" }}
            gap={1}
          >
            <Typography typography="s1" fontWeight={500}>
              You have unsaved changes. Save stores them and tries to apply them
              to the gateway.
            </Typography>
            <Stack direction="row" spacing={1} justifyContent="flex-end">
              <Button
                variant="outlined"
                size="small"
                color="inherit"
                onClick={handleReset}
                disabled={Boolean(busy) || isSaving}
              >
                Discard
              </Button>
              <GatewayAdminOnly allowed={canEdit}>
                <LoadingButton
                  loading={isSaving || busy === "save"}
                  variant="contained"
                  color="primary"
                  size="small"
                  onClick={handleSave}
                  disabled={!canEdit || Boolean(busy)}
                >
                  Save & apply
                </LoadingButton>
              </GatewayAdminOnly>
            </Stack>
          </Stack>
        </Paper>
      )}
    </ReadOnly.Provider>,
  );
};

export default FallbacksSection;
