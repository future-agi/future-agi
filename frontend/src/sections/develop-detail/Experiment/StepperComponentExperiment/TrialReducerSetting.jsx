// W1-8: how each row's tries become the row's value in this experiment's
// runs (the leaderboard and the results header read it): Mean · Median ·
// Most common · Best · pass@k · pass^k · At least [n] of k. The create body
// sends `trial_reducer: {kind, n?}`; the definitions are trialReducers.js.
import React, { useEffect } from "react";
import PropTypes from "prop-types";
import { Box, MenuItem, Stack, TextField, Typography } from "@mui/material";
import { useController } from "react-hook-form";
import {
  parseReducer,
  reducerLabel,
} from "src/sections/common/Analysis/trialReducers";

const KINDS = [
  "mean",
  "median",
  "mode",
  "max",
  "pass_at_k",
  "pass_hat_k",
  "at_least",
];
const HELP = {
  mean: "The share of a row's tries that pass (a score: their mean).",
  median: "The middle try.",
  mode: "The verdict most tries gave; a tie counts as a fail.",
  max: "The best try.",
  pass_at_k: "A row passes when any of its tries passes.",
  pass_hat_k: "A row passes only when every try passes.",
  at_least: "A row passes when at least n of its tries pass.",
};

const TrialReducerSetting = ({ control, tries, disabled = false }) => {
  const { field } = useController({ control, name: "trialReducer" });
  const k = Math.max(1, Number(tries) || 1);
  const value = parseReducer(field.value ?? "mean");
  const n = Math.min(k, Math.max(1, Number(value.n) || Math.ceil(k / 2)));
  // Review S2: lowering the tries lowers a stored `At least n` with it, so
  // the value the create sends is the one on screen.
  const { onChange } = field;
  useEffect(() => {
    if (value.kind === "at_least" && Number(value.n) > k)
      onChange({ kind: "at_least", n: k });
  }, [value.kind, value.n, k, onChange]);
  return (
    <Box data-testid="setting-trialReducer">
      <Stack direction="row" alignItems="center" gap={1}>
        <TextField
          select
          size="small"
          label="Score each row from its tries"
          value={value.kind}
          disabled={disabled}
          onChange={(e) =>
            field.onChange(
              e.target.value === "at_least"
                ? { kind: "at_least", n }
                : { kind: e.target.value },
            )
          }
          sx={{ width: 240, bgcolor: "background.paper" }}
        >
          {KINDS.map((kind) => (
            <MenuItem key={kind} value={kind}>
              {kind === "at_least"
                ? `At least n of ${k}`
                : reducerLabel(kind, k)}
            </MenuItem>
          ))}
        </TextField>
        {value.kind === "at_least" ? (
          <TextField
            size="small"
            type="number"
            label="n"
            value={n}
            disabled={disabled}
            onChange={(e) =>
              field.onChange({
                kind: "at_least",
                n: Math.min(
                  k,
                  Math.max(1, Math.round(Number(e.target.value)) || 1),
                ),
              })
            }
            inputProps={{
              min: 1,
              max: k,
              "aria-label": "Tries that must pass",
            }}
            sx={{ width: 72 }}
          />
        ) : null}
      </Stack>
      <Typography
        typography="s3"
        color="text.secondary"
        sx={{ mt: 0.5, maxWidth: 320 }}
      >
        {HELP[value.kind]}
      </Typography>
      {/* v2.1 (knob:trial-reducer): where the choice is applied, nothing
          more: Success and the medals read every try. */}
      <Typography
        typography="s3"
        color="text.disabled"
        sx={{ mt: 0.25, maxWidth: 320 }}
        data-testid="trial-reducer-scope"
      >
        Used by the run&apos;s score in the results table and the
        leaderboard&apos;s From tries column. Success and medals count every
        try.
      </Typography>
    </Box>
  );
};

TrialReducerSetting.propTypes = {
  control: PropTypes.object,
  tries: PropTypes.number,
  disabled: PropTypes.bool,
};

export default TrialReducerSetting;
