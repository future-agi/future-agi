import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Checkbox,
  Stack,
  Typography,
  Table,
  TableHead,
  TableBody,
  TableRow,
  TableCell,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { fDateTime, formatDuration } from "src/utils/format-time";
import SoonChip from "../../../components/SoonChip";
import { runColor } from "../runs.constants";
import StatusChip from "../StatusChip";
import StopRunControl from "../StopRunControl";
import AgentDiffersTag from "../../run/AgentDiffersTag";
import {
  deltaAgainst,
  evalMetric,
  FAILED_METRIC,
  PASS_METRIC,
  RUN_TIME_METRIC,
} from "./summaryData";

// The dashed cell a run with no value for a column shows.
const DASH = "-";
// Room for a change beside each number once a baseline is set, so the
// numbers keep one right edge.
const DELTA_W = 56;

// Vel's columns in Vel's order. `soon` columns have no field on the runs
// list: the header carries the soon chip and the cells stay empty.
const COLUMNS = [
  {
    id: "scenarioPass",
    soon: {
      label: "Scenario pass",
      what: "A scenario pass rate (scenarios passing most of their repeats)",
    },
  },
  { id: "callPass", label: "Call pass", tip: "Every call counted" },
  {
    id: "flaky",
    soon: { label: "Flaky", what: "Flaky scenario counts", plural: true },
  },
  { id: "failing", label: "Failing", tip: "Calls that failed" },
  { id: "calls", label: "Calls", tip: "Scenarios × repeats" },
  { id: "runTime", label: "Run time", tip: "Start to finish" },
  {
    id: "startedBy",
    soon: { label: "Started by", what: "Who started a run" },
  },
  {
    id: "duration",
    soon: { label: "Duration", what: "The average duration per call" },
  },
  {
    id: "tokens",
    soon: { label: "Tokens", what: "Token counts", plural: true },
  },
  { id: "cost", soon: { label: "Cost", what: "A run's cost" } },
  {
    id: "saidNotDone",
    soon: {
      label: "Said, not done",
      what: "Counts of calls where the agent said it was done but wasn't",
      plural: true,
    },
  },
  { id: "return", soon: { label: "Return", what: "The mean return" } },
];
const SOON_AFTER_RUN_TIME = COLUMNS.slice(
  COLUMNS.findIndex((c) => c.id === "runTime") + 1,
);

// The runs table in Vel's columns. Each row ticks for Compare, Set as
// baseline and Delete; clicking the rest of the row opens the run. With a
// baseline every number carries its change against it.
export default function SummaryTable({
  rows,
  evals,
  onOpenRun,
  selectedIds = [],
  onToggle,
  baseline = null,
}) {
  return (
    // Clips the table to the card's bottom corners; a scroll box can't clip
    // its own scrollbar, hence the wrapper.
    <Box
      sx={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        borderBottomLeftRadius: 12,
        borderBottomRightRadius: 12,
      }}
    >
      {/* Takes the card's remaining height and scrolls both ways under a
        pinned header. */}
      <Box sx={{ overflow: "auto", flex: 1, minHeight: 0 }}>
        <Table stickyHeader size="small" sx={{ minWidth: 1280 }}>
          <TableHead>
            <TableRow
              sx={{
                "& th": {
                  border: 0,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  py: 1,
                  typography: "s3",
                  fontWeight: "fontWeightSemiBold",
                  color: "text.subtitle",
                  whiteSpace: "nowrap",
                },
              }}
            >
              <TableCell padding="checkbox" />
              <TableCell>Run</TableCell>
              <TableCell>Status</TableCell>
              {COLUMNS.map((c) => (
                <TableCell key={c.id} align="right">
                  {c.soon ? (
                    <SoonChip
                      label={c.soon.label}
                      what={c.soon.what}
                      plural={c.soon.plural}
                      size="tiny"
                    />
                  ) : (
                    <CustomTooltip show arrow size="small" title={c.tip}>
                      <span>{c.label}</span>
                    </CustomTooltip>
                  )}
                </TableCell>
              ))}
              {evals.map((e, i) => (
                <TableCell
                  key={e.id}
                  align="right"
                  sx={i === 0 ? { borderLeft: "1px solid" } : undefined}
                >
                  {e.name}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((r) => (
              <SummaryRow
                key={r.id}
                row={r}
                evals={evals}
                onOpenRun={onOpenRun}
                picked={selectedIds.includes(r.id)}
                onToggle={onToggle}
                baseline={baseline}
              />
            ))}
          </TableBody>
        </Table>
      </Box>
    </Box>
  );
}

SummaryTable.propTypes = {
  rows: PropTypes.arrayOf(PropTypes.object).isRequired,
  evals: PropTypes.arrayOf(
    PropTypes.shape({ id: PropTypes.string, name: PropTypes.string }),
  ).isRequired,
  onOpenRun: PropTypes.func,
  selectedIds: PropTypes.arrayOf(PropTypes.string),
  onToggle: PropTypes.func,
  // The summary row every other row is read against.
  baseline: PropTypes.object,
};

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

function SummaryRow({ row, evals, onOpenRun, picked, onToggle, baseline }) {
  const color = runColor(row.ordinal);
  const clickable = !!row.executionId;
  const open = () => clickable && onOpenRun?.(row);
  const isBaseline = baseline?.id === row.id;
  // With a baseline, every number reserves room for its change.
  const deltaW = baseline ? DELTA_W : 0;
  const against = (metric) => deltaAgainst(metric, row, baseline);
  const toggle = (e) => {
    e.stopPropagation();
    onToggle?.(row);
  };

  // The run's own agent version (per-execution, real). A run pinned to an
  // environment version whose agent differs is tagged; its versions show on
  // the run's own page.
  const sub = row.agentVersion ? `agent ${row.agentVersion}` : "";
  const scenarios =
    row.scenarioCount == null
      ? `${DASH} scenarios`
      : plural(row.scenarioCount, "scenario", "scenarios");
  const trials = row.trials ?? 1;

  return (
    <TableRow
      hover={clickable}
      onClick={open}
      selected={picked}
      sx={{
        cursor: clickable ? "pointer" : "default",
        "& td": {
          border: 0,
          borderBottom: "1px solid",
          borderColor: "divider",
          py: 0.875,
          whiteSpace: "nowrap",
        },
        // The baseline is marked by a rule, not a filled row, so the numbers
        // stay the thing people read.
        "& td:first-of-type": {
          boxShadow: (t) =>
            isBaseline ? `inset 2px 0 0 ${t.palette.primary.main}` : "none",
        },
      }}
    >
      {/* The whole cell ticks; a click here never opens the run. */}
      <TableCell padding="checkbox" onClick={toggle}>
        <Checkbox
          size="small"
          checked={picked}
          onClick={(e) => e.stopPropagation()}
          onChange={() => onToggle?.(row)}
          inputProps={{ "aria-label": `Select ${row.label}` }}
          sx={{ p: 0.5 }}
        />
      </TableCell>
      <TableCell>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.25}
          sx={{ minWidth: 0 }}
        >
          <Box
            sx={{
              minWidth: 26,
              height: 22,
              px: 0.75,
              borderRadius: "4px",
              flexShrink: 0,
              display: "grid",
              placeItems: "center",
              bgcolor: (t) =>
                alpha(color, t.palette.mode === "dark" ? 0.22 : 0.14),
              color,
              typography: "s3",
              fontWeight: "fontWeightSemiBold",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {row.ordinal}
          </Box>
          <Box minWidth={0}>
            <Stack direction="row" alignItems="center" spacing={0.75}>
              <Typography
                noWrap
                sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}
              >
                {row.label}
                {sub ? ` · ${sub}` : ""}
              </Typography>
              {row.agentDiffers && (
                <AgentDiffersTag
                  agentVersion={row.pinnedAgentVersion}
                  environmentVersion={row.environmentVersion}
                />
              )}
              {isBaseline && (
                <Typography
                  sx={{
                    px: 0.75,
                    py: 0.125,
                    borderRadius: "4px",
                    flexShrink: 0,
                    typography: "s3",
                    fontWeight: "fontWeightSemiBold",
                    color: "primary.main",
                    bgcolor: (t) => alpha(t.palette.primary.main, 0.1),
                  }}
                >
                  Baseline
                </Typography>
              )}
            </Stack>
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.subtitle" }}
            >
              {row.at ? fDateTime(row.at) : DASH}
            </Typography>
          </Box>
        </Stack>
      </TableCell>
      <TableCell>
        {/* Same shape as the product's "Run status" cell: the status, then Stop. */}
        <Stack direction="row" alignItems="center" spacing={1}>
          {row.runState ? <StatusChip status={row.runState} /> : DASH}
          <StopRunControl
            executionId={row.executionId}
            stoppable={row.stoppable}
          />
        </Stack>
      </TableCell>
      <TableCell />
      <NumCell
        value={`${row.passRate}%`}
        bold
        deltaW={deltaW}
        delta={against(PASS_METRIC)}
      />
      <TableCell />
      <NumCell
        value={row.failed ?? DASH}
        muted={!row.failed}
        deltaW={deltaW}
        delta={against(FAILED_METRIC)}
      />
      <NumCell
        deltaW={deltaW}
        value={
          <CustomTooltip
            show
            arrow
            size="small"
            title={`${scenarios} × ${plural(trials, "repeat", "repeats")}`}
          >
            <Box component="span" sx={{ cursor: "default" }}>
              {row.tasks}
            </Box>
          </CustomTooltip>
        }
      />
      <NumCell
        value={row.durationS != null ? formatDuration(row.durationS) : DASH}
        muted
        deltaW={deltaW}
        delta={against(RUN_TIME_METRIC)}
      />
      {SOON_AFTER_RUN_TIME.map((c) => (
        <TableCell key={c.id} />
      ))}
      {evals.map((e, i) => {
        const v = row.scores?.[e.id];
        return (
          <NumCell
            key={e.id}
            value={v == null ? DASH : `${Math.round(v)}%`}
            muted={v == null}
            divider={i === 0}
            deltaW={deltaW}
            delta={against(evalMetric(e))}
          />
        );
      })}
    </TableRow>
  );
}

SummaryRow.propTypes = {
  row: PropTypes.object.isRequired,
  evals: PropTypes.array.isRequired,
  onOpenRun: PropTypes.func,
  picked: PropTypes.bool,
  onToggle: PropTypes.func,
  baseline: PropTypes.object,
};

function NumCell({ value, bold, muted, divider, deltaW = 0, delta }) {
  return (
    <TableCell
      align="right"
      sx={{
        typography: "s2",
        fontWeight: bold ? "fontWeightSemiBold" : "fontWeightMedium",
        color: muted ? "text.subtitle" : "text.primary",
        fontVariantNumeric: "tabular-nums",
        ...(divider && { borderLeft: "1px solid" }),
      }}
    >
      {deltaW ? (
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: `1fr ${deltaW}px`,
            alignItems: "center",
            columnGap: 0.75,
          }}
        >
          <Box sx={{ textAlign: "right" }}>{value}</Box>
          <Delta delta={delta} />
        </Box>
      ) : (
        value
      )}
    </TableCell>
  );
}

NumCell.propTypes = {
  value: PropTypes.node,
  bold: PropTypes.bool,
  muted: PropTypes.bool,
  divider: PropTypes.bool,
  deltaW: PropTypes.number,
  delta: PropTypes.object,
};

// The arrow says which way the number moved; the colour says whether that was
// good, so a faster run shows a green down arrow. No change renders nothing.
function Delta({ delta }) {
  if (!delta || delta.flat) return <Box />;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.125}
      aria-label={delta.label ?? delta.text}
      sx={{
        minWidth: 0,
        color: delta.better ? "success.main" : "error.main",
      }}
    >
      <Iconify
        icon={delta.up ? "solar:arrow-up-linear" : "solar:arrow-down-linear"}
        width={12}
        sx={{ flexShrink: 0 }}
      />
      <Typography
        noWrap
        sx={{
          typography: "s3",
          fontWeight: "fontWeightSemiBold",
          color: "inherit",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {delta.text}
      </Typography>
    </Stack>
  );
}

Delta.propTypes = { delta: PropTypes.object };
