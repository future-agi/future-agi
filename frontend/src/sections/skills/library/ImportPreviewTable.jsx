import PropTypes from "prop-types";
import { Checkbox, Stack, Table, TableHead, TableBody, TableRow, TableCell, TableContainer, Typography } from "@mui/material";
import { stackedTableSx } from "../components/responsiveTable";
import StatusBadge from "src/components/status-badge";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { formatBytes } from "src/sections/skills/utils/format";

const STATUS = {
  new: { status: "pending", label: "New" },
  changed: { status: "warning", label: "Changed" },
  unchanged: { status: "success", label: "Up to date" },
  invalid: { status: "failed", label: "Invalid" },
};

export default function ImportPreviewTable({ bundles, selected, onToggle, disabled = false }) {
  const columns = [
    { id: "selection", header: "Select", size: 64, cell: ({ row: { original: bundle } }) => (
      <Checkbox size="small" checked={selected.includes(bundle.key)}
        disabled={disabled || bundle.status === "invalid" || bundle.checks.summary.errors > 0}
        inputProps={{ "aria-label": `Select ${bundle.name}` }} onChange={() => onToggle(bundle.key)} />
    ) },
    { accessorKey: "name", header: "Name", minSize: 160, meta: { flex: 1 }, cell: ({ row: { original: bundle } }) => (
      <Stack sx={{ minWidth: 0 }}>
        <Typography variant="s1" noWrap sx={{ fontFamily: "monospace" }}>{bundle.name}</Typography>
        <Typography variant="s3" color="text.secondary" noWrap>{bundle.root || "Root folder"}</Typography>
      </Stack>
    ) },
    { accessorKey: "fileCount", header: "Files", size: 64 },
    { accessorKey: "totalBytes", header: "Size", size: 88, cell: ({ getValue }) => formatBytes(getValue()) },
    { accessorKey: "status", header: "Status", size: 112, cell: ({ getValue }) => (
      <StatusBadge {...(STATUS[getValue()] || { status: "unknown", label: getValue() })} />
    ) },
    { id: "checks", header: "Checks", minSize: 170, cell: ({ row: { original: bundle } }) => (
      <Typography variant="s3" color="text.secondary">
        {bundle.checks.summary.errors} errors · {bundle.checks.summary.warnings} warnings
      </Typography>
    ) },
  ].map((column) => ({ ...column, enableSorting: false }));
  return <TableContainer><Table size="small" aria-label="Import preview" sx={[stackedTableSx, { "& tbody td": { overflowWrap: "normal", wordBreak: "normal" }, "& tbody td:not(.MuiTableCell-paddingCheckbox)": { display: { xs: "block", sm: "table-cell" } }, "& tbody td[data-label]:not(.MuiTableCell-paddingCheckbox)::before": { content: { xs: 'attr(data-label) ": "', sm: "none" }, typography: "s3", color: "text.secondary" } }]}>
    <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>{columns.map((column) => <TableCell key={column.id || column.accessorKey}>{column.header}</TableCell>)}</TableRow></TableHead>
    <TableBody>{bundles.map((bundle) => <TableRow hover key={bundle.key} sx={{ height: ROW_HEIGHT.twoLine }}>
      {columns.map((column) => <TableCell padding={column.id === "selection" ? "checkbox" : "normal"} data-label={column.header} key={column.id || column.accessorKey}>{column.cell ? column.cell({ row: { original: bundle }, getValue: () => bundle[column.accessorKey] }) : bundle[column.accessorKey]}</TableCell>)}
    </TableRow>)}</TableBody>
  </Table></TableContainer>;
}

ImportPreviewTable.propTypes = {
  bundles: PropTypes.arrayOf(PropTypes.object).isRequired,
  selected: PropTypes.arrayOf(PropTypes.string).isRequired,
  onToggle: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};
