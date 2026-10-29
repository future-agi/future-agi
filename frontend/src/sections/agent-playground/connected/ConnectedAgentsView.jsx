import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CONNECTED_AGENTS_PATH } from "../agentsTabs";
import { Box, Button, Stack, Typography } from "@mui/material";
import { format } from "date-fns";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { DataTable, DataTablePagination } from "src/components/data-table";
import { useDebounce } from "src/hooks/use-debounce";
import { useConnectedAgents } from "src/api/agents/connectedAgents";
import {
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
    cell: ({ row }) => {
      const a = row.original;
      return (
        <Stack justifyContent="center" sx={{ minWidth: 0, height: "100%" }}>
          <Typography variant="body2" noWrap sx={{ fontWeight: 500 }}>
            {a.name}
          </Typography>
          {a.description && (
            <Typography
              noWrap
              title={a.description}
              sx={{ typography: "s3", color: "text.subtitle" }}
            >
              {a.description}
            </Typography>
          )}
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
  const search = useDebounce(query.trim(), 500);

  const { data, isLoading, isError } = useConnectedAgents({
    page,
    pageSize,
    search,
  });
  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const rowHeight = useMemo(
    () => (items.some((a) => a.description) ? 52 : 44),
    [items],
  );

  if (!isLoading && !isError && !search && !query && total === 0) {
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
      <FormSearchField
        searchQuery={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setPage(0);
        }}
        size="small"
        placeholder="Search"
        sx={{ width: 250, "& .MuiOutlinedInput-root": { height: "30px" } }}
      />
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
