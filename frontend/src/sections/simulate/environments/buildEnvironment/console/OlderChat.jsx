import PropTypes from "prop-types";
import { useEffect, useRef } from "react";
import { Button, Fade, Stack, Typography } from "@mui/material";
import CircularProgress from "@mui/material/CircularProgress";
import useMediaQuery from "@mui/material/useMediaQuery";

import { CONSOLE_COPY } from "../build.constants";

/**
 * Where the held messages or steps start. Reaching it loads the page before;
 * the button does the same, for a keyboard and after a failure.
 */
export default function OlderChat({ turn, listRef, auto }) {
  const copy = CONSOLE_COPY.older[turn.kind];
  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const markRef = useRef(null);
  const { hasOlder, loading, failed, oldest, onLoad } = turn;

  // Watched afresh after each page, once it is drawn and the place kept, so the
  // next loads only while the start is still in view.
  useEffect(() => {
    if (!auto || !hasOlder || loading || failed) return undefined;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return;
        observer.disconnect();
        onLoad();
      },
      { root: listRef.current },
    );
    observer.observe(markRef.current);
    return () => observer.disconnect();
  }, [auto, hasOlder, loading, failed, oldest, onLoad, listRef]);

  let text = null;
  if (!hasOlder) text = copy.end;
  else if (failed && !loading) text = copy.failed;
  return (
    // One height in every state, so nothing below moves when it changes.
    <Stack
      ref={markRef}
      direction="row"
      alignItems="center"
      justifyContent="center"
      spacing={1}
      sx={{ minHeight: 30 }}
    >
      <Typography
        role="status"
        sx={{
          typography: "s3",
          color: "text.tertiary",
          display: text ? "block" : "none",
        }}
      >
        <Fade
          in={Boolean(text)}
          key={text}
          timeout={reduceMotion ? 0 : undefined}
        >
          <span>{text}</span>
        </Fade>
      </Typography>
      {hasOlder && (
        // Kept enabled while it loads, so a keyboard reader keeps focus.
        <Button
          size="small"
          color="inherit"
          aria-disabled={loading || undefined}
          onClick={loading ? undefined : onLoad}
          startIcon={
            loading ? <CircularProgress size={12} color="inherit" /> : undefined
          }
          sx={{ color: "text.secondary" }}
        >
          {loading && copy.loading}
          {!loading && (failed ? CONSOLE_COPY.older.retry : copy.load)}
        </Button>
      )}
    </Stack>
  );
}
OlderChat.propTypes = {
  turn: PropTypes.shape({
    kind: PropTypes.oneOf(["messages", "events"]),
    oldest: PropTypes.number,
    hasOlder: PropTypes.bool,
    loading: PropTypes.bool,
    failed: PropTypes.bool,
    onLoad: PropTypes.func,
  }).isRequired,
  listRef: PropTypes.object.isRequired,
  // Whether reaching it loads: the reader scrolled here.
  auto: PropTypes.bool,
};
