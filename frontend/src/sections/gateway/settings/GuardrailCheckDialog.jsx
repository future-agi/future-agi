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
} from "@mui/material";

import axios, { endpoints } from "src/utils/axios";
import { logger } from "src/utils/logger";
import {
  MASK_ACTION_HELP,
  actionLabel,
  actionOptionsFor,
} from "../constants/guardrailCheckActions";

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

// absentAction and absentThreshold are what the gateway runs a saved check
// with when it carries no action or no threshold: block and 0.8 for a rule or
// a check of the policy sync's list (the config push's defaults), log and 0
// for a check of a section stored as a checks map, which the push forwards as
// stored. The form opens on them, so choosing another value is an edit, and
// re-saving the shown value is not.
const GuardrailCheckDialog = ({
  open,
  onClose,
  onSave,
  checkName,
  initialData,
  providerMeta,
  absentAction = "block",
  absentThreshold = 0.8,
}) => {
  const [form, setForm] = useState({
    enabled: true,
    action: "block",
    confidence_threshold: 0.8,
  });
  const [providerFields, setProviderFields] = useState({});
  // What the form showed when it opened on a saved check, so a re-save writes
  // only the fields the admin changed (see handleSave).
  const [shownAtOpen, setShownAtOpen] = useState(null);
  // Track which credential fields had existing values (to avoid overwriting with mask)
  const [maskedCredentials, setMaskedCredentials] = useState({});
  // Async options cache for async-select fields
  const [asyncOptions, setAsyncOptions] = useState({});
  const [asyncLoading, setAsyncLoading] = useState({});

  useEffect(() => {
    if (!open) return;

    if (initialData) {
      const shownForm = {
        enabled: initialData.enabled !== false,
        action: initialData.action || absentAction,
        confidence_threshold:
          initialData.confidence_threshold ??
          initialData.confidenceThreshold ??
          absentThreshold,
      };
      setForm(shownForm);
      let shownFields = {};

      // Extract provider-specific fields from initialData (check both top-level and config sub-object)
      if (providerMeta?.fields) {
        const pf = {};
        const mc = {};
        const cfgObj = initialData.config || {};
        providerMeta.fields.forEach(({ key, defaultValue, type }) => {
          const val = initialData[key] ?? cfgObj[key];
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
        shownFields = pf;
      } else {
        setProviderFields({});
        setMaskedCredentials({});
      }
      setShownAtOpen({ form: shownForm, fields: shownFields });
    } else {
      setShownAtOpen(null);
      setForm({ enabled: true, action: "block", confidence_threshold: 0.8 });
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
  }, [open, initialData, providerMeta, absentAction, absentThreshold]);

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

  const handleFieldChange = (key, value) => {
    setProviderFields((prev) => ({ ...prev, [key]: value }));
    // If user edits a masked credential field, clear the mask tracking
    if (maskedCredentials[key] && value !== CREDENTIAL_MASK) {
      setMaskedCredentials((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
    }
  };

  // The value a provider field's input holds, as the check stores it: a
  // masked credential the admin left alone is its stored value, a list is
  // parsed, text is trimmed. undefined when the input is empty.
  const storedValue = (key, type) => {
    const val = providerFields[key];
    if (CREDENTIAL_KEYS.has(key) && val === CREDENTIAL_MASK) {
      return maskedCredentials[key];
    }
    if (type === "multiline-list") {
      const parsed = parseMultilineList(val);
      return parsed.length > 0 ? parsed : undefined;
    }
    if (val === undefined || val === "") return undefined;
    return typeof val === "string" ? val.trim() : val;
  };

  // A new check is built from the form, defaults included.
  const buildNewCheck = () => {
    const data = {
      enabled: form.enabled,
      action: form.action,
      confidence_threshold: form.confidence_threshold,
    };
    if (providerMeta?.provider) {
      data.provider = providerMeta.provider;
    }
    if (providerMeta?.fields) {
      const cfg = {};
      providerMeta.fields.forEach(({ key, type }) => {
        const val = storedValue(key, type);
        if (val !== undefined) cfg[key] = val;
      });
      if (Object.keys(cfg).length > 0) {
        data.config = cfg;
      }
    }
    return data;
  };

  // A saved check is saved again exactly as it was, apart from the fields the
  // admin changed: re-saving must never change what a check does. A stored
  // setting the form does not show (such as
  // DynamoAI's text_type, Tool Permissions' apply_to or MCP Security's
  // validate_outputs), one it shows with a default (an absent threshold, an
  // absent max_calls_per_request) and one it would normalize (untrimmed
  // text, a list) all stay as stored, in their place. A changed field is
  // written where the check keeps it (the top level or config), and a field
  // the admin cleared is removed.
  const resaveCheck = () => {
    // The check came from the API, so a JSON copy is exact.
    const data = JSON.parse(JSON.stringify(initialData));
    const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
    ["enabled", "action"].forEach((key) => {
      if (!same(form[key], shownAtOpen.form[key])) data[key] = form[key];
    });
    if (
      !same(form.confidence_threshold, shownAtOpen.form.confidence_threshold)
    ) {
      data.confidence_threshold = form.confidence_threshold;
      if ("confidenceThreshold" in data) {
        data.confidenceThreshold = form.confidence_threshold;
      }
    }
    (providerMeta?.fields || []).forEach(({ key, type }) => {
      if (same(providerFields[key], shownAtOpen.fields[key])) return;
      const inTop = key in initialData;
      const inConfig = key in (initialData.config || {});
      const val = storedValue(key, type);
      if (val === undefined) {
        if (inTop) delete data[key];
        if (inConfig) delete data.config[key];
        return;
      }
      if (inTop) data[key] = val;
      if (inConfig || !inTop) {
        data.config = data.config || {};
        data.config[key] = val;
      }
    });
    return data;
  };

  const handleSave = () => {
    onSave(
      checkName,
      initialData && shownAtOpen ? resaveCheck() : buildNewCheck(),
    );
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
  // Mask only for a check that can rewrite the request (pii-detection); a
  // stored action the check does not take stays listed, disabled.
  const actionOptions = actionOptionsFor(checkName, form.action);
  const storedOnly = actionOptions.some(
    (o) => o.disabled && o.value === form.action,
  );

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
            onChange={(e) => setForm((p) => ({ ...p, action: e.target.value }))}
            fullWidth
            helperText={
              storedOnly
                ? "This check cannot take this action; the gateway runs it as Log."
                : form.action === "mask"
                  ? MASK_ACTION_HELP
                  : undefined
            }
          >
            {actionOptions.map(({ value, disabled }) => (
              <MenuItem key={value} value={value} disabled={disabled}>
                {actionLabel(value)}
                {disabled ? " (runs as Log)" : ""}
              </MenuItem>
            ))}
          </TextField>

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
        <Button variant="contained" onClick={handleSave}>
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default GuardrailCheckDialog;
