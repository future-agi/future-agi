import React, { useEffect, useId, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import FormControlLabel from "@mui/material/FormControlLabel";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";

import { useSaveCreditSettings } from "./api";
import { formatAmount } from "./format";

const asText = (value) =>
  value === null || value === undefined ? "" : String(value);

// The low-balance alert threshold and auto-reload preference
// (managed-credits/settings/). An auto-reload amount is at least the
// minimum top-up, as the server requires.
export default function AutoReloadForm({ summary, canBuy }) {
  const titleId = useId();
  const save = useSaveCreditSettings();
  const [lowBalance, setLowBalance] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [threshold, setThreshold] = useState("");
  const [amount, setAmount] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setLowBalance(asText(summary.low_balance_threshold_usd));
    setEnabled(!!summary.auto_reload?.enabled);
    setThreshold(asText(summary.auto_reload?.threshold_usd));
    setAmount(asText(summary.auto_reload?.amount_usd));
  }, [summary]);

  if (summary.enabled !== true) return null;

  const minTopup = Number(summary.min_topup_usd);
  const disabledReason = summary.auto_reload?.disabled_reason;

  const submit = (event) => {
    event.preventDefault();
    if (enabled) {
      if (threshold === "" || amount === "") {
        setError("Set both the reload threshold and the reload amount.");
        return;
      }
      if (Number(amount) < minTopup) {
        setError(`The reload amount is at least ${formatAmount(minTopup)}.`);
        return;
      }
    }
    setError("");
    save.mutate(
      {
        low_balance_threshold_usd: lowBalance === "" ? null : lowBalance,
        auto_reload: {
          enabled,
          threshold_usd: enabled ? threshold : null,
          amount_usd: enabled ? amount : null,
        },
      },
      {
        onSuccess: () =>
          enqueueSnackbar("Credit settings saved.", { variant: "success" }),
        onError: (err) =>
          setError(
            err?.result?.message ||
              (typeof err?.result === "string" ? err.result : "") ||
              "The settings were not saved. Try again.",
          ),
      },
    );
  };

  return (
    <Box
      component="section"
      aria-labelledby={titleId}
      sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
    >
      {/* The heading names only the controls below it: auto-reload shows
          only where this build can charge for credit. */}
      <Typography id={titleId} typography="s1" fontWeight="fontWeightMedium">
        {canBuy ? "Low balance and auto-reload" : "Low balance alert"}
      </Typography>
      {disabledReason && (
        <Alert severity="info">
          {canBuy
            ? `Auto-reload was turned off: ${disabledReason}. Turn it on again to resume.`
            : `Auto-reload was turned off: ${disabledReason}.`}
        </Alert>
      )}
      <Box component="form" noValidate onSubmit={submit}>
        <Stack spacing={1.5} sx={{ maxWidth: 520 }}>
          <TextField
            size="small"
            type="number"
            label="Alert when balance falls below (USD)"
            value={lowBalance}
            onChange={(event) => setLowBalance(event.target.value)}
            inputProps={{ min: 0, step: "0.01" }}
            helperText="Leave empty for no alert."
          />
          {canBuy && (
            <FormControlLabel
              control={
                <Switch
                  checked={enabled}
                  onChange={(event) => setEnabled(event.target.checked)}
                />
              }
              label="Auto-reload"
            />
          )}
          {canBuy && enabled && (
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
              <TextField
                size="small"
                type="number"
                label="Reload when balance falls below (USD)"
                value={threshold}
                onChange={(event) => setThreshold(event.target.value)}
                inputProps={{ min: 0, step: "0.01" }}
                fullWidth
              />
              <TextField
                size="small"
                type="number"
                label="Reload amount (USD)"
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
                inputProps={{ min: minTopup, step: "0.01" }}
                fullWidth
              />
            </Stack>
          )}
          {error && (
            <Typography typography="s2" color="error.main" role="alert">
              {error}
            </Typography>
          )}
          <Box>
            <Button
              type="submit"
              variant="outlined"
              size="small"
              disabled={save.isPending}
            >
              Save
            </Button>
          </Box>
        </Stack>
      </Box>
    </Box>
  );
}

AutoReloadForm.propTypes = {
  summary: PropTypes.object.isRequired,
  canBuy: PropTypes.bool,
};
