import React, { useId } from "react";
import PropTypes from "prop-types";
import { Box, FormControlLabel, Switch, Typography } from "@mui/material";
import { useController } from "react-hook-form";
import { RECORD_HELP, RECORD_LABEL } from "./recordModel";

/**
 * K-2 (RT-09): `Record on the leaderboard`, on by default (form field
 * `record`; the create body sends `record: false` only when it is off).
 * Off: the run stays off the leaderboard, is reachable by its link, and is
 * deleted a day after it starts unless someone records it.
 */
export default function RecordToggle({ control, name = "record", disabled }) {
  const { field } = useController({ control, name });
  const helpId = useId();
  const on = field.value !== false;
  return (
    <Box data-testid="review-record" sx={{ maxWidth: 320 }}>
      <FormControlLabel
        sx={{ ml: 0, gap: 0.75 }}
        control={
          <Switch
            size="small"
            checked={on}
            disabled={disabled}
            onChange={(e) => field.onChange(e.target.checked)}
            inputProps={{
              "aria-describedby": helpId,
              "data-testid": "review-record-switch",
            }}
          />
        }
        label={
          <Typography typography="s2" color="text.primary">
            {RECORD_LABEL}
          </Typography>
        }
      />
      <Typography
        id={helpId}
        typography="s3"
        color="text.secondary"
        sx={{ mt: 0.5 }}
      >
        {RECORD_HELP}
      </Typography>
    </Box>
  );
}

RecordToggle.propTypes = {
  control: PropTypes.object.isRequired,
  name: PropTypes.string,
  disabled: PropTypes.bool,
};
