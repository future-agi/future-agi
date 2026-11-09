import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import Box from "@mui/material/Box";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { paths } from "src/routes/paths";

import {
  GatewayEmptyState,
  GatewayFilterBar,
  GatewayGrid,
  GatewayPageFrame,
} from "../components/frame";
import RunLink from "../logs/RunLink";
import { formatLogTime } from "../logs/logDates";
import { formatMs } from "../logs/logStatus";
import { EMPTY } from "../utils/formatters";
import { RUNS_WINDOW, useRecentRequests } from "./hooks/useRunRequests";
import { groupRuns, runCost } from "./runModel";
import { GATEWAY_MONO } from "../utils/fonts";

// Agent runs that called the gateway: requests that carried a run id
// (x-agentcc-run-id), grouped by run. Built on the request logs, newest
// first; the page says how many requests it read, and any run, also an older
// one, opens by its id.

const cellText = (value) => (value === "" || value == null ? EMPTY : value);

function Text({ value, mono, title }) {
  return (
    <Box
      component="span"
      title={title || (typeof value === "string" ? value : undefined)}
      sx={{
        display: "block",
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
        fontVariantNumeric: "tabular-nums",
        ...(mono && { fontFamily: GATEWAY_MONO }),
      }}
    >
      {cellText(value)}
    </Box>
  );
}

Text.propTypes = {
  value: PropTypes.node,
  mono: PropTypes.bool,
  title: PropTypes.string,
};

// Each minimum holds its header (the label, 32 px of padding, the 24 px menu
// button and, sortable, the 24 px sort arrow) and its widest value. Shown by
// default, they add up to 1,056 px, so the grid fits at 1440 px and Steps,
// the widest flex, takes the spare room. Tokens and Models start hidden (one
// choice away in the column menu's Choose Columns; the run page breaks the
// run down by model): with them the grid would scroll sideways at 1440 px.
// runs/__tests__/RunsPages.test.jsx checks all of it.
const COLUMNS = [
  {
    colId: "run",
    headerName: "Run",
    minWidth: 132,
    flex: 1,
    valueGetter: ({ data }) => data.runId,
    // The row opens the run too; the link is there for keyboard, middle
    // click and copy. It reads like the other cells, coloured on hover.
    cellRenderer: ({ data }) => (
      <RunLink
        runId={data.runId}
        variant="inherit"
        underline="hover"
        noWrap
        sx={{
          display: "block",
          fontFamily: "inherit",
          color: "inherit",
          "&:hover, &:focus-visible": { color: "primary.main" },
        }}
      />
    ),
  },
  {
    colId: "agent",
    headerName: "Agent",
    minWidth: 124,
    flex: 1,
    valueGetter: ({ data }) => data.agentId,
    cellRenderer: ({ value }) => <Text value={value} />,
  },
  {
    colId: "steps",
    headerName: "Steps",
    minWidth: 120,
    flex: 3,
    sortable: false,
    valueGetter: ({ data }) => data.steps.join(" → "),
    cellRenderer: ({ value }) => <Text value={value} />,
  },
  {
    colId: "requests",
    headerName: "Requests",
    minWidth: 144,
    flex: 0.5,
    valueGetter: ({ data }) => data.requests,
    cellRenderer: ({ value }) => <Text value={value} />,
  },
  {
    colId: "errors",
    headerName: "Errors",
    minWidth: 122,
    flex: 0.5,
    valueGetter: ({ data }) => data.errors,
    cellRenderer: ({ value }) => (
      <Box
        component="span"
        sx={{ color: value > 0 ? "error.main" : "text.secondary" }}
      >
        {value}
      </Box>
    ),
  },
  {
    colId: "models",
    headerName: "Models",
    minWidth: 110,
    flex: 1,
    hide: true,
    sortable: false,
    valueGetter: ({ data }) => data.models.join(", "),
    cellRenderer: ({ value }) => <Text value={value} />,
  },
  {
    colId: "cost",
    headerName: "Cost",
    minWidth: 120,
    flex: 0.5,
    // Unknown (no call priced) sorts as missing, not as 0; a run with some
    // unpriced calls reads a floor.
    valueGetter: ({ data }) =>
      runCost(data, { cell: true }).unknown ? null : data.cost,
    cellRenderer: ({ data }) => {
      const { text, title } = runCost(data, { cell: true });
      return <Text value={text} title={title ?? undefined} />;
    },
  },
  {
    colId: "tokens",
    headerName: "Tokens",
    minWidth: 130,
    flex: 0.5,
    hide: true,
    valueGetter: ({ data }) => data.tokens,
    cellRenderer: ({ value }) => (
      <Text value={Number(value).toLocaleString()} />
    ),
  },
  {
    colId: "duration",
    headerName: "Duration",
    minWidth: 138,
    flex: 0.5,
    valueGetter: ({ data }) => data.durationMs,
    cellRenderer: ({ value }) => <Text value={formatMs(value)} />,
  },
  {
    colId: "last",
    headerName: "Last call",
    minWidth: 156,
    flex: 1,
    sort: "desc",
    valueGetter: ({ data }) => data.lastAt,
    cellRenderer: ({ value }) => <Text value={formatLogTime(value)} />,
  },
];

export default function RunsSection() {
  const navigate = useNavigate();
  const { data, isLoading, error, refetch } = useRecentRequests();
  const [search, setSearch] = useState("");
  const [openId, setOpenId] = useState("");

  const runs = useMemo(() => groupRuns(data?.results), [data]);
  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return runs;
    return runs.filter((run) =>
      [run.runId, run.agentId, run.workflow, ...run.steps, ...run.models]
        .join(" ")
        .toLowerCase()
        .includes(q),
    );
  }, [runs, search]);

  const read = data?.results?.length ?? 0;
  const total = data?.count ?? 0;

  const openRun = (event) => {
    event.preventDefault();
    const id = openId.trim();
    if (id) navigate(paths.dashboard.gateway.runDetail(id));
  };

  return (
    <GatewayPageFrame
      title="Runs"
      description="Agent runs that called the gateway: requests that carried a run id, grouped by run."
    >
      <GatewayFilterBar
        search={search}
        onSearchChange={setSearch}
        searchPlaceholder="Filter by run, agent, step or model"
        actions={
          <Box component="form" onSubmit={openRun} sx={{ display: "flex" }}>
            <TextField
              size="small"
              value={openId}
              onChange={(event) => setOpenId(event.target.value)}
              placeholder="Open a run by id"
              inputProps={{ "aria-label": "Open a run by id" }}
              sx={{
                width: { xs: "100%", sm: 220 },
                "& .MuiOutlinedInput-root": { height: "30px" },
              }}
            />
          </Box>
        }
      />

      {!error && read > 0 && (
        <Typography typography="s2" color="text.secondary">
          {`Runs in the newest ${Math.min(read, RUNS_WINDOW).toLocaleString()} of ${total.toLocaleString()} requests. `}
          {total > read
            ? "Counts cover the requests read; open a run for all of its requests, or open an older run by its id."
            : "Open a run for its timeline, steps and models."}
        </Typography>
      )}

      {error ? (
        <GatewayEmptyState
          icon="alertCircle"
          title="Runs could not be loaded"
          description={error.message || "The gateway API did not answer."}
          actionLabel="Retry"
          onAction={() => refetch()}
        />
      ) : (
        <GatewayGrid
          rowData={isLoading ? undefined : shown}
          loading={isLoading}
          columnDefs={COLUMNS}
          stateKey="gateway-runs"
          getRowId={(params) => params.data.runId}
          onRowClicked={(event) => {
            if (event.event?.target?.closest?.("a")) return;
            navigate(paths.dashboard.gateway.runDetail(event.data.runId));
          }}
          rowStyle={{ cursor: "pointer" }}
          rowHeight={36}
          emptyState={
            search && runs.length > 0
              ? {
                  variant: "no-results",
                  title: "No matching runs",
                  description: "No run in this window matches the filter.",
                  actionLabel: "Clear filter",
                  onAction: () => setSearch(""),
                }
              : {
                  icon: "runs",
                  title: "No agent runs yet",
                  description:
                    "Requests that send an x-agentcc-run-id header (with x-agentcc-agent-id and x-agentcc-step) are grouped here by run.",
                }
          }
        />
      )}
    </GatewayPageFrame>
  );
}
