import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";

import StatusBadge from "src/components/status-badge/StatusBadge";
import Iconify from "src/components/iconify";

import { DRAFTS_COPY } from "../workspace.constants";
import { SUB_TASK_SHAPE } from "./scenarios.shapes";
import { normaliseSubTasks } from "./scenarioEditor.constants";
import {
  LIFECYCLE_COPY,
  hasUnsavedChange,
  isUnsavedAddition,
  unsavedReasonId,
} from "./scenarios.constants";
import { isNotMeasured, lifecycleChip } from "./lifecycleView";

const LIFECYCLE_ICONS = {
  draft: "solar:shield-warning-linear",
  kept_as_written: "solar:document-text-linear",
  archived: "solar:archive-linear",
  runs_unvalidated: "solar:info-circle-linear",
};
const LIFECYCLE_ICON_COLORS = {
  warning: "status.warning.fg",
  neutral: "text.secondary",
};

// A proved scenario whose check reads evidence this agent doesn't give, the server's reason on hover.
export function NotMeasuredChip({ lifecycle }) {
  if (!isNotMeasured(lifecycle)) return null;
  return (
    <Tooltip arrow title={lifecycle.warning_text || ""}>
      <span data-testid="not-measured-chip">
        <StatusBadge
          status="not_measured"
          role="warning"
          label={LIFECYCLE_COPY.notMeasured}
          size="sm"
          dot={false}
        />
      </span>
    </Tooltip>
  );
}
NotMeasuredChip.propTypes = { lifecycle: PropTypes.object };

// A row's state as one small mark, its name and reason on hover. Active rows get none.
export function LifecycleMark({ lifecycle, validating = false }) {
  if (validating) {
    return (
      <StatusBadge
        status="validating"
        role="info"
        label={LIFECYCLE_COPY.validate.rowValidating}
        size="sm"
        dot={false}
      />
    );
  }
  const chip = lifecycleChip(lifecycle);
  if (!chip) return <NotMeasuredChip lifecycle={lifecycle} />;
  const title = chip.hover ? `${chip.label}: ${chip.hover}` : chip.label;
  return (
    <Tooltip arrow title={title}>
      <Box
        role="img"
        aria-label={chip.label}
        data-testid="lifecycle-mark"
        sx={{
          display: "flex",
          flexShrink: 0,
          color: LIFECYCLE_ICON_COLORS[chip.tone],
        }}
      >
        <Iconify icon={LIFECYCLE_ICONS[chip.key]} width={14} />
      </Box>
    </Tooltip>
  );
}
LifecycleMark.propTypes = {
  lifecycle: PropTypes.object,
  // A running validation is proving this row.
  validating: PropTypes.bool,
};

// "Unsaved" on a row the unsaved changes add or change; an added row also says
// why it can't be selected.
export function UnsavedMark({ row }) {
  if (!hasUnsavedChange(row)) return null;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.75}
      sx={{ flexShrink: 0 }}
    >
      <StatusBadge
        status="unsaved"
        role="warning"
        label={DRAFTS_COPY.unsaved}
        size="sm"
        dot={false}
      />
      {isUnsavedAddition(row) && (
        <Typography
          id={unsavedReasonId(row)}
          sx={{ typography: "s3", color: "text.subtitle" }}
        >
          {DRAFTS_COPY.saveToRun}
        </Typography>
      )}
    </Stack>
  );
}
UnsavedMark.propTypes = {
  row: PropTypes.shape({ id: PropTypes.string, draftChange: PropTypes.string }),
};

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
        <Box
          sx={{
            typography: "s3",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            maxWidth: 460,
          }}
        >
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
          typography: "s1",
          color: "text.secondary",
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
    return (
      <Typography sx={{ typography: "s1", color: "text.subtitle" }}>
        -
      </Typography>
    );
  }
  const fullList = list.map((st, i) => `${i + 1}. ${st.label}`).join("\n");
  return (
    <TruncTooltip title={fullList}>
      <Stack spacing={0.375}>
        {list.slice(0, limit).map((st, i) => (
          <Stack
            key={st.id}
            direction="row"
            spacing={0.75}
            alignItems="flex-start"
          >
            <Typography
              sx={{
                typography: "s1",
                color: "text.subtitle",
                fontVariantNumeric: "tabular-nums",
                flexShrink: 0,
              }}
            >
              {i + 1}.
            </Typography>
            <Typography
              noWrap
              sx={{ typography: "s1", color: "text.secondary", minWidth: 0 }}
            >
              {st.label}
            </Typography>
          </Stack>
        ))}
        {list.length > limit && (
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", pl: 1.75 }}
          >
            + {list.length - limit} more
          </Typography>
        )}
      </Stack>
    </TruncTooltip>
  );
}
SubTasksCell.propTypes = {
  subTasks: PropTypes.arrayOf(SUB_TASK_SHAPE),
  limit: PropTypes.number,
};
