import React, { useEffect, useRef } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";

import { LOG_STATE } from "../lib/useLogStream";
import { visibleLogEntries } from "../detail/logs/logPresentation";

const clock = (iso) => {
  const at = Date.parse(iso);
  if (!Number.isFinite(at)) return "";
  return new Date(at).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
};

function statusText({ state, endReason, error, emptyText }) {
  if (state === LOG_STATE.CONNECTING) return "Connecting…";
  if (state === LOG_STATE.STREAMING) return "Following";
  if (state === LOG_STATE.ENDED)
    return endReason === "complete" || endReason === "revision log closed"
      ? "End of log"
      : `Stream ended: ${endReason}`;
  if (state === LOG_STATE.ERROR)
    return error?.message || "The log stream stopped.";
  return emptyText;
}

/**
 * A terminal-style log panel. It sticks to the newest line unless the reader
 * scrolls up, and says whether it is still following.
 */
export default function LogViewer({
  stream,
  height = 360,
  emptyText = "No log lines yet.",
  testId = "log-viewer",
  showRevision = false,
  source,
}) {
  const { lines, state, endReason, error, restart } = stream;
  const { entries, hiddenCount } = visibleLogEntries(lines, source);
  const box = useRef(null);
  const pinned = useRef(true);

  useEffect(() => {
    const el = box.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [lines]);

  const following =
    state === LOG_STATE.STREAMING || state === LOG_STATE.CONNECTING;

  return (
    <Box
      data-testid={testId}
      data-state={state}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
        bgcolor: "background.paper",
      }}
    >
      <Box
        ref={box}
        role="log"
        aria-live="off"
        onScroll={(event) => {
          const el = event.currentTarget;
          pinned.current =
            el.scrollHeight - el.scrollTop - el.clientHeight < 24;
        }}
        sx={{
          height,
          overflow: "auto",
          px: 1.5,
          py: 1,
          fontFamily: "monospace",
          fontSize: 12.5,
          lineHeight: 1.65,
          color: "text.primary",
          bgcolor: "background.neutral",
        }}
      >
        {entries.length === 0 ? (
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ fontFamily: "inherit" }}
          >
            {state === LOG_STATE.CONNECTING
              ? "Waiting for the first line…"
              : source === "build" && hiddenCount > 0
                ? "No build output yet."
                : emptyText}
          </Typography>
        ) : (
          entries.map((entry) => (
            <Box
              key={entry.key}
              data-testid="log-line"
              sx={{ display: "flex", gap: 1.5, minWidth: 0 }}
            >
              <Box
                component="span"
                sx={{
                  color: "text.disabled",
                  flexShrink: 0,
                  userSelect: "none",
                }}
              >
                {clock(entry.at)}
              </Box>
              {showRevision && entry.revision ? (
                <Box
                  component="span"
                  sx={{ color: "text.disabled", flexShrink: 0 }}
                  title={`Revision ${entry.revision}`}
                >
                  r{entry.revision}
                </Box>
              ) : null}
              <Box
                component="span"
                sx={{
                  whiteSpace: "pre-wrap",
                  overflowWrap: "anywhere",
                  minWidth: 0,
                  ...(entry.note && {
                    color: "text.secondary",
                    fontStyle: "italic",
                  }),
                }}
              >
                {entry.line}
              </Box>
            </Box>
          ))
        )}
      </Box>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 1,
          px: 1.5,
          py: 0.75,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.75,
            minWidth: 0,
            flexWrap: "wrap",
          }}
        >
          <Box
            component="span"
            sx={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              flexShrink: 0,
              bgcolor:
                state === LOG_STATE.ERROR
                  ? "error.main"
                  : following
                    ? "success.main"
                    : "text.disabled",
            }}
          />
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid={`${testId}-status`}
            noWrap
          >
            {statusText({ state, endReason, error, emptyText: "Idle" })}
          </Typography>
          {hiddenCount > 0 && (
            <Typography
              typography="s2"
              color="text.secondary"
              data-testid={`${testId}-hidden-noise`}
              noWrap
            >
              · {hiddenCount} setup line{hiddenCount === 1 ? "" : "s"} hidden
            </Typography>
          )}
        </Box>
        {(state === LOG_STATE.ENDED || state === LOG_STATE.ERROR) &&
          restart && (
            <Button size="small" color="inherit" onClick={restart}>
              Reload
            </Button>
          )}
      </Box>
    </Box>
  );
}

LogViewer.propTypes = {
  stream: PropTypes.shape({
    lines: PropTypes.array.isRequired,
    state: PropTypes.string.isRequired,
    endReason: PropTypes.string,
    error: PropTypes.object,
    restart: PropTypes.func,
  }).isRequired,
  height: PropTypes.oneOfType([PropTypes.number, PropTypes.object]),
  emptyText: PropTypes.string,
  testId: PropTypes.string,
  /** Tag each line with the revision that printed it (runtime logs). */
  showRevision: PropTypes.bool,
  /** Build logs hide low-level Docker setup and deprecation noise. */
  source: PropTypes.oneOf(["runtime", "build"]),
};
