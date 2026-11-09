// W5-5 (W5-SCOPE §7 C2): one leaderboard's ranked rows. Every number is the
// server's (R1 aggregates through the leaderboard rule); this only formats.
import React from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import {
  Box,
  Link,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TableSortLabel,
  Typography,
} from "@mui/material";
import { formatValue } from "src/sections/common/Metrics/metricFormat";
import TagChips from "src/sections/common/Tags/TagChips";

const experimentHref = (row) =>
  `/dashboard/develop/experiment/${row.experiment_id}/data?run=${row.run_id}`;

function BoardRow({ r, columns, tagsByName, testId, muted = false }) {
  return (
    <TableRow
      key={r.run_id}
      data-testid={testId}
      data-run-id={r.run_id}
      hover
      sx={muted ? { opacity: 0.7 } : undefined}
    >
      <TableCell>
        <Typography variant="body2" fontWeight={600}>
          {r.rank ?? "—"}
        </Typography>
      </TableCell>
      <TableCell>
        <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
          <Link
            component={RouterLink}
            to={experimentHref(r)}
            underline="hover"
            variant="body2"
          >
            {[r.label, r.name].filter(Boolean).join(" ")}
          </Link>
          <TagChips
            tags={(r.tags ?? []).map(
              (n) => tagsByName?.[n] ?? { id: n, name: n },
            )}
            max={3}
          />
        </Stack>
        {r.experiment_name && (
          <Typography variant="caption" color="text.secondary">
            {r.experiment_name}
          </Typography>
        )}
      </TableCell>
      <TableCell>
        <Typography variant="body2">{r.dataset}</Typography>
      </TableCell>
      {columns.map((c) => (
        <TableCell key={c.key} align="right" data-col={c.key}>
          <Typography
            variant="body2"
            sx={{ fontVariantNumeric: "tabular-nums" }}
          >
            {formatValue(c, r.values?.[c.key])}
          </Typography>
          {r.n?.[c.key] != null && (
            <Typography variant="caption" color="text.secondary">
              {`n ${r.n[c.key]}`}
            </Typography>
          )}
        </TableCell>
      ))}
    </TableRow>
  );
}

BoardRow.propTypes = {
  r: PropTypes.object.isRequired,
  columns: PropTypes.array.isRequired,
  tagsByName: PropTypes.object,
  testId: PropTypes.string.isRequired,
  muted: PropTypes.bool,
};

export default function LeaderboardTable({ board, tagsByName, onSort }) {
  const { columns = [], rows = [], unranked = [], definition } = board;
  const sort = definition?.sort ?? {};
  const sortLabel = columns.find((c) => c.key === sort.column)?.label;
  return (
    <Box
      sx={{
        overflowX: "auto",
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      <Table size="small" data-testid="leaderboard-table">
        <TableHead>
          <TableRow>
            <TableCell sx={{ width: 48 }}>#</TableCell>
            <TableCell>Run</TableCell>
            <TableCell>Dataset</TableCell>
            {columns.map((c) => (
              <TableCell
                key={c.key}
                align="right"
                sortDirection={sort.column === c.key ? sort.dir : false}
              >
                <TableSortLabel
                  active={sort.column === c.key}
                  direction={sort.column === c.key ? sort.dir : "desc"}
                  onClick={() =>
                    onSort?.({
                      column: c.key,
                      dir:
                        sort.column === c.key && sort.dir === "desc"
                          ? "asc"
                          : "desc",
                    })
                  }
                >
                  {c.label}
                </TableSortLabel>
              </TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((r) => (
            <BoardRow
              key={r.run_id}
              r={r}
              columns={columns}
              tagsByName={tagsByName}
              testId="leaderboard-row"
            />
          ))}
          {unranked.length > 0 && (
            <TableRow>
              <TableCell
                colSpan={3 + columns.length}
                sx={{ bgcolor: "action.hover", py: 0.75 }}
              >
                <Typography variant="caption" color="text.secondary">
                  {`Not ranked · no ${sortLabel ?? "value"} value (${unranked.length})`}
                </Typography>
              </TableCell>
            </TableRow>
          )}
          {unranked.map((r) => (
            <BoardRow
              key={r.run_id}
              r={r}
              columns={columns}
              tagsByName={tagsByName}
              testId="leaderboard-row-unranked"
              muted
            />
          ))}
          {!rows.length && !unranked.length && (
            <TableRow>
              <TableCell colSpan={3 + columns.length}>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ py: 2 }}
                >
                  No finished run on these datasets matches yet. A run that
                  matches shows up here the next time the board is read.
                </Typography>
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </Box>
  );
}

LeaderboardTable.propTypes = {
  board: PropTypes.object.isRequired,
  tagsByName: PropTypes.object,
  onSort: PropTypes.func,
};
