import PropTypes from "prop-types";
import React, { memo, useState } from "react";
import {
  ButtonBase,
  Chip,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
  tableCellClasses,
} from "@mui/material";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  GROUP_METRIC_COLUMNS,
  TRACE_COLUMNS,
  TRACE_HEAD_WIDTH,
  bodyCellSx,
  evalGrading,
  headCellSx,
} from "../detail/trace/traceTable.constants";
import { EvalCell, TraceCell } from "../detail/trace/traceRowCells";
import {
  GroupEvalMean,
  GroupMetricValue,
} from "../detail/trace/TraceGroupHeaderRow";
import { COMPARE_COPY } from "../../workspace.constants";
import {
  COMPARE_CELL_KEYS,
  SHARED_COLUMNS,
  STATUS_COLUMN,
} from "./compare.constants";
import { wordDiff } from "./compareDiff";
import { CompareStatusCell, DiffArrow, RunDot } from "./compareCells";

const LEAD_CLASS = "compare-lead";
// Every cell takes a left divider, except the scenario column: with spanned
// cells the first cell of a line is not the first column.
const tableSx = {
  minWidth: 1000,
  tableLayout: "auto",
  borderCollapse: "separate",
  borderSpacing: 0,
  [`&& .${tableCellClasses.root}`]: {
    borderLeft: "1px solid",
    borderLeftColor: "divider",
  },
  [`&& .${LEAD_CLASS}`]: { borderLeft: 0 },
};
const leadCellSx = { ...bodyCellSx, width: 240, minWidth: 240, maxWidth: 240 };
const groupCellSx = {
  ...bodyCellSx,
  py: 1.5,
  bgcolor: "background.neutral",
  borderTop: "2px solid",
  borderTopColor: "divider",
  cursor: "pointer",
};
// Under a group, a scenario's lead cell steps in so it reads as the group's.
const nestedLeadCellSx = { ...leadCellSx, pl: 4 };
const focusRingSx = {
  "&:focus-visible": {
    outline: "2px solid",
    outlineColor: "primary.main",
    outlineOffset: -2,
  },
};
// The lead cell's control: a native button styled as the cell's text. The
// row keeps its own click for the mouse; the button stops the bubble so one
// press acts once.
const leadButtonSx = {
  width: "100%",
  justifyContent: "flex-start",
  textAlign: "left",
  font: "inherit",
  color: "inherit",
  borderRadius: 0.5,
  ...focusRingSx,
};
const ownClick = (fn) => (e) => {
  e.stopPropagation();
  fn();
};

// The measure a column's change arrow reads.
const MEASURE_OF = {
  callDetails: "duration",
  csat: "csat",
  turns: "turns",
  latency: "latency",
  stopLatency: "stopLatency",
  aiInterruptions: "aiInterruptions",
  tokens: "tokens",
};
const evalChipSx = {
  px: 0.75,
  py: 0.25,
  borderRadius: 0.5,
  whiteSpace: "nowrap",
};

const columnLabel = (key) =>
  key === STATUS_COLUMN.key
    ? STATUS_COLUMN.label
    : TRACE_COLUMNS.find((c) => c.key === key)?.label;

// A row's shared cells read the first call it has (baseline first); its own
// fields stand in when it has none.
const representative = (row, runs) =>
  runs.map((run) => row.calls[run.id]?.[0]).find(Boolean) ?? {
    id: row.rowId,
    persona: row.persona,
    personaDetails: { name: row.persona, voice: null, age: null, traits: [] },
    scenario: row.scenario,
    scenarioDetails: row.situation,
    idealOutcome: row.idealOutcome,
  };

// One line per call of each run, ordered by trial; a run with no call on the
// row gets one "Not run" line.
const rowLines = (row, runs) =>
  runs.flatMap((run) => {
    const calls = row.calls[run.id] ?? [];
    if (!calls.length) return [{ run, task: null, first: true, span: 1 }];
    return calls.map((task, i) => ({
      run,
      task,
      first: i === 0,
      span: calls.length,
    }));
  });

// A line naming the non-baseline runs among `ids` by number; null when none
// has one.
const namedRuns = (ids, runs, words) => {
  const ordinals = runs
    .filter(
      (run) => !run.isBaseline && run.ordinal != null && ids.includes(run.id),
    )
    .map((run) => run.ordinal);
  return ordinals.length ? words(ordinals) : null;
};

const dash = (
  <Typography
    component="span"
    sx={{ typography: "s2", color: "text.disabled" }}
  >
    -
  </Typography>
);

function PerRun({ runs, entries, render }) {
  return (
    <Stack spacing={0.75}>
      {runs.map((run) => (
        <Stack key={run.id} direction="row" alignItems="center" spacing={0.75}>
          <RunDot ordinal={run.ordinal} colorIndex={run.colorIndex} />
          {entries[run.id] ? render(entries[run.id], run) : dash}
        </Stack>
      ))}
    </Stack>
  );
}
PerRun.propTypes = {
  runs: PropTypes.array.isRequired,
  entries: PropTypes.object.isRequired,
  render: PropTypes.func.isRequired,
};

// A group's header: its name and scenario count, then each run's figures for
// the group, column by column, as the run detail's group header shows them.
function CompareGroupHeaderRow({
  group,
  rows,
  runs,
  sharedKeys,
  lineKeys,
  scored,
  collapsed,
  onToggle,
}) {
  const entries = group.runs;
  const cell = (key) => {
    if (key === STATUS_COLUMN.key)
      return (
        <PerRun
          runs={runs}
          entries={entries}
          render={(e) => (
            <Typography
              sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}
            >
              {e.passRate == null ? "-" : `${Math.round(e.passRate)}%`}
            </Typography>
          )}
        />
      );
    const metric = GROUP_METRIC_COLUMNS.find((c) => c.key === key);
    if (!metric) return null;
    return (
      <PerRun
        runs={runs}
        entries={entries}
        render={(e) => (
          <GroupMetricValue
            value={e.agg[metric.field]}
            suffix={metric.suffix}
            metric={metric.metric}
            aggregation={metric.aggregation}
          />
        )}
      />
    );
  };
  return (
    <TableRow onClick={onToggle}>
      <TableCell className={LEAD_CLASS} sx={groupCellSx}>
        <ButtonBase
          disableRipple
          aria-expanded={!collapsed}
          onClick={ownClick(onToggle)}
          sx={{ ...leadButtonSx, gap: 1, minWidth: 0 }}
        >
          <Iconify
            icon={
              collapsed
                ? "solar:alt-arrow-right-linear"
                : "solar:alt-arrow-down-linear"
            }
            width={13}
            sx={{ color: "text.subtitle", flexShrink: 0 }}
          />
          <Typography
            component="span"
            noWrap
            sx={{ typography: "s1", fontWeight: "fontWeightBold" }}
          >
            {group.label}
          </Typography>
          <Typography
            component="span"
            noWrap
            sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}
          >
            {COMPARE_COPY.scenarioCount(group.rowCount)}
          </Typography>
        </ButtonBase>
      </TableCell>
      {sharedKeys.map((key) => (
        <TableCell key={key} sx={groupCellSx} />
      ))}
      {lineKeys.map((key) => (
        <TableCell key={key} sx={groupCellSx}>
          {cell(key)}
        </TableCell>
      ))}
      {scored.map((e) => (
        <TableCell key={e.id} sx={groupCellSx}>
          <PerRun
            runs={runs}
            entries={entries}
            render={(entry, run) => (
              <GroupEvalMean
                aggregate={entry.agg.evals?.[e.id]}
                grading={evalGrading(
                  rows.flatMap((row) => row.calls[run.id] ?? []),
                  e.id,
                )}
                sx={evalChipSx}
              />
            )}
          />
        </TableCell>
      ))}
    </TableRow>
  );
}
CompareGroupHeaderRow.propTypes = {
  group: PropTypes.object.isRequired,
  rows: PropTypes.array.isRequired,
  runs: PropTypes.array.isRequired,
  sharedKeys: PropTypes.array.isRequired,
  lineKeys: PropTypes.array.isRequired,
  scored: PropTypes.array.isRequired,
  collapsed: PropTypes.bool,
  onToggle: PropTypes.func,
};

/**
 * The scenario-by-scenario table: one row per scenario, one line per call of
 * each run under it. Calls render with the run detail table's cells; the
 * scenario's own columns lead, drawn once across its lines, and each run's
 * columns follow.
 */
function CompareTable({
  runs,
  rows,
  groups = null,
  columns,
  evals,
  subGoalEvals = [],
  showDiff = false,
  onOpenRow,
}) {
  const [collapsed, setCollapsed] = useState(() => new Set());
  const show = (key) => columns.has(key);
  const keys = COMPARE_CELL_KEYS.filter(show);
  const sharedKeys = keys.filter((k) => SHARED_COLUMNS.has(k));
  const lineKeys = keys.filter((k) => !SHARED_COLUMNS.has(k));
  const scored = [...(show("subGoalEvals") ? subGoalEvals : []), ...evals];
  const firstLineKey = lineKeys[0];

  const renderLine = (row, rep, lines, line, index, prefix) => {
    const { run, task } = line;
    const marker = <RunDot ordinal={run.ordinal} colorIndex={run.colorIndex} />;
    const diff = showDiff && task ? wordDiff(task.diff) : null;
    const name = `${rep.sourceScenario || row.scenario || row.scenarioKey}${
      row.trialIndex != null ? ` · ${COMPARE_COPY.trial(row.trialIndex)}` : ""
    }`;
    const nameText = (
      <Typography
        component="span"
        sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}
      >
        {name}
      </Typography>
    );
    const editedBy = namedRuns(
      row.editedRunIds,
      runs,
      COMPARE_COPY.playedEdited,
    );
    const unverifiedBy = namedRuns(
      row.unverifiedRunIds,
      runs,
      COMPARE_COPY.notPinned,
    );
    return (
      <TableRow
        key={`${prefix}:${index}`}
        hover
        onClick={() => onOpenRow?.(row)}
        sx={{ cursor: "pointer" }}
        data-run={run.id}
      >
        {index === 0 && (
          <TableCell
            className={LEAD_CLASS}
            rowSpan={lines.length}
            sx={groups ? nestedLeadCellSx : leadCellSx}
          >
            {onOpenRow ? (
              <ButtonBase
                disableRipple
                aria-label={COMPARE_COPY.openRow(name)}
                onClick={ownClick(() => onOpenRow(row))}
                sx={leadButtonSx}
              >
                {nameText}
              </ButtonBase>
            ) : (
              nameText
            )}
            {row.useCase && (
              <Typography
                sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
              >
                {row.useCase}
              </Typography>
            )}
            {row.editedRunIds.length > 0 && (
              <CustomTooltip
                show
                describeChild
                title={editedBy || COMPARE_COPY.editedHint}
              >
                <Chip
                  size="small"
                  label={COMPARE_COPY.edited}
                  color="warning"
                  tabIndex={0}
                  sx={{ mt: 0.5 }}
                />
              </CustomTooltip>
            )}
            {editedBy && (
              <Typography
                sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
              >
                {editedBy}
              </Typography>
            )}
            {row.unverifiedRunIds.length > 0 && (
              <CustomTooltip
                show
                describeChild
                title={COMPARE_COPY.cantVerifyHint}
              >
                <Chip
                  size="small"
                  label={COMPARE_COPY.cantVerify}
                  tabIndex={0}
                  sx={{ mt: 0.5 }}
                />
              </CustomTooltip>
            )}
            {unverifiedBy && (
              <Typography
                sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
              >
                {unverifiedBy}
              </Typography>
            )}
          </TableCell>
        )}
        {index === 0 &&
          sharedKeys.map((key) => (
            <TraceCell
              key={key}
              columnKey={key}
              task={rep}
              rowSpan={lines.length}
            />
          ))}
        {lineKeys.map((key) => {
          if (!task)
            return (
              <TableCell key={key} sx={bodyCellSx}>
                {key === firstLineKey ? (
                  <Stack direction="row" spacing={1} alignItems="center">
                    {marker}
                    <Typography
                      sx={{ typography: "s2", color: "text.disabled" }}
                    >
                      {COMPARE_COPY.notRun}
                    </Typography>
                  </Stack>
                ) : (
                  dash
                )}
              </TableCell>
            );
          if (key === STATUS_COLUMN.key)
            return (
              <CompareStatusCell
                key={key}
                task={task}
                diff={diff}
                marker={marker}
              />
            );
          const change = diff?.measures[MEASURE_OF[key]];
          return (
            <TraceCell
              key={key}
              columnKey={key}
              task={task}
              adornment={change ? <DiffArrow change={change} /> : null}
            />
          );
        })}
        {scored.map((e) =>
          task ? (
            <EvalCell
              key={e.id}
              column={e}
              task={task}
              adornment={
                diff?.evals[e.id] ? (
                  <DiffArrow change={diff.evals[e.id]} />
                ) : null
              }
            />
          ) : (
            <TableCell key={e.id} sx={bodyCellSx}>
              {dash}
            </TableCell>
          ),
        )}
      </TableRow>
    );
  };

  const renderRow = (row, prefix = "") => {
    const lines = rowLines(row, runs);
    const rep = representative(row, runs);
    return lines.map((line, i) =>
      renderLine(row, rep, lines, line, i, `${prefix}${row.rowId}`),
    );
  };

  const headCell = (key) => (
    <TableCell
      key={key}
      sx={{ ...headCellSx, width: TRACE_HEAD_WIDTH[key] ?? 160 }}
    >
      {columnLabel(key)}
    </TableCell>
  );

  const rowsById = new Map(rows.map((row) => [row.rowId, row]));
  // A group's rows on this page.
  const groupRows = (g) =>
    g.rowIds.map((id) => rowsById.get(id)).filter(Boolean);
  const toggle = (key) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <Table size="small" sx={tableSx}>
      <TableHead>
        <TableRow>
          <TableCell className={LEAD_CLASS} sx={{ ...headCellSx, width: 240 }}>
            {COMPARE_COPY.scenarioColumn}
          </TableCell>
          {sharedKeys.map(headCell)}
          {lineKeys.map(headCell)}
          {scored.map((e) => (
            <TableCell key={e.id} sx={{ ...headCellSx, width: 150 }}>
              {e.name}
            </TableCell>
          ))}
        </TableRow>
      </TableHead>
      <TableBody>
        {groups
          ? groups.map((g) => (
              <React.Fragment key={g.key}>
                <CompareGroupHeaderRow
                  group={g}
                  rows={groupRows(g)}
                  runs={runs}
                  sharedKeys={sharedKeys}
                  lineKeys={lineKeys}
                  scored={scored}
                  collapsed={collapsed.has(g.key)}
                  onToggle={() => toggle(g.key)}
                />
                {!collapsed.has(g.key) &&
                  groupRows(g).map((row) => renderRow(row, `${g.key}:`))}
              </React.Fragment>
            ))
          : rows.map((row) => renderRow(row))}
      </TableBody>
    </Table>
  );
}
CompareTable.propTypes = {
  runs: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string,
      ordinal: PropTypes.number,
      colorIndex: PropTypes.number,
      isBaseline: PropTypes.bool,
    }),
  ).isRequired,
  rows: PropTypes.array.isRequired,
  groups: PropTypes.array,
  columns: PropTypes.instanceOf(Set).isRequired,
  evals: PropTypes.array.isRequired,
  subGoalEvals: PropTypes.array,
  showDiff: PropTypes.bool,
  onOpenRow: PropTypes.func,
};

// Memoized: the page re-renders on every runs list poll, which this table
// does not read.
export default memo(CompareTable);
