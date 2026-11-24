import PropTypes from "prop-types";
import { Button, Collapse, Stack, Typography } from "@mui/material";
import CircularProgress from "@mui/material/CircularProgress";
import useMediaQuery from "@mui/material/useMediaQuery";

import { CONSOLE_COPY } from "../build.constants";

/** Where the held messages or steps start: loads the ones before, a page at a time. */
export default function OlderChat({ turn }) {
  const copy = CONSOLE_COPY.older[turn.kind];
  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  if (!turn.hasOlder) {
    return (
      <Typography
        sx={{ typography: "s3", color: "text.tertiary", textAlign: "center" }}
      >
        {copy.end}
      </Typography>
    );
  }
  const label = turn.failed ? CONSOLE_COPY.older.retry : copy.load;
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
          {turn.failed ? copy.failed : ""}
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
        {turn.loading ? copy.loading : label}
      </Button>
    </Stack>
  );
}
OlderChat.propTypes = {
  turn: PropTypes.shape({
    kind: PropTypes.oneOf(["messages", "events"]),
    hasOlder: PropTypes.bool,
    loading: PropTypes.bool,
    failed: PropTypes.bool,
    onLoad: PropTypes.func,
  }).isRequired,
};
