/* eslint-disable react/prop-types */
import React, { useState, useEffect } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Stack,
  MenuItem,
  Slider,
  Typography,
  FormControlLabel,
  Switch,
  Autocomplete,
  Chip,
  Divider,
  CircularProgress,
  Alert,
} from "@mui/material";

import axios, { endpoints } from "src/utils/axios";
import { logger } from "src/utils/logger";

import {
  guardrailStage,
  RESPONSE_ONLY_CHECKS,
  requestOnlyGuardrail,
  withoutGuardrailRetries,
} from "./guardrailOptions";

const ACTION_OPTIONS = ["block", "warn", "mask", "log"];
const CREDENTIAL_MASK = "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022";
const CREDENTIAL_KEYS = new Set(["api_key", "secret_key", "access_key"]);

const parseMultilineList = (value) => {
  if (Array.isArray(value)) return value;
  if (typeof value !== "string") return [];

  const seen = new Set();

  return value
    .split(/\n|,/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
    .filter((item) => {
      if (seen.has(item)) return false;
      seen.add(item);
      return true;
    });
};

const GuardrailCheckDialog = ({
  open,
  onClose,
  onSave,
  checkName,
  initialData,
  providerMeta,
}) => {
  const [form, setForm] = useState({
    enabled: true,
    action: "block",
    confidence_threshold: 0.8,
    stage: "pre",
    stageExplicit: false,
    on_block: "reject",
    reflect_max_attempts: 1,
    reflect_debug: false,
  });
  const [providerFields, setProviderFields] = useState({});
  // Track which credential fields had existing values (to avoid overwriting with mask)
  const [maskedCredentials, setMaskedCredentials] = useState({});
  // Async options cache for async-select fields
  const [asyncOptions, setAsyncOptions] = useState({});
  const [asyncLoading, setAsyncLoading] = useState({});

  useEffect(() => {
    if (!open) return;

    const retryControls = {
      stage: guardrailStage(checkName, initialData),
      stageExplicit:
        initialData?.config?.stage !== undefined ||
        initialData?.config?.on_block === "reflect",
      on_block: initialData?.config?.on_block || "reject",
      reflect_max_attempts: initialData?.config?.reflect_max_attempts ?? 1,
      reflect_debug: initialData?.config?.reflect_debug ?? false,
    };
    if (initialData) {
      setForm({
        ...retryControls,
        enabled: initialData.enabled !== false,
        action: initialData.action || "block",
        confidence_threshold:
          initialData.confidence_threshold ??
          initialData.confidenceThreshold ??
          0.8,
      });

      // Provider config wins over legacy top-level fields: a rule's execution
      // mode is not the provider-specific mode inside config.
      if (providerMeta?.fields) {
        const pf = {};
        const mc = {};
        const cfgObj = initialData.config || {};
        providerMeta.fields.forEach(({ key, defaultValue, type }) => {
          const val = cfgObj[key] ?? initialData[key];
          if (CREDENTIAL_KEYS.has(key) && val) {
            pf[key] = CREDENTIAL_MASK;
            mc[key] = val; // remember original
          } else if (type === "multiline-list") {
            const listValue = Array.isArray(val)
              ? val.join("\n")
              : defaultValue ?? "";
            pf[key] = listValue;
          } else if (val !== undefined && val !== "") {
            pf[key] = val;
          } else {
            pf[key] = defaultValue ?? "";
          }
        });
        setProviderFields(pf);
        setMaskedCredentials(mc);
      } else {
        setProviderFields({});
        setMaskedCredentials({});
      }
    } else {
      setForm({
        enabled: true,
        action: "block",
        confidence_threshold: 0.8,
        ...retryControls,
      });
      // Set defaults for provider fields
      if (providerMeta?.fields) {
        const pf = {};
        providerMeta.fields.forEach(({ key, defaultValue }) => {
          pf[key] = defaultValue ?? "";
        });
        setProviderFields(pf);
      } else {
        setProviderFields({});
      }
      setMaskedCredentials({});
    }
  }, [open, initialData, providerMeta, checkName]);

  // Fetch async options for async-select fields when dialog opens
  useEffect(() => {
    if (!open || !providerMeta?.fields) return;

    providerMeta.fields.forEach((field) => {
      if (
        !["async-select", "async-multiselect"].includes(field.type) ||
        !field.fetchKey
      )
        return;
      if (asyncOptions[field.fetchKey]) return; // already fetched

      setAsyncLoading((prev) => ({ ...prev, [field.fetchKey]: true }));

      const fetchOptions = async () => {
        try {
          const url = endpoints.gateway[field.fetchKey];
          const { data } = await axios.get(url);
          const options = (data.result || data.data || data || []).map((t) => ({
            value: String(t.evalId ?? t.eval_id ?? t.id ?? t.value),
            label: t.name || t.label || String(t.evalId ?? t.eval_id ?? t.id),
          }));
          setAsyncOptions((prev) => ({ ...prev, [field.fetchKey]: options }));
        } catch (err) {
          logger.warn(`Failed to fetch ${field.fetchKey}:`, err);
          setAsyncOptions((prev) => ({ ...prev, [field.fetchKey]: [] }));
        } finally {
          setAsyncLoading((prev) => ({ ...prev, [field.fetchKey]: false }));
        }
      };
      fetchOptions();
    });
  }, [open, providerMeta, asyncOptions]);

  const effectiveProviderConfig = {
    ...initialData?.config,
    ...providerFields,
    provider: providerMeta?.provider ?? initialData?.config?.provider,
  };
  const requestOnly = requestOnlyGuardrail(checkName, effectiveProviderConfig);

  const handleFieldChange = (key, value) => {
    setProviderFields((prev) => ({ ...prev, [key]: value }));
    if (
      key === "apply_to" &&
      requestOnlyGuardrail(checkName, {
        ...effectiveProviderConfig,
        [key]: value,
      })
    ) {
      setForm((prev) => ({
        ...prev,
        stage: "pre",
        stageExplicit: true,
        on_block: "reject",
      }));
    }
    // If user edits a masked credential field, clear the mask tracking
    if (maskedCredentials[key] && value !== CREDENTIAL_MASK) {
      setMaskedCredentials((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
    }
  };

  const canReflect =
    form.stage === "post" &&
    !requestOnly &&
    form.action === "block" &&
    [undefined, null, "", "sync"].includes(initialData?.mode);
  const reflects = canReflect && form.on_block === "reflect";
  const attempts = Number(form.reflect_max_attempts);
  const invalidAttempts =
    reflects && (!Number.isInteger(attempts) || attempts < 1 || attempts > 5);

  const handleSave = () => {
    if (invalidAttempts) return;
    const data = {
      enabled: form.enabled,
      action: form.action,
      confidence_threshold: form.confidence_threshold,
    };

    // Add provider field
    if (providerMeta?.provider) {
      data.provider = providerMeta.provider;
    }

    // Preserve shared and provider controls unknown to this dialog. Explicitly
    // cleared provider fields must still be removed (including credentials).
    const cfg = { ...initialData?.config };
    if (providerMeta?.fields) {
      providerMeta.fields.forEach(({ key, type }) => {
        delete cfg[key];
        const val = providerFields[key];
        if (
          CREDENTIAL_KEYS.has(key) &&
          val === CREDENTIAL_MASK &&
          maskedCredentials[key]
        ) {
          cfg[key] = maskedCredentials[key];
        } else if (type === "multiline-list") {
          const parsed = parseMultilineList(val);
          if (parsed.length > 0) {
            cfg[key] = parsed;
          }
        } else if (val !== undefined && val !== "") {
          cfg[key] = typeof val === "string" ? val.trim() : val;
        }
      });
    }
    if (form.stageExplicit || reflects) {
      cfg.stage = form.stage;
      data.stage = form.stage;
    }
    if (reflects) {
      cfg.on_block = "reflect";
      cfg.reflect_max_attempts = attempts;
      cfg.reflect_debug = form.reflect_debug;
    }
    data.config = reflects ? cfg : withoutGuardrailRetries(cfg);

    onSave(checkName, data);
    onClose();
  };

  // Use the label so the title matches the list — deriving it from the slug
  // mangles acronyms ("presidio-pii" -> "Presidio Pii").
  const displayName =
    providerMeta?.label ||
    (checkName || "")
      .replace(/-/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase());

  const renderProviderField = (field) => {
    const {
      key,
      label,
      type,
      required,
      options,
      min,
      max,
      step,
      placeholder,
      helperText,
    } = field;
    const value = providerFields[key] ?? "";

    switch (type) {
      case "password":
        return (
          <TextField
            key={key}
            label={label}
            type="password"
            value={value}
            onChange={(e) => handleFieldChange(key, e.target.value)}
            onFocus={() => {
              if (value === CREDENTIAL_MASK) handleFieldChange(key, "");
            }}
            required={required}
            fullWidth
            size="small"
            placeholder={placeholder}
          />
        );

      case "select":
        return (
          <TextField
            key={key}
            select
            label={label}
            value={value || field.defaultValue || ""}
            onChange={(e) => handleFieldChange(key, e.target.value)}
            required={required}
            fullWidth
            size="small"
          >
            {(options || []).map((opt) => (
              <MenuItem key={opt} value={opt}>
                {opt}
              </MenuItem>
            ))}
          </TextField>
        );

      case "async-select": {
        const opts = asyncOptions[field.fetchKey] || [];
        const loading = asyncLoading[field.fetchKey] || false;
        return (
          <TextField
            key={key}
            select
            label={label}
            value={value ? String(value) : ""}
            onChange={(e) => handleFieldChange(key, e.target.value)}
            required={required}
            fullWidth
            size="small"
            disabled={loading}
            InputProps={{
              endAdornment: loading ? (
                <CircularProgress size={18} sx={{ mr: 2 }} />
              ) : null,
            }}
          >
            {opts.map((opt) => (
              <MenuItem key={opt.value} value={opt.value}>
                {opt.label}
              </MenuItem>
            ))}
          </TextField>
        );
      }

      case "async-multiselect": {
        const opts = asyncOptions[field.fetchKey] || [];
        const loading = asyncLoading[field.fetchKey] || false;
        const selected = Array.isArray(value) ? value : [];
        // Build a label lookup from value→label
        const labelMap = {};
        opts.forEach((o) => {
          labelMap[o.value] = o.label;
        });
        // Use plain string values for Autocomplete
        const optionValues = opts.map((o) => o.value);
        return (
          <Autocomplete
            key={key}
            multiple
            loading={loading}
            options={optionValues}
            getOptionLabel={(v) => labelMap[v] || v}
            value={selected.filter((v) => optionValues.includes(v))}
            onChange={(_, newVal) => handleFieldChange(key, newVal)}
            renderTags={(vals, getTagProps) =>
              vals.map((v, i) => (
                <Chip
                  key={v}
                  label={labelMap[v] || v}
                  size="small"
                  {...getTagProps({ index: i })}
                />
              ))
            }
            renderInput={(params) => (
              <TextField
                {...params}
                label={label}
                size="small"
                required={required}
                InputProps={{
                  ...params.InputProps,
                  endAdornment: (
                    <>
                      {loading ? <CircularProgress size={18} /> : null}
                      {params.InputProps.endAdornment}
                    </>
                  ),
                }}
              />
            )}
          />
        );
      }

      case "multiselect":
        return (
          <Autocomplete
            key={key}
            multiple
            options={options || []}
            value={Array.isArray(value) ? value : field.defaultValue || []}
            onChange={(_, newVal) => handleFieldChange(key, newVal)}
            renderTags={(vals, getTagProps) =>
              vals.map((v, i) => (
                <Chip
                  key={v}
                  label={v}
                  size="small"
                  {...getTagProps({ index: i })}
                />
              ))
            }
            renderInput={(params) => (
              <TextField {...params} label={label} size="small" />
            )}
          />
        );

      case "number":
        return (
          <TextField
            key={key}
            label={label}
            type="number"
            value={value !== "" ? value : field.defaultValue ?? ""}
            onChange={(e) => handleFieldChange(key, Number(e.target.value))}
            required={required}
            fullWidth
            size="small"
            inputProps={{ min, max, step }}
            placeholder={placeholder}
          />
        );

      case "multiline-list":
        return (
          <TextField
            key={key}
            label={label}
            value={value}
            onChange={(e) => handleFieldChange(key, e.target.value)}
            required={required}
            fullWidth
            size="small"
            multiline
            minRows={4}
            placeholder={placeholder}
            helperText={helperText}
          />
        );

      default: // text
        return (
          <TextField
            key={key}
            label={label}
            value={value}
            onChange={(e) => handleFieldChange(key, e.target.value)}
            required={required}
            fullWidth
            size="small"
            placeholder={placeholder || field.defaultValue}
            helperText={helperText}
          />
        );
    }
  };

  const hideConfidence = providerMeta?.hideConfidence === true;

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        Configure: {displayName}
        {providerMeta?.description && (
          <Typography variant="body2" color="text.secondary">
            {providerMeta.description}
          </Typography>
        )}
      </DialogTitle>
      <DialogContent>
        <Stack spacing={3} sx={{ mt: 1 }}>
          <FormControlLabel
            control={
              <Switch
                checked={form.enabled}
                onChange={(e) =>
                  setForm((p) => ({ ...p, enabled: e.target.checked }))
                }
              />
            }
            label="Enabled"
          />

          <TextField
            select
            label="Action"
            value={form.action}
            onChange={(e) =>
              setForm((p) => ({
                ...p,
                action: e.target.value,
                on_block: e.target.value === "block" ? p.on_block : "reject",
              }))
            }
            fullWidth
          >
            {ACTION_OPTIONS.map((a) => (
              <MenuItem key={a} value={a}>
                {a.charAt(0).toUpperCase() + a.slice(1)}
              </MenuItem>
            ))}
          </TextField>

          <TextField
            select
            label="Check stage"
            value={form.stage}
            onChange={(e) =>
              setForm((p) => ({
                ...p,
                stage: e.target.value,
                stageExplicit: true,
                on_block: e.target.value === "post" ? p.on_block : "reject",
              }))
            }
            fullWidth
            helperText={
              requestOnly
                ? "This check inspects requests only."
                : "Request checks run before generation. Response checks run after generation."
            }
          >
            <MenuItem
              value="pre"
              disabled={RESPONSE_ONLY_CHECKS.has(checkName)}
            >
              Request
            </MenuItem>
            <MenuItem value="post" disabled={requestOnly}>
              Response
            </MenuItem>
          </TextField>

          {canReflect ? (
            <Stack spacing={2}>
              <TextField
                select
                label="When a response is blocked"
                value={form.on_block}
                onChange={(e) =>
                  setForm((p) => ({ ...p, on_block: e.target.value }))
                }
                fullWidth
              >
                <MenuItem value="reject">Reject response</MenuItem>
                <MenuItem value="reflect">Retry with feedback</MenuItem>
              </TextField>
              {reflects && (
                <>
                  <TextField
                    label="Additional attempts"
                    type="number"
                    value={form.reflect_max_attempts}
                    onChange={(e) =>
                      setForm((p) => ({
                        ...p,
                        reflect_max_attempts: e.target.value,
                      }))
                    }
                    inputProps={{ min: 1, max: 5, step: 1 }}
                    error={invalidAttempts}
                    helperText={
                      invalidAttempts
                        ? "Enter a whole number from 1 to 5."
                        : "After the original response. Stops at the first passing response; rejects if the limit is reached."
                    }
                    fullWidth
                  />
                  <Alert severity="info">
                    Sends the rejected answer and check feedback back to the
                    model. Each additional generation uses provider tokens and
                    is checked against rate limits, credits and budgets. This
                    adds latency. Streaming responses are buffered until checks
                    pass; rejected text is never streamed to the client.
                  </Alert>
                  <Typography variant="caption" color="text.secondary">
                    Use /v1/chat/completions. Other generation endpoints reject
                    requests while this rule applies. Repair supports
                    single-choice text responses. Blocked tool calls, reasoning
                    and multi-choice responses are rejected without a repair
                    attempt. Normal provider failover can add upstream calls.
                  </Typography>
                  <FormControlLabel
                    control={
                      <Switch
                        checked={form.reflect_debug}
                        onChange={(e) =>
                          setForm((p) => ({
                            ...p,
                            reflect_debug: e.target.checked,
                          }))
                        }
                      />
                    }
                    label="Include safe retry details in request logs"
                  />
                  <Typography variant="caption" color="text.secondary">
                    Records attempt outcomes, not rejected answers or
                    credentials.
                  </Typography>
                </>
              )}
            </Stack>
          ) : (
            <Typography variant="body2" color="text.secondary">
              Retry with feedback is available for synchronous response checks
              with the Block action.
            </Typography>
          )}

          {!hideConfidence && (
            <Stack spacing={1}>
              <Typography variant="subtitle2" color="text.secondary">
                Confidence Threshold: {form.confidence_threshold.toFixed(2)}
              </Typography>
              <Slider
                value={form.confidence_threshold}
                onChange={(_, v) =>
                  setForm((p) => ({ ...p, confidence_threshold: v }))
                }
                min={0}
                max={1}
                step={0.05}
                marks={[
                  { value: 0, label: "0.0" },
                  { value: 0.5, label: "0.5" },
                  { value: 1, label: "1.0" },
                ]}
                valueLabelDisplay="auto"
              />
            </Stack>
          )}

          {/* Provider-specific fields */}
          {providerMeta?.fields?.length > 0 && (
            <>
              <Divider />
              <Typography variant="subtitle2">Provider Settings</Typography>
              {providerMeta.fields.map(renderProviderField)}
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={handleSave}
          disabled={invalidAttempts}
        >
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default GuardrailCheckDialog;
