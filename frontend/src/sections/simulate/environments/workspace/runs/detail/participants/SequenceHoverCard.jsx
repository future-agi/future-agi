import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { KIND_WORDS, SLOW_SEC, kindOf, took } from "./sequenceLayout";

// The hover card for a row: the full words and what they are, never cut.
export default function HoverCard({ seq, row: r, step: s }) {
  const k = kindOf(r, s);
  const reply = r.type === "back" || r.type === "none" || r.type === "took";
  const closedLoop = r.loop && r.closed;
  const where = closedLoop
    ? `Steps ${r.loop.first}–${r.loop.last}`
    : reply
      ? `Reply to step ${s.n}`
      : `Step ${s.n}`;
  const who =
    r.type === "tool"
      ? `${seq.nameOf(s.from)} called ${s.tool}`
      : reply
        ? `${seq.nameOf(s.to)} → ${seq.nameOf(r.type === "took" ? seq.lanes[0].id : s.from)}`
        : `${seq.nameOf(s.from)} → ${seq.nameOf(s.to)}`;
  const words =
    r.type === "tool"
      ? `${s.result || "No result"}`
      : reply
        ? s.answer?.full || s.answer?.label
        : s.full || s.label;
  const state = closedLoop
    ? `Repeated ${r.loop.times} times. Click to see every round`
    : r.type === "none"
      ? null
      : s.refused && r.type === "tool"
        ? "Refused by the world" /* the reason is already in the words */
        : s.bad && !reply
          ? "Failed"
          : /* The arrow's rule: never "slow" for a conversation with the
               caller, or several turns in one step. */
            s.dur != null &&
              s.dur >= SLOW_SEC &&
              !reply &&
              s.from !== seq.lanes[0].id &&
              !(s.turns > 1)
            ? `Slow · took ${took(s.dur)}`
            : null;
  return (
    <Box sx={{ maxWidth: 340, py: 0.25 }}>
      <Typography sx={{ typography: "s3", fontWeight: 600, color: "inherit" }}>
        {`${where}${
          r.type === "none"
            ? " · No reply came back"
            : k
              ? ` · ${KIND_WORDS[k]}`
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
          Click for the full detail
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
