import React from "react";
import PropTypes from "prop-types";
import { Box, TextField, Tooltip } from "@mui/material";

import { PARAM_FIELDS, paramErrors, takes } from "./tryParams";

// Try it's sampling parameters (tryParams.js): empty is the model's default.
export default function TryParameters({ row, values, onChange }) {
  const errors = paramErrors(values, row);
  return (
    <Box
      role="group"
      aria-label="Parameters"
      sx={{ display: "flex", gap: 1.5, flexWrap: "wrap" }}
    >
      {PARAM_FIELDS.map((field) => {
        const allowed = takes(row, field);
        const input = (
          <TextField
            key={field.name}
            label={field.label}
            type="number"
            size="small"
            placeholder="Default"
            value={allowed ? values[field.name] ?? "" : ""}
            onChange={(e) =>
              onChange({ ...values, [field.name]: e.target.value })
            }
            disabled={!allowed}
            error={Boolean(errors[field.name])}
            helperText={errors[field.name]}
            InputLabelProps={{ shrink: true }}
            inputProps={{
              min: field.min,
              max: field.max(row),
              step: field.step,
              inputMode: field.integer ? "numeric" : "decimal",
            }}
            sx={{ width: 132 }}
          />
        );
        if (allowed) return input;
        return (
          <Tooltip
            key={field.name}
            title={`This model does not take ${field.label.toLowerCase()}.`}
            arrow
            describeChild
          >
            <span>{input}</span>
          </Tooltip>
        );
      })}
    </Box>
  );
}

TryParameters.propTypes = {
  row: PropTypes.object.isRequired,
  values: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
};
