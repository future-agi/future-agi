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
import { recordActivationEvent } from "src/sections/onboarding-home/api/onboarding-home-api";
import { buildGatewayPolicyCreatedPayload } from "../gatewayOnboardingEvents";

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
  {
    value: "per_key",
    label: "Per API Key",
    description: "Spending limit applied to each individual API key",
  },
  {
    value: "per_user",
    label: "Per User",
    description: "Spending limit per user across all their API keys",
  },
  {
    value: "per_model",
    label: "Per Model",
    description: "Spending limit per model (e.g., cap expensive models)",
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
    description: "Rate limit requests when budget is exceeded",
  },
];

const SetBudgetDialog = ({
  open,
  onClose,
  gatewayId,
  budget,
  onboardingRequestId,
  shouldRecordOnboardingCompletion = false,
}) => {
  const isEditMode = Boolean(budget);
  const [level, setLevel] = useState("");
  const [limit, setLimit] = useState("");
  const [alertThreshold, setAlertThreshold] = useState("80");
  const [onExceed, setOnExceed] = useState("warn");

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
    } else if (open && !budget) {
      setLevel("");
      setLimit("");
      setAlertThreshold("80");
      setOnExceed("warn");
    }
  }, [budget, open]);

  const handleSave = () => {
    if (!level || !limit) return;

    const config = {
      limit: Number(limit),
      alert_threshold: Number(alertThreshold),
      on_exceed: onExceed,
    };

    setBudgetMutation.mutate(
      { gatewayId, level, config },
      {
        onSuccess: () => {
          const label =
            BUDGET_LEVELS.find((b) => b.value === level)?.label || level;
          enqueueSnackbar(`Budget "${label}" saved`, { variant: "success" });
          if (shouldRecordOnboardingCompletion) {
            recordActivationEvent(
              buildGatewayPolicyCreatedPayload({
                gatewayId,
                policyId: `budget:${level}`,
                policyType: "budget",
                requestId: onboardingRequestId,
                source: "gateway_budget_onboarding",
                metadata: {
                  budget_level: level,
                  limit: Number(limit),
                  alert_threshold: Number(alertThreshold),
                  on_exceed: onExceed,
                },
              }),
            ).catch(() => null);
          }
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
            label="Monthly Limit ($)"
            type="number"
            fullWidth
            required
            value={limit}
            onChange={(e) => setLimit(e.target.value)}
            placeholder="1000"
            helperText="Maximum spend in USD per billing period"
            inputProps={{ min: 0, step: 100 }}
          />

          <TextField
            label="Alert Threshold (%)"
            type="number"
            fullWidth
            value={alertThreshold}
            onChange={(e) => setAlertThreshold(e.target.value)}
            helperText="Get notified when usage reaches this percentage of the limit"
            inputProps={{ min: 0, max: 100, step: 5 }}
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
          disabled={!level || !limit || setBudgetMutation.isPending}
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
  onboardingRequestId: PropTypes.string,
  shouldRecordOnboardingCompletion: PropTypes.bool,
};

export default SetBudgetDialog;
