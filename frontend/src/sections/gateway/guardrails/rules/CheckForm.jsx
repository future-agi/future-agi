import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Divider from "@mui/material/Divider";
import FormControlLabel from "@mui/material/FormControlLabel";
import MenuItem from "@mui/material/MenuItem";
import Slider from "@mui/material/Slider";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import {
  RUNS_AS_LOG,
  actionChoices,
  actionHelp,
  actionName,
  runsAsLog,
} from "../catalog/actions";
import CheckField from "./CheckFields";
import { scoresOneOnMatch } from "../catalog";
import { GatewayStatusChip } from "../../components/frame";
import {
  GUARDRAILS_OFF_REASON,
  NOT_ENFORCED_STATUS,
} from "../../utils/guardrailPipeline";

/**
 * A check's settings: on or off, what happens on a match, the score that
 * counts as a match, and the check's own fields. Controlled: `values` is
 * formValuesFor()'s shape and `onChange` receives the next one. The Action
 * select offers the actions the gateway takes for this check (`checkName`,
 * the name the gateway receives): mask only where the check can rewrite the
 * request. A stored action the check does not take stays listed, disabled,
 * so a save that changes nothing keeps it. `enforced` false (the gateway
 * runs no check): the check reads "Not enforced" alongside its saved on
 * switch. All stored settings remain editable (850(a)).
 */
export default function CheckForm({
  meta,
  checkName = meta?.name,
  values,
  onChange,
  disabled,
  enforced = true,
  check = null,
}) {
  const set = (patch) => onChange({ ...values, ...patch });
  const setField = (key, value) =>
    onChange({ ...values, fields: { ...values.fields, [key]: value } });
  const fields = meta?.fields || [];
  const choices = actionChoices(checkName, values.action);
  const storedOnly = runsAsLog(checkName, values.action);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <FormControlLabel
        sx={{ ml: 0, gap: 1 }}
        control={
          <Switch
            size="small"
            checked={values.enabled}
            disabled={disabled}
            onChange={(event) => set({ enabled: event.target.checked })}
          />
        }
        label={
          <Typography typography="s1" color="text.primary">
            {/* NEW: Saved on / Saved off describe the saved flag, not enforcement. */}
            {enforced
              ? values.enabled
                ? "On"
                : "Off"
              : values.enabled
                ? "On in draft"
                : "Off in draft"}
          </Typography>
        }
      />
      {!enforced && (
        <Box
          component="span"
          title={GUARDRAILS_OFF_REASON}
          sx={{ display: "inline-flex", alignSelf: "flex-start" }}
        >
          <GatewayStatusChip
            tone="neutral"
            label={NOT_ENFORCED_STATUS}
            showIcon={false}
          />
        </Box>
      )}

      <TextField
        select
        size="small"
        fullWidth
        label="Action"
        value={values.action}
        disabled={disabled}
        onChange={(event) => set({ action: event.target.value })}
        helperText={
          storedOnly ? RUNS_AS_LOG : actionHelp(values.action, { enforced })
        }
        SelectProps={{ renderValue: (action) => actionName(action) }}
      >
        {choices.map((choice) => (
          <MenuItem
            key={choice.value}
            value={choice.value}
            disabled={choice.disabled}
          >
            <Box>
              <Typography typography="s1">{choice.label}</Typography>
              {!choice.disabled && (
                <Typography typography="s3" color="text.secondary">
                  {actionHelp(choice.value, { enforced })}
                </Typography>
              )}
            </Box>
          </MenuItem>
        ))}
      </TextField>

      {!meta?.hideConfidence && !scoresOneOnMatch(checkName, check) && (
        <Box>
          <Typography
            id="guardrail-threshold-label"
            typography="s2"
            color="text.secondary"
          >
            Match threshold: {Number(values.threshold).toFixed(2)}
          </Typography>
          <Slider
            size="small"
            aria-labelledby="guardrail-threshold-label"
            value={Number(values.threshold)}
            min={0}
            max={1}
            step={0.05}
            disabled={disabled}
            onChange={(_, threshold) => set({ threshold })}
            valueLabelDisplay="auto"
          />
          <Typography typography="s3" color="text.secondary">
            A match scoring at or below this is ignored.
          </Typography>
        </Box>
      )}

      {fields.length > 0 && (
        <>
          <Divider />
          <Typography typography="s1" fontWeight="fontWeightMedium">
            Settings
          </Typography>
          {fields.map((field) => (
            <CheckField
              key={field.key}
              field={field}
              value={values.fields?.[field.key]}
              stored={Boolean(values.storedSecrets?.[field.key])}
              disabled={disabled}
              onChange={(value) => setField(field.key, value)}
            />
          ))}
        </>
      )}
    </Box>
  );
}

CheckForm.propTypes = {
  meta: PropTypes.object,
  checkName: PropTypes.string,
  values: PropTypes.shape({
    enabled: PropTypes.bool,
    action: PropTypes.string,
    threshold: PropTypes.number,
    fields: PropTypes.object,
    storedSecrets: PropTypes.object,
  }).isRequired,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  // False while the gateway runs no check (guardrails off in its config).
  enforced: PropTypes.bool,
  // The stored check, when there is one: whether a threshold applies can
  // depend on its settings (scoresOneOnMatch).
  check: PropTypes.object,
};
