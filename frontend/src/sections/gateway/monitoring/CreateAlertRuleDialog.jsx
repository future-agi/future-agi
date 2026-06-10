import React, { useEffect, useState } from "react";
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

const METRICS = [
  "error_rate",
  "latency_p99",
  "latency_p95",
  "request_rate",
  "cost_per_hour",
];
const CONDITIONS = [">", ">=", "<", "<=", "=="];
const SEVERITIES = ["critical", "warning", "info"];

function ruleName(rule, index) {
  return rule?.name || `Rule ${index + 1}`;
}

function rulesToMap(rules) {
  return Object.fromEntries(
    rules.map((rule, index) => {
      const name = ruleName(rule, index);
      return [name, { ...rule, name }];
    }),
  );
}

const CreateAlertRuleDialog = ({
  open,
  onClose,
  gatewayId,
  initialRule = null,
  existingRules = [],
  replaceRules = false,
}) => {
  const isEdit = Boolean(initialRule);
  const [name, setName] = useState("");
  const [metric, setMetric] = useState("error_rate");
  const [condition, setCondition] = useState(">");
  const [threshold, setThreshold] = useState("");
  const [windowValue, setWindowValue] = useState("5m");
  const [severity, setSeverity] = useState("warning");

  const updateConfig = useUpdateConfig();

  useEffect(() => {
    if (!open) return;
    setName(initialRule?.name || "");
    setMetric(initialRule?.metric || "error_rate");
    setCondition(initialRule?.condition || initialRule?.operator || ">");
    setThreshold(
      initialRule?.threshold === undefined || initialRule?.threshold === null
        ? ""
        : String(initialRule.threshold),
    );
    setWindowValue(initialRule?.window || initialRule?.duration || "5m");
    setSeverity(initialRule?.severity || "warning");
  }, [initialRule, open]);

  const resetForm = () => {
    setName("");
    setMetric("error_rate");
    setCondition(">");
    setThreshold("");
    setWindowValue("5m");
    setSeverity("warning");
  };

  const handleClose = () => {
    resetForm();
    onClose();
  };

  const handleCreate = () => {
    const rule = {
      name,
      metric,
      condition,
      threshold: Number(threshold),
      window: windowValue,
      severity,
      enabled: true,
    };
    const originalName = initialRule?.name;
    const rulesPatch = replaceRules ? rulesToMap(existingRules) : {};
    if (originalName && originalName !== name) {
      if (replaceRules) {
        delete rulesPatch[originalName];
      } else {
        rulesPatch[originalName] = null;
      }
    }
    rulesPatch[name] = rule;

    updateConfig.mutate(
      {
        gatewayId,
        config: {
          alerting: {
            rules: rulesPatch,
          },
        },
      },
      {
        onSuccess: () => {
          enqueueSnackbar(
            `Alert rule "${name}" ${isEdit ? "updated" : "created"}`,
            {
              variant: "success",
            },
          );
          handleClose();
        },
        onError: () => {
          enqueueSnackbar(
            `Failed to ${isEdit ? "update" : "create"} alert rule`,
            {
              variant: "error",
            },
          );
        },
      },
    );
  };

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        {isEdit ? "Edit Alert Rule" : "Create Alert Rule"}
      </DialogTitle>
      <DialogContent>
        <Stack spacing={2} mt={1}>
          <TextField
            label="Rule Name"
            fullWidth
            required
            value={name}
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
            {METRICS.map((m) => (
              <MenuItem key={m} value={m}>
                {m}
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
          <TextField
            label="Window"
            fullWidth
            value={windowValue}
            onChange={(e) => setWindowValue(e.target.value)}
            placeholder="e.g., 5m, 1h"
          />
          <TextField
            label="Severity"
            select
            fullWidth
            value={severity}
            onChange={(e) => setSeverity(e.target.value)}
          >
            {SEVERITIES.map((s) => (
              <MenuItem key={s} value={s}>
                {s}
              </MenuItem>
            ))}
          </TextField>
          {updateConfig.isError && (
            <Alert severity="error">
              {updateConfig.error?.message || "Failed to create rule"}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={handleCreate}
          disabled={!name.trim() || !threshold || updateConfig.isPending}
        >
          {updateConfig.isPending
            ? isEdit
              ? "Saving..."
              : "Creating..."
            : isEdit
              ? "Save Rule"
              : "Create Rule"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

CreateAlertRuleDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  gatewayId: PropTypes.string,
  initialRule: PropTypes.object,
  existingRules: PropTypes.arrayOf(PropTypes.object),
  replaceRules: PropTypes.bool,
};

export default CreateAlertRuleDialog;
