import PropTypes from "prop-types";
import { Button, Collapse, Stack, Typography } from "@mui/material";
import CircularProgress from "@mui/material/CircularProgress";
import useMediaQuery from "@mui/material/useMediaQuery";

import { CONSOLE_COPY } from "../build.constants";

/** Where the held steps start: loads the ones before them, a page at a time. */
export default function OlderSteps({ turn }) {
  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  if (!turn.hasOlder) {
    return (
      <Typography
        sx={{ typography: "s3", color: "text.tertiary", textAlign: "center" }}
      >
        {CONSOLE_COPY.olderEnd}
      </Typography>
    );
  }
  const label = turn.failed ? CONSOLE_COPY.retryOlder : CONSOLE_COPY.loadOlder;
  return (
    <Stack alignItems="center">
      <Collapse
        in={Boolean(turn.failed)}
        timeout={reduceMotion ? 0 : undefined}
      >
        <Typography
          role="status"
          sx={{ typography: "s3", color: "text.secondary", pb: 0.5 }}
        >
          {turn.failed ? CONSOLE_COPY.olderFailed : ""}
        </Typography>
      </Collapse>
      {/* Kept enabled while it loads, so a keyboard reader keeps focus. */}
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        aria-disabled={turn.loading || undefined}
        onClick={turn.loading ? undefined : turn.onLoad}
        startIcon={
          turn.loading ? (
            <CircularProgress size={12} color="inherit" />
          ) : undefined
        }
        sx={{ color: "text.secondary" }}
      >
        {turn.loading ? CONSOLE_COPY.loadingOlder : label}
      </Button>
    </Stack>
  );
}
OlderSteps.propTypes = {
  turn: PropTypes.shape({
    hasOlder: PropTypes.bool,
    loading: PropTypes.bool,
    failed: PropTypes.bool,
    onLoad: PropTypes.func,
  }).isRequired,
};
