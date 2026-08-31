import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { ALK_MONO } from "./alkTokens";

/**
 * The likeliest cause is transient: the harness is mid-build and answers nothing
 * until the step finishes, or its container is restarting. Say that, rather than
 * prescribing a local dev command to somebody on the platform.
 */
const HarnessUnreachable = ({ baseUrl, onRetry }) => (
  <Stack spacing={2} alignItems="center" justifyContent="center" sx={{ height: "100%", p: 4 }}>
    <Typography variant="h6">Can&apos;t reach the environment service</Typography>
    <Typography variant="body2" color="text.secondary" align="center">
      Nothing answered at{" "}
      <Box component="span" sx={{ fontFamily: ALK_MONO }}>
        {baseUrl}
      </Box>
      . It is usually busy with a long build step or restarting, and comes back on
      its own — try again in a moment. If this persists, ask whoever runs your
      deployment to check the harness container.
    </Typography>
    <Button variant="outlined" onClick={onRetry}>
      Try again
    </Button>
  </Stack>
);

HarnessUnreachable.propTypes = {
  baseUrl: PropTypes.string.isRequired,
  onRetry: PropTypes.func.isRequired,
};

export default HarnessUnreachable;
