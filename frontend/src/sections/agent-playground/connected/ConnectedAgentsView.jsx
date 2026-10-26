import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { CONNECTED_AGENTS_PATH } from "../agentsTabs";
import { Box, Button, Stack, Typography } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { format } from "date-fns";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import FilterPanel from "src/components/filter-panel/FilterPanel";
import { ConfirmDialog } from "src/components/custom-dialog";
import { DataTable, DataTablePagination } from "src/components/data-table";
import { useDebounce } from "src/hooks/use-debounce";
import {
  useConnectedAgents,
  useDeleteConnectedAgents,
} from "src/api/agents/connectedAgents";
import {
  CONNECTED_AGENT_FILTER_FIELDS,
  deleteResultMessage,
  modalityIcon,
  modalityLabel,
  sourceLabel,
  usedInLabel,
  versionsParts,
} from "./connectedAgentLabels";

const PAGE_SIZE_DEFAULT = 25;
const MONO = "ui-monospace, Menlo, monospace";

const CellText = ({ children, color = "text.primary" }) => (
  <Typography variant="body2" noWrap sx={{ fontSize: 13, color }}>
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
    cell: ({ row }) => (
      <Stack justifyContent="center" sx={{ minWidth: 0, height: "100%" }}>
        <Typography variant="body2" noWrap sx={{ fontWeight: 500 }}>
          {row.original.name}
        </Typography>
      </Stack>
    ),
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
          <Box component="span" sx={{ fontFamily: MONO, fontWeight: 600 }}>
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
        <CellText color={empty ? "text.disabled" : "text.primary"}>
          {text}
        </CellText>
      );
    },
  },
  {
    id: "createdAt",
    accessorKey: "createdAt",
    header: "Created at",
    size: 180,
    enableSorting: false,
    cell: ({ row }) => (
      <CellText>
        {row.original.createdAt
          ? format(new Date(row.original.createdAt), "dd-MM-yyyy, h:mm a")
          : "—"}
      </CellText>
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
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(PAGE_SIZE_DEFAULT);
  const [filters, setFilters] = useState(null);
  const [filterAnchorEl, setFilterAnchorEl] = useState(null);
  const search = useDebounce(query.trim(), 500);
  const activeFilterCount = filters ? Object.keys(filters).length : 0;

  const { data, isLoading, isError } = useConnectedAgents({
    page,
    pageSize,
    search,
    filters,
  });
  const items = data?.items ?? [];
  const total = data?.total ?? 0;

  const [rowSelection, setRowSelection] = useState({});
  const [confirmDelete, setConfirmDelete] = useState(false);
  const deleteAgents = useDeleteConnectedAgents();
  const selected = items.filter((_, index) => rowSelection[index]);

  useEffect(() => {
    setRowSelection({});
  }, [page, pageSize, search, filters]);

  const handleDelete = () => {
    deleteAgents.mutate(
      selected.map((agent) => agent.id),
      {
        onSuccess: ({ data: result }) => {
          const deleted = result.agents_updated ?? 0;
          enqueueSnackbar(
            deleteResultMessage({
              deleted,
              skipped: result.skipped_agent_ids?.length ?? 0,
            }),
            { variant: deleted ? "success" : "warning" },
          );
          setRowSelection({});
          setConfirmDelete(false);
        },
        onError: () => {
          enqueueSnackbar("Couldn't delete the selected agents", {
            variant: "error",
          });
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
      <Box
        sx={{
          p: 5,
          textAlign: "center",
          border: "1px dashed",
          borderColor: "divider",
          borderRadius: 1.5,
        }}
      >
        <Iconify
          icon="solar:link-circle-linear"
          width={28}
          sx={{ color: "text.subtitle" }}
        />
        <Typography sx={{ typography: "s1", fontWeight: 600, mt: 1 }}>
          No connected agents yet
        </Typography>
        <Typography
          sx={{
            typography: "s2",
            color: "text.subtitle",
            mt: 0.5,
            mb: 2,
            maxWidth: 460,
            mx: "auto",
          }}
        >
          Connect your agent from a hosted platform, an endpoint, a repository
          or an upload. Simulations run against a copy of it.
        </Typography>
        <Button
          variant="contained"
          color="primary"
          size="small"
          onClick={onConnect}
          sx={{ typography: "s2", fontWeight: 700 }}
        >
          Connect an agent
        </Button>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        flex: 1,
        minHeight: 0,
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1}>
        <FormSearchField
          searchQuery={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setPage(0);
          }}
          size="small"
          placeholder="Search"
          sx={{ width: 280, "& .MuiOutlinedInput-root": { height: "30px" } }}
        />
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="solar:filter-linear" width={16} />}
          endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={14} />}
          onClick={(e) => setFilterAnchorEl(e.currentTarget)}
          sx={{
            textTransform: "none",
            fontSize: "13px",
            height: "30px",
            borderColor: activeFilterCount > 0 ? "primary.main" : "divider",
            color: activeFilterCount > 0 ? "primary.main" : "text.secondary",
          }}
        >
          Filter{activeFilterCount > 0 ? ` (${activeFilterCount})` : ""}
        </Button>
        <Box flex={1} />
        {selected.length > 0 ? (
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {selected.length} selected
            </Typography>
            <Button
              size="small"
              variant="outlined"
              color="error"
              startIcon={
                <Iconify icon="solar:trash-bin-trash-linear" width={16} />
              }
              onClick={() => setConfirmDelete(true)}
              sx={{ textTransform: "none", height: "30px" }}
            >
              Delete
            </Button>
            <Button
              size="small"
              onClick={() => setRowSelection({})}
              sx={{
                textTransform: "none",
                height: "30px",
                color: "text.secondary",
              }}
            >
              Cancel
            </Button>
          </Stack>
        ) : (
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Connected = we can reach it: its repo, platform, endpoint or traces.
          </Typography>
        )}
      </Stack>
      <FilterPanel
        anchorEl={filterAnchorEl}
        open={Boolean(filterAnchorEl)}
        onClose={() => setFilterAnchorEl(null)}
        filterFields={CONNECTED_AGENT_FILTER_FIELDS}
        currentFilters={filters}
        onApply={(next) => {
          setFilters(next);
          setPage(0);
        }}
        basicOnly
      />
      <DataTable
        columns={columns}
        data={items}
        isLoading={isLoading}
        rowCount={total}
        getRowId={(r) => r.id}
        rowHeight={44}
        enableSelection
        rowSelection={rowSelection}
        onRowSelectionChange={setRowSelection}
        onRowClick={(r) => navigate(`${CONNECTED_AGENTS_PATH}/${r.id}`)}
        emptyMessage={
          isError
            ? "Couldn't load connected agents"
            : "No agents match your search or filters"
        }
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
      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title={`Delete ${selected.length === 1 ? "agent" : `${selected.length} agents`}?`}
        content="Agents still used in an environment are skipped. The rest are deleted with all their versions."
        action={
          <LoadingButton
            variant="contained"
            color="error"
            size="small"
            loading={deleteAgents.isPending}
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
