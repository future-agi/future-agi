import React, { useId } from "react";
import { Box, FormControlLabel, Switch, Typography } from "@mui/material";
import { RECORD_HELP, RECORD_LABEL } from "./recordModel";

/**
 * K-2 (RT-09): `Record on the leaderboard`. In this release every run is
 * recorded (the server has no `record` field), so the switch is on and not
 * changeable, with the line that says so (W7).
 * It reads no form value: the create body never sends `record`.
 */
export default function RecordToggle() {
  const helpId = useId();
  return (
    <Box data-testid="review-record" sx={{ maxWidth: 320 }}>
      <FormControlLabel
        sx={{ ml: 0, gap: 0.75 }}
        control={
          <Switch
            size="small"
            checked
            disabled
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
