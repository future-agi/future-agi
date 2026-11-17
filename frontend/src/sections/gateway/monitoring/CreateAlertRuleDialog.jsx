import React, { useEffect, useRef, useState } from "react";
import { isEqual } from "lodash";
import PropTypes from "prop-types";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Stack,
  Alert,
  MenuItem,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { useUpdateConfig } from "../providers/hooks/useGatewayConfig";
import {
  ALERT_METRIC_OPTIONS,
  ALERT_SEVERITY_OPTIONS,
  DEFAULT_ALERT_COOLDOWN,
  DEFAULT_ALERT_METRIC,
  DEFAULT_ALERT_SEVERITY,
  DEFAULT_ALERT_WINDOW,
  GATEWAY_EMPTY_ALERT_WINDOW,
  isAlertDuration,
} from "../constants/alerting";
import { reportAlertingSaved } from "./alertingModel";

const CONDITIONS = [">", ">=", "<", "<=", "=="];
const DUPLICATE_NAME_MESSAGE =
  "A rule with this name already exists. Choose a unique name or use Edit.";

// An empty duration is left out of the rule (the gateway applies its default);
// anything else must be a duration the gateway can read.
const durationInvalid = (value) =>
  value.trim() !== "" && !isAlertDuration(value);

const formValues = (rule) => ({
  metric: rule?.metric ?? DEFAULT_ALERT_METRIC,
  condition: rule?.condition ?? ">",
  threshold: rule?.threshold == null ? "" : String(rule.threshold),
  window: rule ? rule.window ?? "" : DEFAULT_ALERT_WINDOW,
  cooldown: rule ? rule.cooldown ?? "" : DEFAULT_ALERT_COOLDOWN,
  severity: rule?.severity ?? DEFAULT_ALERT_SEVERITY,
  channels: rule?.channels ?? [],
});

const CreateAlertRuleDialog = ({
  open,
  onClose,
  gatewayId,
  existingRules = [],
  getLatestRules,
  channelNames = [],
  rule = null,
  onSave,
}) => {
  const initial = formValues(rule);
  const [name, setName] = useState(rule?.name || "");
  const [metric, setMetric] = useState(initial.metric);
  const [condition, setCondition] = useState(initial.condition);
  const [threshold, setThreshold] = useState(initial.threshold);
  const [window, setWindow] = useState(initial.window);
  const [cooldown, setCooldown] = useState(initial.cooldown);
  const [severity, setSeverity] = useState(initial.severity);
  const [ruleChannels, setRuleChannels] = useState(initial.channels);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const savingRef = useRef(false);

  useEffect(() => {
    if (!open || !rule) return;
    const values = formValues(rule);
    setName(rule.name);
    setMetric(values.metric);
    setCondition(values.condition);
    setThreshold(values.threshold);
    setWindow(values.window);
    setCooldown(values.cooldown);
    setSeverity(values.severity);
    setRuleChannels(values.channels);
    setSaveError(null);
  }, [open, rule]);

  const updateConfig = useUpdateConfig();
  const busy = saving || updateConfig.isPending;
  const windowInvalid = durationInvalid(window);
  const cooldownInvalid = durationInvalid(cooldown);
  const thresholdInvalid = threshold.trim() === "" || !Number.isFinite(Number(threshold));
  const nameExists = !rule && existingRules.some((saved) => saved?.name === name.trim());
  const values = { metric, condition, threshold, window, cooldown, severity, channels: ruleChannels };
  const changes = Object.fromEntries(
    Object.entries(values)
      .filter(([field, value]) => !isEqual(value, initial[field]))
      .map(([field, value]) => {
        if (field === "threshold") return [field, Number(value)];
        if (field === "window" || field === "cooldown") return [field, value.trim() || undefined];
        return [field, value];
      }),
  );
  const availableChannels = [...new Set([...channelNames, ...ruleChannels])];

  const resetForm = () => {
    setName("");
    setMetric(DEFAULT_ALERT_METRIC);
    setCondition(">");
    setThreshold("");
    setWindow(DEFAULT_ALERT_WINDOW);
    setCooldown(DEFAULT_ALERT_COOLDOWN);
    setSeverity(DEFAULT_ALERT_SEVERITY);
    setRuleChannels([]);
  };

  const handleClose = () => {
    if (busy || savingRef.current) return;
    resetForm();
    setSaveError(null);
    onClose();
  };

  const handleEdit = async () => {
    if (savingRef.current || busy || !onSave || thresholdInvalid || windowInvalid || cooldownInvalid) return;
    savingRef.current = true;
    setSaving(true);
    setSaveError(null);
    try {
      await onSave(rule, changes);
      onClose();
    } catch (error) {
      setSaveError(error.message || "The alert rule could not be updated.");
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  const handleCreate = async () => {
    if (savingRef.current || busy || !name.trim() || nameExists || thresholdInvalid || windowInvalid || cooldownInvalid) return;
    const ruleName = name.trim();
    const rule = {
      name: ruleName,
      metric,
      condition,
      threshold: Number(threshold),
      channels: ruleChannels,
      severity,
      enabled: true,
    };
    if (window.trim()) rule.window = window.trim();
    if (cooldown.trim()) rule.cooldown = cooldown.trim();

    savingRef.current = true;
    setSaving(true);
    setSaveError(null);
    const finishSaving = () => {
      savingRef.current = false;
      setSaving(false);
    };
    try {
      // The live page re-reads before creation: a rule added while this dialog
      // was open must not be overwritten, even if its name was not cached.
      const latestRules = getLatestRules ? await getLatestRules() : existingRules;
      if (!Array.isArray(latestRules)) {
        throw new Error("Could not read the latest rules. Try again before creating a rule.");
      }
      if (latestRules.some((saved) => saved?.name === ruleName)) {
        throw new Error(DUPLICATE_NAME_MESSAGE);
      }
      // The patch endpoint replaces lists wholesale. Append to the fresh
      // array without filtering or changing any saved rule; updates use Edit.
      const rules = [...latestRules, rule];

      updateConfig.mutate(
        // `enabled` gates per-org alerting in the gateway, and this page has no
        // switch for it — without it the rule syncs and is then never evaluated.
        { gatewayId, config: { alerting: { enabled: true, rules } } },
        {
          onSuccess: (result) => {
            reportAlertingSaved(result, `Alert rule "${ruleName}" created`);
            resetForm();
            onClose();
          },
          onError: () => {
            enqueueSnackbar("The alert rule could not be created", {
              variant: "error",
            });
          },
          onSettled: finishSaving,
        },
      );
    } catch (error) {
      setSaveError(error.message || "The alert rule could not be created.");
      finishSaving();
    }
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
      <DialogTitle>{rule ? "Edit alert rule" : "Create alert rule"}</DialogTitle>
      <DialogContent>
        <Stack component="fieldset" disabled={busy} spacing={2} mt={1} sx={{ border: 0, p: 0, minWidth: 0 }}>
          <TextField
            label="Rule name"
            fullWidth
            required
            value={name}
            InputProps={{ readOnly: Boolean(rule) }}
            error={nameExists}
            helperText={nameExists
              ? DUPLICATE_NAME_MESSAGE
              : rule
                ? "The rule name stays the same. Editing does not change its enabled state."
                : undefined}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g., high-error-rate"
          />
          <TextField
            label="Metric"
            select
            fullWidth
            value={metric}
            onChange={(e) => setMetric(e.target.value)}
          >
            {ALERT_METRIC_OPTIONS.map((m) => (
              <MenuItem key={m.value} value={m.value}>
                {m.label}
              </MenuItem>
            ))}
          </TextField>
          <Stack direction="row" spacing={2}>
            <TextField
              label="Condition"
              select
              value={condition}
              onChange={(e) => setCondition(e.target.value)}
              sx={{ width: 120 }}
            >
              {CONDITIONS.map((c) => (
                <MenuItem key={c} value={c}>
                  {c}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              label="Threshold"
              type="number"
              fullWidth
              required
              value={threshold}
              onChange={(e) => setThreshold(e.target.value)}
              placeholder="e.g., 5"
            />
          </Stack>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
            <TextField
              label="Window"
              fullWidth
              value={window}
              onChange={(e) => setWindow(e.target.value)}
              placeholder="e.g., 5m, 1h"
              error={windowInvalid}
              helperText={
                windowInvalid
                  ? "Not a duration: use e.g. 30s, 5m or 1h."
                  : `The metric is summed (latency averaged) over this window. Empty: ${GATEWAY_EMPTY_ALERT_WINDOW}.`
              }
            />
            <TextField
              label="Cooldown"
              fullWidth
              value={cooldown}
              onChange={(e) => setCooldown(e.target.value)}
              placeholder={`e.g., ${DEFAULT_ALERT_COOLDOWN}, 1h`}
              error={cooldownInvalid}
              helperText={
                cooldownInvalid
                  ? "Not a duration: use e.g. 30s, 15m or 1h."
                  : `After it fires, the rule waits this long before it can fire again. Empty: ${DEFAULT_ALERT_COOLDOWN}; 0s fires on every request past the threshold.`
              }
            />
          </Stack>
          <TextField
            label="Severity"
            select
            fullWidth
            value={severity}
            onChange={(e) => setSeverity(e.target.value)}
            helperText="Sent with every alert: the webhook payload's severity and a line in the Slack message."
          >
            {ALERT_SEVERITY_OPTIONS.map((s) => (
              <MenuItem key={s.value} value={s.value}>
                {s.label}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            label="Notify channels"
            select
            fullWidth
            value={ruleChannels}
            onChange={(e) => setRuleChannels(e.target.value)}
            SelectProps={{ multiple: true }}
            disabled={busy || availableChannels.length === 0}
            helperText={
              availableChannels.length === 0
                ? "Add a notification channel first. A rule with no channel notifies nobody."
                : "A rule with no channel notifies nobody."
            }
          >
            {availableChannels.map((channelName) => (
              <MenuItem key={channelName} value={channelName}>
                {channelName}{!channelNames.includes(channelName) ? " (not configured)" : ""}
              </MenuItem>
            ))}
          </TextField>
          {saveError && <Alert severity="warning">{saveError}</Alert>}
          {updateConfig.isError && (
            <Alert severity="error">
              {updateConfig.error?.message ||
                "The alert rule could not be created"}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose} disabled={busy}>Cancel</Button>
        <Button
          variant="contained"
          color="primary"
          onClick={rule ? handleEdit : handleCreate}
          disabled={
            !name.trim() ||
            nameExists ||
            thresholdInvalid ||
            windowInvalid ||
            cooldownInvalid ||
            busy ||
            (Boolean(rule) && (!onSave || Object.keys(changes).length === 0))
          }
        >
          {rule ? (busy ? "Saving..." : "Save changes") : (busy ? "Creating..." : "Create rule")}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

CreateAlertRuleDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  gatewayId: PropTypes.string,
  existingRules: PropTypes.arrayOf(PropTypes.object),
  getLatestRules: PropTypes.func,
  channelNames: PropTypes.arrayOf(PropTypes.string),
  rule: PropTypes.object,
  onSave: PropTypes.func,
};

export default CreateAlertRuleDialog;
