import React, { useEffect, useRef } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { describeOutcome, isExecLive } from "../lib/execOutput";
import { formatMs } from "../lib/format";
import {
  formatByteCap,
  formatSeconds,
  timeLimits,
  workdirLabel,
} from "../lib/runOptions";

const monoFont =
  '"JetBrains Mono", "SFMono-Regular", Menlo, Consolas, monospace';

/** A quiet line under the output, for what the outcome alone does not say. */
function Note({ children, testId }) {
  return (
    <Typography
      typography="s2"
      color="text.secondary"
      data-testid={testId}
      sx={{ px: 1.5, py: 0.75, borderTop: "1px solid", borderColor: "divider" }}
    >
      {children}
    </Typography>
  );
}

Note.propTypes = { children: PropTypes.node, testId: PropTypes.string };

/**
 * The streamed output of the last command: its header (command, folder,
 * outcome and duration), stdout and stderr as they arrive, and a note when the
 * command hit its time limit or printed past the output cap.
 */
export default function RunOutput({ state, ranWith, caps }) {
  const end = useRef(null);
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "nearest" });
  }, [state.chunks.length]);
  const outcome = state.outcome;
  const failed =
    state.status === "failed" ||
    (outcome &&
      (outcome.exitCode !== 0 || outcome.cancelled || outcome.timedOut));
  const limit = ranWith?.timeoutSeconds ?? timeLimits(caps)?.default ?? null;
  const max = timeLimits(caps)?.max ?? null;
  const cap = formatByteCap(caps?.exec?.maxOutputBytes);
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
        minWidth: 0,
      }}
    >
      <Box
        sx={{
          px: 1.5,
          py: 0.75,
          display: "flex",
          justifyContent: "space-between",
          gap: 1,
          bgcolor: "background.neutral",
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography
          typography="s2"
          color="text.secondary"
          title={`${workdirLabel(ranWith?.cwd)} $ ${state.command}`}
          data-testid="run-header"
          sx={{
            fontFamily: monoFont,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            minWidth: 0,
          }}
        >
          {ranWith?.cwd ? `${workdirLabel(ranWith.cwd)} ` : ""}$ {state.command}
        </Typography>
        <Typography
          typography="s2"
          data-testid="run-status"
          data-status={state.status}
          sx={{
            flexShrink: 0,
            color: failed
              ? "error.main"
              : state.status === "done"
                ? "success.main"
                : "text.secondary",
          }}
        >
          {describeOutcome(state)}
          {outcome && typeof outcome.durationMs === "number"
            ? ` · ${formatMs(outcome.durationMs)}`
            : ""}
        </Typography>
      </Box>
      <Box
        component="pre"
        data-testid="run-output"
        aria-live="polite"
        sx={{
          m: 0,
          p: 1.5,
          minHeight: 160,
          maxHeight: 420,
          overflow: "auto",
          fontFamily: monoFont,
          fontSize: 12.5,
          lineHeight: 1.6,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          bgcolor: "background.paper",
          color: "text.primary",
        }}
      >
        {state.chunks.length === 0 && isExecLive(state) && (
          <Box component="span" sx={{ color: "text.disabled" }}>
            Waiting for output…
          </Box>
        )}
        {state.chunks.length === 0 && state.status === "done" && (
          <Box component="span" sx={{ color: "text.disabled" }}>
            No output.
          </Box>
        )}
        {state.chunks.map((chunk, index) => (
          <Box
            component="span"
            key={index}
            data-stream={chunk.stream}
            sx={chunk.stream === "stderr" ? { color: "error.main" } : undefined}
          >
            {chunk.text}
          </Box>
        ))}
        <span ref={end} />
      </Box>
      {outcome?.timedOut && limit !== null && (
        <Note testId="run-timeout-note">
          {`Stop requested after ${formatSeconds(limit)}.`}
          {max !== null && limit < max
            ? ` Choose a longer limit in Options (up to ${formatSeconds(max)}) and run it again.`
            : ""}
        </Note>
      )}
      {outcome?.truncated && (
        <Note testId="run-truncated-note">
          {cap
            ? `Output past the ${cap} limit was not kept. Reaching the output limit does not stop the command.`
            : "Output past the limit was not kept. Reaching the output limit does not stop the command."}
        </Note>
      )}
    </Box>
  );
}

RunOutput.propTypes = {
  state: PropTypes.object.isRequired,
  ranWith: PropTypes.shape({
    cwd: PropTypes.string,
    timeoutSeconds: PropTypes.number,
  }),
  caps: PropTypes.object,
};
