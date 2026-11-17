import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";
import { alpha } from "@mui/material/styles";

const IMAGE = /\.(png|jpe?g|gif|webp)$/i;

const CODE_SX = {
  m: 0,
  py: 1,
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
  fontSize: 12.5,
};

const NUMBER_SX = {
  width: 44,
  flexShrink: 0,
  textAlign: "right",
  pr: 1.5,
  color: "text.disabled",
  userSelect: "none",
};

// A line as written, on one line; the pane scrolls sideways for a line wider
// than it.
const LINE_SX = { whiteSpace: "pre", pr: 2 };

// A marked line wraps inside the box instead, so the whole flagged call
// shows, with no part of it past the box's right edge, at any width.
const MARKED_LINE_SX = {
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
  minWidth: 0,
  pr: 2,
};

// A marked line and its notes are never cut, so they are drawn above the
// right-edge fade, which is only for the lines that run past the edge.
const ABOVE_FADE = { position: "relative", zIndex: 1 };

// A marked line's fill: the warning tint, stronger on a light page.
const markedFill = (t) =>
  alpha(t.palette.warning.main, t.palette.mode === "dark" ? 0.16 : 0.3);

// The viewer's frame: its border and fill, and the box the fade is placed
// in. Its own stacking context keeps what is drawn above the fade under
// anything the page draws above the viewer. While the pane has keyboard
// focus, the frame draws the pane's ring (its ::after, inside its edge,
// where the frame does not clip it) above the fade and the flagged rows, so
// no side of the ring is lost under the fade; it takes no pointer.
const FRAME_SX = {
  position: "relative",
  isolation: "isolate",
  border: "1px solid",
  borderColor: "divider",
  borderRadius: 1,
  overflow: "hidden",
  bgcolor: "background.neutral",
  "&:has(> .hub-file-pane:focus-visible)::after": {
    content: '""',
    position: "absolute",
    inset: 0,
    zIndex: 2,
    pointerEvents: "none",
    borderRadius: "inherit",
    outline: (theme) =>
      `2px solid ${theme.palette.border?.focus || theme.palette.primary.main}`,
    outlineOffset: -2,
  },
};

// The pane the lines scroll in, sideways and down. While it scrolls either
// way it takes keyboard focus; its ring is the frame's (FRAME_SX), as one
// of its own would be drawn under the fade.
const PANE_SX = {
  overflow: "auto",
  maxHeight: 560,
  "&:focus-visible": { outline: "none" },
};

// The fade at the pane's right edge: clear at its left, the pane's own fill
// at the edge, so a line that runs on past the edge visibly does. It takes
// no pointer, so a click or a selection under it reaches the line.
const FADE_SX = {
  position: "absolute",
  top: 0,
  width: 32,
  pointerEvents: "none",
  backgroundImage: (t) => {
    const fill = t.palette.background.neutral;
    return `linear-gradient(to right, ${alpha(fill, 0)}, ${fill})`;
  },
};

const FITS = { wide: false, tall: false, cut: false, right: 0, bottom: 0 };

// The pane's edges, measured: whether its lines run past its right edge
// (`wide`), whether they run past its bottom edge (`tall`), whether any line
// is cut at the right edge now, that is, not scrolled to its end (`cut`),
// and the room the pane's own scroll bars take at its right and bottom,
// which the fade leaves clear. One pixel is allowed for fractional sizes.
function edgeOf(node) {
  const beyond = node.scrollWidth - node.clientWidth;
  return {
    wide: beyond > 1,
    tall: node.scrollHeight - node.clientHeight > 1,
    cut: beyond > 1 && node.scrollLeft < beyond - 1,
    right: Math.max(0, node.offsetWidth - node.clientWidth),
    bottom: Math.max(0, node.offsetHeight - node.clientHeight),
  };
}

const sameEdge = (a, b) =>
  a.wide === b.wide &&
  a.tall === b.tall &&
  a.cut === b.cut &&
  a.right === b.right &&
  a.bottom === b.bottom;

// A plain-text file viewer with line numbers. It never renders HTML (SPEC
// §5.2): the text goes into the DOM as text. Lines with a finding or a
// reviewer note are marked, wrap inside the viewer, and the marks are listed
// under the line. Every other line stays on one line as written: the pane
// scrolls sideways for it, and while a line is cut at the pane's right edge
// a fade there says so, until the pane is scrolled to its end.
export default function FileViewer({
  path,
  text,
  isLoading,
  error,
  marks = [],
  imageUrl,
}) {
  if (!path) {
    return (
      <Typography typography="s2" color="text.secondary" sx={{ p: 2 }}>
        Choose a file to read it.
      </Typography>
    );
  }
  if (IMAGE.test(path)) {
    return imageUrl ? (
      <Box sx={{ p: 2 }}>
        <Box
          component="img"
          src={imageUrl}
          alt={path}
          sx={{ maxWidth: "100%" }}
        />
      </Box>
    ) : (
      <Typography typography="s2" color="text.secondary" sx={{ p: 2 }}>
        An image file. It is not shown here.
      </Typography>
    );
  }
  if (isLoading) return <Skeleton variant="rounded" height={240} />;
  if (error) {
    return (
      <Typography typography="s2" color="error.main" sx={{ p: 2 }}>
        {error.message || "This file did not load."}
      </Typography>
    );
  }
  return <CodePane path={path} text={text} marks={marks} />;
}

// The file's lines in their pane, with the fade at its right edge while a
// line is cut there. The edges are measured after each render that can
// change the lines, on each scroll, and whenever the pane's box changes
// size. While the pane scrolls, sideways or down, it is a named region that
// takes keyboard focus, so it can be scrolled without a pointer.
function CodePane({ path, text, marks }) {
  const pane = useRef(null);
  const [edge, setEdge] = useState(FITS);
  const measure = useCallback(() => {
    const node = pane.current;
    if (!node) return;
    const next = edgeOf(node);
    setEdge((now) => (sameEdge(now, next) ? now : next));
  }, []);
  useLayoutEffect(() => measure(), [measure, text, marks]);
  useEffect(() => {
    const node = pane.current;
    if (!node || typeof ResizeObserver !== "function") return undefined;
    const watch = new ResizeObserver(measure);
    watch.observe(node);
    return () => watch.disconnect();
  }, [measure]);

  const byLine = {};
  for (const mark of marks) {
    const line = Number(mark.line) || 0;
    (byLine[line] = byLine[line] || []).push(mark);
  }
  const lines = String(text ?? "")
    .replace(/\n$/, "")
    .split("\n");
  const reachable = edge.wide || edge.tall;
  return (
    <Box data-testid="hub-file-viewer" data-path={path} sx={FRAME_SX}>
      <Box
        ref={pane}
        className="hub-file-pane"
        data-testid="hub-file-pane"
        onScroll={measure}
        tabIndex={reachable ? 0 : undefined}
        role={reachable ? "region" : undefined}
        aria-label={reachable ? `Contents of ${path}` : undefined}
        sx={PANE_SX}
      >
        {(byLine[0] || []).map((mark, i) => (
          <MarkRow key={`0-${i}`} mark={mark} />
        ))}
        <Box component="pre" sx={CODE_SX}>
          {lines.map((line, index) => {
            const number = index + 1;
            const lineMarks = byLine[number] || [];
            const marked = lineMarks.length > 0;
            return (
              <React.Fragment key={number}>
                <Box
                  sx={{
                    display: "flex",
                    bgcolor: marked ? markedFill : "transparent",
                    ...(marked ? ABOVE_FADE : null),
                  }}
                >
                  <Box component="span" sx={NUMBER_SX}>
                    {number}
                  </Box>
                  <Box component="span" sx={marked ? MARKED_LINE_SX : LINE_SX}>
                    {line}
                  </Box>
                </Box>
                {lineMarks.map((mark, i) => (
                  <MarkRow key={`${number}-${i}`} mark={mark} />
                ))}
              </React.Fragment>
            );
          })}
        </Box>
      </Box>
      {edge.cut && (
        <Box
          aria-hidden="true"
          data-testid="hub-file-fade"
          sx={{ ...FADE_SX, right: edge.right, bottom: edge.bottom }}
        />
      )}
    </Box>
  );
}

CodePane.propTypes = {
  path: PropTypes.string.isRequired,
  text: PropTypes.string,
  marks: PropTypes.array.isRequired,
};

function MarkRow({ mark }) {
  const note = mark.kind === "note";
  return (
    <Box
      data-testid={note ? "hub-file-note" : "hub-file-finding"}
      sx={{
        ...ABOVE_FADE,
        mx: 1,
        my: 0.5,
        ml: 6,
        px: 1.25,
        py: 0.75,
        borderLeft: "3px solid",
        borderColor: note ? "info.main" : "warning.main",
        bgcolor: "background.paper",
        borderRadius: 0.5,
        whiteSpace: "normal",
        fontFamily: "inherit",
      }}
    >
      <Typography typography="s2" color="text.primary">
        {note ? "Reviewer note: " : `${mark.rule || "finding"}: `}
        {mark.text}
      </Typography>
    </Box>
  );
}

MarkRow.propTypes = { mark: PropTypes.object };

FileViewer.propTypes = {
  path: PropTypes.string,
  text: PropTypes.string,
  isLoading: PropTypes.bool,
  error: PropTypes.object,
  marks: PropTypes.array,
  imageUrl: PropTypes.string,
};
