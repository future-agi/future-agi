import { Typography, IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import { relativeTime } from "src/utils/format-time";
import StatusPill from "./StatusPill";
import RunsPill from "./RunsPill";
import { NumberCell, AgentTypeCell } from "./environmentTableCells";
import DummyHeaderLabel from "./DummyHeaderLabel";

// Rows arrive pre-flattened (see harnessJobToRow), so column accessors stay
// simple. Columns the harness-jobs list has no field for render a placeholder
// cell and a "dummy" header pill until the real endpoint lands.
export function buildEnvironmentColumns({ onRowActions }) {
  return [
    {
      id: "name",
      accessorKey: "name",
      header: "Name",
      meta: { flex: 1 },
      minSize: 180,
      cell: ({ getValue }) => (
        <Typography noWrap sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>
          {getValue()}
        </Typography>
      ),
    },
    {
      id: "description",
      accessorKey: "description",
      header: "Description",
      renderHeader: () => <DummyHeaderLabel label="Description" />,
      meta: { flex: 1.6 },
      minSize: 240,
      enableSorting: false,
      cell: ({ getValue }) => (
        <Typography noWrap sx={{ typography: "s2", color: "text.secondary" }}>
          {getValue() || "—"}
        </Typography>
      ),
    },
    {
      id: "status",
      accessorKey: "status",
      header: "Status",
      size: 130,
      cell: ({ getValue, row }) => (
        <StatusPill status={getValue()} progress={row?.original?.buildProgress} />
      ),
    },
    {
      id: "agentType",
      accessorKey: "agentType",
      header: "Agent type",
      size: 180,
      cell: AgentTypeCell,
    },
    {
      id: "tools",
      accessorKey: "tools",
      header: "Tools",
      renderHeader: () => <DummyHeaderLabel label="Tools" />,
      size: 128,
      cell: NumberCell,
    },
    {
      id: "scenarios",
      accessorKey: "scenarios",
      header: "Scenarios",
      renderHeader: () => <DummyHeaderLabel label="Scenarios" />,
      size: 150,
      cell: NumberCell,
    },
    {
      id: "subgoals",
      accessorKey: "subgoals",
      header: "Sub-goals",
      renderHeader: () => <DummyHeaderLabel label="Sub-goals" />,
      size: 150,
      cell: NumberCell,
    },
    {
      id: "runs",
      accessorKey: "runsTotal",
      header: "Runs",
      renderHeader: () => <DummyHeaderLabel label="Runs" />,
      size: 128,
      cell: ({ row }) => <RunsPill total={row?.original?.runsTotal} />,
    },
    {
      id: "updated",
      accessorKey: "updatedAt",
      header: "Updated",
      size: 130,
      cell: ({ getValue }) => (
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          {relativeTime(getValue())}
        </Typography>
      ),
    },
    {
      id: "actions",
      accessorKey: "id",
      header: "",
      size: 56,
      enableSorting: false,
      cell: ({ row }) => (
        <IconButton
          size="small"
          aria-label="Row actions"
          onClick={(e) => {
            e.stopPropagation();
            onRowActions?.(row?.original, e.currentTarget);
          }}
          sx={{ color: "text.subtitle" }}
        >
          <Iconify icon="solar:menu-dots-bold" width={16} />
        </IconButton>
      ),
    },
  ];
}
