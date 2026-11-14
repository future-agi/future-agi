import PropTypes from "prop-types";
import { Stack, TextField, Typography } from "@mui/material";
import { MAX_PARALLELISM, PARALLELISM_COPY } from "../parallelism.constants";

export default function ParallelismField({ value, input, onChange, enabled = true, admitted }) {
  const shown = enabled ? value : 1;
  return (
    <Stack spacing={0.5}>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems={{ sm: "center" }}>
        <TextField
          size="small"
          type="number"
          label={PARALLELISM_COPY.label}
          value={enabled ? input ?? String(value) : 1}
          onChange={(event) => onChange(event.target.value)}
          disabled={!enabled}
          inputProps={{ min: 1, max: MAX_PARALLELISM }}
          helperText={enabled && String(input ?? "") !== String(value) ? `Will use ${value}` : undefined}
          sx={{ width: 140, flexShrink: 0 }}
        />
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {enabled ? PARALLELISM_COPY.enabledHint : PARALLELISM_COPY.disabledHint}
        </Typography>
      </Stack>
      {enabled && typeof admitted === "number" && (
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {PARALLELISM_COPY.admitted(admitted, shown)}
        </Typography>
      )}
    </Stack>
  );
}

ParallelismField.propTypes = {
  value: PropTypes.number,
  input: PropTypes.string,
  onChange: PropTypes.func,
  enabled: PropTypes.bool,
  admitted: PropTypes.number,
};
