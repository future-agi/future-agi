import React from "react";
import PropTypes from "prop-types";
import { Box, FormControlLabel, Switch, Typography } from "@mui/material";

import { ALLOW_NA_HINT } from "./allowNaModel";

/** W1-4: the switch and its hint (allowNaModel.js says when it shows). */
const AllowNaSwitch = ({ checked, onChange }) => (
  <Box data-testid="allow-na-switch">
    <FormControlLabel
      sx={{ ml: 0 }}
      control={
        <Switch
          size="small"
          checked={Boolean(checked)}
          onChange={(e) => onChange(e.target.checked)}
          inputProps={{ "aria-label": "Let the judge answer N/A" }}
        />
      }
      label={
        <Typography variant="body2" fontWeight={600} sx={{ ml: 0.75 }}>
          Let the judge answer N/A
        </Typography>
      }
    />
    <Typography
      variant="caption"
      color="text.secondary"
      sx={{ display: "block", mt: 0.25 }}
    >
      {ALLOW_NA_HINT}
    </Typography>
  </Box>
);

AllowNaSwitch.propTypes = {
  checked: PropTypes.bool,
  onChange: PropTypes.func.isRequired,
};

export default AllowNaSwitch;
