import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

export function buildWidgetGroupBy(breakdowns, limit) {
  if (
    !Number.isInteger(limit) || limit < 1 || limit > 10 ||
    breakdowns.length !== 1
  ) return undefined;
  const item = breakdowns[0];
  if (item.source && item.source !== "traces") return undefined;
  const type = item.type === "custom_attribute"
    ? "attribute"
    : ["system", "system_metric"].includes(item.type) && item.id === "tag"
      ? "tag"
      : null;
  return type && item.id ? { type, key: item.id, limit } : undefined;
}

// Selection stays in the editor's shared, paginated attribute/filter catalog.
// This control only adds the bounded grouping policy to that same selection.
export default function WidgetGroupByControl({
  breakdowns,
  limit,
  onChange,
  onSelect,
  selectAriaLabel,
  supported = true,
}) {
  const available = supported && Boolean(buildWidgetGroupBy(breakdowns, 5));
  return (
    <Box>
      <Button onClick={onSelect} aria-label={selectAriaLabel} size="small" color="inherit">Group by</Button>
      <Stack spacing={1}>
        <FormControlLabel
          control={
            <Checkbox
              size="small"
              checked={available && limit !== null}
              disabled={!available}
              onChange={(_, checked) => onChange(checked ? 5 : null)}
            />
          }
          label="Top values + other"
        />
        {available && limit !== null && (
          <TextField
            size="small"
            type="number"
            label="Top values"
            value={limit}
            inputProps={{ min: 1, max: 10, step: 1 }}
            onChange={(event) => {
              const value = Number(event.target.value);
              if (Number.isInteger(value) && value >= 1 && value <= 10) {
                onChange(value);
              }
            }}
          />
        )}
        <Typography variant="caption" color="text.secondary">
          {available
            ? "Top values by observation count across the selected time window; remaining values form other."
            : "Choose one span attribute or Tags with trace system or attribute metrics to show top values + other."}
        </Typography>
      </Stack>
    </Box>
  );
}

WidgetGroupByControl.propTypes = {
  breakdowns: PropTypes.array.isRequired,
  limit: PropTypes.number,
  onChange: PropTypes.func.isRequired,
  onSelect: PropTypes.func.isRequired,
  selectAriaLabel: PropTypes.string,
  supported: PropTypes.bool,
};
