/* eslint-disable react/prop-types */
import { Box, Button, Chip, Stack, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { format } from "date-fns";
import PropTypes from "prop-types";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router";

import Iconify from "src/components/iconify";
import { DataTable, DataTablePagination } from "src/components/data-table";
import { useDebounce } from "src/hooks/use-debounce";
import axios, { endpoints } from "src/utils/axios";
import { formatDuration } from "src/utils/format-time";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";

import { AGENT_TYPES } from "src/sections/agents/constants";
import { SIMULATION_TYPE } from "src/components/run-tests/common";
import {
  statusStyles,
  STOPPABLE_STATUSES,
  TERMINAL_STATUSES,
  useCancelExecution,
} from "src/sections/common/simulation";

import { useTestDetailContext } from "../context/TestDetailContext";
import { useTestRunsGridStore } from "../states";
import { useTestRunsSearchStoreShallow } from "./states";

const POLL_INTERVAL_MS = 5000;

// ── Cell renderers ──

function DateCell({ getValue }) {
  const value = getValue();
  if (!value) {
    return (
      <Typography variant="body2" sx={{ fontSize: 13, color: "text.disabled" }}>
        —
      </Typography>
    );
  }
  try {
    return (
      <Typography variant="body2" sx={{ fontSize: 13 }}>
        {format(new Date(value), "yyyy-MM-dd HH:mm")}
      </Typography>
    );
  } catch {
    return null;
  }
}

function DurationCell({ getValue }) {
  const v = getValue();
  return (
    <Typography variant="body2" sx={{ fontSize: 13 }}>
      {v ? formatDuration(v) : "—"}
    </Typography>
  );
}

function PercentCell({ getValue }) {
  const v = getValue();
  return (
    <Typography variant="body2" sx={{ fontSize: 13 }}>
      {v == null ? "—" : `${v}%`}
    </Typography>
  );
}

function NumberCell({ getValue }) {
  const v = getValue();
  return (
    <Typography variant="body2" sx={{ fontSize: 13 }}>
      {v ?? 0}
    </Typography>
  );
}

function TextCell({ getValue }) {
  const v = getValue();
  return (
    <Typography variant="body2" noWrap sx={{ fontSize: 13 }}>
      {v || "—"}
    </Typography>
  );
}

function AgentDefinitionCell({ row }) {
  const { agent_definition: name, agent_version: version } = row.original;
  const label = name && version ? `${name} (${version})` : "—";
  return (
    <Typography variant="body2" noWrap sx={{ fontSize: 13 }}>
      {label}
    </Typography>
  );
}

function StatusCell({ getValue, row }) {
  const status = getValue();
  const showStop = STOPPABLE_STATUSES.includes(status);
  const { mutate: cancelExecution } = useCancelExecution();
  const { refreshTestRunGrid } = useTestDetailContext();

  const handleStop = (e) => {
    e.stopPropagation();
    cancelExecution(row.original.id, {
      onSuccess: () => refreshTestRunGrid?.(),
    });
  };

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
      <Chip
        variant="soft"
        label={status || "—"}
        size="small"
        sx={{
          typography: "s3",
          fontWeight: "fontWeightRegular",
          pointerEvents: "none",
          ...(statusStyles[status] || {}),
        }}
      />
      {showStop && (
        <Button
          variant="outlined"
          size="small"
          onClick={handleStop}
          sx={{
            borderRadius: "4px",
            px: 1.5,
            minWidth: 0,
            height: 26,
            textTransform: "none",
            fontSize: 12,
          }}
          startIcon={<Iconify icon="bi:stop-circle" width={14} />}
        >
          Stop
        </Button>
      )}
    </Box>
  );
}

// ── Column defs by type ──

function buildColumns(agentType, simulationType) {
  // Prompt simulations
  if (simulationType === SIMULATION_TYPE.PROMPT) {
    return [
      {
        id: "scenarios",
        accessorKey: "scenarios",
        header: "Scenario",
        meta: { flex: 1.5 },
        minSize: 180,
        enableSorting: false,
        cell: TextCell,
      },
      {
        id: "start_time",
        accessorKey: "start_time",
        header: "Run Start Time",
        size: 170,
        enableSorting: false,
        cell: DateCell,
      },
      {
        id: "agent_version",
        accessorKey: "agent_version",
        header: "Prompt Version",
        size: 130,
        enableSorting: false,
        cell: TextCell,
      },
      {
        id: "total_chats",
        accessorKey: "total_chats",
        header: "Total Runs",
        size: 110,
        enableSorting: false,
        cell: NumberCell,
      },
      {
        id: "success_rate",
        accessorKey: "success_rate",
        header: "% Completed",
        size: 130,
        enableSorting: false,
        cell: PercentCell,
      },
      {
        id: "status",
        accessorKey: "status",
        header: "Run Status",
        size: 180,
        enableSorting: false,
        cell: StatusCell,
      },
      {
        id: "duration",
        accessorKey: "duration",
        header: "Duration",
        size: 110,
        enableSorting: false,
        cell: DurationCell,
      },
    ];
  }

  // Chat simulations
  if (agentType === AGENT_TYPES.CHAT) {
    return [
      {
        id: "scenarios",
        accessorKey: "scenarios",
        header: "Scenario",
        meta: { flex: 1.5 },
        minSize: 180,
        enableSorting: false,
        cell: TextCell,
      },
      {
        id: "start_time",
        accessorKey: "start_time",
        header: "Run Start Time",
        size: 170,
        enableSorting: false,
        cell: DateCell,
      },
      {
        id: "total_chats",
        accessorKey: "total_chats",
        header: "Total Chats",
        size: 110,
        enableSorting: false,
        cell: NumberCell,
      },
      {
        id: "status",
        accessorKey: "status",
        header: "Run Status",
        size: 180,
        enableSorting: false,
        cell: StatusCell,
      },
      {
        id: "total_number_of_fagi_agent_turns",
        accessorKey: "total_number_of_fagi_agent_turns",
        header: "Total Turns",
        size: 110,
        enableSorting: false,
        cell: ({ getValue }) => {
          const v = getValue();
          return (
            <Typography variant="body2" sx={{ fontSize: 13 }}>
              {v ?? "—"}
            </Typography>
          );
        },
      },
      {
        id: "success_rate",
        accessorKey: "success_rate",
        header: "% Chats Completed",
        size: 160,
        enableSorting: false,
        cell: PercentCell,
      },
    ];
  }

  // Voice simulations (default)
  return [
    {
      id: "scenarios",
      accessorKey: "scenarios",
      header: "Scenario",
      meta: { flex: 1.5 },
      minSize: 180,
      enableSorting: false,
      cell: TextCell,
    },
    {
      id: "start_time",
      accessorKey: "start_time",
      header: "Run Start Time",
      size: 170,
      enableSorting: false,
      cell: DateCell,
    },
    {
      id: "agent_definition",
      accessorKey: "agent_definition",
      header: "Agent Definition",
      size: 180,
      enableSorting: false,
      cell: AgentDefinitionCell,
    },
    {
      id: "calls_attempted",
      accessorKey: "calls_attempted",
      header: "Calls Attempted",
      size: 140,
      enableSorting: false,
      cell: NumberCell,
    },
    {
      id: "calls_connected_percentage",
      accessorKey: "calls_connected_percentage",
      header: "% Calls Connected",
      size: 160,
      enableSorting: false,
      cell: PercentCell,
    },
    {
      id: "status",
      accessorKey: "status",
      header: "Run Status",
      size: 180,
      enableSorting: false,
      cell: StatusCell,
    },
    {
      id: "duration",
      accessorKey: "duration",
      header: "Duration",
      size: 110,
      enableSorting: false,
      cell: DurationCell,
    },
  ];
}

// ── Empty state ──

function EmptyState() {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexDirection: "column",
        flex: 1,
        minHeight: 300,
      }}
    >
      <img
        style={{ height: "109px", width: "117px" }}
        alt="no runs"
        src="/assets/illustrations/no-dataset-added.svg"
      />
      <Stack direction="column" alignItems="center" sx={{ mt: 1 }}>
        <Typography color="text.primary" typography="subtitle2">
          No rows to show
        </Typography>
        <Typography typography="s1" color="text.secondary">
          No tests are run to show
        </Typography>
      </Stack>
    </Box>
  );
}

// ── Component ──

const TestRunsGrid = ({ agentType, simulationType }) => {
  const { testId } = useParams();
  const navigate = useNavigate();

  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);

  const search = useTestRunsSearchStoreShallow((s) => s.search);
  const debouncedSearch = useDebounce(search?.trim() || "", 300);

  // Reset page when search changes
  useEffect(() => {
    setPage(0);
  }, [debouncedSearch]);

  // Reset the zustand selection store when the grid unmounts
  useEffect(() => {
    return () => {
      useTestRunsGridStore.getState().reset();
    };
  }, []);

  const { data, isLoading, isPending } = useQuery({
    queryKey: ["test-runs-executions", testId, page, pageSize, debouncedSearch],
    queryFn: () =>
      axios.get(endpoints.runTests.detailExecutions(testId), {
        params: {
          page: page + 1,
          limit: pageSize,
          search: debouncedSearch || undefined,
        },
      }),
    select: (d) => d.data,
    keepPreviousData: true,
    enabled: !!testId,
    // Poll while any run is still in progress so the row auto-populates on completion.
    refetchInterval: (query) => {
      const rows = query?.state?.data?.data?.results ?? [];
      const anyInProgress = rows.some(
        (row) => !TERMINAL_STATUSES.includes(row.status),
      );
      return anyInProgress ? POLL_INTERVAL_MS : false;
    },
    refetchIntervalInBackground: false,
  });

  const items = useMemo(() => data?.results ?? [], [data]);
  const total = data?.count ?? 0;

  // ── Selection: store is the single source of truth ──
  // DataTable's local rowSelection map is derived from the zustand store's
  // `toggledNodes` (array of execution IDs). onChange writes back to the store.
  const toggledNodes = useTestRunsGridStore((s) => s.toggledNodes);

  const rowSelection = useMemo(() => {
    const map = {};
    items.forEach((item, idx) => {
      if (toggledNodes.includes(item.id)) {
        map[idx] = true;
      }
    });
    return map;
  }, [items, toggledNodes]);

  const handleRowSelectionChange = useCallback(
    (newSel) => {
      const ids = Object.keys(newSel)
        .filter((k) => newSel[k])
        .map((k) => items[parseInt(k, 10)]?.id)
        .filter(Boolean);
      useTestRunsGridStore.setState({
        toggledNodes: ids,
        selectAll: false,
      });
    },
    [items],
  );

  const columns = useMemo(
    () => buildColumns(agentType ?? AGENT_TYPES.VOICE, simulationType),
    [agentType, simulationType],
  );

  const handleRowClick = useCallback(
    (row) => {
      if (!row?.id) return;
      trackEvent(Events.runTestRunRowClicked, {
        [PropertyName.id]: row.id,
        [PropertyName.propId]: testId,
      });
      navigate(`/dashboard/simulate/test/${testId}/${row.id}`);
    },
    [navigate, testId],
  );

  const showEmpty =
    !isLoading && !isPending && items.length === 0 && !debouncedSearch;

  if (showEmpty) {
    return <EmptyState />;
  }

  return (
    <Box
      sx={{
        flex: 1,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <DataTable
        columns={columns}
        data={items}
        isLoading={isLoading}
        rowCount={total}
        rowSelection={rowSelection}
        onRowSelectionChange={handleRowSelectionChange}
        onRowClick={handleRowClick}
        getRowId={(row) => row.id}
        enableSelection
        rowHeight={48}
        emptyMessage="No test runs to show"
      />
      <DataTablePagination
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(0);
        }}
      />
    </Box>
  );
};

TestRunsGrid.propTypes = {
  agentType: PropTypes.string,
  simulationType: PropTypes.string,
};

export default React.memo(TestRunsGrid);
