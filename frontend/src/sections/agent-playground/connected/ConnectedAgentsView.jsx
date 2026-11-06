import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { CONNECTED_AGENTS_PATH } from "../agentsTabs";
import { Box, Button, Stack, Typography } from "@mui/material";
import { format } from "date-fns";
import SvgColor from "src/components/svg-color";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { DataTable, DataTablePagination } from "src/components/data-table";
import { ROW_HEIGHT } from "src/theme/sizes";
import { useDebounce } from "src/hooks/use-debounce";
import { useConnectedAgents } from "src/api/agents/connectedAgents";
import {
  modalityIcon,
  modalityLabel,
  sourceLabel,
  usedInLabel,
  versionsParts,
} from "./connectedAgentLabels";
import ListToolbar from "src/components/list-toolbar/ListToolbar";
import EmptyState from "src/components/empty-state";

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
  const search = useDebounce(query.trim(), 500);

  const { data, isLoading, isError } = useConnectedAgents({
    page,
    pageSize,
    search,
  });
  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  // One-line rows: the name only.
  const rowHeight = ROW_HEIGHT.default;

  if (!isLoading && !isError && !search && !query && total === 0) {
    return (
      <EmptyState
        icon="solar:link-circle-linear"
        title="No connected agents yet"
        description="Connect your agent from a hosted platform, an endpoint, a repository or an upload. Simulations run against a copy of it."
        action={
          <Button
            variant="contained"
            color="primary"
            size="small"
            onClick={onConnect}
          >
            Connect an agent
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
          onChange={(e) => {
            setQuery(e.target.value);
            setPage(0);
          }}
          size="small"
          placeholder="Search"
          sx={{ width: 250 }}
        />
      </ListToolbar>
      <DataTable
        columns={columns}
        data={items}
        isLoading={isLoading}
        rowCount={total}
        getRowId={(r) => r.id}
        rowHeight={rowHeight}
        onRowClick={(r) => navigate(`${CONNECTED_AGENTS_PATH}/${r.id}`)}
        emptyMessage={
          isError
            ? "Couldn't load connected agents"
            : "No agents match your search"
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
    </Box>
  );
}

ConnectedAgentsView.propTypes = { onConnect: PropTypes.func };
