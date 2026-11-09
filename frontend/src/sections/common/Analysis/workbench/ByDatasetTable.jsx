import React, { useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import {
  Box,
  Button,
  IconButton,
  Link,
  Menu,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { byCellText } from "./byDatasetModel";

const fmtDate = (iso) =>
  iso
    ? new Date(iso).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
      })
    : "";

/** `+1 run ▾`: the other runs of this column on the dataset, with dates. */
function MoreRuns({ cell, onPick }) {
  const [anchor, setAnchor] = useState(null);
  if (!cell?.others?.length) return null;
  return (
    <>
      <Button
        size="small"
        onClick={(e) => {
          e.stopPropagation();
          setAnchor(e.currentTarget);
        }}
        endIcon={<Iconify icon="mdi:chevron-down" width={14} />}
        sx={{ typography: "s3", minWidth: 0, px: 0.5, py: 0, ml: 0.5 }}
      >
        +{cell.others.length} {cell.others.length === 1 ? "run" : "runs"}
      </Button>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        onClick={(e) => e.stopPropagation()}
      >
        {[cell.run, ...cell.others].map((r) => (
          <MenuItem
            key={r.run_id}
            selected={r.run_id === cell.run.run_id}
            onClick={() => {
              setAnchor(null);
              onPick(r.run_id);
            }}
            sx={{ typography: "s1", gap: 1 }}
          >
            <Typography typography="s1" fontWeight={600}>
              {r.label}
            </Typography>
            <Typography typography="s1" sx={{ flex: 1 }} noWrap>
              {r.name}
            </Typography>
            <Typography typography="s3" color="text.secondary">
              {fmtDate(r.created_at)}
            </Typography>
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}

MoreRuns.propTypes = {
  cell: PropTypes.object,
  onPick: PropTypes.func.isRequired,
};

/**
 * By dataset (R2-SPEC §3.3.7): datasets × config columns, each cell that
 * run's value on the dataset's own headline measure. A cell (or row) opens
 * that dataset's compare with the row's runs.
 */
const ByDatasetTable = ({
  model,
  measureNames,
  sentence,
  onOpenRow,
  onPickRun,
  compareWith,
  onMatchCell,
  focusRunId,
}) => (
  <Box
    sx={{
      border: 1,
      borderColor: "divider",
      borderRadius: 1,
      bgcolor: "background.paper",
      overflow: "hidden",
    }}
  >
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="space-between"
      gap={1}
      sx={{ px: 1.5, py: 1, borderBottom: 1, borderColor: "divider" }}
    >
      <Typography typography="s1">{sentence}</Typography>
      {compareWith}
    </Stack>
    <Box sx={{ overflow: "auto" }}>
      <Table size="small" sx={{ "& td, & th": { borderColor: "divider" } }}>
        <TableHead>
          <TableRow>
            <TableCell sx={{ minWidth: 260 }}>
              <Typography typography="s2" color="text.secondary">
                Dataset · headline measure
              </Typography>
            </TableCell>
            {model.columns.map((c) => (
              <TableCell key={c.key} sx={{ minWidth: 170 }}>
                <Typography
                  typography="s2"
                  fontWeight="fontWeightSemiBold"
                  noWrap
                >
                  {c.label}
                </Typography>
              </TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {model.rows.map((row) => (
            <TableRow
              key={row.dataset.datasetId}
              hover
              onClick={() => onOpenRow(row)}
              sx={{ cursor: "pointer" }}
            >
              <TableCell>
                <Link
                  component={RouterLink}
                  to={`/dashboard/develop/${row.dataset.datasetId}`}
                  underline="hover"
                  typography="s1"
                  fontWeight={500}
                  onClick={(e) => e.stopPropagation()}
                >
                  {row.dataset.name}
                </Link>
                <Typography
                  typography="s3"
                  color="text.secondary"
                  component="span"
                >
                  {" · "}
                  {measureNames[row.dataset.datasetId] ?? "Headline measure"}
                </Typography>
              </TableCell>
              {model.columns.map((c) => {
                const cell = row.cells[c.key];
                if (!cell)
                  return (
                    <TableCell key={c.key}>
                      <CustomTooltip
                        show
                        title={`${c.label} never ran on this dataset.`}
                        size="small"
                        placement="top"
                        arrow
                      >
                        <Typography
                          typography="s1"
                          color="text.disabled"
                          component="span"
                        >
                          —
                        </Typography>
                      </CustomTooltip>
                    </TableCell>
                  );
                return (
                  <TableCell key={c.key}>
                    <Stack direction="row" alignItems="center">
                      <CustomTooltip
                        show
                        size="small"
                        placement="top"
                        arrow
                        title={`${cell.run.label} ${cell.run.name} · ${cell.n.toLocaleString("en-US")} rows · open the compare`}
                      >
                        <Typography
                          typography="s1"
                          sx={{ fontVariantNumeric: "tabular-nums" }}
                        >
                          {byCellText(cell, row.cells)}
                        </Typography>
                      </CustomTooltip>
                      <MoreRuns
                        cell={cell}
                        onPick={(runId) => onPickRun(row, c, runId)}
                      />
                      {onMatchCell && cell.run.run_id !== focusRunId && (
                        <CustomTooltip
                          show
                          size="small"
                          placement="top"
                          arrow
                          title={`Compare rows by key with ${cell.run.label} on ${row.dataset.name}`}
                        >
                          <IconButton
                            size="small"
                            aria-label={`Compare rows by key… ${row.dataset.name} ${cell.run.label}`}
                            data-testid="by-dataset-match"
                            onClick={(e) => {
                              e.stopPropagation();
                              onMatchCell(row, c, cell);
                            }}
                            sx={{ ml: 0.5 }}
                          >
                            <Iconify icon="mdi:key-link" width={16} />
                          </IconButton>
                        </CustomTooltip>
                      )}
                    </Stack>
                  </TableCell>
                );
              })}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  </Box>
);

ByDatasetTable.propTypes = {
  model: PropTypes.object.isRequired,
  measureNames: PropTypes.object.isRequired,
  sentence: PropTypes.node,
  onOpenRow: PropTypes.func.isRequired,
  onPickRun: PropTypes.func.isRequired,
  compareWith: PropTypes.node,
  onMatchCell: PropTypes.func,
  focusRunId: PropTypes.string,
};

export default ByDatasetTable;
