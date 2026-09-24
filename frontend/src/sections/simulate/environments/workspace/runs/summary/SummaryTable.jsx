import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Checkbox, Table, TableHead, TableBody,
  TableRow, TableCell,
} from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { fDateTime, formatDuration } from "src/utils/format-time";
import { runColor } from "../runs.constants";
import ColumnDummyTag from "./ColumnDummyTag";

// The dashed placeholder a not-yet-backed cell shows.
const DASH = "—";

// The run comparison table. Real columns (pass, avg duration) plus the derived
// eval columns render live values; the columns with no backend field show a
// dashed cell under a "Dummy"-tagged header. Selecting runs to compare is a
// later phase, so the checkboxes are present (for parity with the design) but
// disabled behind a "coming soon" tooltip.
export default function SummaryTable({ rows, evals, envVersion, onOpenRun }) {
  return (
    <Box sx={{ overflowX: "auto" }}>
      <Table size="small" sx={{ minWidth: 720 }}>
        <TableHead>
          <TableRow sx={{ "& th": { border: 0, py: 1, typography: "s3", color: "text.subtitle", whiteSpace: "nowrap" } }}>
            <TableCell padding="checkbox">
              <CustomTooltip show arrow size="small" title="Comparing runs is coming soon">
                <span>
                  <Checkbox size="small" disabled sx={{ p: 0.5 }} />
                </span>
              </CustomTooltip>
            </TableCell>
            <TableCell>Run</TableCell>
            <TableCell align="right">Scenarios</TableCell>
            <TableCell align="right">Trials</TableCell>
            <TableCell align="right">Simulations</TableCell>
            <TableCell align="right">Pass</TableCell>
            <TableCell align="right">Avg duration</TableCell>
            <TableCell align="right">Tokens<ColumnDummyTag /></TableCell>
            <TableCell align="right">Cost<ColumnDummyTag /></TableCell>
            <TableCell align="right">Said not done<ColumnDummyTag /></TableCell>
            <TableCell align="right">Mean return<ColumnDummyTag /></TableCell>
            {evals.map((e) => (
              <TableCell key={e.id} align="right">{e.name}</TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((r) => (
            <SummaryRow key={r.id} row={r} evals={evals} envVersion={envVersion} onOpenRun={onOpenRun} />
          ))}
        </TableBody>
      </Table>
    </Box>
  );
}

SummaryTable.propTypes = {
  rows: PropTypes.arrayOf(PropTypes.object).isRequired,
  evals: PropTypes.arrayOf(PropTypes.shape({ id: PropTypes.string, name: PropTypes.string })).isRequired,
  envVersion: PropTypes.string,
  onOpenRun: PropTypes.func,
};

function SummaryRow({ row, evals, envVersion, onOpenRun }) {
  const color = runColor(row.ordinal);
  const clickable = !!row.executionId;
  const open = () => clickable && onOpenRun?.(row);

  const sub = [
    row.agentVersion ? `agent ${row.agentVersion}` : null,
    envVersion ? `env ${envVersion}` : null,
  ].filter(Boolean).join(" × ");

  return (
    <TableRow
      hover={clickable}
      onClick={open}
      sx={{
        cursor: clickable ? "pointer" : "default",
        "& td": { border: 0, borderTop: "1px solid", borderColor: "divider", py: 1.25, whiteSpace: "nowrap" },
      }}
    >
      <TableCell padding="checkbox">
        <CustomTooltip show arrow size="small" title="Comparing runs is coming soon">
          <span>
            <Checkbox size="small" disabled sx={{ p: 0.5 }} onClick={(e) => e.stopPropagation()} />
          </span>
        </CustomTooltip>
      </TableCell>

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
            <Typography noWrap sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>
              {row.label}{sub ? ` · ${sub}` : ""}
            </Typography>
            <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
              {row.at ? fDateTime(row.at) : DASH}
            </Typography>
          </Box>
        </Stack>
      </TableCell>
      <NumCell value={row.scenarioCount ?? DASH} />
      <NumCell value={row.trials ?? 1} />
      <NumCell value={row.tasks} />

      <NumCell value={`${row.passRate}%`} bold />
      <NumCell value={row.durationS != null ? formatDuration(row.durationS) : DASH} />
      <NumCell value={DASH} muted />
      <NumCell value={DASH} muted />
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
  envVersion: PropTypes.string,
  onOpenRun: PropTypes.func,
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
