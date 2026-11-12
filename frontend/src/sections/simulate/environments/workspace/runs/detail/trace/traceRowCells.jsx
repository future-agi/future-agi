import PropTypes from "prop-types";
import { Box, Stack, TableCell, Typography } from "@mui/material";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import StatusBadge from "src/components/status-badge/StatusBadge";
import {
  SubTasksCell,
  TruncTooltip,
} from "../../../scenarios/ScenarioTableCells";
import {
  TEXT_COL_WIDTH,
  bodyCellSx,
  isCallLive,
  numCellSx,
  runOutcome,
  subGoalOutcome,
} from "./traceTable.constants";
import {
  CsatValue,
  Field,
  MetricValue,
  Score,
  UnscoredEval,
} from "./traceCells";

// One call's cells, shared by the run's calls table and the run compare table
// so a call reads the same on both.

const textCellSx = (width) => ({
  ...bodyCellSx,
  width,
  minWidth: width,
  maxWidth: width,
  typography: "s2",
  color: "text.secondary",
});
const clampSx = {
  display: "-webkit-box",
  WebkitLineClamp: 4,
  WebkitBoxOrient: "vertical",
  overflow: "hidden",
  wordBreak: "break-word",
};

// A persona is worth showing when any field is filled, not only the name: the
// API sends name: null when the persona has no name key.
const hasPersonaDetails = (p) =>
  !!(p && (p.name || p.voice || p.age || p.traits?.length));

// A value with an optional mark after it (the compare table's change arrow).
// Without one the value renders alone, unwrapped.
function WithAdornment({ adornment, children }) {
  if (!adornment) return children;
  return (
    <Stack direction="row" alignItems="center" spacing={0.75}>
      {children}
      {adornment}
    </Stack>
  );
}
WithAdornment.propTypes = {
  adornment: PropTypes.node,
  children: PropTypes.node,
};

// A call's sub-goals, each with its verdict: met, missed or inconclusive.
export function SubGoalVerdicts({ goals, sx }) {
  return (
    <Stack spacing={0.5} sx={sx}>
      {goals.map((goal, goalIndex) => {
        const verdict = subGoalOutcome(goal);
        return (
          <Stack
            key={`${goal.name}-${goalIndex}`}
            direction="row"
            alignItems="center"
            spacing={0.75}
          >
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {goal.name}
            </Typography>
            {goal.passed == null ? (
              <Typography sx={{ typography: "s3", color: verdict.color }}>
                {verdict.label}
              </Typography>
            ) : (
              <Box
                role="img"
                aria-label={verdict.label}
                title={verdict.label}
                sx={{ display: "flex", color: verdict.color }}
              >
                <Iconify
                  icon={
                    goal.passed ? "solar:check-linear" : "solar:close-linear"
                  }
                  width={16}
                  aria-hidden="true"
                />
              </Box>
            )}
          </Stack>
        );
      })}
    </Stack>
  );
}
SubGoalVerdicts.propTypes = {
  goals: PropTypes.array.isRequired,
  sx: PropTypes.object,
};

function CallDetailsBody({ task: t, durationAdornment }) {
  const outcome = runOutcome(t.status);
  return (
    <Box minWidth={0}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ mb: 0.125 }}
      >
        <Typography
          noWrap
          sx={{
            typography: "s2",
            fontWeight: "fontWeightSemiBold",
            maxWidth: 300,
          }}
        >
          {t.scenario}
        </Typography>
        {t.critical && (
          <CustomTooltip
            show
            arrow
            title="Critical: a failure here is a release blocker"
          >
            <Box sx={{ display: "flex" }}>
              <Iconify
                icon="solar:danger-triangle-bold"
                width={12}
                sx={{ color: "status.danger.solid" }}
              />
            </Box>
          </CustomTooltip>
        )}
      </Stack>
      <Stack direction="row" alignItems="center" spacing={0.75}>
        <StatusBadge
          status={t.status}
          label={outcome.label}
          role={outcome.role}
          variant="dot"
        />
        {t.durationMs != null && (
          <>
            <Box
              sx={{
                width: 3,
                height: 3,
                borderRadius: "50%",
                bgcolor: "text.disabled",
              }}
            />
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {(t.durationMs / 1000).toFixed(1)}s
            </Typography>
            {durationAdornment}
          </>
        )}
      </Stack>
      {t.subGoalResults?.length > 0 && (
        <SubGoalVerdicts goals={t.subGoalResults} sx={{ mt: 1 }} />
      )}
    </Box>
  );
}
CallDetailsBody.propTypes = {
  task: PropTypes.object.isRequired,
  durationAdornment: PropTypes.node,
};

const METRIC_CELLS = {
  turns: { field: "turns" },
  latency: { field: "latencyMs", suffix: "ms" },
  stopLatency: { field: "stopLatencyMs", suffix: "ms" },
  aiInterruptions: { field: "aiInterruptions" },
  tokens: { field: "tokens" },
};

/**
 * One fixed column of a call row. `adornment` sits after the value (a metric's
 * change, or the duration's on Run details); `rowSpan` spans a cell the compare
 * table shares across a scenario's lines.
 */
export function TraceCell({
  columnKey,
  task: t,
  onOpen,
  adornment = null,
  rowSpan,
}) {
  const open = onOpen ? () => onOpen(t) : undefined;
  const span = rowSpan > 1 ? { rowSpan } : {};
  switch (columnKey) {
    case "callDetails":
      // Indented past the group row's chevron so a call reads as nested
      // under its group, lined up with the group name.
      return (
        <TableCell sx={{ ...bodyCellSx, pl: 5 }} onClick={open} {...span}>
          <CallDetailsBody task={t} durationAdornment={adornment} />
        </TableCell>
      );
    case "persona":
      return (
        <TableCell sx={bodyCellSx} onClick={open} {...span}>
          {hasPersonaDetails(t.personaDetails) ? (
            <Stack spacing={0.5} sx={{ minWidth: 210 }}>
              <Field
                icon="solar:user-id-linear"
                label="Name"
                value={t.personaDetails.name}
              />
              <Field
                icon="solar:user-linear"
                label="Voice"
                value={t.personaDetails.voice}
              />
              <Field
                icon="solar:users-group-rounded-linear"
                label="Age"
                value={t.personaDetails.age}
              />
              <Field
                icon="solar:tag-linear"
                label="Traits"
                value={t.personaDetails.traits?.join(", ")}
              />
            </Stack>
          ) : (
            <Typography sx={{ typography: "s3", color: "text.disabled" }}>
              -
            </Typography>
          )}
        </TableCell>
      );
    case "scenario":
      return (
        <TableCell
          sx={textCellSx(TEXT_COL_WIDTH.long)}
          onClick={open}
          {...span}
        >
          <Box sx={clampSx}>{t.scenario || "-"}</Box>
        </TableCell>
      );
    case "situation":
      return (
        <TableCell
          sx={textCellSx(TEXT_COL_WIDTH.long)}
          onClick={open}
          {...span}
        >
          <TruncTooltip title={t.scenarioDetails}>
            <Box sx={clampSx}>{t.scenarioDetails || "-"}</Box>
          </TruncTooltip>
        </TableCell>
      );
    case "subGoals":
      return (
        <TableCell
          sx={textCellSx(TEXT_COL_WIDTH.long)}
          onClick={open}
          {...span}
        >
          <SubTasksCell
            subTasks={(t.subGoalResults || []).map((goal) => goal.name)}
          />
        </TableCell>
      );
    case "idealOutcome":
      return (
        <TableCell
          sx={textCellSx(TEXT_COL_WIDTH.long)}
          onClick={open}
          {...span}
        >
          <TruncTooltip title={t.idealOutcome}>
            <Box sx={clampSx}>{t.idealOutcome || "-"}</Box>
          </TruncTooltip>
        </TableCell>
      );
    case "conversationBranch":
      return (
        <TableCell
          sx={textCellSx(TEXT_COL_WIDTH.short)}
          onClick={open}
          {...span}
        >
          <Box sx={clampSx}>{t.conversationBranch || "-"}</Box>
        </TableCell>
      );
    case "csat":
      return (
        <TableCell sx={numCellSx} onClick={open} {...span}>
          <WithAdornment adornment={adornment}>
            <CsatValue
              value={t.csat}
              status={t.csatStatus}
              reason={t.csatReason}
              callLive={isCallLive(t)}
            />
          </WithAdornment>
        </TableCell>
      );
    default: {
      const metric = METRIC_CELLS[columnKey];
      if (!metric) return null;
      return (
        <TableCell sx={numCellSx} onClick={open} {...span}>
          <WithAdornment adornment={adornment}>
            <MetricValue
              metric={columnKey}
              value={t[metric.field]}
              suffix={metric.suffix}
              loading={isCallLive(t)}
            />
          </WithAdornment>
        </TableCell>
      );
    }
  }
}
TraceCell.propTypes = {
  columnKey: PropTypes.string.isRequired,
  task: PropTypes.object.isRequired,
  onOpen: PropTypes.func,
  adornment: PropTypes.node,
  rowSpan: PropTypes.number,
};

// One scored column (an evaluation or a sub-goal result) of a call row.
export function EvalCell({ column, task: t, onOpen, adornment = null }) {
  const r = t.evalResults?.find((x) => x.id === column.id);
  return (
    <TableCell
      sx={{ ...bodyCellSx, p: 0, position: "relative" }}
      onClick={onOpen ? () => onOpen(t) : undefined}
    >
      {r?.score != null || r?.label ? (
        <Score result={r} adornment={adornment} />
      ) : (
        <UnscoredEval
          result={r}
          callLive={isCallLive(t)}
          callStatus={t.executionStatus}
        />
      )}
    </TableCell>
  );
}
EvalCell.propTypes = {
  column: PropTypes.shape({ id: PropTypes.string }).isRequired,
  task: PropTypes.object.isRequired,
  onOpen: PropTypes.func,
  adornment: PropTypes.node,
};
