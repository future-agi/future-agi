import React, { useState, useEffect } from "react";
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
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { useSetBudget } from "../providers/hooks/useGatewayConfig";

const BUDGET_LEVELS = [
  {
    value: "org_limit",
    label: "Organization",
    description: "Overall spending limit for your entire organization",
  },
  {
    value: "hard_limit",
    label: "Hard Limit",
    description: "Absolute cap — requests are blocked when exceeded",
  },
];

const ACTIONS = [
  {
    value: "warn",
    label: "Warn",
    description: "Log a warning but allow the request",
  },
  {
    value: "block",
    label: "Block",
    description: "Reject the request with a 429 error",
  },
  {
    value: "throttle",
    label: "Throttle",
    description:
      "Past the limit, allow only the set number of requests a minute on each gateway replica; the rest get a 429 with Retry-After",
  },
];

const DEFAULT_THROTTLE_RPM = "1";

// The period the gateway applies to the limit (the org's period; set in
// Org Config > Budgets), as the limit field's helper text states it.
const PERIOD_TEXT = {
  daily: "per day (resets at 00:00 UTC)",
  weekly: "per week (resets Monday 00:00 UTC)",
  monthly: "per month (resets on the 1st, 00:00 UTC)",
  total: "in total (never resets)",
};

function limitHelperText(period) {
  const text = PERIOD_TEXT[period] || PERIOD_TEXT.monthly;
  return `Maximum spend in USD ${text}; the period is set in Org Config > Budgets`;
}

// A throttle rate the gateway takes: a whole number from 1.
function isThrottleRpm(value) {
  return /^\d+$/.test(String(value).trim()) && Number(value) >= 1;
}

// An alert threshold the gateway takes: more than 0 and at most 100 (%).
function isAlertThreshold(value) {
  const n = Number(value);
  return String(value).trim() !== "" && n > 0 && n <= 100;
}

const SetBudgetDialog = ({
  open,
  onClose,
  gatewayId,
  budget,
  period = "monthly",
}) => {
  const isEditMode = Boolean(budget);
  const [level, setLevel] = useState("");
  const [limit, setLimit] = useState("");
  const [alertThreshold, setAlertThreshold] = useState("80");
  const [onExceed, setOnExceed] = useState("warn");
  const [throttleRpm, setThrottleRpm] = useState(DEFAULT_THROTTLE_RPM);

  const setBudgetMutation = useSetBudget();

  useEffect(() => {
    if (budget && open) {
      setLevel(budget.level || budget.name || "");
      setLimit(budget.limit || budget.max || budget.hard_cap || "");
      setAlertThreshold(
        budget.alert_threshold ?? budget.alertThreshold ?? "80",
      );
      setOnExceed(
        budget.on_exceed || budget.onExceed || budget.action || "warn",
      );
      setThrottleRpm(
        String(
          budget.throttle_rpm ?? budget.throttleRpm ?? DEFAULT_THROTTLE_RPM,
        ),
      );
    } else if (open && !budget) {
      setLevel("");
      setLimit("");
      setAlertThreshold("80");
      setOnExceed("warn");
      setThrottleRpm(DEFAULT_THROTTLE_RPM);
    }
  }, [budget, open]);

  const throttling = onExceed === "throttle";
  const throttleRpmValid = !throttling || isThrottleRpm(throttleRpm);
  const alertThresholdValid = isAlertThreshold(alertThreshold);
  const canSave =
    Boolean(level && limit) && throttleRpmValid && alertThresholdValid;

  const handleSave = () => {
    if (!canSave) return;

    // saved_at tells the push which of the Organization and Hard Limit
    // budgets was saved last: that one is enforced.
    const config = {
      limit: Number(limit),
      alert_threshold: Number(alertThreshold),
      on_exceed: onExceed,
      saved_at: new Date().toISOString(),
      ...(throttling ? { throttle_rpm: Number(throttleRpm) } : {}),
    };

    setBudgetMutation.mutate(
      { gatewayId, level, config },
      {
        onSuccess: () => {
          const label =
            BUDGET_LEVELS.find((b) => b.value === level)?.label || level;
          enqueueSnackbar(`Budget "${label}" saved`, { variant: "success" });
          onClose();
        },
        onError: () => {
          enqueueSnackbar("Failed to save budget", { variant: "error" });
        },
      },
    );
  };

  const selectedLevel = BUDGET_LEVELS.find((b) => b.value === level);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{isEditMode ? "Edit Budget" : "Set Budget"}</DialogTitle>
      <DialogContent>
        <Stack spacing={2.5} mt={1}>
          <TextField
            label="Budget Level"
            select
            fullWidth
            required
            value={level}
            onChange={(e) => setLevel(e.target.value)}
            disabled={isEditMode}
            helperText={
              isEditMode
                ? "Budget level cannot be changed"
                : selectedLevel?.description ||
                  "Select what this budget applies to"
            }
          >
            {BUDGET_LEVELS.map((opt) => (
              <MenuItem key={opt.value} value={opt.value}>
                {opt.label}
              </MenuItem>
            ))}
          </TextField>

          <TextField
            label="Limit ($)"
            type="number"
            fullWidth
            required
            value={limit}
            onChange={(e) => setLimit(e.target.value)}
            placeholder="1000"
            helperText={limitHelperText(period)}
            inputProps={{ min: 0, step: 100 }}
          />

          <TextField
            label="Alert Threshold (%)"
            type="number"
            fullWidth
            value={alertThreshold}
            onChange={(e) => setAlertThreshold(e.target.value)}
            error={!alertThresholdValid}
            helperText={
              alertThresholdValid
                ? "From this percentage of the limit, each request's log carries a budget warning (org_budget_warning)"
                : "Enter a percentage above 0 and up to 100"
            }
            inputProps={{ min: 1, max: 100, step: 5 }}
          />

          <TextField
            label="When Limit Exceeded"
            select
            fullWidth
            value={onExceed}
            onChange={(e) => setOnExceed(e.target.value)}
          >
            {ACTIONS.map((a) => (
              <MenuItem key={a.value} value={a.value}>
                <Stack>
                  <Typography variant="body2">{a.label}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    {a.description}
                  </Typography>
                </Stack>
              </MenuItem>
            ))}
          </TextField>

          {throttling && (
            <TextField
              label="Requests per minute"
              type="number"
              fullWidth
              required
              value={throttleRpm}
              onChange={(e) => setThrottleRpm(e.target.value)}
              error={!throttleRpmValid}
              helperText={
                throttleRpmValid
                  ? "Past the limit, each gateway replica allows this many requests a minute for this budget, so N replicas allow up to N times this"
                  : "Enter a whole number from 1"
              }
              inputProps={{ min: 1, step: 1 }}
            />
          )}

          {setBudgetMutation.isError && (
            <Alert severity="error">
              {setBudgetMutation.error?.message || "Failed to save budget"}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={handleSave}
          disabled={!canSave || setBudgetMutation.isPending}
        >
          {setBudgetMutation.isPending ? "Saving..." : "Save"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

SetBudgetDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  gatewayId: PropTypes.string,
  budget: PropTypes.object,
  period: PropTypes.string,
};

export default SetBudgetDialog;
