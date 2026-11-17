import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { CONNECTED_AGENTS_PATH, CONNECT_AGENT_PLACE } from "../agentsTabs";
import { Box, Button, Stack, Typography } from "@mui/material";
import { format } from "date-fns";
import SvgColor from "src/components/svg-color";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { DataTable, DataTablePagination } from "src/components/data-table";
import OverflowCellText from "src/components/data-table/OverflowCellText";
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
const CellText = ({ children, color = "text.secondary", title }) => (
  <Typography variant="body2" noWrap title={title} sx={{ color }}>
    {children}
  </Typography>
);

CellText.propTypes = {
  children: PropTypes.node,
  color: PropTypes.string,
  title: PropTypes.string,
};

// Every column's minimum (764 px) fits the 768 px a 1024 px window leaves
// the table (1024 - 200 menu - 2 x 24 padding, less the 8 px bar when the
// rows scroll), so no column hides past the right edge; wider windows give
// the room to Agent name and Used in. Value widths are Inter's own at
// 12 px/400, plus the cell's 2 x 10 px padding and 1 px for rounding.
const columns = [
  {
    id: "name",
    accessorKey: "name",
    header: "Agent name",
    meta: { flex: 2 },
    // About 142 px at 1024, 359 at 1280, 466 at 1440. A longer name ends in
    // an ellipsis and shows whole on hover or when its cell takes focus.
    minSize: 138,
    enableSorting: false,
    cell: ({ row }) => {
      const a = row.original;
      return (
        <Stack justifyContent="center" sx={{ minWidth: 0, height: "100%" }}>
          {/* One line: the name; the tooltip adds the description. */}
          <OverflowCellText
            text={a.name}
            detail={a.description || undefined}
            sx={{ fontWeight: 500 }}
          />
        </Stack>
      );
    },
  },
  {
    id: "modality",
    accessorKey: "modality",
    header: "Modality",
    // "Voice · Outbound" (98.7 px) after its 16 px icon and 6 px gap.
    size: 142,
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
    size: 90,
    enableSorting: false,
    cell: ({ row }) => <CellText>{sourceLabel(row.original.source)}</CellText>,
  },
  {
    id: "versions",
    accessorKey: "latestLabel",
    header: "Versions",
    size: 100,
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
    // "No environments yet" (118.1 px), whole. A long environment name
    // ends in an ellipsis and shows whole on hover or when its cell takes
    // focus.
    minSize: 140,
    enableSorting: false,
    cell: ({ row }) => {
      const { text, empty } = usedInLabel(row.original);
      return (
        <OverflowCellText
          text={text}
          sx={{ color: empty ? "text.disabled" : "text.secondary" }}
        />
      );
    },
  },
  {
    id: "createdAt",
    accessorKey: "createdAt",
    header: "Created at",
    // Every "dd-MM-yyyy, h:mm a" value whole: the widest from 2026 to 2030,
    // "04-04-2030, 10:44 AM", is 132.3 px.
    size: 154,
    enableSorting: false,
    cell: ({ row }) => {
      const at = row.original.createdAt
        ? format(new Date(row.original.createdAt), "dd-MM-yyyy, h:mm a")
        : null;
      return <CellText title={at ?? undefined}>{at ?? "—"}</CellText>;
    },
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
    // Name only the ways in that Build environment offers, and make no
    // claim about a copy: hosted agents are simulated on their own platform.
    return (
      <EmptyState
        icon="solar:link-circle-linear"
        title="No connected agents yet"
        description="Connect your agent from a hosted platform, a repository or an upload."
        action={
          // Connecting happens in Simulate, so the button names where it goes.
          <Button
            variant="contained"
            color="primary"
            size="small"
            onClick={onConnect}
          >
            Connect in {CONNECT_AGENT_PLACE}
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
        showScrollbar
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
