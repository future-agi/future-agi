import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Checkbox, Table, TableHead, TableBody,
  TableRow, TableCell,
} from "@mui/material";
import { fDateTime, formatDuration } from "src/utils/format-time";
import CustomTooltip from "src/components/tooltip";
import { runColor } from "../runs.constants";
import StatusChip from "../StatusChip";
import StopRunControl from "../StopRunControl";
import AgentDiffersTag from "../../run/AgentDiffersTag";
import { neutralCheckboxSx } from "../detail/trace/traceTable.constants";
import { COMPARE_COPY } from "../../workspace.constants";

// The dashed cell a run with no value for a column shows.
const DASH = "-";

// The run comparison table. Real columns (pass, avg duration) plus the derived
// eval columns render live values; Tokens and Cost have no field on the runs
// list and show a plain dashed cell. With `onToggleSelect`, each row leads with
// a checkbox for picking runs to compare.
export default function SummaryTable({ rows, evals, onOpenRun, selectedIds = [], canSelect, onToggleSelect }) {
  return (
    // The card no longer clips (the graph tooltip must escape it), so clip the
    // table to the card's bottom corners here; a scroll box can't clip its own
    // scrollbar, hence the wrapper.
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
      <Table stickyHeader size="small" sx={{ minWidth: 720 }}>
        <TableHead>
          <TableRow sx={{ "& th": { border: 0, py: 1, typography: "s3", color: "text.subtitle", whiteSpace: "nowrap" } }}>
            {onToggleSelect && <TableCell padding="checkbox" />}
            <TableCell>Run</TableCell>
            <TableCell>Status</TableCell>
            <TableCell align="right">Scenarios</TableCell>
            <TableCell align="right">Trials</TableCell>
            <TableCell align="right">Simulations</TableCell>
            <TableCell align="right">Pass</TableCell>
            <TableCell align="right">Duration</TableCell>
            <TableCell align="right">Tokens</TableCell>
            <TableCell align="right">Cost</TableCell>
            {evals.map((e) => (
              <TableCell key={e.id} align="right">{e.name}</TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((r) => (
            <SummaryRow
              key={r.id} row={r} evals={evals} onOpenRun={onOpenRun}
              selected={selectedIds.includes(r.executionId)}
              selectable={!!canSelect?.(r)}
              onToggleSelect={onToggleSelect}
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
  evals: PropTypes.arrayOf(PropTypes.shape({ id: PropTypes.string, name: PropTypes.string })).isRequired,
  onOpenRun: PropTypes.func,
  selectedIds: PropTypes.arrayOf(PropTypes.string),
  canSelect: PropTypes.func,
  onToggleSelect: PropTypes.func,
};

function SummaryRow({ row, evals, onOpenRun, selected, selectable, onToggleSelect }) {
  const color = runColor(row.ordinal);
  const clickable = !!row.executionId;
  const open = () => clickable && onOpenRun?.(row);

  // The run's own agent version (per-execution, real). A run pinned to an
  // environment version whose agent differs is tagged; its versions show on
  // the run's own page.
  const sub = row.agentVersion ? `agent ${row.agentVersion}` : "";

  return (
    <TableRow
      hover={clickable}
      onClick={open}
      sx={{
        cursor: clickable ? "pointer" : "default",
        "& td": { border: 0, borderTop: "1px solid", borderColor: "divider", py: 1.25, whiteSpace: "nowrap" },
      }}
    >
      {onToggleSelect && (
        <TableCell padding="checkbox" onClick={(e) => e.stopPropagation()}>
          {/* A disabled box says why: the run isn't finished, or two are picked. */}
          <CustomTooltip
            show={!selected && !selectable} arrow size="small"
            title={row.executionId && row.runState === "finished" ? COMPARE_COPY.atCap : COMPARE_COPY.onlyFinished}
          >
            <span>
              <Checkbox
                size="small" checked={selected} disabled={!selected && !selectable}
                onChange={() => onToggleSelect(row)}
                inputProps={{ "aria-label": COMPARE_COPY.selectRun(row.label) }}
                sx={{ p: 0.5, ...neutralCheckboxSx }}
              />
            </span>
          </CustomTooltip>
        </TableCell>
      )}
      <TableCell>
        <Stack direction="row" alignItems="center" spacing={1.25} sx={{ minWidth: 0 }}>
          <Box
            sx={{
              width: 22, height: 22, borderRadius: 0.75, flexShrink: 0,
              display: "grid", placeItems: "center",
              bgcolor: (t) => alpha(color, t.palette.mode === "dark" ? 0.2 : 0.12),
              color, typography: "s3", fontWeight: "fontWeightBold",
            }}
          >
            {row.ordinal}
          </Box>
          <Box minWidth={0}>
            <Stack direction="row" alignItems="center" spacing={0.75} sx={{ minWidth: 0 }}>
              <Typography noWrap sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>
                {row.label}{sub ? ` · ${sub}` : ""}
              </Typography>
              {row.agentDiffers && (
                <AgentDiffersTag
                  agentVersion={row.pinnedAgentVersion}
                  environmentVersion={row.environmentVersion}
                />
              )}
            </Stack>
            <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
              {row.at ? fDateTime(row.at) : DASH}
            </Typography>
          </Box>
        </Stack>
      </TableCell>
      <TableCell>
        {/* Same shape as the product's "Run status" cell: the status, then Stop. */}
        <Stack direction="row" alignItems="center" spacing={1}>
          {row.runState ? <StatusChip status={row.runState} /> : DASH}
          <StopRunControl executionId={row.executionId} stoppable={row.stoppable} />
        </Stack>
      </TableCell>
      <NumCell value={row.scenarioCount ?? DASH} />
      <NumCell value={row.trials ?? 1} />
      <NumCell value={row.tasks} />

      <NumCell value={`${row.passRate}%`} bold />
      <NumCell value={row.durationS != null ? formatDuration(row.durationS) : DASH} />
      <NumCell value={DASH} muted />
      <NumCell value={DASH} muted />
      {evals.map((e) => {
        const v = row.scores?.[e.id];
        return <NumCell key={e.id} value={v == null ? DASH : `${Math.round(v)}%`} muted={v == null} />;
      })}
    </TableRow>
  );
}

SummaryRow.propTypes = {
  row: PropTypes.object.isRequired,
  evals: PropTypes.array.isRequired,
  onOpenRun: PropTypes.func,
  selected: PropTypes.bool,
  selectable: PropTypes.bool,
  onToggleSelect: PropTypes.func,
};

function NumCell({ value, bold, muted }) {
  return (
    <TableCell
      align="right"
      sx={{
        typography: "s2",
        fontWeight: bold ? "fontWeightBold" : "fontWeightMedium",
        color: muted ? "text.subtitle" : "text.primary",
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {value}
    </TableCell>
  );
}

NumCell.propTypes = {
  value: PropTypes.node,
  bold: PropTypes.bool,
  muted: PropTypes.bool,
};
