// Section 4 · limits (R4-SPEC D21; lane Ra MUST 6): `Tries per task 3` ·
// `Run 4 at a time` (max = the agent's declared concurrency) · `Stop if
// cost goes over $5.00` · `Time limit per try 120 s`.
import React from "react";
import PropTypes from "prop-types";
import { Box, InputAdornment, TextField, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";

function Field({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  suffix,
  prefix,
  tooltip,
  testId,
  disabled = false,
}) {
  return (
    <CustomTooltip
      show={Boolean(tooltip)}
      title={tooltip ?? ""}
      size="small"
      placement="top"
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, minWidth: 0 }}>
        <Typography typography="s2" color="text.secondary" noWrap>
          {label}
        </Typography>
        <TextField
          size="small"
          type="number"
          value={value}
          disabled={disabled}
          onChange={(e) => {
            const n = Number(e.target.value);
            if (!Number.isFinite(n)) return;
            onChange(Math.min(max ?? n, Math.max(min ?? n, n)));
          }}
          inputProps={{
            min,
            max,
            step,
            "aria-label": label,
            "data-testid": testId,
            style: { textAlign: "right" },
          }}
          InputProps={{
            startAdornment: prefix ? (
              <InputAdornment position="start">{prefix}</InputAdornment>
            ) : null,
            endAdornment: suffix ? (
              <InputAdornment position="end">{suffix}</InputAdornment>
            ) : null,
          }}
          sx={{ width: prefix || suffix ? 104 : 72, "& input": { py: 0.75 } }}
        />
      </Box>
    </CustomTooltip>
  );
}
Field.propTypes = {
  disabled: PropTypes.bool,
  label: PropTypes.string.isRequired,
  value: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  onChange: PropTypes.func.isRequired,
  min: PropTypes.number,
  max: PropTypes.number,
  step: PropTypes.number,
  suffix: PropTypes.string,
  prefix: PropTypes.string,
  tooltip: PropTypes.string,
  testId: PropTypes.string,
};

/** Run at a time when nothing caps it (S5: no invented cap). */
export const AT_A_TIME_MAX = 50;

export default function LimitsRow({
  value,
  onChange,
  rowNoun = "task",
  capLine,
}) {
  const v = value ?? {};
  // options-truth L3 G4 fix 1: the field keeps what the user set; the cap
  // (sandbox.js cappedLimits) clamps the value the run uses and says so.
  const asked = v.requestedConcurrency ?? v.concurrency;
  const set = (patch) => {
    const {
      requestedConcurrency: _asked,
      capLine: _line,
      clampReason: _reason,
      ...own
    } = v;
    onChange({ ...own, concurrency: asked, ...patch });
  };
  // A voice config owns tries and run-at-a-time (sourceModel limitsWithSlot).
  const bySource = (k) => (v.setBySource ?? []).includes(k);
  const SET_ABOVE = "Set in the voice config in section 1";
  return (
    <Box
      sx={{ display: "flex", flexWrap: "wrap", columnGap: 2.5, rowGap: 1 }}
      data-testid="limits-row"
    >
      <Field
        label={`Tries per ${rowNoun}`}
        value={v.tries}
        min={1}
        max={10}
        onChange={(n) => set({ tries: n })}
        testId="limit-tries"
        disabled={bySource("tries")}
        tooltip={
          bySource("tries")
            ? SET_ABOVE
            : "Each try runs the row again; pass^k needs more than one"
        }
      />
      <Field
        label="Run at a time"
        value={asked}
        min={1}
        max={AT_A_TIME_MAX}
        onChange={(n) => set({ concurrency: n })}
        testId="limit-concurrency"
        disabled={bySource("concurrency")}
        tooltip={
          bySource("concurrency")
            ? SET_ABOVE
            : "How many rows run at the same time"
        }
      />
      {capLine && !bySource("concurrency") ? (
        // options-truth L3 G4 fix 1 (S5): the cap is said whenever one
        // applies (the sandbox's, or the agent's on its machine), with
        // `(you set N)` when it clamps; never applied silently.
        <Typography
          typography="s2"
          color={v.clampReason ? "warning.dark" : "text.secondary"}
          data-testid="limit-concurrency-cap"
          sx={{ alignSelf: "center" }}
        >
          {capLine}
        </Typography>
      ) : null}
      <Field
        label="Stop if cost goes over"
        prefix="$"
        value={v.maxCostUsd}
        min={0}
        step={0.5}
        onChange={(n) => set({ maxCostUsd: n })}
        testId="limit-cost"
      />
      <Field
        label="Time limit per try"
        suffix="s"
        value={v.timeoutS}
        min={5}
        max={3600}
        step={5}
        onChange={(n) => set({ timeoutS: n })}
        testId="limit-timeout"
      />
    </Box>
  );
}

LimitsRow.propTypes = {
  value: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  rowNoun: PropTypes.string,
  /** `The sandbox runs at most 10 at a time (you set 12)` (cappedLimits). */
  capLine: PropTypes.string,
};
