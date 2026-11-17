/** The pieces the Participants tab is drawn from: times, folding, payloads, answers and turns. */
import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Chip, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import CustomJsonViewer from "src/components/custom-json-viewer/CustomJsonViewer";
import ChatMarkdown from "src/sections/simulate/environments/buildEnvironment/console/ChatMarkdown";
import { MONO_FONT as MONO } from "src/sections/simulate/environments/workspace/contract/contract.constants";
import { formatClock } from "src/components/VoiceDetailDrawerV2/transcriptUtils";
import {
  CALL_WORKERS_COPY as C,
  MOTION_MS,
  NO_MOTION_SX,
} from "./callTraces.constants";
import { asMarkdown, isLong, seconds } from "./utils/callWorkersUtils";

// Past this a heading may hit its three-line clamp, so the whole of it shows on hover.
const LONG_TITLE = 80;
// Time into the call ("0:05"), as the transcript shows it, with the exact clock time on hover.
export function Stamp({ at, start }) {
  const moment = seconds(at);
  if (moment === null) return null;
  const from = seconds(start);
  const label =
    from === null
      ? new Date(moment * 1000).toISOString().slice(11, 19)
      : formatClock(Math.max(0, moment - from));
  return (
    <CustomTooltip
      show
      size="small"
      arrow
      title={`${new Date(moment * 1000).toISOString().replace("T", " ").slice(0, 23)} UTC`}
    >
      <Typography
        component="span"
        sx={{
          typography: "s3",
          fontFamily: MONO,
          color: "text.subtitle",
          flexShrink: 0,
        }}
      >
        {label}
      </Typography>
    </CustomTooltip>
  );
}
Stamp.propTypes = { at: PropTypes.string, start: PropTypes.string };

// Height animates in CSS alone, by the size of a grid row, so opening costs no measuring or per-frame JS.
// Content mounts on first open and stays, so closing keeps its state; `visibility` takes it out of tab order.
export function Reveal({ open, collapsedSize = "0px", children, sx }) {
  const seen = useRef(open);
  if (open) seen.current = true;
  const peeks = collapsedSize !== "0px";
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateRows: `minmax(${collapsedSize}, ${open ? "1fr" : "0fr"})`,
        transition: `grid-template-rows ${MOTION_MS}ms ease`,
        ...NO_MOTION_SX,
        ...sx,
      }}
    >
      <Box
        sx={{
          minWidth: 0,
          minHeight: 0,
          overflow: "hidden",
          ...(!peeks && {
            visibility: open ? "visible" : "hidden",
            transition: `visibility 0s linear ${open ? 0 : MOTION_MS}ms`,
            ...NO_MOTION_SX,
          }),
        }}
      >
        {peeks || seen.current ? children : null}
      </Box>
    </Box>
  );
}
Reveal.propTypes = {
  open: PropTypes.bool,
  collapsedSize: PropTypes.string,
  children: PropTypes.node,
  sx: PropTypes.object,
};

function Line({ title, at, start, color, children }) {
  return (
    <Stack direction="row" spacing={1} alignItems="baseline">
      {/* A long task stays a heading: three lines, the whole of it on hover. */}
      <CustomTooltip
        show={title.length > LONG_TITLE}
        size="small"
        arrow
        title={title}
      >
        <Typography
          sx={{
            typography: "s1",
            fontWeight: 600,
            color,
            flex: 1,
            minWidth: 0,
            wordBreak: "break-word",
            display: "-webkit-box",
            WebkitLineClamp: 3,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {title}
        </Typography>
      </CustomTooltip>
      {children}
      {/* Last in the row, so every time ends at the same right edge. */}
      <Stamp at={at} start={start} />
    </Stack>
  );
}
Line.propTypes = {
  title: PropTypes.string.isRequired,
  at: PropTypes.string,
  start: PropTypes.string,
  color: PropTypes.string,
  children: PropTypes.node,
};

export function Payload({ label, value, omitted }) {
  return (
    <Box>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        {label}
      </Typography>
      {value === null || value === undefined ? (
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {omitted ? C.payloadOmitted : C.noPayload}
        </Typography>
      ) : typeof value === "object" ? (
        <CustomJsonViewer object={value} />
      ) : (
        <Typography
          sx={{
            typography: "s3",
            fontFamily: MONO,
            color: "text.secondary",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {String(value)}
        </Typography>
      )}
    </Box>
  );
}
Payload.propTypes = {
  label: PropTypes.string,
  value: PropTypes.any,
  omitted: PropTypes.bool,
};

export function Answer({ shown, empty }) {
  const [whole, setWhole] = useState(false);
  if (!shown)
    return (
      <Typography sx={{ typography: "s1", color: "text.secondary" }}>
        {empty}
      </Typography>
    );
  const text = asMarkdown(shown);
  const long = isLong(text);
  const body = (
    <Box
      data-testid="answer"
      data-clamped={long && !whole}
      sx={{
        typography: "s1",
        wordBreak: "break-word",
        // Headings inside an answer read as bold lines, not page titles.
        "& h1, & h2, & h3, & h4, & h5, & h6": {
          typography: "s1",
          fontWeight: 600,
          my: 0.25,
        },
      }}
    >
      {/* Worker text isn't ours: ChatMarkdown shows raw HTML as text and never loads an image. */}
      <ChatMarkdown text={text} dim sx={{ typography: "s1" }} />
    </Box>
  );
  return (
    <Box>
      {long ? (
        <Reveal open={whole} collapsedSize="9em" sx={{ typography: "s1" }}>
          {body}
        </Reveal>
      ) : (
        body
      )}
      {long ? (
        <Typography
          component="button"
          onClick={() => setWhole((one) => !one)}
          sx={{
            typography: "s3",
            color: "primary.main",
            border: 0,
            bgcolor: "transparent",
            cursor: "pointer",
            p: 0,
          }}
        >
          {whole ? C.showLess : C.showMore}
        </Typography>
      ) : null}
    </Box>
  );
}
Answer.propTypes = {
  shown: PropTypes.shape({ format: PropTypes.string, text: PropTypes.string }),
  empty: PropTypes.string,
};

// A turn of the caller or the coordinator, styled as the transcript styles it.
export function Turn({ title, turn, agent, start, empty }) {
  return (
    <Box
      sx={{
        borderLeft: "2px solid",
        borderColor: (t) =>
          agent ? t.palette.primary.main : t.palette.text.disabled,
        bgcolor: (t) =>
          agent
            ? alpha(
                t.palette.primary.main,
                t.palette.mode === "dark" ? 0.06 : 0.04,
              )
            : "background.neutral",
        px: 1.75,
        py: 1,
      }}
    >
      <Line title={title} at={turn?.at} start={start} />
      <Answer shown={turn?.shown} empty={empty} />
    </Box>
  );
}
Turn.propTypes = {
  title: PropTypes.string,
  turn: PropTypes.object,
  agent: PropTypes.bool,
  start: PropTypes.string,
  empty: PropTypes.string,
};

// Where the record does not say which hand-off a step belongs to: never guessed from timing.
export function UnknownParent({ sx }) {
  return (
    <Chip
      size="small"
      variant="outlined"
      label={C.parentUnknown}
      data-testid="parent-unknown"
      sx={{ flexShrink: 0, ...sx }}
    />
  );
}
UnknownParent.propTypes = { sx: PropTypes.object };
