import React, { useEffect, useId, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import FormControlLabel from "@mui/material/FormControlLabel";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";

import Section from "src/components/section";

import { useSaveCreditSettings } from "./api";
import {
  AUTO_RELOAD_CONSENT_TERMS,
  AUTO_RELOAD_CONSENT_VERSION,
  AUTO_RELOAD_NOT_RUNNING_TEXT,
  AUTO_RELOAD_TURN_OFF_TO_SAVE,
  AUTO_RELOAD_UNAVAILABLE_TEXT,
  autoReloadUnavailable,
  CONSENT_CHECKBOX_LABEL,
  CONSENT_EVERY_SAVE_NOTE,
  CONSENT_INCOMPLETE_TEXT,
  CONSENT_NOT_ACKNOWLEDGED_ERROR,
  CONSENT_TITLE,
  CONSENT_VERSION_LABEL,
  USD_NOT_A_NUMBER_ERROR,
  BILLING_UNAVAILABLE_NOTE,
  chosenChargeText,
  creditSettingsBody,
  settingsErrorText,
} from "./autoReloadConsent";
import { formatAmount } from "./format";

const asText = (value) =>
  value === null || value === undefined ? "" : String(value);

// A form value the server takes as a USD amount: set, finite, not negative.
const isUsd = (text) =>
  String(text).trim() !== "" &&
  Number.isFinite(Number(text)) &&
  Number(text) >= 0;

// The low-balance alert threshold and auto-reload preference
// (managed-credits/settings/). An auto-reload amount is at least the
// minimum top-up, as the server requires. With auto-reload on, the form shows
// the versioned authorization terms with the chosen threshold and amount, and
// sends consent_version only once the person agrees to them. The server
// refuses every save with auto-reload on that lacks it, so each such save
// needs a fresh agreement (an agreement covers one save of the values shown);
// turning auto-reload off needs none and never sends one. Where the summary
// says no automatic reload runs, the switch stays visible with that sentence
// beside it and can only be turned off: no terms, charge or agreement.
export default function AutoReloadForm({ summary, canBuy }) {
  const titleId = useId();
  const termsId = useId();
  const chargeId = useId();
  const unavailableId = useId();
  const save = useSaveCreditSettings();
  const [lowBalance, setLowBalance] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [threshold, setThreshold] = useState("");
  const [amount, setAmount] = useState("");
  const [error, setError] = useState("");
  // The [threshold, amount] the person agreed to, or null before agreeing.
  const [ackKey, setAckKey] = useState(null);

  useEffect(() => {
    setLowBalance(asText(summary.low_balance_threshold_usd));
    setEnabled(!!summary.auto_reload?.enabled);
    setThreshold(asText(summary.auto_reload?.threshold_usd));
    setAmount(asText(summary.auto_reload?.amount_usd));
  }, [
    summary.low_balance_threshold_usd,
    summary.auto_reload?.enabled,
    summary.auto_reload?.threshold_usd,
    summary.auto_reload?.amount_usd,
  ]);

  // A summary refresh or availability change invalidates an opt-in. Only
  // changed server settings above replace the draft: billing restoration
  // must not undo an unsaved revocation from a still-enabled summary.
  useEffect(() => {
    setAckKey(null);
  }, [summary, canBuy]);

  if (summary.enabled !== true) return null;

  const minTopup = Number(summary.min_topup_usd);
  const unavailable = autoReloadUnavailable(summary);
  const savedOn = !!summary.auto_reload?.enabled;
  // The unavailable reason is stated in words below, never as its code.
  const disabledReason = unavailable
    ? ""
    : summary.auto_reload?.disabled_reason;
  // Turning off an existing preference is independent of buying credits.
  // Keep the switch visible after an unsaved revocation, but never offer
  // re-enabling while purchases or automatic reloads are unavailable.
  const showAutoReload = unavailable || canBuy || savedOn || enabled;
  const canTurnOn = canBuy && !unavailable;
  const valuesReady = isUsd(threshold) && isUsd(amount);
  const chargeKey = JSON.stringify([threshold, amount]);
  const acknowledged = enabled && ackKey === chargeKey;

  const submit = (event) => {
    event.preventDefault();
    if (save.isPending) return;
    if (enabled && unavailable) {
      setError(AUTO_RELOAD_TURN_OFF_TO_SAVE);
      return;
    }
    if (enabled && !canBuy) {
      setError(BILLING_UNAVAILABLE_NOTE);
      return;
    }
    if (lowBalance !== "" && !isUsd(lowBalance)) {
      setError(USD_NOT_A_NUMBER_ERROR);
      return;
    }
    if (enabled) {
      if (threshold === "" || amount === "") {
        setError("Set both the reload threshold and the reload amount.");
        return;
      }
      if (!valuesReady) {
        setError(USD_NOT_A_NUMBER_ERROR);
        return;
      }
      if (Number(amount) < minTopup) {
        setError(`The reload amount is at least ${formatAmount(minTopup)}.`);
        return;
      }
      if (!acknowledged) {
        setError(CONSENT_NOT_ACKNOWLEDGED_ERROR);
        return;
      }
    }
    setError("");
    // Consume the agreement on dispatch as well as settlement: it covers
    // this one save, never a second click before the request settles.
    setAckKey(null);
    save.mutate(
      creditSettingsBody({
        lowBalance,
        enabled,
        threshold,
        amount,
        consentVersion:
          enabled && acknowledged ? AUTO_RELOAD_CONSENT_VERSION : undefined,
      }),
      {
        // An agreement covers one save: whatever the outcome, the next save
        // with auto-reload on asks again.
        onSuccess: () => {
          setAckKey(null);
          enqueueSnackbar("Credit settings saved.", { variant: "success" });
        },
        onError: (err) => {
          setAckKey(null);
          setError(settingsErrorText(err));
        },
      },
    );
  };

  return (
    <Box
      component="section"
      aria-labelledby={titleId}
      sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
    >
      {/* The heading also names revocation when buying is unavailable. */}
      <Typography id={titleId} typography="s1" fontWeight="fontWeightMedium">
        {showAutoReload ? "Low balance and auto-reload" : "Low balance alert"}
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
            helperText="Emails organization owners and admins on a crossing below this balance. Requires configured email delivery in self-hosted deployments. Leave empty for no alert."
          />
          {showAutoReload && (
            <FormControlLabel
              control={
                <Switch
                  checked={enabled}
                  disabled={save.isPending || (!canTurnOn && !enabled)}
                  onChange={(event) => {
                    setEnabled(event.target.checked);
                    setAckKey(null);
                  }}
                  inputProps={
                    unavailable
                      ? { "aria-describedby": unavailableId }
                      : undefined
                  }
                />
              }
              label="Auto-reload"
            />
          )}
          {unavailable && (
            <Typography
              id={unavailableId}
              typography="s2"
              color="text.secondary"
            >
              {savedOn
                ? AUTO_RELOAD_NOT_RUNNING_TEXT
                : AUTO_RELOAD_UNAVAILABLE_TEXT}
            </Typography>
          )}
          {enabled && !canBuy && !unavailable && (
            <Typography typography="s2" color="text.secondary">
              {BILLING_UNAVAILABLE_NOTE}
            </Typography>
          )}
          {enabled && !unavailable && (
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
              <TextField
                size="small"
                type="number"
                label="Reload when balance falls below (USD)"
                value={threshold}
                disabled={!canBuy || save.isPending}
                onChange={(event) => {
                  setThreshold(event.target.value);
                  setAckKey(null);
                }}
                inputProps={{ min: 0, step: "0.01" }}
                fullWidth
              />
              <TextField
                size="small"
                type="number"
                label="Reload amount (USD)"
                value={amount}
                disabled={!canBuy || save.isPending}
                onChange={(event) => {
                  setAmount(event.target.value);
                  setAckKey(null);
                }}
                inputProps={{ min: minTopup, step: "0.01" }}
                fullWidth
              />
            </Stack>
          )}
          {/* Keep the saved terms visible during an outage. New opt-in waits
              for purchasing to return; revocation above never needs it.
              Where no automatic reload runs there is nothing to agree to. */}
          {enabled && !unavailable && (
            <Section variant="panel">
              <Stack spacing={1}>
                <Typography typography="s2" fontWeight="fontWeightMedium">
                  {CONSENT_TITLE}
                </Typography>
                <Typography typography="s2" color="text.secondary">
                  {CONSENT_VERSION_LABEL}
                </Typography>
                <Typography id={termsId} typography="s2">
                  {AUTO_RELOAD_CONSENT_TERMS}
                </Typography>
                <Typography id={chargeId} typography="s2">
                  {valuesReady
                    ? chosenChargeText(threshold, amount)
                    : CONSENT_INCOMPLETE_TEXT}
                </Typography>
                <Typography typography="s2" color="text.secondary">
                  {CONSENT_EVERY_SAVE_NOTE}
                </Typography>
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={acknowledged}
                      disabled={!canBuy || !valuesReady || save.isPending}
                      onChange={(event) =>
                        setAckKey(event.target.checked ? chargeKey : null)
                      }
                      inputProps={{
                        "aria-describedby": `${termsId} ${chargeId}`,
                      }}
                    />
                  }
                  label={CONSENT_CHECKBOX_LABEL}
                />
              </Stack>
            </Section>
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
