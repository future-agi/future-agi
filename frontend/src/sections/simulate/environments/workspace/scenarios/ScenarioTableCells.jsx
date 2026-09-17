import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";

import { SUB_TASK_SHAPE } from "./scenarios.shapes";

// Attach a tooltip to a truncated line so hovering reveals the full value. Kept
// thin — the tooltip lives at the row level, not per-Typography, so the same
// wrapper gates both single-line noWrap labels and multi-line clamped bodies.
export function TruncTooltip({ title, children }) {
  if (!title) return children;
  return (
    <Tooltip
      arrow
      enterDelay={300}
      title={
        <Box sx={{ typography: "s3", whiteSpace: "pre-wrap", wordBreak: "break-word", maxWidth: 460 }}>
          {title}
        </Box>
      }
    >
      <Box sx={{ minWidth: 0 }}>{children}</Box>
    </Tooltip>
  );
}
TruncTooltip.propTypes = { title: PropTypes.node, children: PropTypes.node };

// Multi-line text cell — clamped to 3 lines so the row stays a predictable
// height, full content one hover away. Empty values render as an em-dash.
export function ClampCell({ text }) {
  const value = text || "—";
  return (
    <TruncTooltip title={text}>
      <Typography
        sx={{
          typography: "s2",
          color: "text.secondary",
          lineHeight: 1.45,
          display: "-webkit-box",
          WebkitLineClamp: 3,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
          wordBreak: "break-word",
        }}
      >
        {value}
      </Typography>
    </TruncTooltip>
  );
}
ClampCell.propTypes = { text: PropTypes.string };

// Accepts both shapes callers pass: [{ id, label }] and plain-string arrays.
// Coerces to a common { id, label } up front so no caller renders bare numbers
// with the label missing.
function normaliseSubTasks(subTasks) {
  return (subTasks || [])
    .map((st, i) => {
      if (typeof st === "string") return { id: `st-${i}`, label: st };
      if (!st) return null;
      return { id: st.id || `st-${i}`, label: st.label || st.text || st.title || "" };
    })
    .filter((st) => st && st.label);
}

// Sub-tasks column body: up to 3 inline, anything past that summarised as
// "+ N more". Hovering the row reveals the full numbered list.
export function SubTasksCell({ subTasks }) {
  const list = normaliseSubTasks(subTasks);
  if (!list.length) {
    return <Typography sx={{ typography: "s3", color: "text.subtitle" }}>—</Typography>;
  }
  const fullList = list.map((st, i) => `${i + 1}. ${st.label}`).join("\n");
  return (
    <TruncTooltip title={fullList}>
      <Stack spacing={0.375}>
        {list.slice(0, 3).map((st, i) => (
          <Stack key={st.id} direction="row" spacing={0.75} alignItems="flex-start">
            <Typography
              sx={{
                typography: "s3",
                color: "text.subtitle",
                fontVariantNumeric: "tabular-nums",
                flexShrink: 0,
                mt: "1px",
              }}
            >
              {i + 1}.
            </Typography>
            <Typography noWrap sx={{ typography: "s3", color: "text.secondary", minWidth: 0 }}>
              {st.label}
            </Typography>
          </Stack>
        ))}
        {list.length > 3 && (
          <Typography sx={{ typography: "s3", color: "text.subtitle", pl: 1.75 }}>
            + {list.length - 3} more
          </Typography>
        )}
      </Stack>
    </TruncTooltip>
  );
}
SubTasksCell.propTypes = { subTasks: PropTypes.arrayOf(SUB_TASK_SHAPE) };
