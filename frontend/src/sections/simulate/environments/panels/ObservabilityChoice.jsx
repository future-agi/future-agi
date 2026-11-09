import PropTypes from "prop-types";
import {
  OBSERVE_KNOWN_ISSUES_URL,
  SANDBOX_OBSERVE_NOTICE,
} from "src/utils/platformObserveAvailability";
import {
  Box,
  Checkbox,
  FormControlLabel,
  Link,
  Typography,
} from "@mui/material";

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
      <Typography
        id="observability-helper"
        sx={{ typography: "s3", color: "text.subtitle", ml: 4 }}
      >
        {SANDBOX_OBSERVE_NOTICE}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.subtitle", ml: 4 }}>
        Simulation results remain in the simulation views.{" "}
        <Link
          href={OBSERVE_KNOWN_ISSUES_URL}
          target="_blank"
          rel="noopener noreferrer"
        >
          2.0.0 release notes: Known issues
        </Link>
      </Typography>
    </Box>
  );
}

ObservabilityChoice.propTypes = {
  checked: PropTypes.bool.isRequired,
  onChange: PropTypes.func.isRequired,
};
