import { IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { relativeTime } from "src/utils/format-time";
import StatusPill from "./StatusPill";
import {
  NumberCell,
  AgentTypeCell,
  NameCell,
  AgentCell,
  LastRunCell,
  RunButtonCell,
} from "./environmentTableCells";

// No column sorts: the list API takes no ordering and always returns newest
// first, so a sort arrow would promise something the server can't do.
export function buildEnvironmentColumns({ onRowActions, onRun }) {
  return [
    {
      id: "name",
      accessorKey: "name",
      header: "Name",
      enableSorting: false,
      meta: { flex: 1 },
      minSize: 280,
      cell: NameCell,
    },
    {
      id: "status",
      accessorKey: "status",
      header: "Status",
      enableSorting: false,
      size: 156,
      cell: ({ getValue, row }) => (
        <StatusPill
          status={getValue()}
          progress={row?.original?.buildProgress}
        />
      ),
    },
    {
      id: "agentType",
      accessorKey: "agentType",
      header: "Agent type",
      enableSorting: false,
      size: 120,
      cell: AgentTypeCell,
    },
    {
      id: "agent",
      accessorKey: "agent",
      header: "Agent",
      enableSorting: false,
      size: 220,
      cell: AgentCell,
    },
    {
      id: "lastRun",
      accessorKey: "lastRun",
      header: "Last run",
      meta: { align: "right" },
      enableSorting: false,
      size: 100,
      cell: LastRunCell,
    },
    {
      id: "scenarios",
      accessorKey: "scenarios",
      header: "Scenarios",
      meta: { align: "right" },
      enableSorting: false,
      size: 84,
      cell: NumberCell,
    },
    {
      id: "subgoals",
      accessorKey: "subgoals",
      header: "Sub-goals",
      meta: { align: "right" },
      enableSorting: false,
      size: 84,
      cell: NumberCell,
    },
    {
      id: "runs",
      accessorKey: "runsTotal",
      header: "Runs",
      meta: { align: "right" },
      enableSorting: false,
      size: 64,
      cell: NumberCell,
    },
    {
      id: "updated",
      accessorKey: "updatedAt",
      header: "Updated",
      enableSorting: false,
      size: 100,
      cell: ({ getValue }) => (
        <Typography noWrap sx={{ typography: "s1", color: "text.secondary" }}>
          {relativeTime(getValue())}
        </Typography>
      ),
    },
    {
      id: "run",
      accessorKey: "id",
      header: "",
      size: 96,
      enableSorting: false,
      cell: (props) => <RunButtonCell {...props} onRun={onRun} />,
    },
    {
      id: "actions",
      accessorKey: "id",
      header: "",
      size: 48,
      enableSorting: false,
      cell: ({ row }) => {
        const env = row?.original;
        return (
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.5}
            sx={{ width: "100%", justifyContent: "flex-end" }}
          >
            <IconButton
              size="small"
              aria-label="Row actions"
              onClick={(e) => {
                e.stopPropagation();
                onRowActions?.(env, e.currentTarget);
              }}
              sx={{ color: "text.subtitle" }}
            >
              <Iconify icon="solar:menu-dots-bold" width={16} />
            </IconButton>
          </Stack>
        );
      },
    },
  ];
}
