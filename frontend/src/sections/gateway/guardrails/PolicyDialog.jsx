import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Autocomplete,
  Box,
  FormControl,
  FormControlLabel,
  FormLabel,
  Radio,
  RadioGroup,
  Stack,
  Switch,
  TextField,
  Typography,
  MenuItem,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import { useApiKeys } from "../keys/hooks/useApiKeys";
import { GUARDRAIL_CHECKS } from "../settings/guardrailChecks";
import GuardrailCheckDialog from "../settings/GuardrailCheckDialog";
import {
  useCreateGuardrailPolicy,
  useUpdateGuardrailPolicy,
} from "./hooks/useGuardrailPolicies";

const initial = {
  name: "",
  description: "",
  scope: "key",
  mode: "monitor",
  applied_keys: [],
  checks: [],
  is_active: true,
};
const errorText = (value) => {
  if (Array.isArray(value)) return value.map(errorText).join(" ");
  if (value && typeof value === "object")
    return Object.values(value).map(errorText).join(" ");
  return value || "";
};

// src/utils/axios.js rejects with the 400 body spread flat: per-field errors
// are under `details`, and `result` is a one-line summary string.
const fieldErrors = (error) => {
  const body = error?.response?.data ?? error ?? {};
  const details =
    body.details ??
    (typeof body.result === "object" && body.result !== null
      ? body.result
      : null);
  if (details && typeof details === "object" && !Array.isArray(details)) {
    return details;
  }
  return {
    form: errorText(body.result ?? body.message) || "Could not save policy.",
  };
};

// Key prefixes may already end in "..."; show one ellipsis.
const keyPrefixLabel = (key) =>
  (key.keyPrefix || key.key_prefix || "").replace(/\.+$/, "");

// The check editor reads `confidence_threshold`; policy checks may store
// either spelling.
const thresholdForEditor = (check) =>
  check && {
    ...check,
    confidence_threshold: check.threshold ?? check.confidence_threshold,
  };

export default function PolicyDialog({ open, onClose, policy, gatewayId }) {
  const [form, setForm] = useState(initial);
  const [errors, setErrors] = useState({});
  const [editingCheck, setEditingCheck] = useState(null);
  const { data: keys = [] } = useApiKeys(gatewayId);
  const create = useCreateGuardrailPolicy();
  const update = useUpdateGuardrailPolicy();
  const pending = create.isPending || update.isPending;

  useEffect(() => {
    if (open) {
      setForm(
        policy
          ? {
              ...initial,
              ...policy,
              checks: policy.checks || [],
              applied_keys: policy.applied_keys || [],
            }
          : initial,
      );
      setErrors({});
    }
  }, [open, policy]);

  const change = (field, value) => {
    setForm((prev) => ({
      ...prev,
      [field]: value,
      ...(field === "scope" && value === "global" ? { applied_keys: [] } : {}),
    }));
    setErrors((prev) => ({ ...prev, [field]: undefined }));
  };
  const setCheck = (name, patch) => {
    setForm((prev) => {
      const checks = prev.checks.filter((check) => check.name !== name);
      return {
        ...prev,
        checks: [
          ...checks,
          {
            ...prev.checks.find((check) => check.name === name),
            name,
            ...(!prev.checks.some((check) => check.name === name)
              ? { enabled: true, action: "block" }
              : {}),
            ...patch,
          },
        ],
      };
    });
    setErrors((prev) => ({ ...prev, checks: undefined }));
  };
  const save = async () => {
    if (form.scope === "key" && !form.applied_keys.length) {
      setErrors({ applied_keys: "Select at least one active key." });
      return;
    }
    if (!form.name.trim()) {
      setErrors({ name: "Enter a policy name." });
      return;
    }
    if (form.name.startsWith("__")) {
      setErrors({ name: "Names starting with __ are reserved." });
      return;
    }
    const payload = {
      name: form.name.trim(),
      description: form.description,
      scope: form.scope,
      mode: form.mode,
      applied_keys: form.scope === "key" ? form.applied_keys : [],
      checks: form.checks,
      is_active: form.is_active,
    };
    try {
      const result = policy
        ? await update.mutateAsync({ id: policy.id, ...payload })
        : await create.mutateAsync(payload);
      enqueueSnackbar(policy ? "Policy updated" : "Policy created", {
        variant: "success",
      });
      if (result?.gateway_synced === false) {
        enqueueSnackbar(result.gateway_warning || "Gateway sync is pending", {
          variant: "warning",
        });
      }
      onClose();
    } catch (error) {
      setErrors(fieldErrors(error));
    }
  };

  return (
    <>
      <ModalWrapper
        open={open}
        onClose={onClose}
        title={policy ? "Edit policy" : "New policy"}
        actionBtnTitle="Save policy"
        onSubmit={save}
        isValid={!pending}
        isLoading={pending}
        modalWidth="640px"
        paperSx={{ maxHeight: "90vh" }}
      >
        <Box
          data-testid="guardrail-policy-dialog"
          sx={{ overflowY: "auto", pr: 1 }}
        >
          <Stack spacing={2}>
            {errors.form && (
              <Typography variant="s1" color="error">
                {errorText(errors.form)}
              </Typography>
            )}
            <TextField
              label="Name"
              size="small"
              fullWidth
              value={form.name}
              onChange={(event) => change("name", event.target.value)}
              error={Boolean(errors.name)}
              helperText={errorText(errors.name)}
            />
            <TextField
              label="Description"
              size="small"
              fullWidth
              multiline
              minRows={2}
              value={form.description}
              onChange={(event) => change("description", event.target.value)}
            />
            <FormControl error={Boolean(errors.scope)}>
              <FormLabel>Scope</FormLabel>
              <RadioGroup
                row
                value={form.scope}
                onChange={(event) => change("scope", event.target.value)}
              >
                <FormControlLabel
                  value="global"
                  control={<Radio size="small" />}
                  label="Org-wide"
                />
                <FormControlLabel
                  value="key"
                  control={<Radio size="small" />}
                  label="Keys"
                />
              </RadioGroup>
              {errors.scope && (
                <Typography variant="s3" color="error">
                  {errorText(errors.scope)}
                </Typography>
              )}
            </FormControl>
            {form.scope === "key" && (
              <Autocomplete
                multiple
                options={keys}
                value={keys.filter((key) => form.applied_keys.includes(key.id))}
                getOptionLabel={(key) =>
                  `${key.name} (${keyPrefixLabel(key)}…) · ${key.status}`
                }
                getOptionDisabled={(key) => key.status !== "active"}
                isOptionEqualToValue={(a, b) => a.id === b.id}
                onChange={(_, selected) =>
                  change(
                    "applied_keys",
                    selected.map((key) => key.id),
                  )
                }
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label="API keys"
                    size="small"
                    error={Boolean(errors.applied_keys)}
                    helperText={errorText(errors.applied_keys)}
                  />
                )}
              />
            )}
            <FormControl error={Boolean(errors.mode)}>
              <FormLabel>Mode</FormLabel>
              <RadioGroup
                row
                value={form.mode}
                onChange={(event) => change("mode", event.target.value)}
              >
                <FormControlLabel
                  value="monitor"
                  control={<Radio size="small" />}
                  label="Monitor (log only)"
                />
                <FormControlLabel
                  value="enforce"
                  control={<Radio size="small" />}
                  label="Enforce"
                />
              </RadioGroup>
              {errors.mode && (
                <Typography variant="s3" color="error">
                  {errorText(errors.mode)}
                </Typography>
              )}
            </FormControl>
            <Box>
              <Typography variant="m3">Checks</Typography>
              {errors.checks && (
                <Typography variant="s3" color="error">
                  {errorText(errors.checks)}
                </Typography>
              )}
              <Stack spacing={1} sx={{ mt: 1 }}>
                {GUARDRAIL_CHECKS.map((item) => {
                  const check = form.checks.find(
                    (entry) => entry.name === item.name,
                  );
                  return (
                    <Stack
                      key={item.name}
                      direction="row"
                      alignItems="center"
                      gap={1}
                      flexWrap="wrap"
                    >
                      <FormControlLabel
                        sx={{ minWidth: 190 }}
                        control={
                          <Switch
                            size="small"
                            checked={Boolean(check?.enabled)}
                            onChange={(_, enabled) =>
                              setCheck(item.name, { enabled })
                            }
                          />
                        }
                        label={
                          <Typography variant="s1">{item.label}</Typography>
                        }
                      />
                      {check?.enabled && (
                        <>
                          <TextField
                            label={`${item.label} action`}
                            select
                            size="small"
                            value={check.action}
                            onChange={(event) =>
                              setCheck(item.name, {
                                action: event.target.value,
                              })
                            }
                            sx={{ minWidth: 110 }}
                          >
                            {["block", "warn", "log"].map((action) => (
                              <MenuItem key={action} value={action}>
                                {action[0].toUpperCase() + action.slice(1)}
                              </MenuItem>
                            ))}
                          </TextField>
                          {item.name === "pii-detection" && (
                            <TextField
                              label="Remediation"
                              select
                              size="small"
                              value={check.config?.remediation || "none"}
                              onChange={(event) => {
                                const config = { ...check.config };
                                if (event.target.value === "none")
                                  delete config.remediation;
                                else config.remediation = event.target.value;
                                setCheck(item.name, { config });
                              }}
                              sx={{ minWidth: 125 }}
                            >
                              {["none", "mask", "redact", "hash"].map(
                                (value) => (
                                  <MenuItem key={value} value={value}>
                                    {value[0].toUpperCase() + value.slice(1)}
                                  </MenuItem>
                                ),
                              )}
                            </TextField>
                          )}
                          {item.fields && (
                            <Typography
                              component="button"
                              variant="s1"
                              onClick={() => setEditingCheck(item)}
                            >
                              Configure
                            </Typography>
                          )}
                        </>
                      )}
                    </Stack>
                  );
                })}
              </Stack>
            </Box>
          </Stack>
        </Box>
      </ModalWrapper>
      {editingCheck && (
        <GuardrailCheckDialog
          open
          onClose={() => setEditingCheck(null)}
          checkName={editingCheck.name}
          initialData={thresholdForEditor(
            form.checks.find((check) => check.name === editingCheck.name),
          )}
          providerMeta={editingCheck}
          onSave={(_, { confidence_threshold: threshold, ...data }) =>
            setCheck(editingCheck.name, {
              ...data,
              action: ["block", "warn", "log"].includes(data.action)
                ? data.action
                : "log",
              // Policies store one spelling, `threshold`, which the gateway
              // payload reads first.
              threshold,
              confidence_threshold: undefined,
            })
          }
        />
      )}
    </>
  );
}

PolicyDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  policy: PropTypes.object,
  gatewayId: PropTypes.string,
};
