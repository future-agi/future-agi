import PropTypes from "prop-types";
import { Box, Checkbox, FormControlLabel, Typography } from "@mui/material";

// The "Turn on observability" choice, shared by the flows that build from code.
export default function ObservabilityChoice({ checked, onChange }) {
  return (
    <Box>
      <FormControlLabel
        label="Turn on observability"
        control={
          <Checkbox
            size="small"
            checked={checked}
            onChange={(e) => onChange(e.target.checked)}
            inputProps={{ "aria-describedby": "observability-helper" }}
          />
        }
      />
      <Typography id="observability-helper" sx={{ typography: "s3", color: "text.subtitle", ml: 4 }}>
        We can observe your agent only if its code already sends telemetry (OpenTelemetry or traceAI). If it doesn&apos;t, the Environment still builds, without observability.
      </Typography>
    </Box>
  );
}

ObservabilityChoice.propTypes = {
  checked: PropTypes.bool.isRequired,
  onChange: PropTypes.func.isRequired,
};
