import { useLayoutEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import ChatMarkdown from "src/sections/simulate/environments/buildEnvironment/console/ChatMarkdown";
import { SEQUENCE_COPY as C } from "./sequence.constants";
import { SLOW_SEC, kindOf, readable, took } from "./sequenceLayout";

const MAX_LINES = 8;
const LINE_PX = 16;

// A hover card's words: prose the API marks as markdown is rendered, anything else kept as written.
// A long message is cut here; the step panel shows it in full.
export function HoverWords({ text, shown, quoted, code }) {
  const box = useRef(null);
  const [cut, setCut] = useState(false);
  const markdown = !!shown && shown.format !== "code";
  // Rendered blocks don't end on a line, so a cut message fades out instead of stopping mid-line.
  useLayoutEffect(() => {
    const el = box.current;
    if (el) setCut(el.scrollHeight > el.clientHeight + 1);
  }, [markdown, shown?.text]);
  if (markdown) {
    return (
      <Box
        ref={box}
        sx={{
          mt: 0.5,
          maxHeight: MAX_LINES * LINE_PX,
          overflow: "hidden",
          ...(cut && {
            maskImage: `linear-gradient(to bottom, black calc(100% - ${LINE_PX * 2}px), transparent)`,
          }),
        }}
      >
        {/* Worker text isn't ours: ChatMarkdown shows raw HTML as text and never loads an image. */}
        <ChatMarkdown
          text={shown.text}
          basicHtml
          sx={{
            typography: "s3",
            color: "inherit",
            wordBreak: "break-word",
            "& p, & ul, & ol, & pre, & table": { mb: 0.5 },
            "& h1, & h2, & h3, & h4, & h5, & h6": {
              font: "inherit",
              fontWeight: 600,
              m: 0,
              mb: 0.25,
            },
            "& code": { typography: "code_sm" },
            // The card is the inverse surface: blocks and borders take its text colour.
            "& pre": {
              m: 0,
              mb: 0.5,
              bgcolor: "transparent",
              whiteSpace: "pre-wrap",
            },
            "& th, & td": {
              px: 0.5,
              border: "1px solid",
              borderColor: "currentColor",
              textAlign: "left",
            },
            ...(quoted && {
              "& > p:first-of-type::before": { content: '"“"' },
              "& > p:last-of-type::after": { content: '"”"' },
            }),
          }}
        />
      </Box>
    );
  }
  return (
    <Typography
      sx={{
        typography: code ? "code_sm" : "s3",
        color: "inherit",
        mt: 0.5,
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
        display: "-webkit-box",
        WebkitLineClamp: MAX_LINES,
        WebkitBoxOrient: "vertical",
        overflow: "hidden",
      }}
    >
      {quoted ? `“${readable(text)}”` : readable(text)}
    </Typography>
  );
}

HoverWords.propTypes = {
  text: PropTypes.string,
  shown: PropTypes.shape({ format: PropTypes.string, text: PropTypes.string }),
  quoted: PropTypes.bool,
  code: PropTypes.bool,
};

// The hover card for a row: its words (up to MAX_LINES) and what they are.
export default function HoverCard({ seq, row: r, step: s }) {
  const k = kindOf(r, s);
  const reply = r.type === "back" || r.type === "none" || r.type === "took";
  const closedLoop = r.loop && r.closed;
  const where = closedLoop
    ? C.stepsRange(r.loop.first, r.loop.last)
    : reply
      ? C.replyTo(s.n)
      : C.step(s.n);
  const who =
    r.type === "tool"
      ? C.called(seq.nameOf(s.from), s.tool)
      : reply
        ? `${seq.nameOf(s.to)} → ${seq.nameOf(r.type === "took" ? seq.lanes[0].id : s.from)}`
        : `${seq.nameOf(s.from)} → ${seq.nameOf(s.to)}`;
  const words =
    r.type === "tool"
      ? s.result || C.noResult
      : reply
        ? s.answer?.full || s.answer?.label
        : s.full || s.label;
  // Only a message's own words carry the API's format; a label of ours never does.
  const shown =
    r.type === "tool"
      ? null
      : reply
        ? s.answer?.full && s.answer?.fullShown
        : s.full && s.fullShown;
  const state = closedLoop
    ? C.repeated(r.loop.times)
    : r.type === "none"
      ? null
      : s.refused && r.type === "tool"
        ? C.refusedByWorld() /* the reason is already in the words */
        : s.bad && !reply
          ? C.failed
          : /* The arrow's rule: never "slow" for a conversation with the
               caller, or several turns in one step. */
            s.dur != null &&
              s.dur >= SLOW_SEC &&
              !reply &&
              s.from !== seq.lanes[0].id &&
              !(s.turns > 1)
            ? C.slowTook(took(s.dur))
            : null;
  return (
    <Box sx={{ maxWidth: 340, py: 0.25 }}>
      <Typography sx={{ typography: "s3", fontWeight: 600, color: "inherit" }}>
        {`${where}${
          r.type === "none"
            ? s.answer?.cut
              ? ` · ${C.recordCut}`
              : s.answer?.stopped
                ? ` · ${C.stoppedByUser}`
                : ` · ${C.noReplyCame}`
            : k
              ? ` · ${C.kindWords[k]}`
              : ""
        }`}
      </Typography>
      <Typography sx={{ typography: "s3", color: "inherit", opacity: 0.75 }}>
        {who}
      </Typography>
      {words && (
        <HoverWords
          text={words}
          shown={shown || null}
          code={r.type === "tool"}
          /* One quote for one turn's words; several turns read as a list. */
          quoted={s.kind === "message" && r.type !== "tool" && !(s.turns > 1)}
        />
      )}
      {state && (
        <Typography
          sx={{
            typography: "s3",
            fontWeight: 600,
            color: "inherit",
            mt: 0.5,
          }}
        >
          {state}
        </Typography>
      )}
      {!closedLoop && (
        <Typography
          sx={{ typography: "s3", color: "inherit", opacity: 0.6, mt: 0.5 }}
        >
          {C.clickForDetail}
        </Typography>
      )}
    </Box>
  );
}

HoverCard.propTypes = {
  seq: PropTypes.object.isRequired,
  row: PropTypes.object.isRequired,
  step: PropTypes.object.isRequired,
};
