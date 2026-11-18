import PropTypes from "prop-types";
import { Box, Stack, TextField, Typography } from "@mui/material";
import UiOnlyFlag from "../../../components/UiOnlyFlag";
import { T12 } from "../../drawerStyles";

/**
 * What the environment should test: the agent's code says what it can do,
 * this says what to check. Kept in the form; nothing sends it yet.
 */
export default function BuildTestField({
  value,
  onChange,
  required = false,
  flagged = false,
}) {
  return (
    <Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ mb: 0.5 }}
      >
        <Typography component="label" sx={{ ...T12, fontWeight: 600 }}>
          What should this environment test?
          {required ? (
            <Box component="span" sx={{ color: "error.main", ml: 0.5 }}>
              *
            </Box>
          ) : (
            <Box
              component="span"
              sx={{ color: "text.disabled", fontWeight: 400, ml: 0.75 }}
            >
              optional
            </Box>
          )}
        </Typography>
        {flagged && <UiOnlyFlag feature="environmentFiles" />}
      </Stack>
      <TextField
        size="small"
        fullWidth
        multiline
        minRows={2}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="e.g. Refunds inside and outside the 30-day window, and when a manager must approve"
        inputProps={{ "aria-label": "What should this environment test?" }}
        sx={{ "& .MuiInputBase-input": { ...T12 } }}
      />
    </Box>
  );
}
BuildTestField.propTypes = {
  // A "UI only" pill, for a form whose build is wired but ignores this.
  flagged: PropTypes.bool,
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
  // Only where the build is UI only: a wired build never waits on it.
  required: PropTypes.bool,
};
