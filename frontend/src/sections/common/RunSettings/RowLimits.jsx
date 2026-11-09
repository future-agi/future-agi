// W6-16 (A1): "Limits per row · stop at [ ] tokens · [ ] messages · $[ ] ·
// [ ] s" on a prompt or agent card. The values live on the card as
// `rowLimits` and reach the create body as `row_limits`
// (cardExtras/{prompt,agent}.js); the run stops each row at the first limit
// it crosses (rowLimitsModel.stopOf, the engine's own rule).
import React from "react";
import PropTypes from "prop-types";
import { Stack, TextField, Typography } from "@mui/material";
import { Controller, useWatch } from "react-hook-form";
import { ROW_LIMIT_FIELDS, rowLimitIssues } from "./rowLimitsModel";
import { heldFields, wizardSettingShown } from "src/launch/wizardSettings";

const toValue = (v) => (v === "" || v == null ? null : v);

export default function RowLimits({ control, fieldPrefix }) {
  const values = useWatch({ control, name: `${fieldPrefix}.rowLimits` });
  const issues = rowLimitIssues(values ?? {});
  // L6 launch switch (knob:limits:*): dev's runs take no per-row limits; a
  // launch build shows them only while one is set (wizardSettings.js).
  if (!wizardSettingShown("rowLimits", heldFields(values))) return null;
  return (
    <Stack gap={0.25} data-testid="row-limits">
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        <Typography typography="s2" color="text.secondary">
          Limits per row · stop at
        </Typography>
        {ROW_LIMIT_FIELDS.map(({ key, label, step, prefix }, i) => (
          <Stack key={key} direction="row" alignItems="center" gap={0.5}>
            {prefix ? (
              <Typography typography="s2" color="text.secondary">
                {label}
              </Typography>
            ) : null}
            <Controller
              control={control}
              name={`${fieldPrefix}.rowLimits.${key}`}
              render={({ field }) => (
                <TextField
                  size="small"
                  type="number"
                  value={field.value ?? ""}
                  onChange={(e) => field.onChange(toValue(e.target.value))}
                  inputProps={{
                    min: 0,
                    step,
                    "aria-label": `Limit per row: ${key === "cost_usd" ? "cost in dollars" : key === "timeout_s" ? "seconds" : key}`,
                    "data-testid": `row-limit-${key}`,
                  }}
                  sx={{ width: 96 }}
                />
              )}
            />
            {prefix ? null : (
              <Typography typography="s2" color="text.secondary">
                {label}
                {i < ROW_LIMIT_FIELDS.length - 1 ? " ·" : ""}
              </Typography>
            )}
            {prefix && i < ROW_LIMIT_FIELDS.length - 1 ? (
              <Typography typography="s2" color="text.secondary">
                ·
              </Typography>
            ) : null}
          </Stack>
        ))}
      </Stack>
      {issues.length ? (
        <Typography
          typography="s3"
          color="error.main"
          data-testid="row-limits-issues"
        >
          {issues.join(" · ")}
        </Typography>
      ) : null}
    </Stack>
  );
}

RowLimits.propTypes = {
  control: PropTypes.object.isRequired,
  fieldPrefix: PropTypes.string.isRequired,
};
