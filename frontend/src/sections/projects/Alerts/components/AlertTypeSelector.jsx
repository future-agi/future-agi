import React from "react";
import {
  FormControl,
  FormControlLabel,
  Radio,
  RadioGroup,
  Stack,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import { alertTypes } from "../common";

export default function AlertTypeSelector({ onChange, selectedAlert }) {
  return (
    <FormControl component="fieldset">
      <RadioGroup
        value={selectedAlert}
        onChange={(e) => onChange(e.target.value)}
      >
        {alertTypes.map((group) => (
          <div key={group.category}>
            <Typography
              variant="s1"
              fontWeight={"fontWeightSemiBold"}
              color={"text.primary"}
              sx={{ mb: 2 }}
            >
              {group.category}
            </Typography>
            <Stack
              sx={{
                ml: 0.45,
                mt: 1.5,
                mb: 3,
              }}
            >
              {group.options.map((option) => (
                <FormControlLabel
                  key={option.value}
                  value={option.value}
                  control={<Radio />}
                  label={option.label}
                  data-alert-type-option={option.value}
                  sx={{
                    "& .MuiFormControlLabel-label": {
                      typography: "s1",
                      color: "text.primary",
                      fontWeight: "fontWeightRegular",
                      ml: 0.5,
                    },
                  }}
                />
              ))}
            </Stack>
          </div>
        ))}
      </RadioGroup>
    </FormControl>
  );
}

AlertTypeSelector.displayName = "AlertTypeSelector";

AlertTypeSelector.propTypes = {
  selectedAlert: PropTypes.string,
  onChange: PropTypes.func,
};
