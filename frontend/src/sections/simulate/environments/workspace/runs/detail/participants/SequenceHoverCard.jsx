import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { SEQUENCE_COPY as C } from "./sequence.constants";
import { SLOW_SEC, kindOf, readable, took } from "./sequenceLayout";

const MAX_LINES = 8;

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
  const words = readable(
    r.type === "tool"
      ? s.result || C.noResult
      : reply
        ? s.answer?.full || s.answer?.label
        : s.full || s.label,
  );
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
        <Typography
          sx={{
            typography: "s3",
            color: "inherit",
            mt: 0.5,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            ...(r.type === "tool" && { typography: "code_sm" }),
            // A long message is cut here; the step panel shows it in full.
            display: "-webkit-box",
            WebkitLineClamp: MAX_LINES,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {/* One quote for one turn's words; several turns read as a list. */}
          {s.kind === "message" && r.type !== "tool" && !(s.turns > 1)
            ? `“${words}”`
            : words}
        </Typography>
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
