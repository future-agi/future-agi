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
  OwnerCell,
  RunsCell,
} from "./environmentTableCells";

// No column sorts: the list API takes no ordering and always returns newest
// first, so a sort arrow would promise something the server can't do.
// Rows arrive pre-flattened (see harnessEnvToRow), so column accessors stay
// simple. Vel's column set: the description is the Name cell's second line.
// A field the API doesn't send yet draws a "Coming soon" chip; one it sends
// as null draws a dash.
export function buildEnvironmentColumns({ onRowActions }) {
  return [
    {
      id: "name",
      accessorKey: "name",
      header: "Name",
      enableSorting: false,
      meta: { flex: 1 },
      // Room for a readable name beside its version chip.
      minSize: 280,
      cell: NameCell,
    },
    {
      id: "status",
      accessorKey: "status",
      header: "Status",
      enableSorting: false,
      size: 100,
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
      size: 100,
      cell: AgentTypeCell,
    },
    {
      id: "agent",
      accessorKey: "agent",
      header: "Agent",
      size: 140,
      enableSorting: false,
      cell: AgentCell,
    },
    {
      id: "lastRun",
      accessorKey: "lastRun",
      header: "Last run",
      size: 124,
      enableSorting: false,
      cell: LastRunCell,
    },
    {
      id: "tools",
      accessorKey: "tools",
      header: "Tools",
      enableSorting: false,
      size: 58,
      cell: NumberCell,
    },
    {
      id: "scenarios",
      accessorKey: "scenarios",
      header: "Scenarios",
      enableSorting: false,
      size: 84,
      cell: NumberCell,
    },
    {
      id: "subgoals",
      accessorKey: "subgoals",
      header: "Sub-goals",
      enableSorting: false,
      size: 84,
      cell: NumberCell,
    },
    {
      id: "runs",
      accessorKey: "runsTotal",
      header: "Runs",
      enableSorting: false,
      size: 56,
      cell: RunsCell,
    },
    {
      id: "owner",
      accessorKey: "owner",
      header: "Owner",
      // Wide enough for the Coming soon chip until the API sends owners.
      size: 130,
      enableSorting: false,
      cell: OwnerCell,
    },
    {
      id: "updated",
      accessorKey: "updatedAt",
      header: "Updated",
      enableSorting: false,
      size: 100,
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
