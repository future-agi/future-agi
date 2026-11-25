import PropTypes from "prop-types";
import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { useQueryClient } from "@tanstack/react-query";
import { CONNECTED_AGENTS_PATH } from "../agentsTabs";
import { Box, Button, Stack, Typography } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { relativeTime } from "src/utils/format-time";
import SvgColor from "src/components/svg-color";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { FilterButton } from "src/components/filter";
import SelectionBar from "src/components/selection-bar";
import { ConfirmDialog } from "src/components/custom-dialog";
import { DataTable, DataTablePagination } from "src/components/data-table";
import { ROW_HEIGHT } from "src/theme/sizes";
import { useDebounce } from "src/hooks/use-debounce";
import {
  useConnectedAgents,
  useDeleteConnectedAgents,
} from "src/api/agents/connectedAgents";
import {
  CONNECTED_AGENT_FILTER_FIELDS,
  deleteBlockedReason,
  deleteConfirmText,
  deleteResultMessage,
  filtersToRows,
  modalityIcon,
  modalityLabel,
  sourceLabel,
  rowsToFilters,
  usedInLabel,
  versionsParts,
} from "./connectedAgentLabels";
import ListToolbar from "src/components/list-toolbar/ListToolbar";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import useCanEditAgent from "../hooks/useCanEditAgent";

const DELETE_DENIED = "You don't have permission to delete agents.";

const PAGE_SIZE_DEFAULT = 25;
const MONO = "ui-monospace, Menlo, monospace";

// Table values are 12/18 (body-sm), one size under the row title's weight.
const CellText = ({ children, color = "text.secondary" }) => (
  <Typography variant="body2" noWrap sx={{ color }}>
    {children}
  </Typography>
);

CellText.propTypes = { children: PropTypes.node, color: PropTypes.string };

const columns = [
  {
    id: "name",
    accessorKey: "name",
    header: "Agent name",
    meta: { flex: 2 },
    minSize: 280,
    enableSorting: false,
    cell: ({ row }) => {
      const a = row.original;
      return (
        <Stack justifyContent="center" sx={{ minWidth: 0, height: "100%" }}>
          {/* One line: the name; the description shows on hover. */}
          <Typography
            variant="body2"
            noWrap
            title={a.description || a.name}
            sx={{ fontWeight: 500 }}
          >
            {a.name}
          </Typography>
        </Stack>
      );
    },
  },
  {
    id: "modality",
    accessorKey: "modality",
    header: "Modality",
    size: 160,
    enableSorting: false,
    cell: ({ row }) => {
      const icon = modalityIcon(row.original);
      return (
        <Stack direction="row" alignItems="center" spacing={0.75} minWidth={0}>
          {icon && (
            <SvgColor
              src={icon}
              data-testid={`modality-icon-${row.original.id}`}
              sx={{
                width: 16,
                height: 16,
                flexShrink: 0,
                color: "text.secondary",
              }}
            />
          )}
          <CellText>{modalityLabel(row.original)}</CellText>
        </Stack>
      );
    },
  },
  {
    id: "source",
    accessorKey: "source",
    header: "Source",
    size: 150,
    enableSorting: false,
    cell: ({ row }) => <CellText>{sourceLabel(row.original.source)}</CellText>,
  },
  {
    id: "versions",
    accessorKey: "latestLabel",
    header: "Versions",
    size: 140,
    enableSorting: false,
    cell: ({ row }) => {
      const { version, rest } = versionsParts(row.original);
      return (
        <CellText>
          <Box component="span" sx={{ fontFamily: MONO, fontWeight: 500 }}>
            {version}
          </Box>
          <Box component="span" sx={{ color: "text.secondary" }}>
            {rest}
          </Box>
        </CellText>
      );
    },
  },
  {
    id: "usedIn",
    accessorKey: "environmentCount",
    header: "Used in",
    meta: { flex: 1 },
    minSize: 200,
    enableSorting: false,
    cell: ({ row }) => {
      const { text, empty } = usedInLabel(row.original);
      return (
        <CellText color={empty ? "text.disabled" : "text.secondary"}>
          {text}
        </CellText>
      );
    },
  },
  {
    id: "updatedAt",
    accessorKey: "updatedAt",
    header: "Last updated",
    size: 150,
    enableSorting: false,
    cell: ({ row }) => (
      <CellText>{relativeTime(row.original.updatedAt)}</CellText>
    ),
  },
  {
    id: "createdAt",
    accessorKey: "createdAt",
    header: "Connected on",
    size: 150,
    enableSorting: false,
    cell: ({ row }) => (
      <CellText>{relativeTime(row.original.createdAt)}</CellText>
    ),
  },
];

/**
 * Every agent connected in the workspace — hosted platforms, endpoints,
 * repositories and uploads. One row per agent, however many environments
 * test it.
 */
export default function ConnectedAgentsView({ onConnect }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(PAGE_SIZE_DEFAULT);
  const [filters, setFilters] = useState(null);
  const filterRows = useMemo(() => filtersToRows(filters), [filters]);
  const search = useDebounce(query.trim(), 500);
  const activeFilterCount = filters ? Object.keys(filters).length : 0;

  const { data, isLoading, isError, isPlaceholderData, refetch } =
    useConnectedAgents({
      page,
      pageSize,
      search,
      filters,
    });
  const { canDelete } = useCanEditAgent();
  const items = useMemo(() => data?.items ?? [], [data]);
  const total = data?.total ?? 0;
  // One-line rows: the name only.
  const rowHeight = ROW_HEIGHT.default;

  const [picked, setPicked] = useState([]);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmed, setConfirmed] = useState({ deletable: [], inUse: [] });
  const deleteAgents = useDeleteConnectedAgents();
  const deleting = useRef(false);
  const latest = useRef({ page, pageSize, total });
  latest.current = { page, pageSize, total };
  const selected = useMemo(() => {
    const fresh = new Map(items.map((agent) => [agent.id, agent]));
    return picked.map((agent) => fresh.get(agent.id) ?? agent);
  }, [items, picked]);
  const rowSelection = useMemo(() => {
    const ids = new Set(selected.map((agent) => agent.id));
    return Object.fromEntries(
      items.flatMap((agent, index) =>
        ids.has(agent.id) ? [[index, true]] : [],
      ),
    );
  }, [items, selected]);
  const inUse = selected.filter((agent) => agent.environmentCount > 0);
  const deletable = selected.filter((agent) => !(agent.environmentCount > 0));
  const refreshing = isPlaceholderData;
  const deleteBlocked = !canDelete
    ? DELETE_DENIED
    : selected.length > 0 && deletable.length === 0
      ? deleteBlockedReason(inUse)
      : undefined;

  const goToPage = (next) => {
    setPicked([]);
    setPage(next);
  };
  const changeSearch = (value) => {
    setPicked([]);
    setQuery(value);
    setPage(0);
  };
  const changeFilters = (next) => {
    setPicked([]);
    setFilters(next);
    setPage(0);
  };
  const clearSearchAndFilters = () => {
    setPicked([]);
    setQuery("");
    setFilters(null);
    setPage(0);
  };
  const openConfirm = () => {
    setConfirmed({ deletable, inUse });
    setConfirmDelete(true);
  };
  const closeConfirm = () => {
    if (!deleting.current) setConfirmDelete(false);
  };

  const handleDelete = () => {
    if (deleting.current || !canDelete || !confirmed.deletable.length) return;
    deleting.current = true;
    deleteAgents.mutate(
      confirmed.deletable.map((agent) => agent.id),
      {
        onSuccess: ({ data: result }) => {
          const deleted = result.agents_updated ?? 0;
          const skippedIds = new Set(result.skipped_agent_ids ?? []);
          const skipped = [
            ...confirmed.inUse,
            ...confirmed.deletable.filter((agent) => skippedIds.has(agent.id)),
          ];
          const now = latest.current;
          const lastPage = Math.max(
            0,
            Math.ceil((now.total - deleted) / now.pageSize) - 1,
          );
          const pageGone = now.page > lastPage;
          if (pageGone) setPage(lastPage);
          queryClient.invalidateQueries({
            queryKey: ["agents", "connected"],
            refetchType: pageGone ? "none" : "active",
          });
          enqueueSnackbar(deleteResultMessage(deleted, skipped), {
            variant: deleted ? "success" : "warning",
          });
          setPicked([]);
          setConfirmDelete(false);
        },
        onError: () => {
          enqueueSnackbar("Couldn't delete the selected agents", {
            variant: "error",
          });
        },
        onSettled: () => {
          deleting.current = false;
        },
      },
    );
  };

  if (
    !isLoading &&
    !isError &&
    !search &&
    !query &&
    !activeFilterCount &&
    total === 0
  ) {
    return (
      <EmptyState
        icon="solar:link-circle-linear"
        title="No agents yet"
        description="Bring one from code or a hosted platform. Simulations run against a copy of it."
        action={
          <Button
            variant="contained"
            color="primary"
            size="small"
            onClick={onConnect}
          >
            Connect agent
          </Button>
        }
      />
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        minHeight: 0,
      }}
    >
      {/* Same toolbar wrapper as the Agent Playground tab: 12px below the
          tabs, 8px above the table, so switching tabs moves nothing. */}
      <ListToolbar>
        <FormSearchField
          searchQuery={query}
          onChange={(e) => changeSearch(e.target.value)}
          size="small"
          placeholder="Search"
          sx={{ width: 280 }}
        />
        <FilterButton
          fields={CONNECTED_AGENT_FILTER_FIELDS}
          value={filterRows}
          onChange={(rows) => changeFilters(rowsToFilters(rows))}
          showQueryTab={false}
        />
        <Box sx={{ flex: 1 }} />
        {selected.length > 0 ? (
          <SelectionBar
            count={selected.length}
            onCancel={() => setPicked([])}
            actions={[
              {
                label: "Delete",
                icon: "solar:trash-bin-trash-linear",
                onClick: openConfirm,
                disabled: Boolean(deleteBlocked) || refreshing,
                tooltip: deleteBlocked,
              },
            ]}
          />
        ) : (
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
            Connected means we can reach it: its repository, platform or upload.
          </Typography>
        )}
      </ListToolbar>
      {isError && !items.length ? (
        <ErrorState
          title="Couldn't load connected agents"
          onRetry={() => refetch()}
          sx={{ flex: 1 }}
        />
      ) : !isLoading && !refreshing && total === 0 ? (
        <EmptyState
          variant="no-results"
          icon="solar:magnifer-linear"
          title="No agents match your search or filters"
          description="Try a different search, or clear the filters to see every connected agent."
          onClearFilters={clearSearchAndFilters}
        />
      ) : (
        <DataTable
          columns={columns}
          data={items}
          isLoading={isLoading || refreshing}
          rowCount={total}
          getRowId={(r) => r.id}
          rowHeight={rowHeight}
          enableSelection
          rowSelection={rowSelection}
          onRowSelectionChange={(selection) =>
            setPicked(items.filter((_, index) => selection[index]))
          }
          onRowClick={(r) => navigate(`${CONNECTED_AGENTS_PATH}/${r.id}`)}
          emptyMessage="No agents match your search or filters"
        />
      )}
      {total > 0 && (
        <DataTablePagination
          page={page}
          pageSize={pageSize}
          total={total}
          onPageChange={goToPage}
          onPageSizeChange={(size) => {
            setPicked([]);
            setPageSize(size);
            setPage(0);
          }}
        />
      )}
      <ConfirmDialog
        open={confirmDelete}
        onClose={closeConfirm}
        closeDisabled={deleteAgents.isPending}
        title={
          confirmed.deletable.length === 1
            ? `Delete ${confirmed.deletable[0].name}?`
            : `Delete ${confirmed.deletable.length} agents?`
        }
        content={deleteConfirmText(confirmed.deletable, confirmed.inUse)}
        action={
          <LoadingButton
            variant="contained"
            color="error"
            size="small"
            loading={deleteAgents.isPending}
            disabled={!canDelete || !confirmed.deletable.length}
            onClick={handleDelete}
          >
            Delete
          </LoadingButton>
        }
      />
    </Box>
  );
}

ConnectedAgentsView.propTypes = { onConnect: PropTypes.func };
