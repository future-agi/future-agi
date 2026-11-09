import React from "react";
import PropTypes from "prop-types";
import {
  FormControlLabel,
  Radio,
  RadioGroup,
  Stack,
  Typography,
} from "@mui/material";
import { RERUN_SCOPES } from "./common";
import {
  RERUN_EVERY_ROW_LINE,
  RERUN_OPERATION_BODY,
  rerunScopeSupported,
} from "./rerunScope";

/**
 * Choose which generated cells Save recomputes. Without the capability and
 * request contract, explain the all-rows behavior rather than offer a choice.
 */
const RerunScopeField = ({
  value,
  onChange,
  definition = RERUN_OPERATION_BODY,
}) =>
  rerunScopeSupported(definition) ? (
    <Stack gap={0.5} sx={{ mt: 1 }}>
      <Typography fontWeight={700} fontSize="12px" color="text.secondary">
        On save
      </Typography>
      <RadioGroup
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label="Rows to re-run on save"
      >
        {RERUN_SCOPES.map((s) => (
          <FormControlLabel
            key={s.value}
            value={s.value}
            control={<Radio size="small" />}
            label={<Typography typography="s2">{s.label}</Typography>}
            sx={{ my: -0.25 }}
          />
        ))}
      </RadioGroup>
    </Stack>
  ) : (
    <Stack gap={0.5} sx={{ mt: 1 }} data-testid="rerun-scope-line">
      <Typography fontWeight={700} fontSize="12px" color="text.secondary">
        On save
      </Typography>
      <Typography typography="s2" color="text.secondary">
        {RERUN_EVERY_ROW_LINE}
      </Typography>
    </Stack>
  );

RerunScopeField.propTypes = {
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
  definition: PropTypes.string,
};

export default RerunScopeField;
