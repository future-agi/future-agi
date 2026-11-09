// W1-7: how errored tries (a rate limit, a timeout, a tool error) count in
// the run's scores: as a failed 0 (the default) or left out of n and every
// mean. The create body sends `error_policy`; the run engine builds each
// errored try's cells by it (scoreCell.scoreCellOf), so every score reads it.
import React from "react";
import PropTypes from "prop-types";
import { Box, MenuItem, TextField, Typography } from "@mui/material";
import { useController } from "react-hook-form";

const ERROR_POLICY_OPTIONS = [
  {
    value: "zero",
    label: "Count as 0",
    // v2.1 (knob:error-policy): exactly what the engine applies: an eval
    // that skips the row (no expected value) stays Skipped.
    helper:
      "An errored try stays in n as a failed try: a 0 in each eval that scores it.",
  },
  {
    value: "exclude",
    label: "Leave out",
    helper: "An errored try gets no score: it leaves n and every mean.",
  },
];

const ErrorPolicySetting = ({ control, disabled = false }) => {
  const { field } = useController({ control, name: "errorPolicy" });
  const value = field.value === "exclude" ? "exclude" : "zero";
  const option = ERROR_POLICY_OPTIONS.find((o) => o.value === value);
  return (
    <Box>
      <TextField
        select
        size="small"
        label="Errored tries in scores"
        value={value}
        disabled={disabled}
        onChange={(e) => field.onChange(e.target.value)}
        sx={{ width: 200, bgcolor: "background.paper" }}
        data-testid="setting-errorPolicy"
      >
        {ERROR_POLICY_OPTIONS.map((o) => (
          <MenuItem key={o.value} value={o.value}>
            {o.label}
          </MenuItem>
        ))}
      </TextField>
      <Typography
        typography="s3"
        color="text.secondary"
        sx={{ mt: 0.5, maxWidth: 300 }}
      >
        {option.helper}
      </Typography>
    </Box>
  );
};

ErrorPolicySetting.propTypes = {
  control: PropTypes.object,
  disabled: PropTypes.bool,
};

export default ErrorPolicySetting;
