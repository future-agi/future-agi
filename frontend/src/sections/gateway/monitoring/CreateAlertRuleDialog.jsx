import React, { useState } from "react";
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

const CreateAlertRuleDialog = ({ open, onClose, gatewayId }) => {
  const [name, setName] = useState("");
  const [metric, setMetric] = useState("error_rate");
  const [condition, setCondition] = useState(">");
  const [threshold, setThreshold] = useState("");
  const [window, setWindow] = useState("5m");
  const [severity, setSeverity] = useState("warning");

  const updateConfig = useUpdateConfig();

  const resetForm = () => {
    setName("");
    setMetric("error_rate");
    setCondition(">");
    setThreshold("");
    setWindow("5m");
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
      window,
      severity,
      enabled: true,
    };

    // We'll send this as a config patch that adds the rule
    updateConfig.mutate(
      {
        gatewayId,
        config: {
          alerting: {
            rules: { [name]: rule },
          },
        },
      },
      {
        onSuccess: () => {
          enqueueSnackbar(`Alert rule "${name}" created`, {
            variant: "success",
          });
          handleClose();
        },
        onError: () => {
          enqueueSnackbar("Failed to create alert rule", { variant: "error" });
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
      <DialogTitle>Create Alert Rule</DialogTitle>
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
            value={window}
            onChange={(e) => setWindow(e.target.value)}
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
          {updateConfig.isPending ? "Creating..." : "Create Rule"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

CreateAlertRuleDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  gatewayId: PropTypes.string,
};

export default CreateAlertRuleDialog;
