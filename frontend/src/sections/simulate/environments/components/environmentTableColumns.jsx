import { Typography, IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import { relativeTime } from "src/utils/format-time";
import StatusPill from "./StatusPill";
import { NumberCell, AgentTypeCell } from "./environmentTableCells";

// Rows arrive pre-flattened (see harnessJobToRow), so column accessors stay
// simple. Every column now maps to a real field on the §1 list contract
// (domain, sub_goals_count, runs_count included); a value the backend has not
// authored yet arrives null and its cell renders a dash.
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
      id: "domain",
      accessorKey: "domain",
      header: "Domain",
      size: 150,
      enableSorting: false,
      // §1 `domain` is verbatim from metadata.domain and is null whenever the
      // submitter sent none — render the blank placeholder rather than a guess.
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
      size: 128,
      cell: NumberCell,
    },
    {
      id: "scenarios",
      accessorKey: "scenarios",
      header: "Scenarios",
      size: 150,
      cell: NumberCell,
    },
    {
      id: "subgoals",
      accessorKey: "subgoals",
      header: "Sub-goals",
      size: 150,
      cell: NumberCell,
    },
    {
      id: "runs",
      accessorKey: "runsTotal",
      header: "Runs",
      size: 128,
      // §1 `runs_count` as a number (0/1 today; a null — still building or an
      // older row — renders as a dash, like the other count columns).
      cell: NumberCell,
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
