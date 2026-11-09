// Small shared pieces of the dock's change tabs: the monospace editor with
// a character count, the live word diff, a labelled row with the parent's
// value greyed on the right, and a compact select.
import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import TextDiff from "src/sections/common/ValueViewer/TextDiff";

export const MONO =
  "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace";

/** Multiline monospace editor (native undo/redo) + `123 characters`. */
export const PromptEditor = ({
  value,
  onChange,
  readOnly,
  rows = 6,
  label,
}) => (
  <Box>
    <TextField
      multiline
      fullWidth
      minRows={rows}
      maxRows={rows}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value)}
      inputProps={{
        "aria-label": label,
        "data-iterate-editor": "true",
        readOnly,
        spellCheck: false,
      }}
      sx={{
        "& .MuiInputBase-root": {
          fontFamily: MONO,
          fontSize: 12,
          lineHeight: 1.55,
          p: 1,
          bgcolor: "background.neutral",
        },
      }}
    />
    <Typography
      typography="s3"
      color="text.disabled"
      sx={{ display: "block", textAlign: "right", mt: 0.25 }}
    >
      {`${(value ?? "").length.toLocaleString("en-US")} characters`}
    </Typography>
  </Box>
);

PromptEditor.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  readOnly: PropTypes.bool,
  rows: PropTypes.number,
  label: PropTypes.string,
};

/** The word diff vs the parent, with the toolbar legend. */
/**
 * iterate r2 G3 (P11): the diff's own legend — `Removed · Added`, drawn
 * like the diff marks (struck red / underlined green).
 */
export const ChangeDiffLegend = () => (
  <Stack
    direction="row"
    alignItems="center"
    gap={1}
    data-testid="change-diff-legend"
    sx={{ typography: "s3", color: "text.secondary" }}
  >
    <Box
      component="span"
      sx={{
        color: "red.600",
        textDecoration: "line-through",
        bgcolor: (t) => `${t.palette.error.main}14`,
        px: 0.5,
        borderRadius: 0.5,
      }}
    >
      Removed
    </Box>
    <Box component="span" aria-hidden>
      ·
    </Box>
    <Box
      component="span"
      sx={{
        color: "green.700",
        bgcolor: (t) => `${t.palette.success.main}1f`,
        px: 0.5,
        borderRadius: 0.5,
      }}
    >
      Added
    </Box>
  </Stack>
);

export const ChangeDiff = ({ from, to, caption }) =>
  from === to ? null : (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.75,
        p: 1,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{ mb: 0.5 }}
      >
        <Typography typography="s3" color="text.secondary">
          {caption}
        </Typography>
        <ChangeDiffLegend />
      </Stack>
      <Box sx={{ typography: "s2", "& *": { fontSize: "12px !important" } }}>
        <TextDiff from={from ?? ""} to={to ?? ""} kind="words" />
      </Box>
    </Box>
  );

ChangeDiff.propTypes = {
  from: PropTypes.string,
  to: PropTypes.string,
  caption: PropTypes.string,
};

/** `Label  [control]  was <parent>` (parent value greyed, only when changed). */
export const ParamRow = ({ label, children, parent, changed }) => (
  <Stack direction="row" alignItems="center" gap={1.5} sx={{ minHeight: 36 }}>
    <Typography
      typography="s2"
      color="text.secondary"
      sx={{ width: 112, flexShrink: 0 }}
    >
      {label}
    </Typography>
    <Box sx={{ flex: 1, minWidth: 0 }}>{children}</Box>
    <Typography
      typography="s3"
      color="text.disabled"
      noWrap
      sx={{ width: 120, flexShrink: 0, textAlign: "right" }}
    >
      {changed ? `was ${parent}` : parent}
    </Typography>
  </Stack>
);

ParamRow.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
  parent: PropTypes.node,
  changed: PropTypes.bool,
};

/** Compact select (`[{value, label}]`). */
export const SmallSelect = ({
  value,
  options,
  onChange,
  disabled,
  label,
  sx,
}) => (
  <Select
    size="small"
    value={value ?? ""}
    disabled={disabled}
    onChange={(e) => onChange(e.target.value)}
    inputProps={{ "aria-label": label }}
    sx={{ height: 30, typography: "s2", minWidth: 120, ...sx }}
    MenuProps={{ slotProps: { paper: { sx: { maxHeight: 320 } } } }}
  >
    {options.map((o) => (
      <MenuItem key={o.value} value={o.value} sx={{ typography: "s2" }}>
        {o.label}
      </MenuItem>
    ))}
  </Select>
);

SmallSelect.propTypes = {
  value: PropTypes.any,
  options: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  label: PropTypes.string,
  sx: PropTypes.object,
};

/** A number input sized for the param rows. */
export const NumberInput = ({
  value,
  onChange,
  disabled,
  label,
  step = 1,
  min = 0,
  max,
}) => (
  <TextField
    size="small"
    type="number"
    value={value ?? ""}
    disabled={disabled}
    onChange={(e) =>
      onChange(e.target.value === "" ? null : Number(e.target.value))
    }
    inputProps={{ "aria-label": label, step, min, max }}
    sx={{ width: 120, "& .MuiInputBase-input": { py: 0.75, typography: "s2" } }}
  />
);

NumberInput.propTypes = {
  value: PropTypes.number,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  label: PropTypes.string,
  step: PropTypes.number,
  min: PropTypes.number,
  max: PropTypes.number,
};
