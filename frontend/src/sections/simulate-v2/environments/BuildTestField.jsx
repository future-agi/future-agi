import PropTypes from "prop-types";
import { TextField, Typography, Box } from "@mui/material";

/*
  What the environment should test — the first thing every build from an
  agent asks (builder.md "Build from my agent": describe the behavior to
  test). The agent's code says what it can do; this says what to check.
  Shown in the design review, before anything is built. Optional when the
  builder can read the agent (hosted platform, code, a prompt built here) —
  see buildTestRule.js.
*/
export default function BuildTestField({
  value,
  onChange,
  optional = false,
  error = false,
}) {
  return (
    <Box>
      <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.5 }}>
        What should this environment test?
        {/* Required: the build waits for it (the same mark as a required key). */}
        {!optional && (
          <Box
            component="span"
            aria-label="required"
            sx={{ color: "error.main", ml: 0.25 }}
          >
            *
          </Box>
        )}
        {optional && (
          <Box
            component="span"
            sx={{ color: "text.tertiary", fontWeight: 400, ml: 0.75 }}
          >
            optional
          </Box>
        )}
      </Typography>
      <TextField
        size="small"
        fullWidth
        multiline
        minRows={2}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="e.g. Refunds inside and outside the 30-day window, and when a manager must approve"
        error={error}
        helperText={
          error
            ? "Say what it should test — a sentence is enough."
            : optional
              ? "Leave empty to let the builder propose tests from the agent."
              : undefined
        }
        InputProps={{ sx: { typography: "s2" } }}
      />
    </Box>
  );
}

BuildTestField.propTypes = {
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
  optional: PropTypes.bool,
  /* Build was pressed with it empty. */
  error: PropTypes.bool,
};
