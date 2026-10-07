import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";

import { SUB_TASK_SHAPE } from "./scenarios.shapes";
import { normaliseSubTasks } from "./scenarioEditor.constants";

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

// Multi-line text cell — clamped to `lines` (from the row height; 0 shows it
// all) so the row stays a predictable height, full content one hover away.
// Empty values render as an em-dash.
export function ClampCell({ text, lines = 3 }) {
  const value = text || "-";
  return (
    <TruncTooltip title={text}>
      <Typography
        sx={{
          typography: "s2",
          color: "text.secondary",
          lineHeight: 1.45,
          wordBreak: "break-word",
          ...(lines > 0 && {
            display: "-webkit-box",
            WebkitLineClamp: lines,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }),
        }}
      >
        {value}
      </Typography>
    </TruncTooltip>
  );
}
ClampCell.propTypes = { text: PropTypes.string, lines: PropTypes.number };

// Sub-tasks column body: up to `limit` inline (from the row height), anything
// past that summarised as "+ N more". Hovering the row reveals the full list.
export function SubTasksCell({ subTasks, limit = 3 }) {
  const list = normaliseSubTasks(subTasks);
  if (!list.length) {
    return <Typography sx={{ typography: "s3", color: "text.subtitle" }}>-</Typography>;
  }
  const fullList = list.map((st, i) => `${i + 1}. ${st.label}`).join("\n");
  return (
    <TruncTooltip title={fullList}>
      <Stack spacing={0.375}>
        {list.slice(0, limit).map((st, i) => (
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
        {list.length > limit && (
          <Typography sx={{ typography: "s3", color: "text.subtitle", pl: 1.75 }}>
            + {list.length - limit} more
          </Typography>
        )}
      </Stack>
    </TruncTooltip>
  );
}
SubTasksCell.propTypes = { subTasks: PropTypes.arrayOf(SUB_TASK_SHAPE), limit: PropTypes.number };
