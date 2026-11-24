import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Box, Link, MenuItem, TextField, Typography } from "@mui/material";

import { DataTable } from "src/components/data-table";
import DataTablePagination from "src/components/data-table/DataTablePagination";
import ListToolbar from "src/components/list-toolbar";
import { paths } from "src/routes/paths";
import { fDateTime } from "src/utils/format-time";
import {
  FIX_LIVE_STATES,
  FIX_RUNS_PAGE_SIZE,
  useEnvironmentFixRuns,
} from "src/api/simulate-environments/fixRuns";

import FixStatusChip from "./FixStatusChip";
import {
  STATE_LABEL,
  fixName,
  formatCredits,
  isShipped,
  listResult,
  winnerOf,
} from "./fixRunView";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";

const ROW_HEIGHT = 44;
const HEADER_HEIGHT = 40;

const dash = (
  <Typography sx={{ typography: "s3", color: "text.disabled" }}>—</Typography>
);

function HandOff({ fix }) {
  const winner = winnerOf(fix);
  if (winner?.pull_request_url) {
    return (
      <Link
        href={winner.pull_request_url}
        target="_blank"
        rel="noopener noreferrer"
        onClick={(e) => e.stopPropagation()}
        sx={{ typography: "s2" }}
      >
        Pull request
      </Link>
    );
  }
  if (isShipped(fix, winner)) {
    return <Typography sx={{ typography: "s2" }}>Default</Typography>;
  }
  return dash;
}
HandOff.propTypes = { fix: PropTypes.object.isRequired };

const columns = [
  {
    id: "name",
    header: "Name",
    meta: { flex: 1.6 },
    minSize: 260,
    enableSorting: false,
    cell: ({ row: { original: fix } }) => (
      <Box minWidth={0}>
        <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
          {fixName(fix)}
        </Typography>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
          {`${fix.agent.name} · v${fix.agent.version_number}`}
        </Typography>
      </Box>
    ),
  },
  {
    id: "status",
    header: "Status",
    size: 190,
    enableSorting: false,
    cell: ({ row: { original: fix } }) => <FixStatusChip state={fix.state} />,
  },
  {
    id: "result",
    header: "Result",
    size: 190,
    enableSorting: false,
    cell: ({ row: { original: fix } }) => {
      const result = listResult(fix);
      if (!result) return dash;
      return (
        <Box minWidth={0}>
          <Typography
            noWrap
            sx={{ typography: "s2", fontVariantNumeric: "tabular-nums" }}
          >
            {result.text}
          </Typography>
          <Typography
            noWrap
            sx={{
              typography: "s3",
              color: result.broke ? BUILD_TONES.red : "text.subtitle",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {result.note}
          </Typography>
        </Box>
      );
    },
  },
  {
    id: "output",
    header: "Hand-off",
    size: 150,
    enableSorting: false,
    cell: ({ row: { original: fix } }) => <HandOff fix={fix} />,
  },
  {
    id: "cost",
    header: "Cost",
    size: 130,
    enableSorting: false,
    cell: ({ row: { original: fix } }) => (
      <Typography
        noWrap
        sx={{ typography: "s2", fontVariantNumeric: "tabular-nums" }}
      >
        {formatCredits(fix.spent_credits)}
      </Typography>
    ),
  },
  {
    id: "createdAt",
    header: "Created",
    size: 170,
    enableSorting: false,
    cell: ({ row: { original: fix } }) => (
      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
        {fDateTime(fix.created_at)}
      </Typography>
    ),
  },
];

// The status filter the design's Epochs list has (ImprovementSourcesList), for
// one environment: queued, running and stopping read as one Running. Each
// option names the states the list asks the server for.
const ALL = "all";
const LIVE = "live";
const LIVE_STATES = [...FIX_LIVE_STATES];
const STATUS_OPTIONS = [
  { value: ALL, label: "All statuses", states: [] },
  { value: LIVE, label: "Running", states: LIVE_STATES },
  ...Object.entries(STATE_LABEL)
    .filter(([state]) => !LIVE_STATES.includes(state))
    .map(([value, label]) => ({ value, label, states: [value] })),
];

const statusOf = (error) => error?.response?.status ?? error?.statusCode;

export default function ImprovementsPanel({ env }) {
  const navigate = useNavigate();
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(FIX_RUNS_PAGE_SIZE);
  const [status, setStatus] = useState(ALL);
  const filtering = status !== ALL;
  const option = STATUS_OPTIONS.find((o) => o.value === status);
  const { fixes, count, isLoading, error } = useEnvironmentFixRuns(env.id, {
    page,
    pageSize,
    states: option.states,
  });
  // Epochs leave a status as they run, so a poll can shrink the count past
  // this page: the server refuses a page past the end, and the one before it
  // says where the end is now. Until then nothing reads as empty.
  const lastPage = Math.max(0, Math.ceil(count / pageSize) - 1);
  const gone = statusOf(error) === 404;
  const pastEnd = page > 0 && (gone || (!isLoading && page > lastPage));
  useEffect(() => {
    if (pastEnd) setPage(gone ? page - 1 : lastPage);
  }, [pastEnd, gone, page, lastPage]);
  return (
    // The tab body scrolls as a block; a full-height panel makes the grid
    // scroll inside itself, so the column header stays put and the pager
    // sits under it.
    <Box
      sx={{
        height: "100%",
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <ListToolbar>
        <TextField
          select
          size="small"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(0);
          }}
          SelectProps={{ SelectDisplayProps: { "aria-label": "Status" } }}
          sx={{ minWidth: 180 }}
        >
          {STATUS_OPTIONS.map((o) => (
            <MenuItem key={o.value} value={o.value}>
              {o.label}
            </MenuItem>
          ))}
        </TextField>
      </ListToolbar>
      <DataTable
        columns={columns}
        data={fixes}
        isLoading={isLoading || pastEnd}
        rowCount={count}
        getRowId={(fix) => fix.id}
        onRowClick={(fix) =>
          navigate(
            paths.dashboard.simulate.environments.improvement(env.id, fix.id),
          )
        }
        rowHeight={ROW_HEIGHT}
        headerHeight={HEADER_HEIGHT}
        emptyMessage={
          filtering
            ? `No epoch in this environment has the status “${option.label}”. Choose All statuses to see every epoch.`
            : "No epochs have run in this environment yet. To start one, open Debug failures on a finished run and choose Run Epoch."
        }
      />
      {count > 0 && (
        <DataTablePagination
          page={page}
          pageSize={pageSize}
          total={count}
          onPageChange={setPage}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setPage(0);
          }}
        />
      )}
    </Box>
  );
}

ImprovementsPanel.propTypes = {
  env: PropTypes.shape({ id: PropTypes.string.isRequired }).isRequired,
};
