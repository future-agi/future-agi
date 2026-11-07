import PropTypes from "prop-types";
import { Box, Checkbox, Stack, Typography } from "@mui/material";
import { DataTable } from "src/components/data-table";
import StatusBadge from "src/components/status-badge";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { formatBytes } from "src/sections/skills/utils/format";

const STATUS = {
  new: { status: "pending", label: "New" },
  changed: { status: "warning", label: "Changed" },
  unchanged: { status: "success", label: "Up to date" },
  invalid: { status: "failed", label: "Invalid" },
};

export default function ImportPreviewTable({ bundles, selected, onToggle }) {
  const columns = [
    { id: "selection", header: "Select", size: 64, cell: ({ row: { original: bundle } }) => (
      <Checkbox size="small" checked={selected.includes(bundle.key)}
        disabled={bundle.status === "invalid" || bundle.checks.summary.errors > 0}
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
  return <Box sx={{ height: bundles.length * ROW_HEIGHT.twoLine + TABLE_HEADER_HEIGHT.default, minHeight: 120, flexShrink: 0 }}><DataTable columns={columns} data={bundles} rowCount={bundles.length} getRowId={(bundle) => bundle.key} rowHeight={ROW_HEIGHT.twoLine} /></Box>;
}

ImportPreviewTable.propTypes = {
  bundles: PropTypes.arrayOf(PropTypes.object).isRequired,
  selected: PropTypes.arrayOf(PropTypes.string).isRequired,
  onToggle: PropTypes.func.isRequired,
};
