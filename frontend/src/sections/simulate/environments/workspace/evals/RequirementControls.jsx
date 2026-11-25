import PropTypes from "prop-types";
import { useState } from "react";
import {
  ButtonBase,
  InputAdornment,
  Stack,
  Switch,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { EVALS_COPY } from "./evals.constants";

const clamp = (value) => Math.min(100, Math.max(0, Math.round(value)));
const asPercent = (fraction) => Math.round(fraction * 100);

const THRESHOLD_TEXT_SX = {
  typography: "s3",
  color: "text.tertiary",
  flexShrink: 0,
  whiteSpace: "nowrap",
  fontVariantNumeric: "tabular-nums",
  mt: 0.375,
};

export function PassThreshold({ name, value, disabled = false, onChange }) {
  const [draft, setDraft] = useState(null);

  const commit = () => {
    const number = Number(draft);
    setDraft(null);
    if (draft === null || draft.trim() === "" || !Number.isFinite(number))
      return;
    const next = clamp(number);
    if (next !== asPercent(value)) onChange(next / 100);
  };

  if (draft !== null)
    return (
      <TextField
        size="small"
        autoFocus
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
          if (event.key === "Escape") setDraft(null);
        }}
        inputProps={{
          inputMode: "numeric",
          "aria-label": EVALS_COPY.thresholdLabel(name),
        }}
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              {EVALS_COPY.passAtLeast}
            </InputAdornment>
          ),
          endAdornment: <InputAdornment position="end">%</InputAdornment>,
        }}
        sx={{
          width: 104,
          flexShrink: 0,
          "& .MuiInputBase-input": { typography: "s3", py: 0.25 },
          "& .MuiInputAdornment-root p": { typography: "s3" },
        }}
      />
    );

  const label = EVALS_COPY.passAt(asPercent(value));
  if (disabled) return <Typography sx={THRESHOLD_TEXT_SX}>{label}</Typography>;
  return (
    <Tooltip arrow title={EVALS_COPY.thresholdTip}>
      <ButtonBase
        onClick={() => setDraft(String(asPercent(value)))}
        aria-label={EVALS_COPY.thresholdLabel(name)}
        sx={{
          ...THRESHOLD_TEXT_SX,
          borderBottom: "1px dotted",
          borderColor: "text.disabled",
          "&:hover": { color: "text.primary" },
        }}
      >
        {label}
      </ButtonBase>
    </Tooltip>
  );
}

PassThreshold.propTypes = {
  name: PropTypes.string.isRequired,
  value: PropTypes.number.isRequired,
  disabled: PropTypes.bool,
  onChange: PropTypes.func.isRequired,
};

export function RequiredSwitch({
  name,
  checked,
  disabled = false,
  disabledTip,
  onChange,
}) {
  return (
    <Tooltip arrow title={disabledTip || EVALS_COPY.requiredHint}>
      <Stack
        component="label"
        direction="row"
        alignItems="center"
        spacing={0.25}
        sx={{ mr: 0.75, cursor: disabled ? "default" : "pointer" }}
      >
        <Switch
          size="small"
          checked={checked}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
          inputProps={{ "aria-label": EVALS_COPY.requiredLabel(name) }}
        />
        <Typography
          sx={{
            typography: "s3",
            fontWeight: 600,
            color: checked ? "text.primary" : "text.tertiary",
          }}
        >
          {EVALS_COPY.required}
        </Typography>
      </Stack>
    </Tooltip>
  );
}

RequiredSwitch.propTypes = {
  name: PropTypes.string.isRequired,
  checked: PropTypes.bool.isRequired,
  disabled: PropTypes.bool,
  disabledTip: PropTypes.string,
  onChange: PropTypes.func.isRequired,
};
